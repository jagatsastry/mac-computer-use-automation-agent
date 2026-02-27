"""Comprehensive unit tests for the ContextMonitor.

All external dependencies (AccessibilityBridge, AXElement) are mocked.
Tests cover: DesktopContext defaults, update_cheap with/without accessibility,
needs_full_vision logic, record_* methods, format_for_planner output,
graceful degradation, and iteration_count tracking.
"""

import pytest
from unittest.mock import MagicMock, patch
from dataclasses import fields

from automation_agent.orchestrator.context_monitor import ContextMonitor, DesktopContext
from automation_agent.perception.accessibility import AXElement


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ax_element(
    role="AXButton",
    title=None,
    description=None,
    value=None,
    position=(100, 200),
    size=(80, 30),
    enabled=True,
    focused=False,
):
    """Create an AXElement with given properties."""
    return AXElement(
        role=role,
        title=title,
        description=description,
        value=value,
        position=position,
        size=size,
        enabled=enabled,
        focused=focused,
    )


def _make_mock_bridge(
    app_name="Safari",
    pid=1234,
    window_title="Google",
    interactive_elements=None,
):
    """Create a mock AccessibilityBridge with configurable return values."""
    bridge = MagicMock()
    bridge.get_frontmost_app.return_value = {
        "name": app_name,
        "pid": pid,
        "bundle_id": f"com.apple.{app_name}",
    }
    window = _make_ax_element(role="AXWindow", title=window_title)
    bridge.get_focused_window.return_value = window
    bridge.get_interactive_elements.return_value = interactive_elements or []
    return bridge


# ---------------------------------------------------------------------------
# DesktopContext defaults
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDesktopContextDefaults:
    """Tests for DesktopContext dataclass defaults."""

    def test_default_values(self):
        ctx = DesktopContext()
        assert ctx.frontmost_app == ""
        assert ctx.window_title == ""
        assert ctx.interactive_elements == []
        assert ctx.recently_clicked is None
        assert ctx.recently_typed is None
        assert ctx.form_fields_filled == {}
        assert ctx.navigation_history == []
        assert ctx.last_vision_description == ""
        assert ctx.iteration_count == 0

    def test_custom_values(self):
        ctx = DesktopContext(
            frontmost_app="Safari",
            window_title="Google",
            iteration_count=5,
        )
        assert ctx.frontmost_app == "Safari"
        assert ctx.window_title == "Google"
        assert ctx.iteration_count == 5

    def test_mutable_defaults_are_independent(self):
        """Each instance gets its own mutable default containers."""
        ctx1 = DesktopContext()
        ctx2 = DesktopContext()
        ctx1.form_fields_filled["email"] = "test@test.com"
        ctx1.navigation_history.append("https://example.com")
        assert ctx2.form_fields_filled == {}
        assert ctx2.navigation_history == []


