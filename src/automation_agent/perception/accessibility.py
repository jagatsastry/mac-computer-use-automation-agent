"""macOS Accessibility API bridge for UI element discovery.

Wraps pyobjc's ApplicationServices and AppKit to provide:
- UI element tree traversal
- Element search by role/title/description
- Element position lookup (pixel-accurate, logical coordinates)
- Frontmost app/window state

Requires: Accessibility permission in System Settings.
All AX calls are wrapped in try/except — returns None/empty on errors.
"""

import logging
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# pyobjc frameworks (AppKit, ApplicationServices) are NOT imported at module
# level.  Instead, they are resolved lazily from sys.modules at call time.
# This ensures test mocks injected via sys.modules.setdefault() are always
# picked up, regardless of the order in which test files are collected.


def _resolve_appkit() -> Any:
    """Resolve AppKit from sys.modules (supports test mock injection)."""
    return sys.modules.get("AppKit")


def _resolve_ax_create_app() -> Any:
    """Resolve AXUIElementCreateApplication from sys.modules."""
    app_svc = sys.modules.get("ApplicationServices")
    if app_svc is not None:
        return getattr(app_svc, "AXUIElementCreateApplication", None)
    return None


def _resolve_ax_copy_attr() -> Any:
    """Resolve AXUIElementCopyAttributeValue from sys.modules."""
    app_svc = sys.modules.get("ApplicationServices")
    if app_svc is not None:
        return getattr(app_svc, "AXUIElementCopyAttributeValue", None)
    return None

# Mapping of natural-language keywords to AX roles.
# Semantic aliases: user descriptions → AX labels.
# Maps common natural-language element names to their actual AX titles.
# Used by _element_match_score to bridge the vocabulary gap.
_ELEMENT_ALIASES: Dict[str, list[str]] = {
    # Safari / browser chrome
    "address bar": ["smart search field", "address and search", "url"],
    "url bar": ["smart search field", "address and search", "url"],
    "search bar": ["smart search field", "search field", "search"],
    "location bar": ["smart search field", "address and search"],
    "back button": ["go back"],
    "forward button": ["go forward"],
    "reload button": ["reload this page"],
    "refresh button": ["reload this page"],
    "new tab button": ["new tab"],
    "close tab button": ["close button"],
    "share button": ["share"],
    # Calculator
    "multiply button": ["multiply", "×"],
    "times button": ["multiply", "×"],
    "divide button": ["divide", "÷"],
    "plus button": ["add", "+"],
    "minus button": ["subtract", "−", "-"],
    "equals button": ["equals", "="],
}

# Keyboard shortcuts for browser chrome elements.
# When the agent wants to click one of these, a keyboard shortcut
# is faster, more reliable, and doesn't need coordinates.
BROWSER_KEYBOARD_SHORTCUTS: Dict[str, list[str]] = {
    "address bar": ["cmd+l"],
    "url bar": ["cmd+l"],
    "search bar": ["cmd+l"],
    "location bar": ["cmd+l"],
    "smart search field": ["cmd+l"],
    "new tab": ["cmd+t"],
    "new tab button": ["cmd+t"],
    "close tab": ["cmd+w"],
    "close tab button": ["cmd+w"],
    "back": ["cmd+["],
    "go back": ["cmd+["],
    "back button": ["cmd+["],
    "forward": ["cmd+]"],
    "go forward": ["cmd+]"],
    "forward button": ["cmd+]"],
    "reload": ["cmd+r"],
    "reload button": ["cmd+r"],
    "refresh": ["cmd+r"],
    "refresh button": ["cmd+r"],
}

_NL_ROLE_MAP: Dict[str, str] = {
    "button": "AXButton",
    "text field": "AXTextField",
    "text area": "AXTextArea",
    "checkbox": "AXCheckBox",
    "radio button": "AXRadioButton",
    "menu item": "AXMenuItem",
    "menu": "AXMenu",
    "tab": "AXTabGroup",
    "slider": "AXSlider",
    "link": "AXLink",
    "image": "AXImage",
    "static text": "AXStaticText",
    "label": "AXStaticText",
    "list": "AXList",
    "table": "AXTable",
    "toolbar": "AXToolbar",
    "window": "AXWindow",
    "scroll bar": "AXScrollBar",
    "pop up button": "AXPopUpButton",
    "combo box": "AXComboBox",
    "dropdown": "AXPopUpButton",
    "group": "AXGroup",
}

# AX roles considered interactive (clickable/typeable).
_INTERACTIVE_ROLES = frozenset({
    "AXButton",
    "AXTextField",
    "AXTextArea",
    "AXCheckBox",
    "AXRadioButton",
    "AXMenuItem",
    "AXLink",
    "AXSlider",
    "AXPopUpButton",
    "AXComboBox",
    "AXIncrementor",
    "AXColorWell",
    "AXMenuButton",
})


