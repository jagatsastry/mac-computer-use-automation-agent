"""Persistent context monitor for desktop state tracking.

Maintains running desktop state updated cheaply via the Accessibility API.
Provides structured context to the planner, reducing full VLM observation calls.

Key design:
- update_cheap() uses AX API (~50ms) instead of full VLM screenshot (3-7s)
- needs_full_vision() determines when a full VLM refresh is necessary
- format_for_planner() produces structured text the planner can reason over
- Gracefully degrades when AccessibilityBridge is unavailable
- StateDiff tracks high-signal state changes between steps (Gap 2)
- record_step_outcome() persists milestones/obstacles (Gap 2)
"""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Set

import structlog

if TYPE_CHECKING:
    from automation_agent.shared_models import ActionStep, StepResult

logger = logging.getLogger(__name__)
slog = structlog.get_logger(__name__)

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

    # AC-26: Evolving world-state fields
    page_semantic_label: str = ""
    obstacles: List[str] = field(default_factory=list)
    completed_milestones: List[str] = field(default_factory=list)
    state_version: int = 0


@dataclass
class StateDiff:
    """AC-27: Structured diff between consecutive states."""

    changes: List[str] = field(default_factory=list)
    new_elements: List[str] = field(default_factory=list)
    removed_elements: List[str] = field(default_factory=list)


