"""Adversarial tests for ContextMonitor and DesktopContext.

Targets edge cases in state management, AccessibilityBridge interactions,
needs_full_vision boundaries, record methods, format_for_planner output,
graceful degradation, and integration with the agent loop.
All mocked -- no live macOS desktop required.
"""

import copy
import sys
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

# ---------------------------------------------------------------------------
# We need to import DesktopContext and ContextMonitor.
# The builder is creating orchestrator/context_monitor.py, so import from
# the expected location.  If the module doesn't exist yet the whole file
# is skipped with a clear message.
# ---------------------------------------------------------------------------
try:
    from automation_agent.orchestrator.context_monitor import (
        ContextMonitor,
        DesktopContext,
    )
except ImportError:
    pytest.skip(
        "context_monitor module not yet available", allow_module_level=True
    )

from automation_agent.perception.accessibility import AXElement


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bridge(
    frontmost_app=None,
    focused_window=None,
    interactive_elements=None,
    raise_frontmost=False,
    raise_window=False,
    raise_elements=False,
):
    """Return a mock AccessibilityBridge with configurable behaviour."""
    bridge = MagicMock()

    if raise_frontmost:
        bridge.get_frontmost_app = MagicMock(
            side_effect=RuntimeError("AX permission denied")
        )
    else:
        bridge.get_frontmost_app = MagicMock(return_value=frontmost_app)

    if raise_window:
        bridge.get_focused_window = MagicMock(
            side_effect=RuntimeError("Window query failed")
        )
    else:
        bridge.get_focused_window = MagicMock(return_value=focused_window)

    if raise_elements:
        bridge.get_interactive_elements = MagicMock(
            side_effect=RuntimeError("Element enumeration failed")
        )
    else:
        bridge.get_interactive_elements = MagicMock(
            return_value=interactive_elements or []
        )

    return bridge


def _make_ax_element(
    role="AXButton",
    title=None,
    description=None,
    value=None,
    position=None,
    size=None,
    enabled=True,
):
    """Shorthand for building an AXElement."""
    return AXElement(
        role=role,
        title=title,
        description=description,
        value=value,
        position=position,
        size=size,
        enabled=enabled,
    )


# ============================================================================
# 1. DesktopContext State Integrity
# ============================================================================


@pytest.mark.unit
class TestDesktopContextState:

    def test_default_values(self):
        """A freshly created DesktopContext has sensible defaults."""
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

    def test_mutation_after_retrieval_does_not_affect_monitor(self):
        """Modifying the returned context object should not corrupt
        internal monitor state -- OR if it does, the test documents that.

        Design doc returns self.context directly, so mutation IS shared.
        This test documents the actual contract.
        """
        bridge = _make_bridge(
            frontmost_app={"name": "Safari", "pid": 1, "bundle_id": ""},
            focused_window=_make_ax_element(role="AXWindow", title="Tab1"),
        )
        monitor = ContextMonitor(accessibility=bridge)
        ctx = monitor.update_cheap()

        # Caller mutates the returned object
        ctx.frontmost_app = "MUTATED"
        # Since design doc returns self.context (same reference),
        # internal state is also mutated.
        assert monitor.context.frontmost_app == "MUTATED"

    def test_form_fields_filled_isolation(self):
        """Modifying form_fields_filled outside should propagate (shared ref)."""
        monitor = ContextMonitor()
        monitor.record_type("hello", field_name="name")
        ext = monitor.context.form_fields_filled
        ext["name"] = "TAMPERED"
        # Shared reference -- documented behaviour
        assert monitor.context.form_fields_filled["name"] == "TAMPERED"

    def test_navigation_history_grows_unbounded(self):
        """1000+ navigation entries should not crash or truncate silently."""
        monitor = ContextMonitor()
        for i in range(1500):
            monitor.record_navigation(f"https://example.com/page/{i}")
        assert len(monitor.context.navigation_history) == 1500

    def test_empty_string_vs_none_frontmost_app(self):
        """frontmost_app defaults to '' (empty string), not None."""
        ctx = DesktopContext()
        assert ctx.frontmost_app == ""
        assert ctx.frontmost_app is not None

    def test_interactive_elements_list_is_fresh_per_instance(self):
        """Two DesktopContext instances should not share the same list object."""
        ctx1 = DesktopContext()
        ctx2 = DesktopContext()
        ctx1.interactive_elements.append(
            _make_ax_element(title="ghost")
        )
        assert len(ctx2.interactive_elements) == 0