@dataclass
class AXElement:
    """Represents a macOS accessibility UI element."""

    role: str  # "AXButton", "AXTextField", etc.
    title: Optional[str] = None
    value: Optional[str] = None
    description: Optional[str] = None
    position: Optional[Tuple[int, int]] = None  # (x, y) screen coords
    size: Optional[Tuple[int, int]] = None  # (width, height)
    enabled: bool = True
    focused: bool = False
    children: List["AXElement"] = field(default_factory=list)
    raw_ref: Any = None

    @property
    def center(self) -> Optional[Tuple[int, int]]:
        """Compute center point of the element in screen coordinates."""
        if self.position and self.size:
            return (
                self.position[0] + self.size[0] // 2,
                self.position[1] + self.size[1] // 2,
            )
        return None


def _get_ax_attr(element: Any, attr: str) -> Any:
    """Safely get an AX attribute value, returning None on any error."""
    try:
        copy_fn = _resolve_ax_copy_attr()
        if copy_fn is None:
            return None
        err, value = copy_fn(element, attr, None)
        if err == 0:
            return value
        return None
    except Exception:
        return None


def _ax_element_from_ref(ref: Any, depth: int, max_depth: int) -> Optional[AXElement]:
    """Recursively build an AXElement tree from a raw AXUIElement reference.

    Args:
        ref: The raw AXUIElement reference.
        depth: Current recursion depth.
        max_depth: Maximum allowed recursion depth.

    Returns:
        An AXElement, or None if the reference can't be read.
    """
    try:
        role = _get_ax_attr(ref, "AXRole") or ""
        title = _get_ax_attr(ref, "AXTitle")
        value = _get_ax_attr(ref, "AXValue")
        desc = _get_ax_attr(ref, "AXDescription")
        enabled = _get_ax_attr(ref, "AXEnabled")
        focused = _get_ax_attr(ref, "AXFocused")

        position = None
        pos_val = _get_ax_attr(ref, "AXPosition")
        if pos_val is not None:
            try:
                position = (int(pos_val.x), int(pos_val.y))
            except (AttributeError, TypeError):
                pass

        size = None
        size_val = _get_ax_attr(ref, "AXSize")
        if size_val is not None:
            try:
                size = (int(size_val.width), int(size_val.height))
            except (AttributeError, TypeError):
                pass

        children: List[AXElement] = []
        if depth < max_depth:
            raw_children = _get_ax_attr(ref, "AXChildren")
            if raw_children:
                for child_ref in raw_children:
                    child = _ax_element_from_ref(child_ref, depth + 1, max_depth)
                    if child is not None:
                        children.append(child)

        return AXElement(
            role=str(role),
            title=str(title) if title is not None else None,
            value=str(value) if value is not None else None,
            description=str(desc) if desc is not None else None,
            position=position,
            size=size,
            enabled=bool(enabled) if enabled is not None else True,
            focused=bool(focused) if focused is not None else False,
            children=children,
            raw_ref=ref,
        )
    except Exception:
        logger.debug("Failed to build AXElement from ref", exc_info=True)
        return None


