"""Unit tests for evolving world-state document (Gap 2, Slice 3).

Tests: DesktopContext extensions, StateDiff, record_step_outcome,
format_state_diff, format_for_planner.

AC-26: New DesktopContext fields
AC-27: StateDiff captures changes
AC-28: format_for_planner includes progress
AC-29: record_step_outcome persists milestones/obstacles
"""

import pytest
from unittest.mock import MagicMock
from dataclasses import fields as dataclass_fields

from automation_agent.orchestrator.context_monitor import (
    ContextMonitor,
    DesktopContext,
    StateDiff,
)
from automation_agent.shared_models import ActionStep, StepResult


def _make_step(action="click", params=None, verify="something visible"):
    return ActionStep(action=action, params=params or {}, verify=verify)


def _make_result(step=None, success=True, error=None, evidence=""):
    step = step or _make_step()
    return StepResult(step=step, success=success, error=error, evidence=evidence)


def _make_element(title="", description="", role="AXButton"):
    """Create a mock accessibility element."""
    el = MagicMock()
    el.title = title
    el.description = description
    el.role = role
    return el


# ---------------------------------------------------------------------------
# DesktopContext field tests
# ---------------------------------------------------------------------------


class TestDesktopContextNewFields:
    """AC-26: DesktopContext has page_semantic_label, obstacles, milestones, version."""

    def test_desktop_context_new_fields(self):
        """AC-26: page_semantic_label, obstacles, milestones, version exist."""
        ctx = DesktopContext()
        assert hasattr(ctx, "page_semantic_label")
        assert ctx.page_semantic_label == ""
        assert hasattr(ctx, "obstacles")
        assert ctx.obstacles == []
        assert hasattr(ctx, "completed_milestones")
        assert ctx.completed_milestones == []
        assert hasattr(ctx, "state_version")
        assert ctx.state_version == 0


# ---------------------------------------------------------------------------
# StateDiff tests
# ---------------------------------------------------------------------------


class TestStateDiff:

    def test_format_state_diff_app_change(self):
        """AC-27: Diff captures app change."""
        mon = ContextMonitor()
        # Set initial state
        mon.context.frontmost_app = "Safari"
        mon.context.window_title = "Google"

        # Advance snapshot (simulates previous update_cheap)
        mon._advance_state_snapshot()

        # Change state
        mon.context.frontmost_app = "Chrome"

        diff = mon.format_state_diff()
        assert diff is not None
        assert any("App changed" in c for c in diff.changes)
        assert any("Safari" in c and "Chrome" in c for c in diff.changes)

    def test_format_state_diff_element_change(self):
        """AC-27: Diff captures new/removed elements by label."""
        mon = ContextMonitor()

        # Initial elements
        mon.context.interactive_elements = [
            _make_element(title="Submit"),
            _make_element(title="Cancel"),
        ]
        mon._advance_state_snapshot()

        # New state: Submit removed, Confirm added
        mon.context.interactive_elements = [
            _make_element(title="Cancel"),
            _make_element(title="Confirm"),
        ]

        diff = mon.format_state_diff()
        assert diff is not None
        assert "Confirm" in diff.new_elements
        assert "Submit" in diff.removed_elements

    def test_format_state_diff_excludes_noisy_fields(self):
        """AC-27: recently_clicked etc. not diffed."""
        mon = ContextMonitor()
        mon.context.frontmost_app = "Safari"
        mon._advance_state_snapshot()

        # Only change noisy fields
        mon.context.recently_clicked = "some button"
        mon.context.recently_typed = "some text"
        mon.context.iteration_count = 999

        diff = mon.format_state_diff()
        # No changes should be detected — noisy fields are excluded
        assert diff is None

    def test_stat_diff_dataclass(self):
        """StateDiff is a proper dataclass with expected fields."""
        diff = StateDiff()
        assert diff.changes == []
        assert diff.new_elements == []
        assert diff.removed_elements == []


# ---------------------------------------------------------------------------
# format_for_planner tests
# ---------------------------------------------------------------------------


class TestFormatForPlanner:

    def test_format_for_planner_includes_progress(self):
        """AC-28: Milestones and obstacles in output."""
        mon = ContextMonitor()
        mon.context.frontmost_app = "Safari"
        mon.context.window_title = "Cart"
        mon.context.completed_milestones = ["Logged in", "Added item to cart"]
        mon.context.obstacles = ["Coupon field not found"]

        output = mon.format_for_planner()

        assert "Progress" in output
        assert "[done] Logged in" in output
        assert "[done] Added item to cart" in output
        assert "Obstacles" in output
        assert "Coupon field not found" in output

    def test_format_for_planner_includes_page_label(self):
        """AC-26+AC-28: page_semantic_label shows in planner context."""
        mon = ContextMonitor()
        mon.context.frontmost_app = "Safari"
        mon.context.window_title = "Cart"
        mon.context.page_semantic_label = "Safari - Amazon Cart"

        output = mon.format_for_planner()
        assert "Safari - Amazon Cart" in output