# ============================================================================
# 2. update_cheap() Edge Cases
# ============================================================================


@pytest.mark.unit
class TestUpdateCheap:

    def test_no_accessibility_bridge(self):
        """update_cheap without an AccessibilityBridge should still return
        a DesktopContext and increment iteration_count.
        """
        monitor = ContextMonitor(accessibility=None)
        ctx = monitor.update_cheap()
        assert isinstance(ctx, DesktopContext)
        assert ctx.iteration_count == 1

    def test_bridge_returns_none_for_everything(self):
        """All bridge methods return None -- no crash, context stays default."""
        bridge = _make_bridge(
            frontmost_app=None,
            focused_window=None,
            interactive_elements=[],
        )
        monitor = ContextMonitor(accessibility=bridge)
        ctx = monitor.update_cheap()
        assert ctx.frontmost_app == ""
        assert ctx.window_title == ""
        assert ctx.interactive_elements == []
        assert ctx.iteration_count == 1

    def test_bridge_get_frontmost_app_raises(self):
        """get_frontmost_app throws -- update_cheap should either catch it
        or propagate. The design doc wraps AX calls in try/except, so
        we test that it doesn't crash the monitor.
        """
        bridge = _make_bridge(raise_frontmost=True)
        monitor = ContextMonitor(accessibility=bridge)
        # If the implementation wraps in try/except, this should not raise.
        # If it doesn't, the test catches the design gap.
        try:
            ctx = monitor.update_cheap()
            assert ctx.iteration_count == 1
        except RuntimeError:
            pytest.fail(
                "update_cheap() should not propagate AX exceptions -- "
                "design doc says graceful degradation"
            )

    def test_bridge_get_focused_window_raises(self):
        """get_focused_window throws -- should degrade gracefully."""
        bridge = _make_bridge(
            frontmost_app={"name": "Safari", "pid": 123, "bundle_id": ""},
            raise_window=True,
        )
        monitor = ContextMonitor(accessibility=bridge)
        try:
            ctx = monitor.update_cheap()
            assert ctx.frontmost_app == "Safari"
            assert ctx.iteration_count == 1
        except RuntimeError:
            pytest.fail(
                "update_cheap() should not propagate focused_window exceptions"
            )

    def test_bridge_get_interactive_elements_raises(self):
        """get_interactive_elements throws -- should degrade gracefully."""
        bridge = _make_bridge(
            frontmost_app={"name": "Notes", "pid": 1, "bundle_id": ""},
            focused_window=_make_ax_element(role="AXWindow", title="Notes"),
            raise_elements=True,
        )
        monitor = ContextMonitor(accessibility=bridge)
        try:
            ctx = monitor.update_cheap()
            assert ctx.frontmost_app == "Notes"
            assert ctx.iteration_count == 1
        except RuntimeError:
            pytest.fail(
                "update_cheap() should not propagate interactive_elements "
                "exceptions"
            )

    def test_frontmost_app_partial_data(self):
        """get_frontmost_app returns dict with name but missing pid."""
        bridge = _make_bridge(
            frontmost_app={"name": "Preview", "pid": 0, "bundle_id": ""},
        )
        monitor = ContextMonitor(accessibility=bridge)
        ctx = monitor.update_cheap()
        assert ctx.frontmost_app == "Preview"

    def test_frontmost_app_empty_name(self):
        """App name is empty string."""
        bridge = _make_bridge(
            frontmost_app={"name": "", "pid": 42, "bundle_id": "com.x"},
        )
        monitor = ContextMonitor(accessibility=bridge)
        ctx = monitor.update_cheap()
        assert ctx.frontmost_app == ""

    def test_focused_window_title_none(self):
        """Window element has title=None -- should become '' in context."""
        bridge = _make_bridge(
            frontmost_app={"name": "App", "pid": 1, "bundle_id": ""},
            focused_window=_make_ax_element(role="AXWindow", title=None),
        )
        monitor = ContextMonitor(accessibility=bridge)
        ctx = monitor.update_cheap()
        assert ctx.window_title == ""

    def test_interactive_elements_large_count(self):
        """1000+ interactive elements returned -- no truncation in update_cheap."""
        elements = [
            _make_ax_element(title=f"Elem{i}", position=(i, i))
            for i in range(1000)
        ]
        bridge = _make_bridge(
            frontmost_app={"name": "App", "pid": 1, "bundle_id": ""},
            focused_window=_make_ax_element(role="AXWindow", title="Win"),
            interactive_elements=elements,
        )
        monitor = ContextMonitor(accessibility=bridge)
        ctx = monitor.update_cheap()
        assert len(ctx.interactive_elements) == 1000

    def test_app_changes_between_calls(self):
        """App changes between get_frontmost_app and get_focused_window.

        This is a TOCTOU race. The design doc calls get_frontmost_app and
        get_focused_window sequentially -- if the app switches between the
        two calls, window_title may not match frontmost_app. The test just
        verifies no crash.
        """
        bridge = MagicMock()
        bridge.get_frontmost_app = MagicMock(
            return_value={"name": "Safari", "pid": 1, "bundle_id": ""}
        )
        # Window belongs to a different app
        bridge.get_focused_window = MagicMock(
            return_value=_make_ax_element(
                role="AXWindow", title="Terminal -- bash"
            )
        )
        bridge.get_interactive_elements = MagicMock(return_value=[])
        monitor = ContextMonitor(accessibility=bridge)
        ctx = monitor.update_cheap()
        # No crash; mismatched state is captured as-is
        assert ctx.frontmost_app == "Safari"
        assert ctx.window_title == "Terminal -- bash"

    def test_called_10000_times_iteration_count(self):
        """iteration_count increments correctly after 10000 calls."""
        monitor = ContextMonitor(accessibility=None)
        for _ in range(10000):
            monitor.update_cheap()
        assert monitor.context.iteration_count == 10000

    def test_iteration_count_type_is_int(self):
        """iteration_count should remain a Python int, not overflow to float."""
        monitor = ContextMonitor(accessibility=None)
        for _ in range(100):
            monitor.update_cheap()
        assert isinstance(monitor.context.iteration_count, int)

    def test_update_cheap_returns_same_context_object(self):
        """update_cheap returns self.context -- same object each call."""
        monitor = ContextMonitor(accessibility=None)
        ctx1 = monitor.update_cheap()
        ctx2 = monitor.update_cheap()
        assert ctx1 is ctx2