class AccessibilityBridge:
    """Bridge to macOS Accessibility API.

    Provides:
    - UI element tree traversal
    - Element search by role/title/description
    - Element position lookup (pixel-accurate, logical coordinates)
    - Frontmost app/window state

    Requires: Accessibility permission in System Settings.
    All methods return None/empty on errors rather than crashing.
    """

    def __init__(self, max_depth: int = 8):
        self.max_depth = max_depth

    def get_frontmost_app(self) -> Optional[Dict[str, Any]]:
        """Get info about the frontmost application.

        Returns:
            Dict with 'name', 'pid', 'bundle_id', or None if unavailable.

        Raises:
            RuntimeError or other exceptions from the Accessibility API
            if the API itself fails (e.g., permission denied). Returns
            None only when AppKit is unavailable or no app is active.
        """
        appkit = _resolve_appkit()
        if appkit is None:
            return None
        workspace = appkit.NSWorkspace.sharedWorkspace()
        active_app = workspace.activeApplication()
        if active_app is None:
            return None
        return {
            "name": active_app.get("NSApplicationName", ""),
            "pid": active_app.get("NSApplicationProcessIdentifier", 0),
            "bundle_id": active_app.get("NSApplicationBundleIdentifier", ""),
        }

    def get_focused_window(self) -> Optional[AXElement]:
        """Get the focused window of the frontmost application.

        Returns:
            AXElement for the focused window, or None.
        """
        try:
            create_app = _resolve_ax_create_app()
            if create_app is None:
                return None

            app_info = self.get_frontmost_app()
            if not app_info or not app_info.get("pid"):
                return None

            app_ref = create_app(app_info["pid"])
            window_ref = _get_ax_attr(app_ref, "AXFocusedWindow")
            if window_ref is None:
                return None

            return _ax_element_from_ref(window_ref, 0, 0)
        except Exception:
            logger.debug("Failed to get focused window", exc_info=True)
            return None

    def get_element_tree(self, max_depth: Optional[int] = None) -> Optional[AXElement]:
        """Get the full accessibility tree of the focused window.

        Args:
            max_depth: Maximum depth to traverse. Uses instance default if None.

        Returns:
            Root AXElement of the window tree, or None.
        """
        try:
            create_app = _resolve_ax_create_app()
            if create_app is None:
                return None

            depth = max_depth if max_depth is not None else self.max_depth

            app_info = self.get_frontmost_app()
            if not app_info or not app_info.get("pid"):
                return None

            app_ref = create_app(app_info["pid"])
            window_ref = _get_ax_attr(app_ref, "AXFocusedWindow")
            if window_ref is None:
                return None

            return _ax_element_from_ref(window_ref, 0, depth)
        except Exception:
            logger.debug("Failed to get element tree", exc_info=True)
            return None

    def _collect_elements(self, node: AXElement) -> List[AXElement]:
        """Recursively collect all elements from an AXElement tree."""
        result = [node]
        for child in node.children:
            result.extend(self._collect_elements(child))
        return result

    def find_elements(
        self,
        role: Optional[str] = None,
        title_contains: Optional[str] = None,
        enabled_only: bool = True,
    ) -> List[AXElement]:
        """Find elements matching criteria in the focused window.

        Args:
            role: AX role to filter by (e.g. "AXButton").
            title_contains: Substring to match in element title (case-insensitive).
            enabled_only: If True, only return enabled elements.

        Returns:
            List of matching AXElement objects.
        """
        try:
            tree = self.get_element_tree()
            if tree is None:
                return []

            all_elements = self._collect_elements(tree)
            results: List[AXElement] = []

            for elem in all_elements:
                if role and elem.role != role:
                    continue
                if title_contains:
                    haystack = " ".join(
                        part
                        for part in (elem.title, elem.value, elem.description)
                        if part
                    ).lower()
                    if title_contains.lower() not in haystack:
                        continue
                if enabled_only and not elem.enabled:
                    continue
                results.append(elem)

            return results
        except Exception:
            logger.debug("Failed to find elements", exc_info=True)
            return []

    @staticmethod
    def _normalize_text(text: Optional[str]) -> str:
        """Normalize text for fuzzy accessibility matching."""
        if not text:
            return ""
        return re.sub(r"\s+", " ", str(text).strip().lower())

    @classmethod
    def _extract_match_target(cls, description: str) -> Tuple[Optional[str], Optional[str]]:
        """Extract a target role and text hint from a natural-language description."""
        desc_lower = cls._normalize_text(description)
        if not desc_lower:
            return None, None

        special_text_prefixes = {
            "text that says ": "AXStaticText",
            "label showing ": "AXStaticText",
            "label ": "AXStaticText",
            "heading ": "AXStaticText",
        }
        for prefix, role in special_text_prefixes.items():
            if desc_lower.startswith(prefix):
                target = desc_lower[len(prefix):].strip(" '\"")
                return role, target or None

        role: Optional[str] = None
        text_hint: Optional[str] = None

        sorted_keywords = sorted(_NL_ROLE_MAP.keys(), key=len, reverse=True)
        for keyword in sorted_keywords:
            if keyword in desc_lower:
                role = _NL_ROLE_MAP[keyword]
                remainder = desc_lower.replace(keyword, "", 1).strip()
                remainder = re.sub(r"^(the|a|an)\s+", "", remainder).strip()
                text_hint = remainder or None
                break

        if role is None:
            clean = desc_lower.strip(" '\"")
            # Expand aliases so find_elements can match by AX title
            alias_targets = _ELEMENT_ALIASES.get(clean, [])
            if alias_targets:
                return None, alias_targets[0]  # Use primary alias as search hint
            return None, clean or None

        return role, text_hint.strip(" '\"") if text_hint else None

    @classmethod
    def _element_match_score(cls, elem: AXElement, text_hint: Optional[str]) -> float:
        """Score how well an element matches the requested text hint."""
        if not text_hint:
            return 1.0 if elem.enabled else 0.8

        query = cls._normalize_text(text_hint)
        fields = [
            cls._normalize_text(elem.title),
            cls._normalize_text(elem.value),
            cls._normalize_text(elem.description),
        ]

        best = 0.0
        query_tokens = set(query.split())

        # Check semantic aliases: "address bar" → "smart search field"
        aliases = _ELEMENT_ALIASES.get(query, [])
        for field in fields:
            if not field:
                continue
            # Direct alias match (high confidence)
            for alias in aliases:
                alias_norm = cls._normalize_text(alias)
                if alias_norm and (alias_norm == field or alias_norm in field):
                    best = max(best, 1.0)
                    break

            if field == query:
                best = max(best, 1.0)
                continue
            if query in field:
                best = max(best, 0.9)
                continue
            field_tokens = set(field.split())
            overlap = len(query_tokens & field_tokens) / max(len(query_tokens), 1)
            if overlap > 0:
                best = max(best, 0.45 + 0.4 * overlap)

        if elem.focused:
            best += 0.05
        if elem.enabled:
            best += 0.02

        return min(best, 1.0)

    def find_element_by_description(self, description: str) -> Optional[AXElement]:
        """Find element matching a natural language description.

        Parses descriptions like:
        - "search button" -> role=AXButton, title_contains="search"
        - "submit button" -> role=AXButton, title_contains="submit"
        - "text field" -> role=AXTextField

        Args:
            description: Natural language description of the element.

        Returns:
            The first matching AXElement, or None.
        """
        try:
            role, text_hint = self._extract_match_target(description)
            if role is None and text_hint is None:
                return None

            enabled_only = role != "AXStaticText"
            matches = self.find_elements(
                role=role,
                title_contains=text_hint,
                enabled_only=enabled_only,
            )
            if not matches and role == "AXStaticText":
                matches = self.find_elements(
                    role=None,
                    title_contains=text_hint,
                    enabled_only=False,
                )
            if not matches:
                return None

            if not text_hint:
                return matches[0]

            return max(matches, key=lambda elem: self._element_match_score(elem, text_hint))
        except Exception:
            logger.debug(
                "Failed to find element by description: %s", description, exc_info=True
            )
            return None

    def get_focused_element(self) -> Optional[AXElement]:
        """Return the currently focused UI element for the frontmost app."""
        try:
            create_app = _resolve_ax_create_app()
            if create_app is None:
                return None

            app_info = self.get_frontmost_app()
            if not app_info or not app_info.get("pid"):
                return None

            app_ref = create_app(app_info["pid"])
            focused_ref = _get_ax_attr(app_ref, "AXFocusedUIElement")
            if focused_ref is None:
                return None

            return _ax_element_from_ref(focused_ref, 0, 0)
        except Exception:
            logger.debug("Failed to get focused element", exc_info=True)
            return None

    def get_interactive_elements(self) -> List[AXElement]:
        """Get all clickable/typeable elements with positions.

        Returns only elements that have an interactive role and
        a known position on screen.

        Returns:
            List of interactive AXElement objects with valid positions.
        """
        try:
            tree = self.get_element_tree()
            if tree is None:
                return []

            all_elements = self._collect_elements(tree)
            return [
                elem
                for elem in all_elements
                if elem.role in _INTERACTIVE_ROLES
                and elem.enabled
                and elem.position is not None
            ]
        except Exception:
            logger.debug("Failed to get interactive elements", exc_info=True)
            return []

    def _serialize_element(self, elem: AXElement, indent: int = 0) -> List[str]:
        """Serialize a single element and its children to compact text lines."""
        lines: List[str] = []
        prefix = "  " * indent

        # Build the element description
        parts: List[str] = []
        role_short = elem.role.replace("AX", "") if elem.role.startswith("AX") else elem.role
        parts.append(role_short)

        if elem.title:
            parts.append(f'"{elem.title}"')

        if elem.value is not None and elem.value != "":
            val_display = elem.value
            if len(val_display) > 40:
                val_display = val_display[:37] + "..."
            parts.append(f'value="{val_display}"')

        if elem.position:
            parts.append(f"@ ({elem.position[0]}, {elem.position[1]})")

        if elem.size:
            parts.append(f"{elem.size[0]}x{elem.size[1]}")

        if not elem.enabled:
            parts.append("DISABLED")

        if elem.focused:
            parts.append("FOCUSED")

        lines.append(f"{prefix}[{' '.join(parts)}]")

        for child in elem.children:
            lines.extend(self._serialize_element(child, indent + 1))

        return lines

    def serialize_tree(self) -> str:
        """Serialize element tree to compact text for LLM context.

        Format example:
            [Window "MyApp" @ (0, 25) 1440x875]
              [Button "Submit" @ (450, 320) 120x40]
              [TextField "Search" value="query" @ (100, 50) 300x30]

        Returns:
            Multi-line string representation, or empty string on error.
        """
        try:
            tree = self.get_element_tree()
            if tree is None:
                return ""

            lines = self._serialize_element(tree)
            return "\n".join(lines)
        except Exception:
            logger.debug("Failed to serialize tree", exc_info=True)
            return ""