# ---------------------------------------------------------------------------
# ContextMonitor init and update_cheap
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateCheap:
    """Tests for ContextMonitor.update_cheap()."""

    def test_update_cheap_without_accessibility(self):
        """update_cheap without accessibility still increments iteration_count."""
        monitor = ContextMonitor(accessibility=None)
        ctx = monitor.update_cheap()
        assert ctx.iteration_count == 1
        assert ctx.frontmost_app == ""
        assert ctx.window_title == ""

    def test_update_cheap_increments_iteration_count(self):
        """Each call to update_cheap increments iteration_count."""
        monitor = ContextMonitor(accessibility=None)
        for i in range(5):
            ctx = monitor.update_cheap()
        assert ctx.iteration_count == 5

    def test_update_cheap_with_accessibility(self):
        """update_cheap with accessibility fills in app and window info."""
        bridge = _make_mock_bridge(
            app_name="Finder",
            window_title="Documents",
            interactive_elements=[
                _make_ax_element(role="AXButton", title="Open"),
            ],
        )
        monitor = ContextMonitor(accessibility=bridge)
        ctx = monitor.update_cheap()
        assert ctx.frontmost_app == "Finder"
        assert ctx.window_title == "Documents"
        assert len(ctx.interactive_elements) == 1
        assert ctx.iteration_count == 1

    def test_update_cheap_returns_same_context_object(self):
        """update_cheap returns monitor.context."""
        monitor = ContextMonitor()
        ctx = monitor.update_cheap()
        assert ctx is monitor.context

    def test_update_cheap_app_info_none(self):
        """When get_frontmost_app returns None, frontmost_app stays unchanged."""
        bridge = MagicMock()
        bridge.get_frontmost_app.return_value = None
        bridge.get_focused_window.return_value = None
        monitor = ContextMonitor(accessibility=bridge)
        monitor.context.frontmost_app = "Previous"
        ctx = monitor.update_cheap()
        assert ctx.frontmost_app == "Previous"
        assert ctx.iteration_count == 1

    def test_update_cheap_window_none(self):
        """When get_focused_window returns None, window_title stays unchanged."""
        bridge = _make_mock_bridge()
        bridge.get_focused_window.return_value = None
        monitor = ContextMonitor(accessibility=bridge)
        monitor.context.window_title = "Old Title"
        ctx = monitor.update_cheap()
        assert ctx.frontmost_app == "Safari"
        assert ctx.window_title == "Old Title"

    def test_update_cheap_window_title_none_becomes_empty(self):
        """When window.title is None, window_title becomes empty string."""
        bridge = _make_mock_bridge()
        window = _make_ax_element(role="AXWindow", title=None)
        bridge.get_focused_window.return_value = window
        monitor = ContextMonitor(accessibility=bridge)
        ctx = monitor.update_cheap()
        assert ctx.window_title == ""

    def test_update_cheap_accessibility_exception_graceful(self):
        """If accessibility raises an exception, update_cheap still works."""
        bridge = MagicMock()
        bridge.get_frontmost_app.side_effect = RuntimeError("AX API error")
        bridge.get_focused_window.side_effect = RuntimeError("AX API error")
        monitor = ContextMonitor(accessibility=bridge)
        ctx = monitor.update_cheap()
        assert ctx.iteration_count == 1
        assert ctx.frontmost_app == ""


# ---------------------------------------------------------------------------
# needs_full_vision
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNeedsFullVision:
    """Tests for ContextMonitor.needs_full_vision()."""

    def test_needs_vision_when_no_description(self):
        """Returns True when no vision description has been captured."""
        monitor = ContextMonitor()
        assert monitor.needs_full_vision() is True

    def test_no_vision_needed_after_description_set(self):
        """Returns False when description exists and not on 3rd iteration."""
        monitor = ContextMonitor()
        monitor.context.last_vision_description = "Desktop showing Safari"
        monitor.context.iteration_count = 1
        assert monitor.needs_full_vision() is False

    def test_needs_vision_every_3rd_iteration(self):
        """Returns True on every 3rd iteration (0, 3, 6, ...)."""
        monitor = ContextMonitor()
        monitor.context.last_vision_description = "Something"

        results = {}
        for i in range(10):
            monitor.context.iteration_count = i
            results[i] = monitor.needs_full_vision()

        assert results[0] is True
        assert results[1] is False
        assert results[2] is False
        assert results[3] is True
        assert results[4] is False
        assert results[5] is False
        assert results[6] is True
        assert results[9] is True

    def test_empty_description_always_needs_vision(self):
        """Empty string description counts as 'no description'."""
        monitor = ContextMonitor()
        monitor.context.last_vision_description = ""
        monitor.context.iteration_count = 4
        assert monitor.needs_full_vision() is True


