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
                    elem_title = elem.title or ""
                    if title_contains.lower() not in elem_title.lower():
                        continue
                if enabled_only and not elem.enabled:
                    continue
                results.append(elem)

            return results
        except Exception:
            logger.debug("Failed to find elements", exc_info=True)
            return []

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
            desc_lower = description.lower().strip()
            role: Optional[str] = None
            title_hint: Optional[str] = None

            # Try to match role keywords (longest match first to prefer
            # "radio button" over "button").
            sorted_keywords = sorted(_NL_ROLE_MAP.keys(), key=len, reverse=True)
            for keyword in sorted_keywords:
                if keyword in desc_lower:
                    role = _NL_ROLE_MAP[keyword]
                    # Everything before the keyword is the title hint
                    remainder = desc_lower.replace(keyword, "").strip()
                    if remainder:
                        # Remove common filler words
                        remainder = re.sub(
                            r"^(the|a|an)\s+", "", remainder
                        ).strip()
                        if remainder:
                            title_hint = remainder
                    break

            if role is None:
                # No role keyword found — treat entire description as title search
                title_hint = desc_lower

            matches = self.find_elements(
                role=role,
                title_contains=title_hint,
                enabled_only=True,
            )
            return matches[0] if matches else None
        except Exception:
            logger.debug(
                "Failed to find element by description: %s", description, exc_info=True
            )
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