# ============================================================================
# 3. needs_full_vision() Logic
# ============================================================================


@pytest.mark.unit
class TestNeedsFullVision:

    def test_first_call_no_description(self):
        """First call with empty last_vision_description -> True."""
        monitor = ContextMonitor()
        assert monitor.needs_full_vision() is True

    def test_with_description_set(self):
        """After setting last_vision_description, should return False
        (unless iteration hits modulo boundary).
        """
        monitor = ContextMonitor()
        monitor.context.last_vision_description = "Screen shows Safari"
        monitor.context.iteration_count = 1
        assert monitor.needs_full_vision() is False

    def test_cleared_description_returns_true(self):
        """If last_vision_description is cleared, returns True again."""
        monitor = ContextMonitor()
        monitor.context.last_vision_description = "something"
        monitor.context.iteration_count = 1
        assert monitor.needs_full_vision() is False
        monitor.context.last_vision_description = ""
        assert monitor.needs_full_vision() is True

    def test_iteration_zero_modulo_boundary(self):
        """iteration_count=0: 0 % 3 == 0 -> True (every-3-step refresh)."""
        monitor = ContextMonitor()
        monitor.context.last_vision_description = "has description"
        monitor.context.iteration_count = 0
        # 0 % 3 == 0 -> True
        assert monitor.needs_full_vision() is True

    def test_iteration_3_modulo_boundary(self):
        """iteration_count=3: 3 % 3 == 0 -> True."""
        monitor = ContextMonitor()
        monitor.context.last_vision_description = "has description"
        monitor.context.iteration_count = 3
        assert monitor.needs_full_vision() is True

    def test_iteration_6_modulo_boundary(self):
        """iteration_count=6: 6 % 3 == 0 -> True."""
        monitor = ContextMonitor()
        monitor.context.last_vision_description = "has description"
        monitor.context.iteration_count = 6
        assert monitor.needs_full_vision() is True

    def test_iteration_1_not_boundary(self):
        """iteration_count=1: 1 % 3 != 0 -> False (with description)."""
        monitor = ContextMonitor()
        monitor.context.last_vision_description = "desc"
        monitor.context.iteration_count = 1
        assert monitor.needs_full_vision() is False

    def test_iteration_2_not_boundary(self):
        """iteration_count=2: 2 % 3 != 0 -> False."""
        monitor = ContextMonitor()
        monitor.context.last_vision_description = "desc"
        monitor.context.iteration_count = 2
        assert monitor.needs_full_vision() is False

    def test_iteration_4_not_boundary(self):
        """iteration_count=4: 4 % 3 != 0 -> False."""
        monitor = ContextMonitor()
        monitor.context.last_vision_description = "desc"
        monitor.context.iteration_count = 4
        assert monitor.needs_full_vision() is False

    def test_iteration_5_not_boundary(self):
        """iteration_count=5: 5 % 3 != 0 -> False."""
        monitor = ContextMonitor()
        monitor.context.last_vision_description = "desc"
        monitor.context.iteration_count = 5
        assert monitor.needs_full_vision() is False

    def test_iteration_9_modulo_boundary(self):
        """iteration_count=9: 9 % 3 == 0 -> True."""
        monitor = ContextMonitor()
        monitor.context.last_vision_description = "desc"
        monitor.context.iteration_count = 9
        assert monitor.needs_full_vision() is True

    def test_no_description_always_true_regardless_of_iteration(self):
        """Without a vision description, every call returns True."""
        monitor = ContextMonitor()
        for i in range(10):
            monitor.context.iteration_count = i
            assert monitor.needs_full_vision() is True

    def test_off_by_one_after_update_cheap(self):
        """update_cheap increments iteration_count. Verify needs_full_vision
        sees the incremented value, not the pre-increment value.
        """
        monitor = ContextMonitor(accessibility=None)
        monitor.context.last_vision_description = "desc"
        # iteration_count starts at 0
        monitor.update_cheap()  # now iteration_count == 1
        assert monitor.needs_full_vision() is False
        monitor.update_cheap()  # now 2
        assert monitor.needs_full_vision() is False
        monitor.update_cheap()  # now 3
        assert monitor.needs_full_vision() is True