# ---------------------------------------------------------------------------
# record_click, record_type, record_navigation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecordMethods:
    """Tests for record_click, record_type, record_navigation."""

    def test_record_click(self):
        monitor = ContextMonitor()
        monitor.record_click("Submit button")
        assert monitor.context.recently_clicked == "Submit button"

    def test_record_click_overwrites(self):
        monitor = ContextMonitor()
        monitor.record_click("First")
        monitor.record_click("Second")
        assert monitor.context.recently_clicked == "Second"

    def test_record_type_text(self):
        monitor = ContextMonitor()
        monitor.record_type("hello world")
        assert monitor.context.recently_typed == "hello world"

    def test_record_type_with_field_name(self):
        monitor = ContextMonitor()
        monitor.record_type("test@test.com", field_name="email")
        assert monitor.context.recently_typed == "test@test.com"
        assert monitor.context.form_fields_filled == {"email": "test@test.com"}

    def test_record_type_without_field_name(self):
        """When field_name is None, form_fields_filled is not updated."""
        monitor = ContextMonitor()
        monitor.record_type("some text")
        assert monitor.context.recently_typed == "some text"
        assert monitor.context.form_fields_filled == {}

    def test_record_type_accumulates_form_fields(self):
        """Multiple calls with different field names accumulate."""
        monitor = ContextMonitor()
        monitor.record_type("John", field_name="first_name")
        monitor.record_type("Doe", field_name="last_name")
        monitor.record_type("john@example.com", field_name="email")
        assert monitor.context.form_fields_filled == {
            "first_name": "John",
            "last_name": "Doe",
            "email": "john@example.com",
        }

    def test_record_type_overwrites_same_field(self):
        """Subsequent call with same field_name overwrites."""
        monitor = ContextMonitor()
        monitor.record_type("wrong", field_name="email")
        monitor.record_type("right@right.com", field_name="email")
        assert monitor.context.form_fields_filled["email"] == "right@right.com"

    def test_record_navigation(self):
        monitor = ContextMonitor()
        monitor.record_navigation("https://example.com")
        assert monitor.context.navigation_history == ["https://example.com"]

    def test_record_navigation_appends(self):
        monitor = ContextMonitor()
        monitor.record_navigation("https://example.com")
        monitor.record_navigation("https://example.com/page2")
        assert monitor.context.navigation_history == [
            "https://example.com",
            "https://example.com/page2",
        ]


# ---------------------------------------------------------------------------
# format_for_planner
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatForPlanner:
    """Tests for ContextMonitor.format_for_planner()."""

    def test_minimal_output(self):
        """With empty context, still produces basic structure."""
        monitor = ContextMonitor()
        output = monitor.format_for_planner()
        assert "## Desktop State" in output
        assert "App:" in output
        assert "Window:" in output

    def test_includes_app_and_window(self):
        monitor = ContextMonitor()
        monitor.context.frontmost_app = "Safari"
        monitor.context.window_title = "Google"
        output = monitor.format_for_planner()
        assert "App: Safari" in output
        assert "Window: Google" in output

    def test_includes_interactive_elements(self):
        monitor = ContextMonitor()
        monitor.context.interactive_elements = [
            _make_ax_element(role="AXButton", title="Submit"),
            _make_ax_element(role="AXTextField", title="Search"),
        ]
        output = monitor.format_for_planner()
        assert "Interactive elements (2):" in output
        assert "[Button] Submit" in output
        assert "[TextField] Search" in output

    def test_elements_capped_at_20(self):
        """Only first 20 elements are included."""
        monitor = ContextMonitor()
        monitor.context.interactive_elements = [
            _make_ax_element(role="AXButton", title=f"Btn{i}")
            for i in range(30)
        ]
        output = monitor.format_for_planner()
        assert "Interactive elements (30):" in output
        assert "[Button] Btn19" in output
        assert "Btn20" not in output

    def test_includes_vision_description(self):
        monitor = ContextMonitor()
        monitor.context.last_vision_description = "Desktop showing Safari browser"
        output = monitor.format_for_planner()
        assert "## Screen: Desktop showing Safari browser" in output

    def test_vision_description_truncated_at_500(self):
        monitor = ContextMonitor()
        monitor.context.last_vision_description = "x" * 600
        output = monitor.format_for_planner()
        # The desc should be truncated to 500 chars
        screen_line = [l for l in output.split("\n") if "## Screen:" in l][0]
        desc_part = screen_line.split("## Screen: ")[1]
        assert len(desc_part) == 500

    def test_includes_form_progress(self):
        monitor = ContextMonitor()
        monitor.context.form_fields_filled = {
            "email": "test@test.com",
            "name": "John",
        }
        output = monitor.format_for_planner()
        assert "## Form Progress" in output
        assert "email: test@test.com" in output
        assert "name: John" in output

    def test_no_form_progress_when_empty(self):
        monitor = ContextMonitor()
        output = monitor.format_for_planner()
        assert "## Form Progress" not in output

    def test_no_elements_section_when_empty(self):
        monitor = ContextMonitor()
        output = monitor.format_for_planner()
        assert "Interactive elements" not in output

    def test_element_fallback_to_description(self):
        """When title is None, uses description as label."""
        monitor = ContextMonitor()
        monitor.context.interactive_elements = [
            _make_ax_element(role="AXButton", title=None, description="close btn"),
        ]
        output = monitor.format_for_planner()
        assert "[Button] close btn" in output

    def test_element_fallback_to_role(self):
        """When title and description are both None, uses role."""
        monitor = ContextMonitor()
        monitor.context.interactive_elements = [
            _make_ax_element(role="AXSlider", title=None, description=None),
        ]
        output = monitor.format_for_planner()
        assert "[Slider] AXSlider" in output

    def test_full_context_format(self):
        """Test a fully populated context produces well-structured output."""
        bridge = _make_mock_bridge(
            app_name="Safari",
            window_title="OpenTable",
            interactive_elements=[
                _make_ax_element(role="AXButton", title="Find a Table"),
                _make_ax_element(role="AXTextField", title="Search"),
            ],
        )
        monitor = ContextMonitor(accessibility=bridge)
        monitor.update_cheap()
        monitor.context.last_vision_description = "A restaurant booking page"
        monitor.record_type("sushi", field_name="cuisine")
        monitor.record_navigation("https://opentable.com")

        output = monitor.format_for_planner()
        assert "App: Safari" in output
        assert "Window: OpenTable" in output
        assert "Interactive elements (2):" in output
        assert "## Screen: A restaurant booking page" in output
        assert "## Form Progress" in output
        assert "cuisine: sushi" in output


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGracefulDegradation:
    """Tests for behavior when accessibility is None or broken."""

    def test_init_without_accessibility(self):
        monitor = ContextMonitor()
        assert monitor.accessibility is None
        assert isinstance(monitor.context, DesktopContext)

    def test_update_cheap_no_accessibility_no_crash(self):
        monitor = ContextMonitor()
        ctx = monitor.update_cheap()
        assert ctx.iteration_count == 1

    def test_format_for_planner_no_accessibility(self):
        """Produces valid output even without accessibility data."""
        monitor = ContextMonitor()
        output = monitor.format_for_planner()
        assert isinstance(output, str)
        assert "## Desktop State" in output

    def test_all_record_methods_work_without_accessibility(self):
        monitor = ContextMonitor()
        monitor.record_click("button")
        monitor.record_type("text", field_name="field")
        monitor.record_navigation("https://example.com")
        assert monitor.context.recently_clicked == "button"
        assert monitor.context.recently_typed == "text"
        assert monitor.context.form_fields_filled == {"field": "text"}
        assert monitor.context.navigation_history == ["https://example.com"]