# ---------------------------------------------------------------------------
# record_step_outcome tests
# ---------------------------------------------------------------------------


class TestRecordStepOutcome:

    def test_record_step_outcome_success(self):
        """AC-29: Successful step -> milestone."""
        mon = ContextMonitor()
        step = _make_step(verify="Cart page is visible")
        result = _make_result(step=step, success=True)

        mon.record_step_outcome(step, result)

        assert "Cart page is visible" in mon.context.completed_milestones

    def test_record_step_outcome_failure(self):
        """AC-29: Failed step -> obstacle."""
        mon = ContextMonitor()
        step = _make_step()
        result = _make_result(step=step, success=False, error="Element not found: Submit button")

        mon.record_step_outcome(step, result)

        assert len(mon.context.obstacles) == 1
        assert "Element not found" in mon.context.obstacles[0]

    def test_record_step_outcome_obstacle_truncation(self):
        """AC-29 + Security: Obstacles truncated to 200 chars."""
        mon = ContextMonitor()
        step = _make_step()
        long_error = "x" * 500
        result = _make_result(step=step, success=False, error=long_error)

        mon.record_step_outcome(step, result)

        assert len(mon.context.obstacles[0]) == 200

    def test_record_step_outcome_no_duplicate_milestones(self):
        """AC-29: Duplicate milestones are deduplicated."""
        mon = ContextMonitor()
        step = _make_step(verify="Cart page is visible")
        result = _make_result(step=step, success=True)

        mon.record_step_outcome(step, result)
        mon.record_step_outcome(step, result)

        assert mon.context.completed_milestones.count("Cart page is visible") == 1

    def test_record_step_outcome_no_duplicate_obstacles(self):
        """AC-29: Duplicate obstacles are deduplicated."""
        mon = ContextMonitor()
        step = _make_step()
        result = _make_result(step=step, success=False, error="timeout")

        mon.record_step_outcome(step, result)
        mon.record_step_outcome(step, result)

        assert mon.context.obstacles.count("timeout") == 1

    def test_page_semantic_label_writer(self):
        """AC-26+AC-29: Updated from window_title on navigation."""
        mon = ContextMonitor()
        mon.context.frontmost_app = "Safari"
        mon.context.window_title = "Amazon - Cart"
        # Set previous title different to trigger update
        mon._previous_title = "Amazon - Home"

        step = _make_step(action="click", verify="Cart visible")
        result = _make_result(step=step, success=True)

        mon.record_step_outcome(step, result)

        assert mon.context.page_semantic_label == "Safari - Amazon - Cart"

    def test_milestones_capped_at_20(self):
        """AC-29: Milestones capped at 20."""
        mon = ContextMonitor()
        for i in range(25):
            step = _make_step(verify=f"milestone_{i}")
            result = _make_result(step=step, success=True)
            mon.record_step_outcome(step, result)

        assert len(mon.context.completed_milestones) == 20
        # Should keep the latest 20
        assert "milestone_24" in mon.context.completed_milestones

    def test_obstacles_capped_at_20(self):
        """Obstacles capped at 20, symmetric with milestones (Finding 8)."""
        mon = ContextMonitor()
        for i in range(25):
            step = _make_step(verify=f"check_{i}")
            result = _make_result(step=step, success=False, error=f"obstacle_{i}")
            mon.record_step_outcome(step, result)

        assert len(mon.context.obstacles) == 20
        # Should keep the latest 20
        assert "obstacle_24" in mon.context.obstacles
        assert "obstacle_0" not in mon.context.obstacles


# ---------------------------------------------------------------------------
# update_cheap integration with state_version
# ---------------------------------------------------------------------------


class TestUpdateCheapStateVersion:

    def test_update_cheap_increments_version(self):
        """AC-26: state_version increments on update_cheap."""
        mon = ContextMonitor()
        assert mon.context.state_version == 0

        mon.update_cheap()
        assert mon.context.state_version == 1

        mon.update_cheap()
        assert mon.context.state_version == 2

    def test_update_cheap_advances_snapshot(self):
        """State snapshot is advanced in update_cheap for next diff."""
        mon = ContextMonitor()
        mon.context.frontmost_app = "Safari"
        mon.update_cheap()

        # Previous should now be "Safari"
        assert mon._previous_app == "Safari"