# ============================================================================
# 4. Record Methods
# ============================================================================


@pytest.mark.unit
class TestRecordMethods:

    # ---- record_click ----

    def test_record_click_normal(self):
        monitor = ContextMonitor()
        monitor.record_click("Submit button")
        assert monitor.context.recently_clicked == "Submit button"

    def test_record_click_empty_string(self):
        monitor = ContextMonitor()
        monitor.record_click("")
        assert monitor.context.recently_clicked == ""

    def test_record_click_very_long_string(self):
        long_str = "x" * 10000
        monitor = ContextMonitor()
        monitor.record_click(long_str)
        assert monitor.context.recently_clicked == long_str

    def test_record_click_unicode(self):
        monitor = ContextMonitor()
        monitor.record_click("\u2603 \u2764 \U0001f600")
        assert monitor.context.recently_clicked == "\u2603 \u2764 \U0001f600"

    def test_record_click_overwrites_previous(self):
        monitor = ContextMonitor()
        monitor.record_click("First")
        monitor.record_click("Second")
        assert monitor.context.recently_clicked == "Second"

    def test_record_click_with_newlines_and_tabs(self):
        monitor = ContextMonitor()
        monitor.record_click("line1\nline2\ttab")
        assert monitor.context.recently_clicked == "line1\nline2\ttab"

    # ---- record_type ----

    def test_record_type_no_field_name(self):
        """field_name=None (default) -> only recently_typed updated."""
        monitor = ContextMonitor()
        monitor.record_type("hello")
        assert monitor.context.recently_typed == "hello"
        assert monitor.context.form_fields_filled == {}

    def test_record_type_field_name_none_explicit(self):
        """Explicitly passing field_name=None."""
        monitor = ContextMonitor()
        monitor.record_type("hello", field_name=None)
        assert monitor.context.recently_typed == "hello"
        assert monitor.context.form_fields_filled == {}

    def test_record_type_field_name_empty_string(self):
        """field_name='' -- the design doc checks `if field_name:`, so
        empty string should NOT add to form_fields_filled.
        """
        monitor = ContextMonitor()
        monitor.record_type("hello", field_name="")
        assert monitor.context.recently_typed == "hello"
        # Empty string is falsy, so no entry added
        assert "" not in monitor.context.form_fields_filled

    def test_record_type_field_name_valid(self):
        monitor = ContextMonitor()
        monitor.record_type("John", field_name="name")
        assert monitor.context.form_fields_filled["name"] == "John"

    def test_record_type_overwrites_field(self):
        monitor = ContextMonitor()
        monitor.record_type("John", field_name="name")
        monitor.record_type("Jane", field_name="name")
        assert monitor.context.form_fields_filled["name"] == "Jane"

    def test_record_type_multiple_fields(self):
        monitor = ContextMonitor()
        monitor.record_type("John", field_name="first_name")
        monitor.record_type("Doe", field_name="last_name")
        assert monitor.context.form_fields_filled == {
            "first_name": "John",
            "last_name": "Doe",
        }

    def test_record_type_empty_text(self):
        monitor = ContextMonitor()
        monitor.record_type("", field_name="search")
        assert monitor.context.recently_typed == ""
        assert monitor.context.form_fields_filled["search"] == ""

    # ---- record_navigation ----

    def test_record_navigation_normal(self):
        monitor = ContextMonitor()
        monitor.record_navigation("https://example.com")
        assert monitor.context.navigation_history == ["https://example.com"]

    def test_record_navigation_empty_string(self):
        monitor = ContextMonitor()
        monitor.record_navigation("")
        assert monitor.context.navigation_history == [""]

    def test_record_navigation_invalid_url(self):
        """Invalid URLs should still be recorded (no validation expected)."""
        monitor = ContextMonitor()
        monitor.record_navigation("not a url")
        assert "not a url" in monitor.context.navigation_history

    def test_record_navigation_duplicates(self):
        """Duplicate URLs should all be appended (list, not set)."""
        monitor = ContextMonitor()
        monitor.record_navigation("https://example.com")
        monitor.record_navigation("https://example.com")
        assert len(monitor.context.navigation_history) == 2

    def test_record_navigation_preserves_order(self):
        monitor = ContextMonitor()
        urls = [f"https://example.com/{i}" for i in range(5)]
        for url in urls:
            monitor.record_navigation(url)
        assert monitor.context.navigation_history == urls

    # ---- rapid sequential records ----

    def test_rapid_sequential_all_captured(self):
        """Rapid interleaved record calls should all be captured."""
        monitor = ContextMonitor()
        for i in range(100):
            monitor.record_click(f"btn{i}")
            monitor.record_type(f"text{i}", field_name=f"field{i}")
            monitor.record_navigation(f"https://site.com/{i}")

        assert monitor.context.recently_clicked == "btn99"
        assert monitor.context.recently_typed == "text99"
        assert len(monitor.context.form_fields_filled) == 100
        assert len(monitor.context.navigation_history) == 100