# ---------------------------------------------------------------------------
# Iteration count tracking across operations
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIterationCountTracking:
    """Tests for iteration_count behavior across operations."""

    def test_iteration_count_starts_at_zero(self):
        monitor = ContextMonitor()
        assert monitor.context.iteration_count == 0

    def test_iteration_count_incremented_by_update_cheap_only(self):
        """record_* methods do not change iteration_count."""
        monitor = ContextMonitor()
        monitor.record_click("btn")
        assert monitor.context.iteration_count == 0
        monitor.record_type("text")
        assert monitor.context.iteration_count == 0
        monitor.record_navigation("https://x.com")
        assert monitor.context.iteration_count == 0

    def test_iteration_count_monotonic(self):
        """iteration_count only goes up."""
        monitor = ContextMonitor()
        counts = []
        for _ in range(10):
            monitor.update_cheap()
            counts.append(monitor.context.iteration_count)
        assert counts == list(range(1, 11))

    def test_needs_full_vision_pattern_over_many_iterations(self):
        """needs_full_vision follows the correct pattern across 12 iterations."""
        monitor = ContextMonitor()
        # First call always needs vision (no description)
        assert monitor.needs_full_vision() is True
        monitor.context.last_vision_description = "Initial description"

        results = []
        for _ in range(12):
            monitor.update_cheap()
            results.append(monitor.needs_full_vision())

        # iteration_count: 1,2,3,4,5,6,7,8,9,10,11,12
        # mod 3 == 0:     F,F,T,F,F,T,F,F,T, F, F, T
        expected = [False, False, True, False, False, True,
                    False, False, True, False, False, True]
        assert results == expected