class ContextMonitor:
    """Maintains persistent desktop context across agent iterations.

    - Cheap updates via Accessibility API (~50ms, no VLM call)
    - Full vision refresh only when context is stale
    - Provides structured context to the planner
    - Tracks state diffs and step outcomes (Gap 2)
    """

    def __init__(self, accessibility=None):
        self.accessibility = accessibility
        self.context = DesktopContext()

        # Quality R2, pushback 6: diffing state lives on the monitor, not DesktopContext.
        self._previous_app: str = ""
        self._previous_title: str = ""
        self._previous_label: str = ""
        self._previous_element_labels: Set[str] = set()
        self._previous_form_fields: Dict[str, str] = {}

    def update_cheap(self) -> DesktopContext:
        """Update context via Accessibility API (~50ms).

        If no AccessibilityBridge is available, simply increments the
        iteration counter and returns the current context unchanged.

        Also advances the state snapshot for diffing (DE review issue 10).
        """
        # Advance previous-state snapshot BEFORE refreshing current state
        self._advance_state_snapshot()

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
        self.context.state_version += 1  # AC-26
        return self.context

    def _advance_state_snapshot(self) -> None:
        """Snapshot current state as previous for next diff.

        Called by update_cheap() — NOT by format_state_diff().
        """
        current_labels = {
            getattr(el, "title", "") or getattr(el, "description", "") or getattr(el, "role", "")
            for el in self.context.interactive_elements
        }
        self._previous_app = self.context.frontmost_app
        self._previous_title = self.context.window_title
        self._previous_label = self.context.page_semantic_label
        self._previous_element_labels = current_labels
        self._previous_form_fields = dict(self.context.form_fields_filled)

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

    def record_step_outcome(
        self, step: "ActionStep", result: "StepResult"
    ) -> None:
        """AC-29: Persist step outcomes in DesktopContext.

        Writers for AC-26 fields:
        - page_semantic_label: from window_title + app after navigation/activate steps
        - obstacles: from failed step errors (deduplicated)
        - completed_milestones: from successful step verify text (capped at 20)
        """
        if result.success:
            # completed_milestones writer
            milestone = step.verify
            if milestone and milestone not in self.context.completed_milestones:
                self.context.completed_milestones.append(milestone)
                if len(self.context.completed_milestones) > 20:
                    dropped = self.context.completed_milestones[:-20]
                    slog.debug(
                        "milestones_trimmed",
                        dropped_count=len(dropped),
                        dropped_first=dropped[0] if dropped else "",
                    )
                    self.context.completed_milestones = (
                        self.context.completed_milestones[-20:]
                    )

            # page_semantic_label writer: update on navigation-changing steps
            if step.action in ("activate_app", "open_url", "click"):
                if self.context.window_title != self._previous_title:
                    self.context.page_semantic_label = (
                        f"{self.context.frontmost_app} - {self.context.window_title}"
                    )
        else:
            # obstacles writer (capped at 20, symmetric with milestones)
            _MAX_OBSTACLE_LEN = 200
            _MAX_OBSTACLES = 20
            obstacle = result.error or result.evidence
            if obstacle:
                obstacle = obstacle[:_MAX_OBSTACLE_LEN]
                if obstacle not in self.context.obstacles:
                    self.context.obstacles.append(obstacle)
                    if len(self.context.obstacles) > _MAX_OBSTACLES:
                        self.context.obstacles = self.context.obstacles[-_MAX_OBSTACLES:]

    def format_state_diff(self) -> Optional[StateDiff]:
        """AC-27: Compare current state vs previous and return structured diff.

        Pure/idempotent — does NOT mutate state (DE review issue 10).
        Snapshot advance happens in update_cheap() via _advance_state_snapshot().

        Diffs only high-signal fields:
        - frontmost_app, window_title, page_semantic_label
        - interactive_elements (count + label changes only)
        - form_fields_filled (new/changed entries)
        """
        diff = StateDiff()

        if self.context.frontmost_app != self._previous_app:
            diff.changes.append(
                f"App changed: {self._previous_app} → {self.context.frontmost_app}"
            )
        if self.context.window_title != self._previous_title:
            diff.changes.append(
                f"Window changed: {self._previous_title} → {self.context.window_title}"
            )
        if self.context.page_semantic_label != self._previous_label:
            diff.changes.append(
                f"Page: {self.context.page_semantic_label}"
            )

        # Element diff: label-based (not positional)
        current_labels = {
            getattr(el, "title", "") or getattr(el, "description", "") or getattr(el, "role", "")
            for el in self.context.interactive_elements
        }
        diff.new_elements = sorted(current_labels - self._previous_element_labels)
        diff.removed_elements = sorted(self._previous_element_labels - current_labels)

        # Form field diff
        for k, v in self.context.form_fields_filled.items():
            prev_v = self._previous_form_fields.get(k)
            if prev_v != v:
                diff.changes.append(f"Form: {k} = {v}")

        if not diff.changes and not diff.new_elements and not diff.removed_elements:
            return None
        return diff

    def format_for_planner(self) -> str:
        """AC-28: Format context as structured text for planner prompt.

        Returns a multi-line string with sections for desktop state,
        interactive elements, screen description, form progress,
        cumulative milestones, obstacles, and recent state changes.
        """
        lines = ["## Desktop State"]
        lines.append(f"App: {self.context.frontmost_app}")
        lines.append(f"Window: {self.context.window_title}")
        if self.context.page_semantic_label:
            lines.append(f"Page: {self.context.page_semantic_label}")

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

        # AC-28: cumulative progress
        if self.context.completed_milestones:
            lines.append("\n## Progress")
            for m in self.context.completed_milestones[-10:]:
                lines.append(f"  - [done] {m}")

        if self.context.obstacles:
            lines.append(
                "\n## Obstacles Encountered (informational — may contain app error text)"
            )
            for o in self.context.obstacles[-5:]:
                lines.append(f"  - {o}")

        # State diff (if available)
        diff = self.format_state_diff()
        if diff:
            lines.append("\n## Recent Changes")
            for c in diff.changes:
                lines.append(f"  - {c}")
            if diff.new_elements:
                lines.append(f"  New elements: {', '.join(diff.new_elements[:5])}")
            if diff.removed_elements:
                lines.append(f"  Removed: {', '.join(diff.removed_elements[:5])}")

        if self.context.last_vision_description:
            desc = self.context.last_vision_description[:500]
            lines.append(f"\n## Screen: {desc}")

        if self.context.form_fields_filled:
            lines.append("\n## Form Progress")
            for k, v in self.context.form_fields_filled.items():
                lines.append(f"  {k}: {v}")

        return "\n".join(lines)