# ============================================================================
# 5. format_for_planner()
# ============================================================================


@pytest.mark.unit
class TestFormatForPlanner:

    def test_empty_context(self):
        """Empty context (no app, no window, no elements) -> valid string."""
        monitor = ContextMonitor()
        result = monitor.format_for_planner()
        assert isinstance(result, str)
        assert "Desktop State" in result
        assert "App:" in result

    def test_basic_context(self):
        monitor = ContextMonitor()
        monitor.context.frontmost_app = "Safari"
        monitor.context.window_title = "Google"
        result = monitor.format_for_planner()
        assert "Safari" in result
        assert "Google" in result

    def test_elements_with_none_title_and_description(self):
        """Elements where title and description are both None --
        should fall back to role without crashing.
        """
        elem = _make_ax_element(
            role="AXButton", title=None, description=None
        )
        monitor = ContextMonitor()
        monitor.context.interactive_elements = [elem]
        result = monitor.format_for_planner()
        assert isinstance(result, str)
        # Should contain the role as fallback label
        assert "Button" in result or "AXButton" in result

    def test_elements_with_empty_strings(self):
        """title='' and description='' -- should not produce 'None' in output."""
        elem = _make_ax_element(
            role="AXTextField", title="", description=""
        )
        monitor = ContextMonitor()
        monitor.context.interactive_elements = [elem]
        result = monitor.format_for_planner()
        assert "None" not in result

    def test_truncation_at_20_elements(self):
        """format_for_planner should only show first 20 interactive elements."""
        elements = [
            _make_ax_element(title=f"Elem{i}", role="AXButton")
            for i in range(50)
        ]
        monitor = ContextMonitor()
        monitor.context.interactive_elements = elements
        result = monitor.format_for_planner()
        # Count element entries: each should have "[Button]" prefix
        button_count = result.count("[Button]")
        # design doc slices [:20]
        assert button_count <= 20

    def test_vision_description_truncated_at_500(self):
        """Very long vision description should be truncated to 500 chars."""
        long_desc = "A" * 1000
        monitor = ContextMonitor()
        monitor.context.last_vision_description = long_desc
        result = monitor.format_for_planner()
        # The design doc does [:500], so at most 500 chars of the desc
        # appear after "Screen: "
        screen_idx = result.index("Screen:")
        screen_section = result[screen_idx:]
        assert len(screen_section) < 600  # 500 desc + "Screen: " prefix

    def test_no_vision_description_omits_screen_section(self):
        """When last_vision_description is empty, no '## Screen' section."""
        monitor = ContextMonitor()
        monitor.context.last_vision_description = ""
        result = monitor.format_for_planner()
        assert "Screen:" not in result

    def test_form_fields_in_output(self):
        monitor = ContextMonitor()
        monitor.record_type("sushi", field_name="cuisine")
        monitor.record_type("2", field_name="party_size")
        result = monitor.format_for_planner()
        assert "Form Progress" in result
        assert "cuisine" in result
        assert "sushi" in result
        assert "party_size" in result

    def test_no_form_fields_omits_section(self):
        monitor = ContextMonitor()
        result = monitor.format_for_planner()
        assert "Form Progress" not in result

    def test_special_characters_in_fields(self):
        """Newlines, tabs, markdown in field values should not break output."""
        monitor = ContextMonitor()
        monitor.context.frontmost_app = "App\nWith\tSpecial"
        monitor.context.window_title = "## Heading"
        monitor.record_type("value\nwith\nnewlines", field_name="field")
        result = monitor.format_for_planner()
        # Just verify no crash and the content is present
        assert isinstance(result, str)
        assert "App" in result

    def test_very_long_form_values_present(self):
        """Long form values should appear (no truncation specified in design)."""
        monitor = ContextMonitor()
        long_val = "X" * 5000
        monitor.record_type(long_val, field_name="essay")
        result = monitor.format_for_planner()
        # The design doc doesn't specify truncation for form values
        assert "essay" in result

    def test_interactive_elements_count_in_header(self):
        """The element count in parentheses should match actual element count."""
        elements = [
            _make_ax_element(title=f"E{i}", role="AXButton")
            for i in range(7)
        ]
        monitor = ContextMonitor()
        monitor.context.interactive_elements = elements
        result = monitor.format_for_planner()
        assert "(7)" in result

    def test_element_role_ax_prefix_stripped(self):
        """Design doc does elem.role.replace('AX', '') for display."""
        elem = _make_ax_element(role="AXPopUpButton", title="Options")
        monitor = ContextMonitor()
        monitor.context.interactive_elements = [elem]
        result = monitor.format_for_planner()
        # Should show "PopUpButton", not "AXPopUpButton"
        assert "PopUpButton" in result
        # If the implementation strips "AX" prefix, "AX" should not precede
        # the role in the element listing (but may appear in other text)

    def test_element_label_priority_title_over_description_over_role(self):
        """Label should be: title if present, else description, else role."""
        # Has title
        e1 = _make_ax_element(
            role="AXButton", title="Submit", description="submit btn"
        )
        # No title, has description
        e2 = _make_ax_element(
            role="AXButton", title=None, description="cancel btn"
        )
        # No title, no description
        e3 = _make_ax_element(
            role="AXButton", title=None, description=None
        )
        monitor = ContextMonitor()
        monitor.context.interactive_elements = [e1, e2, e3]
        result = monitor.format_for_planner()
        assert "Submit" in result
        assert "cancel btn" in result
        # e3 should use role as fallback
        # At minimum it shouldn't say "None"
        lines = result.split("\n")
        element_lines = [l for l in lines if "[Button]" in l]
        for line in element_lines:
            assert "None" not in line


