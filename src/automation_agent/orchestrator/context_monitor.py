"""Persistent context monitor for desktop state tracking.

Maintains running desktop state updated cheaply via the Accessibility API.
Provides structured context to the planner, reducing full VLM observation calls.

Key design:
- update_cheap() uses AX API (~50ms) instead of full VLM screenshot (3-7s)
- needs_full_vision() determines when a full VLM refresh is necessary
- format_for_planner() produces structured text the planner can reason over
- Gracefully degrades when AccessibilityBridge is unavailable
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Import AXElement for type hints; AccessibilityBridge is injected at runtime.
try:
    from automation_agent.perception.accessibility import AccessibilityBridge, AXElement
except ImportError:  # pragma: no cover
    AccessibilityBridge = None  # type: ignore[assignment,misc]
    AXElement = None  # type: ignore[assignment,misc]


@dataclass
class DesktopContext:
    """Full snapshot of the desktop state."""

    frontmost_app: str = ""
    window_title: str = ""
    interactive_elements: list = field(default_factory=list)
    recently_clicked: Optional[str] = None
    recently_typed: Optional[str] = None
    form_fields_filled: Dict[str, str] = field(default_factory=dict)
    navigation_history: List[str] = field(default_factory=list)
    last_vision_description: str = ""
    iteration_count: int = 0


class ContextMonitor:
    """Maintains persistent desktop context across agent iterations.

    - Cheap updates via Accessibility API (~50ms, no VLM call)
    - Full vision refresh only when context is stale
    - Provides structured context to the planner
    """

    def __init__(self, accessibility=None):
        self.accessibility = accessibility
        self.context = DesktopContext()

    def update_cheap(self) -> DesktopContext:
        """Update context via Accessibility API (~50ms).

        If no AccessibilityBridge is available, simply increments the
        iteration counter and returns the current context unchanged.
        """
        if self.accessibility:
            try:
                app_info = self.accessibility.get_frontmost_app()
                if app_info:
                    self.context.frontmost_app = app_info.get("name", "")
            except Exception:
                logger.debug("Failed to get frontmost app", exc_info=True)

            try:
                window = self.accessibility.get_focused_window()
                if window:
                    self.context.window_title = window.title or ""
                    self.context.interactive_elements = (
                        self.accessibility.get_interactive_elements()
                    )
            except Exception:
                logger.debug("Failed to get focused window", exc_info=True)

        self.context.iteration_count += 1
        return self.context

    def needs_full_vision(self) -> bool:
        """Should we do a full VLM observation?

        Returns True if:
        - No vision description has been captured yet
        - Every 3rd iteration (to refresh stale context)
        """
        if not self.context.last_vision_description:
            return True
        if self.context.iteration_count % 3 == 0:
            return True
        return False

    def record_click(self, target: str) -> None:
        """Record a click action target."""
        self.context.recently_clicked = target

    def record_type(self, text: str, field_name: Optional[str] = None) -> None:
        """Record typed text. If field_name is provided, track form progress."""
        self.context.recently_typed = text
        if field_name:
            self.context.form_fields_filled[field_name] = text

    def record_navigation(self, url: str) -> None:
        """Record a navigation event."""
        self.context.navigation_history.append(url)

    def format_for_planner(self) -> str:
        """Format context as structured text for planner prompt.

        Returns a multi-line string with sections for desktop state,
        interactive elements, screen description, and form progress.
        """
        lines = ["## Desktop State"]
        lines.append(f"App: {self.context.frontmost_app}")
        lines.append(f"Window: {self.context.window_title}")

        if self.context.interactive_elements:
            count = len(self.context.interactive_elements)
            lines.append(f"Interactive elements ({count}):")
            for elem in self.context.interactive_elements[:20]:
                label = (
                    getattr(elem, "title", None)
                    or getattr(elem, "description", None)
                    or getattr(elem, "role", "unknown")
                )
                role = getattr(elem, "role", "")
                role_short = role.replace("AX", "") if role.startswith("AX") else role
                lines.append(f"  - [{role_short}] {label}")

        if self.context.last_vision_description:
            desc = self.context.last_vision_description[:500]
            lines.append(f"\n## Screen: {desc}")

        if self.context.form_fields_filled:
            lines.append("\n## Form Progress")
            for k, v in self.context.form_fields_filled.items():
                lines.append(f"  {k}: {v}")

        return "\n".join(lines)