# ============================================================================
# 6. Graceful Degradation
# ============================================================================


@pytest.mark.unit
class TestGracefulDegradation:

    def test_no_bridge_update_cheap_works(self):
        """ContextMonitor with accessibility=None: update_cheap succeeds."""
        monitor = ContextMonitor(accessibility=None)
        ctx = monitor.update_cheap()
        assert ctx.frontmost_app == ""
        assert ctx.iteration_count == 1

    def test_no_bridge_all_methods_work(self):
        """All methods work without a bridge."""
        monitor = ContextMonitor(accessibility=None)
        monitor.update_cheap()
        monitor.record_click("btn")
        monitor.record_type("text", field_name="f")
        monitor.record_navigation("url")
        result = monitor.format_for_planner()
        assert isinstance(result, str)

    def test_bridge_errors_everywhere(self):
        """Bridge raises on every method -- monitor should still function."""
        bridge = _make_bridge(
            raise_frontmost=True,
            raise_window=True,
            raise_elements=True,
        )
        monitor = ContextMonitor(accessibility=bridge)
        try:
            ctx = monitor.update_cheap()
            assert ctx.iteration_count == 1
        except RuntimeError:
            pytest.fail("update_cheap should handle all bridge errors")

    def test_format_for_planner_no_vision_no_elements(self):
        """Totally empty state: format_for_planner returns valid string."""
        monitor = ContextMonitor()
        result = monitor.format_for_planner()
        assert "Desktop State" in result
        assert "App:" in result

    def test_format_for_planner_after_failed_update(self):
        """If update_cheap fails silently, format_for_planner still works."""
        bridge = _make_bridge(raise_frontmost=True)
        monitor = ContextMonitor(accessibility=bridge)
        try:
            monitor.update_cheap()
        except RuntimeError:
            pass  # If it propagates, that's okay
        # Either way, format should work with whatever state we have
        result = monitor.format_for_planner()
        assert isinstance(result, str)


# ============================================================================
# 7. Integration Scenarios
# ============================================================================


@pytest.mark.unit
class TestIntegrationScenarios:

    def test_full_agent_loop_simulation(self):
        """Simulate the agent loop: update_cheap -> needs_full_vision ->
        record actions -> format_for_planner, for 10 iterations.
        """
        bridge = _make_bridge(
            frontmost_app={"name": "Safari", "pid": 1, "bundle_id": ""},
            focused_window=_make_ax_element(
                role="AXWindow", title="Google"
            ),
            interactive_elements=[
                _make_ax_element(
                    role="AXButton",
                    title="Search",
                    position=(100, 200),
                    size=(80, 30),
                ),
            ],
        )
        monitor = ContextMonitor(accessibility=bridge)

        for i in range(10):
            ctx = monitor.update_cheap()

            if monitor.needs_full_vision():
                monitor.context.last_vision_description = (
                    f"Iteration {i}: Google search page"
                )

            monitor.record_click(f"element_{i}")
            monitor.record_type(f"query_{i}", field_name=f"field_{i}")
            monitor.record_navigation(f"https://google.com/q={i}")

            output = monitor.format_for_planner()
            assert isinstance(output, str)
            assert "Safari" in output

        assert monitor.context.iteration_count == 10
        assert len(monitor.context.navigation_history) == 10
        assert len(monitor.context.form_fields_filled) == 10

    def test_context_not_passed_when_monitor_is_none(self):
        """When the agent has no context_monitor, the planner should receive
        no desktop_context. This tests the integration pattern from the
        design doc.
        """
        # Simulate the agent code pattern:
        context_monitor = None  # No monitor configured

        desktop_context = ""
        if context_monitor:
            desktop_context = context_monitor.format_for_planner()

        assert desktop_context == ""

    def test_context_passed_when_monitor_exists(self):
        """When monitor exists, planner receives formatted context."""
        monitor = ContextMonitor()
        monitor.context.frontmost_app = "Terminal"
        monitor.context.window_title = "bash"

        desktop_context = ""
        if monitor:
            desktop_context = monitor.format_for_planner()

        assert "Terminal" in desktop_context
        assert "bash" in desktop_context

    def test_vision_description_updated_by_agent(self):
        """Agent sets last_vision_description on the context, then
        needs_full_vision correctly reflects the update.
        """
        monitor = ContextMonitor(accessibility=None)

        # First iteration: no description -> needs vision
        monitor.update_cheap()  # iteration_count = 1
        assert monitor.needs_full_vision() is True

        # Agent does vision and sets description
        monitor.context.last_vision_description = "Safari showing Google"
        # iteration_count=1, 1%3 != 0, has description -> False
        assert monitor.needs_full_vision() is False

        # Two more iterations
        monitor.update_cheap()  # iteration_count = 2
        assert monitor.needs_full_vision() is False
        monitor.update_cheap()  # iteration_count = 3
        assert monitor.needs_full_vision() is True  # 3%3==0

    def test_sequential_update_cheap_idempotent_on_same_state(self):
        """Calling update_cheap multiple times with same bridge state
        should produce consistent context (except iteration_count).
        """
        bridge = _make_bridge(
            frontmost_app={"name": "Finder", "pid": 1, "bundle_id": ""},
            focused_window=_make_ax_element(
                role="AXWindow", title="Desktop"
            ),
            interactive_elements=[],
        )
        monitor = ContextMonitor(accessibility=bridge)

        monitor.update_cheap()
        app1 = monitor.context.frontmost_app
        title1 = monitor.context.window_title

        monitor.update_cheap()
        assert monitor.context.frontmost_app == app1
        assert monitor.context.window_title == title1

    def test_app_switch_detected(self):
        """Switching apps between update_cheap calls is reflected."""
        bridge = MagicMock()
        bridge.get_frontmost_app = MagicMock(
            side_effect=[
                {"name": "Safari", "pid": 1, "bundle_id": ""},
                {"name": "Terminal", "pid": 2, "bundle_id": ""},
            ]
        )
        bridge.get_focused_window = MagicMock(
            side_effect=[
                _make_ax_element(role="AXWindow", title="Google"),
                _make_ax_element(role="AXWindow", title="bash"),
            ]
        )
        bridge.get_interactive_elements = MagicMock(return_value=[])

        monitor = ContextMonitor(accessibility=bridge)

        monitor.update_cheap()
        assert monitor.context.frontmost_app == "Safari"
        assert monitor.context.window_title == "Google"

        monitor.update_cheap()
        assert monitor.context.frontmost_app == "Terminal"
        assert monitor.context.window_title == "bash"


# ============================================================================
# 8. Constructor
# ============================================================================


@pytest.mark.unit
class TestConstructor:

    def test_default_no_accessibility(self):
        monitor = ContextMonitor()
        assert monitor.accessibility is None
        assert isinstance(monitor.context, DesktopContext)

    def test_with_accessibility(self):
        bridge = _make_bridge()
        monitor = ContextMonitor(accessibility=bridge)
        assert monitor.accessibility is bridge

    def test_context_is_fresh_instance(self):
        """Each ContextMonitor should have its own DesktopContext."""
        m1 = ContextMonitor()
        m2 = ContextMonitor()
        assert m1.context is not m2.context

    def test_context_starts_at_zero_iterations(self):
        monitor = ContextMonitor()
        assert monitor.context.iteration_count == 0
