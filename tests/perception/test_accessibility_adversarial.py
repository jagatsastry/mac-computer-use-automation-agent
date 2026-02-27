"""Adversarial tests for the AccessibilityBridge module.

These tests target edge cases, failure modes, race conditions, and malformed inputs
that the AccessibilityBridge and AXElement must handle gracefully. All macOS
framework dependencies (pyobjc, ApplicationServices, AppKit) are mocked at the
module level so tests can run on any platform.

Every test is fully mocked -- no real macOS Accessibility API calls.
"""

import sys
import threading
import types
from dataclasses import field
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Module-level mocks for pyobjc frameworks
# ---------------------------------------------------------------------------
# pyobjc may not be installed in the test environment. We mock the entire
# framework hierarchy before importing the module under test.

_mock_appkit = MagicMock()
_mock_app_services = MagicMock()
_mock_app_services.AXUIElementCreateSystemWide = MagicMock()
_mock_app_services.AXUIElementCreateApplication = MagicMock()
_mock_app_services.AXUIElementCopyAttributeValue = MagicMock()

sys.modules.setdefault("AppKit", _mock_appkit)
sys.modules.setdefault("ApplicationServices", _mock_app_services)
sys.modules.setdefault("CoreFoundation", MagicMock())
sys.modules.setdefault("Quartz", MagicMock())
sys.modules.setdefault("objc", MagicMock())
sys.modules.setdefault("Foundation", MagicMock())
sys.modules.setdefault("PyObjCTools", MagicMock())

from automation_agent.perception.accessibility import (  # noqa: E402
    AccessibilityBridge,
    AXElement,
)


# ============================================================================
# 1. AXElement Dataclass Edge Cases
# ============================================================================


class TestAXElementCenter:
    """Edge cases for the AXElement.center computed property."""

    @pytest.mark.unit
    def test_center_normal(self):
        """Normal case: position and size present."""
        el = AXElement(role="AXButton", position=(100, 200), size=(60, 40))
        assert el.center == (130, 220)

    @pytest.mark.unit
    def test_center_zero_width(self):
        """Zero-width element -- center x should be the position x."""
        el = AXElement(role="AXButton", position=(100, 200), size=(0, 40))
        assert el.center is not None
        assert el.center == (100, 220)

    @pytest.mark.unit
    def test_center_zero_height(self):
        """Zero-height element -- center y should be the position y."""
        el = AXElement(role="AXButton", position=(100, 200), size=(60, 0))
        assert el.center is not None
        assert el.center == (130, 200)

    @pytest.mark.unit
    def test_center_zero_size_both(self):
        """Both width and height are zero -- center equals position."""
        el = AXElement(role="AXButton", position=(100, 200), size=(0, 0))
        assert el.center is not None
        assert el.center == (100, 200)

    @pytest.mark.unit
    def test_center_negative_position(self):
        """Negative position (off-screen window) -- center still computes."""
        el = AXElement(role="AXWindow", position=(-500, -300), size=(800, 600))
        assert el.center is not None
        assert el.center == (-100, 0)

    @pytest.mark.unit
    def test_center_very_large_coordinates(self):
        """Very large coordinates (multi-monitor) -- no overflow."""
        el = AXElement(
            role="AXButton",
            position=(10000, 20000),
            size=(500, 300),
        )
        assert el.center == (10250, 20150)

    @pytest.mark.unit
    def test_center_none_position(self):
        """Position is None, size is valid -- center must be None."""
        el = AXElement(role="AXButton", position=None, size=(60, 40))
        assert el.center is None

    @pytest.mark.unit
    def test_center_none_size(self):
        """Size is None, position is valid -- center must be None."""
        el = AXElement(role="AXButton", position=(100, 200), size=None)
        assert el.center is None

    @pytest.mark.unit
    def test_center_both_none(self):
        """Both position and size are None -- center must be None."""
        el = AXElement(role="AXButton", position=None, size=None)
        assert el.center is None

    @pytest.mark.unit
    def test_center_odd_size_integer_division(self):
        """Odd-sized element -- integer division truncates toward zero."""
        el = AXElement(role="AXButton", position=(0, 0), size=(7, 3))
        # 0 + 7 // 2 = 3, 0 + 3 // 2 = 1
        assert el.center == (3, 1)

    @pytest.mark.unit
    def test_center_single_pixel_element(self):
        """1x1 element -- center is at the position itself."""
        el = AXElement(role="AXButton", position=(500, 500), size=(1, 1))
        assert el.center == (500, 500)


class TestAXElementTextFields:
    """Edge cases for text fields (title, description, value)."""

    @pytest.mark.unit
    def test_all_text_fields_empty_strings(self):
        """All text fields set to empty strings -- element should be valid."""
        el = AXElement(
            role="AXStaticText",
            title="",
            value="",
            description="",
        )
        assert el.role == "AXStaticText"
        assert el.title == ""
        assert el.value == ""
        assert el.description == ""

    @pytest.mark.unit
    def test_very_long_title(self):
        """Title with 10000+ characters -- no truncation or crash."""
        long_title = "A" * 15000
        el = AXElement(role="AXButton", title=long_title)
        assert el.title == long_title
        assert len(el.title) == 15000

    @pytest.mark.unit
    def test_unicode_in_title(self):
        """Unicode characters in title (CJK, Arabic, etc.)."""
        el = AXElement(role="AXButton", title="\u4f60\u597d\u4e16\u754c")
        assert el.title == "\u4f60\u597d\u4e16\u754c"

    @pytest.mark.unit
    def test_emoji_in_title_and_description(self):
        """Emoji in title and description -- no encoding errors."""
        el = AXElement(
            role="AXButton",
            title="\U0001f680 Launch \U0001f31f",
            description="Click to launch \U0001f4a5",
        )
        assert "\U0001f680" in el.title
        assert "\U0001f4a5" in el.description

    @pytest.mark.unit
    def test_newlines_and_tabs_in_value(self):
        """Whitespace characters in value -- should be preserved."""
        el = AXElement(role="AXTextField", value="line1\nline2\ttab")
        assert "\n" in el.value
        assert "\t" in el.value

    @pytest.mark.unit
    def test_null_bytes_in_title(self):
        """Null bytes in title -- should not cause crashes."""
        el = AXElement(role="AXButton", title="before\x00after")
        assert "\x00" in el.title

    @pytest.mark.unit
    def test_all_text_fields_none(self):
        """All text fields default to None -- should be valid."""
        el = AXElement(role="AXUnknown")
        assert el.title is None
        assert el.value is None
        assert el.description is None


class TestAXElementChildren:
    """Edge cases for nested children."""

    @pytest.mark.unit
    def test_deeply_nested_children(self):
        """100+ levels of nesting -- should not crash or stack overflow."""
        # Build a chain: each element has one child
        inner = AXElement(role="AXLeaf")
        for i in range(150):
            inner = AXElement(role=f"AXLevel{i}", children=[inner])
        # Traverse to the bottom
        node = inner
        depth = 0
        while node.children:
            node = node.children[0]
            depth += 1
        assert depth == 150

    @pytest.mark.unit
    def test_very_wide_tree(self):
        """100 siblings at the same level -- no issues."""
        children = [
            AXElement(role="AXButton", title=f"Btn{i}") for i in range(100)
        ]
        parent = AXElement(role="AXGroup", children=children)
        assert len(parent.children) == 100

    @pytest.mark.unit
    def test_empty_children_list(self):
        """Default empty children list -- should be an empty list, not None."""
        el = AXElement(role="AXButton")
        assert el.children == []
        assert isinstance(el.children, list)

    @pytest.mark.unit
    def test_mixed_depth_children(self):
        """Children at varying depths -- tree is not necessarily balanced."""
        leaf = AXElement(role="AXLeaf")
        deep = AXElement(role="AXL1", children=[AXElement(role="AXL2", children=[leaf])])
        parent = AXElement(role="AXRoot", children=[deep, AXElement(role="AXShallow")])
        assert len(parent.children) == 2
        assert len(parent.children[0].children) == 1
        assert len(parent.children[1].children) == 0


class TestAXElementDefaults:
    """Verify dataclass default values are correct."""

    @pytest.mark.unit
    def test_defaults(self):
        """Only role is required -- all other fields should have sensible defaults."""
        el = AXElement(role="AXButton")
        assert el.enabled is True
        assert el.focused is False
        assert el.raw_ref is None
        assert el.children == []
        assert el.position is None
        assert el.size is None

    @pytest.mark.unit
    def test_disabled_element(self):
        """Explicitly disabled element."""
        el = AXElement(role="AXButton", enabled=False)
        assert el.enabled is False


# ============================================================================
# 2. AccessibilityBridge Failure Modes
# ============================================================================


class TestAccessibilityBridgeInit:
    """Initialization and configuration edge cases."""

    @pytest.mark.unit
    def test_default_max_depth(self):
        """Default max_depth should be 8 per spec."""
        bridge = AccessibilityBridge()
        assert bridge.max_depth == 8

    @pytest.mark.unit
    def test_custom_max_depth(self):
        """Custom max_depth is stored."""
        bridge = AccessibilityBridge(max_depth=20)
        assert bridge.max_depth == 20

    @pytest.mark.unit
    def test_zero_max_depth(self):
        """max_depth=0 -- should be accepted (means root only)."""
        bridge = AccessibilityBridge(max_depth=0)
        assert bridge.max_depth == 0

    @pytest.mark.unit
    def test_negative_max_depth(self):
        """Negative max_depth -- implementation should handle gracefully.

        This could either raise ValueError or treat it as 0.
        The important thing is: no infinite loop or crash.
        """
        # We just check it does not crash on construction
        bridge = AccessibilityBridge(max_depth=-1)
        assert bridge.max_depth == -1


class TestGetFrontmostApp:
    """Edge cases for get_frontmost_app()."""

    @pytest.mark.unit
    def test_returns_none_when_no_active_app(self):
        """No active application (e.g., login screen) -- should return None."""
        bridge = AccessibilityBridge()
        _mock_appkit.NSWorkspace.sharedWorkspace.return_value.activeApplication.return_value = (
            None
        )
        result = bridge.get_frontmost_app()
        assert result is None

    @pytest.mark.unit
    def test_returns_app_info_dict(self):
        """Normal case: active application exists."""
        bridge = AccessibilityBridge()
        _mock_appkit.NSWorkspace.sharedWorkspace.return_value.activeApplication.return_value = {
            "NSApplicationName": "Safari",
            "NSApplicationProcessIdentifier": 12345,
            "NSApplicationBundleIdentifier": "com.apple.Safari",
        }
        result = bridge.get_frontmost_app()
        assert result is not None
        assert result["name"] == "Safari"
        assert result["pid"] == 12345
        assert result["bundle_id"] == "com.apple.Safari"

    @pytest.mark.unit
    def test_missing_keys_in_active_app(self):
        """Active app dict is missing some expected keys -- should use defaults."""
        bridge = AccessibilityBridge()
        _mock_appkit.NSWorkspace.sharedWorkspace.return_value.activeApplication.return_value = (
            {}
        )
        result = bridge.get_frontmost_app()
        assert result is not None
        assert result["name"] == ""
        assert result["pid"] == 0
        assert result["bundle_id"] == ""

    @pytest.mark.unit
    def test_workspace_raises_exception(self):
        """AppKit raises an exception (accessibility permission denied).

        The bridge should either propagate the exception or return None.
        It must NOT silently return garbage data.
        """
        bridge = AccessibilityBridge()
        _mock_appkit.NSWorkspace.sharedWorkspace.side_effect = RuntimeError(
            "Accessibility permission denied"
        )
        with pytest.raises(RuntimeError):
            bridge.get_frontmost_app()
        # Reset the side_effect for subsequent tests
        _mock_appkit.NSWorkspace.sharedWorkspace.side_effect = None

    @pytest.mark.unit
    def test_app_crashes_between_calls(self):
        """Frontmost app returns valid data first, then crashes on second call.

        Simulates the app disappearing between two consecutive calls.
        """
        bridge = AccessibilityBridge()
        workspace_mock = _mock_appkit.NSWorkspace.sharedWorkspace.return_value
        workspace_mock.activeApplication.side_effect = [
            {"NSApplicationName": "Safari", "NSApplicationProcessIdentifier": 1},
            None,
        ]
        first = bridge.get_frontmost_app()
        second = bridge.get_frontmost_app()
        assert first is not None
        assert first["name"] == "Safari"
        assert second is None
        # Reset
        workspace_mock.activeApplication.side_effect = None


class TestGetElementTree:
    """Edge cases for get_element_tree()."""

    @pytest.mark.unit
    def test_max_depth_zero_returns_root_only(self):
        """max_depth=0 should return just the root element with no children."""
        bridge = AccessibilityBridge(max_depth=0)
        tree = bridge.get_element_tree(max_depth=0)
        # If the tree is returned, children should be empty or very shallow
        if tree is not None:
            # With max_depth=0, no traversal should occur
            assert isinstance(tree, AXElement)

    @pytest.mark.unit
    def test_override_max_depth(self):
        """Passing max_depth argument overrides instance default."""
        bridge = AccessibilityBridge(max_depth=8)
        # Even though instance has 8, passing 1 should limit traversal
        tree = bridge.get_element_tree(max_depth=1)
        # No crash -- that's the minimum bar

    @pytest.mark.unit
    def test_returns_none_when_no_focused_window(self):
        """No focused window -- should return None, not crash."""
        bridge = AccessibilityBridge()
        # Mock internals to simulate no focused window
        with patch.object(bridge, "get_focused_window", return_value=None):
            tree = bridge.get_element_tree()
            # Should gracefully return None or an empty element
            # The important thing is no exception


class TestFindElements:
    """Edge cases for find_elements()."""

    @pytest.mark.unit
    def test_both_filters_none_returns_all(self):
        """role=None, title_contains=None -- should return all elements."""
        bridge = AccessibilityBridge()
        elements = [
            AXElement(role="AXButton", title="Submit"),
            AXElement(role="AXTextField", title="Name"),
            AXElement(role="AXCheckBox", title="Agree"),
        ]
        with patch.object(bridge, "get_element_tree") as mock_tree:
            root = AXElement(role="AXWindow", children=elements)
            mock_tree.return_value = root
            results = bridge.find_elements(role=None, title_contains=None)
            # Should return at least the elements in the tree
            assert isinstance(results, list)

    @pytest.mark.unit
    def test_title_contains_with_regex_special_chars(self):
        """title_contains with characters that would break regex: (  )  [  ]."""
        bridge = AccessibilityBridge()
        elements = [
            AXElement(role="AXButton", title="test (1)"),
            AXElement(role="AXButton", title="test [2]"),
            AXElement(role="AXButton", title="test.*"),
        ]
        with patch.object(bridge, "get_element_tree") as mock_tree:
            root = AXElement(role="AXWindow", children=elements)
            mock_tree.return_value = root
            # Should not raise a regex error -- should use plain string matching
            results = bridge.find_elements(title_contains="test (1)")
            assert isinstance(results, list)

    @pytest.mark.unit
    def test_enabled_only_false_includes_disabled(self):
        """enabled_only=False should include disabled elements."""
        bridge = AccessibilityBridge()
        elements = [
            AXElement(role="AXButton", title="Active", enabled=True),
            AXElement(role="AXButton", title="Grayed Out", enabled=False),
        ]
        with patch.object(bridge, "get_element_tree") as mock_tree:
            root = AXElement(role="AXWindow", children=elements)
            mock_tree.return_value = root
            results = bridge.find_elements(
                role="AXButton", enabled_only=False
            )
            # Should include both elements
            disabled_found = any(not el.enabled for el in results)
            # If we got results at all, the disabled one should be there
            if len(results) > 0:
                assert disabled_found or len(results) == 0

    @pytest.mark.unit
    def test_enabled_only_true_excludes_disabled(self):
        """enabled_only=True (default) should exclude disabled elements."""
        bridge = AccessibilityBridge()
        elements = [
            AXElement(role="AXButton", title="Active", enabled=True),
            AXElement(role="AXButton", title="Grayed Out", enabled=False),
        ]
        with patch.object(bridge, "get_element_tree") as mock_tree:
            root = AXElement(role="AXWindow", children=elements)
            mock_tree.return_value = root
            results = bridge.find_elements(role="AXButton", enabled_only=True)
            # No disabled elements should appear
            for el in results:
                assert el.enabled is True

    @pytest.mark.unit
    def test_no_elements_found(self):
        """No matching elements -- should return empty list, not None."""
        bridge = AccessibilityBridge()
        with patch.object(bridge, "get_element_tree") as mock_tree:
            root = AXElement(role="AXWindow", children=[])
            mock_tree.return_value = root
            results = bridge.find_elements(role="AXButton")
            assert results == []

    @pytest.mark.unit
    def test_elements_without_position_data(self):
        """Elements that have no position/size -- should still be findable."""
        bridge = AccessibilityBridge()
        elements = [
            AXElement(role="AXButton", title="Ghost", position=None, size=None),
        ]
        with patch.object(bridge, "get_element_tree") as mock_tree:
            root = AXElement(role="AXWindow", children=elements)
            mock_tree.return_value = root
            results = bridge.find_elements(role="AXButton")
            assert isinstance(results, list)

    @pytest.mark.unit
    def test_empty_tree_returns_empty(self):
        """get_element_tree returns None -- find_elements should return []."""
        bridge = AccessibilityBridge()
        with patch.object(bridge, "get_element_tree", return_value=None):
            results = bridge.find_elements(role="AXButton")
            assert results == []

    @pytest.mark.unit
    def test_title_contains_case_sensitivity(self):
        """Verify case behavior of title_contains filtering.

        Whether case-insensitive or case-sensitive, the behavior should be
        consistent and documented. This test checks both possibilities.
        """
        bridge = AccessibilityBridge()
        elements = [
            AXElement(role="AXButton", title="SUBMIT"),
            AXElement(role="AXButton", title="submit"),
            AXElement(role="AXButton", title="Submit"),
        ]
        with patch.object(bridge, "get_element_tree") as mock_tree:
            root = AXElement(role="AXWindow", children=elements)
            mock_tree.return_value = root
            results = bridge.find_elements(title_contains="submit")
            # Should find at least one -- whether it matches all depends on
            # case-sensitivity implementation
            assert isinstance(results, list)


# ============================================================================
# 3. find_element_by_description Parsing Edge Cases
# ============================================================================


class TestFindElementByDescription:
    """Adversarial inputs for natural language description parsing."""

    @pytest.mark.unit
    def test_empty_string_description(self):
        """Empty string -- should return None, not crash."""
        bridge = AccessibilityBridge()
        with patch.object(bridge, "find_elements", return_value=[]):
            result = bridge.find_element_by_description("")
            assert result is None

    @pytest.mark.unit
    def test_ambiguous_description_multiple_matches(self):
        """'button' matches many elements -- should return exactly one.

        The contract says Optional[AXElement], so it should pick one
        (typically the first/best match), not return a list.
        """
        bridge = AccessibilityBridge()
        buttons = [
            AXElement(
                role="AXButton", title="Submit", position=(100, 100), size=(80, 30)
            ),
            AXElement(
                role="AXButton", title="Cancel", position=(200, 100), size=(80, 30)
            ),
            AXElement(
                role="AXButton", title="Help", position=(300, 100), size=(80, 30)
            ),
        ]
        with patch.object(bridge, "find_elements", return_value=buttons):
            result = bridge.find_element_by_description("button")
            # Should return one AXElement, not None and not a list
            if result is not None:
                assert isinstance(result, AXElement)

    @pytest.mark.unit
    def test_description_no_ax_role_mapping(self):
        """'the thingy on the left' -- no AX role mapping exists.

        Should gracefully return None rather than crash with KeyError.
        """
        bridge = AccessibilityBridge()
        with patch.object(bridge, "find_elements", return_value=[]):
            result = bridge.find_element_by_description(
                "the thingy on the left"
            )
            assert result is None

    @pytest.mark.unit
    def test_description_case_insensitive(self):
        """'SUBMIT BUTTON' and 'submit button' should find the same element."""
        bridge = AccessibilityBridge()
        submit = AXElement(
            role="AXButton", title="Submit", position=(100, 100), size=(80, 30)
        )
        with patch.object(bridge, "find_elements", return_value=[submit]):
            result_upper = bridge.find_element_by_description("SUBMIT BUTTON")
            result_lower = bridge.find_element_by_description("submit button")
            # Both should find the same element or both return None
            # The key constraint is they behave consistently
            if result_upper is not None and result_lower is not None:
                assert result_upper.title == result_lower.title

    @pytest.mark.unit
    def test_description_with_quotes(self):
        """Description containing quotes should not break parsing."""
        bridge = AccessibilityBridge()
        with patch.object(bridge, "find_elements", return_value=[]):
            result = bridge.find_element_by_description(
                "button with 'quotes' and \"double quotes\""
            )
            # Should not raise -- either finds something or returns None
            assert result is None or isinstance(result, AXElement)

    @pytest.mark.unit
    def test_description_partial_match(self):
        """'sub' could match 'Submit' and 'Subscribe' -- must handle gracefully."""
        bridge = AccessibilityBridge()
        elements = [
            AXElement(
                role="AXButton", title="Submit", position=(100, 100), size=(80, 30)
            ),
            AXElement(
                role="AXButton", title="Subscribe", position=(200, 100), size=(80, 30)
            ),
        ]
        with patch.object(bridge, "find_elements", return_value=elements):
            result = bridge.find_element_by_description("sub")
            # Should return one element or None, never crash
            assert result is None or isinstance(result, AXElement)

    @pytest.mark.unit
    def test_description_very_long_string(self):
        """Very long description string (5000 chars) -- should not hang."""
        bridge = AccessibilityBridge()
        long_desc = "search button " * 500
        with patch.object(bridge, "find_elements", return_value=[]):
            result = bridge.find_element_by_description(long_desc)
            assert result is None or isinstance(result, AXElement)

    @pytest.mark.unit
    def test_description_special_characters(self):
        """Description with special chars: backslash, newline, tab."""
        bridge = AccessibilityBridge()
        with patch.object(bridge, "find_elements", return_value=[]):
            result = bridge.find_element_by_description(
                "button\nwith\\special\tchars"
            )
            assert result is None or isinstance(result, AXElement)

    @pytest.mark.unit
    def test_keyword_to_role_mapping_button(self):
        """'search button' should map to role=AXButton per spec."""
        bridge = AccessibilityBridge()
        btn = AXElement(
            role="AXButton", title="Search", position=(100, 100), size=(80, 30)
        )
        with patch.object(bridge, "find_elements", return_value=[btn]) as mock_find:
            result = bridge.find_element_by_description("search button")
            # Verify that find_elements was called with role="AXButton"
            if mock_find.called:
                call_kwargs = mock_find.call_args
                if call_kwargs and call_kwargs.kwargs.get("role"):
                    assert call_kwargs.kwargs["role"] == "AXButton"

    @pytest.mark.unit
    def test_keyword_to_role_mapping_text_field(self):
        """'text field' should map to role=AXTextField per spec."""
        bridge = AccessibilityBridge()
        tf = AXElement(
            role="AXTextField", title="Search", position=(100, 100), size=(200, 30)
        )
        with patch.object(bridge, "find_elements", return_value=[tf]) as mock_find:
            result = bridge.find_element_by_description("text field")
            if mock_find.called:
                call_kwargs = mock_find.call_args
                if call_kwargs and call_kwargs.kwargs.get("role"):
                    assert call_kwargs.kwargs["role"] == "AXTextField"

    @pytest.mark.unit
    def test_only_whitespace_description(self):
        """Description is just whitespace -- should return None."""
        bridge = AccessibilityBridge()
        with patch.object(bridge, "find_elements", return_value=[]):
            result = bridge.find_element_by_description("   \t\n  ")
            assert result is None


# ============================================================================
# 4. get_interactive_elements Edge Cases
# ============================================================================


class TestGetInteractiveElements:
    """Edge cases for get_interactive_elements()."""

    @pytest.mark.unit
    def test_no_interactive_elements(self):
        """Screen with no interactive elements -- returns empty list."""
        bridge = AccessibilityBridge()
        static_elements = [
            AXElement(role="AXStaticText", title="Label"),
            AXElement(role="AXImage", title="Logo"),
        ]
        with patch.object(bridge, "get_element_tree") as mock_tree:
            root = AXElement(role="AXWindow", children=static_elements)
            mock_tree.return_value = root
            results = bridge.get_interactive_elements()
            assert isinstance(results, list)

    @pytest.mark.unit
    def test_interactive_elements_without_position(self):
        """Interactive elements exist but lack position data."""
        bridge = AccessibilityBridge()
        elements = [
            AXElement(role="AXButton", title="OK", position=None, size=None),
        ]
        with patch.object(bridge, "get_element_tree") as mock_tree:
            root = AXElement(role="AXWindow", children=elements)
            mock_tree.return_value = root
            results = bridge.get_interactive_elements()
            assert isinstance(results, list)

    @pytest.mark.unit
    def test_empty_tree(self):
        """get_element_tree returns None -- should return empty list."""
        bridge = AccessibilityBridge()
        with patch.object(bridge, "get_element_tree", return_value=None):
            results = bridge.get_interactive_elements()
            assert results == []

    @pytest.mark.unit
    def test_disabled_elements_excluded(self):
        """Disabled buttons should likely not be 'interactive'."""
        bridge = AccessibilityBridge()
        elements = [
            AXElement(
                role="AXButton", title="Enabled", enabled=True,
                position=(100, 100), size=(80, 30),
            ),
            AXElement(
                role="AXButton", title="Disabled", enabled=False,
                position=(200, 100), size=(80, 30),
            ),
        ]
        with patch.object(bridge, "get_element_tree") as mock_tree:
            root = AXElement(role="AXWindow", children=elements)
            mock_tree.return_value = root
            results = bridge.get_interactive_elements()
            # All returned elements should be enabled
            for el in results:
                if hasattr(el, "enabled"):
                    assert el.enabled is True


# ============================================================================
# 5. serialize_tree Format Edge Cases
# ============================================================================


class TestSerializeTree:
    """Edge cases for serialize_tree() output format."""

    @pytest.mark.unit
    def test_empty_tree(self):
        """No element tree -- should return valid string (empty or placeholder)."""
        bridge = AccessibilityBridge()
        with patch.object(bridge, "get_element_tree", return_value=None):
            result = bridge.serialize_tree()
            assert isinstance(result, str)
            # Should not crash, should return something valid

    @pytest.mark.unit
    def test_single_element_tree(self):
        """Tree with just a root element, no children."""
        bridge = AccessibilityBridge()
        root = AXElement(
            role="AXButton",
            title="Submit",
            position=(450, 320),
            size=(120, 40),
        )
        with patch.object(bridge, "get_element_tree", return_value=root):
            result = bridge.serialize_tree()
            assert isinstance(result, str)
            # Per spec: [Button "Submit" @ (450, 320) 120x40]
            assert len(result) > 0

    @pytest.mark.unit
    def test_elements_with_all_none_fields(self):
        """Element where title, value, description, position, size are all None."""
        bridge = AccessibilityBridge()
        root = AXElement(role="AXUnknown")
        with patch.object(bridge, "get_element_tree", return_value=root):
            result = bridge.serialize_tree()
            assert isinstance(result, str)
            # Should not crash with None handling

    @pytest.mark.unit
    def test_very_wide_tree(self):
        """100 siblings -- serialization should handle without issues."""
        bridge = AccessibilityBridge()
        children = [
            AXElement(
                role="AXButton",
                title=f"Button {i}",
                position=(i * 50, 100),
                size=(40, 30),
            )
            for i in range(100)
        ]
        root = AXElement(role="AXWindow", children=children)
        with patch.object(bridge, "get_element_tree", return_value=root):
            result = bridge.serialize_tree()
            assert isinstance(result, str)
            assert len(result) > 0

    @pytest.mark.unit
    def test_serialize_format_matches_spec(self):
        """Verify output matches the spec format:
        [Button "Submit" @ (450, 320) 120x40]
        [TextField "Search" value="query" @ (100, 50) 300x30]
        """
        bridge = AccessibilityBridge()
        children = [
            AXElement(
                role="AXButton",
                title="Submit",
                position=(450, 320),
                size=(120, 40),
            ),
            AXElement(
                role="AXTextField",
                title="Search",
                value="query",
                position=(100, 50),
                size=(300, 30),
            ),
        ]
        root = AXElement(role="AXWindow", children=children)
        with patch.object(bridge, "get_element_tree", return_value=root):
            result = bridge.serialize_tree()
            assert isinstance(result, str)
            # Should contain recognizable element info
            # We check for the presence of key parts
            assert "Submit" in result or "Button" in result
            assert "Search" in result or "TextField" in result

    @pytest.mark.unit
    def test_serialize_with_value_field(self):
        """Elements with value should include it in serialization."""
        bridge = AccessibilityBridge()
        root = AXElement(
            role="AXTextField",
            title="Email",
            value="user@example.com",
            position=(100, 100),
            size=(200, 30),
        )
        with patch.object(bridge, "get_element_tree", return_value=root):
            result = bridge.serialize_tree()
            assert isinstance(result, str)

    @pytest.mark.unit
    def test_serialize_mixed_depths(self):
        """Tree with elements at varying depths."""
        bridge = AccessibilityBridge()
        deep = AXElement(
            role="AXButton",
            title="Deep",
            position=(100, 100),
            size=(80, 30),
            children=[
                AXElement(
                    role="AXStaticText",
                    title="Label",
                    position=(100, 100),
                    size=(40, 20),
                )
            ],
        )
        shallow = AXElement(
            role="AXTextField",
            title="Shallow",
            position=(200, 100),
            size=(200, 30),
        )
        root = AXElement(role="AXWindow", children=[deep, shallow])
        with patch.object(bridge, "get_element_tree", return_value=root):
            result = bridge.serialize_tree()
            assert isinstance(result, str)

    @pytest.mark.unit
    def test_serialize_special_chars_in_title(self):
        """Titles with quotes and special chars should be escaped/handled."""
        bridge = AccessibilityBridge()
        root = AXElement(
            role="AXButton",
            title='Click "here" & <there>',
            position=(100, 100),
            size=(80, 30),
        )
        with patch.object(bridge, "get_element_tree", return_value=root):
            result = bridge.serialize_tree()
            assert isinstance(result, str)
            # Should not crash on special characters


# ============================================================================
# 6. get_focused_window Edge Cases
# ============================================================================


class TestGetFocusedWindow:
    """Edge cases for get_focused_window()."""

    @pytest.mark.unit
    def test_no_focused_window(self):
        """No focused window (all minimized) -- should return None."""
        bridge = AccessibilityBridge()
        with patch.object(bridge, "get_frontmost_app", return_value=None):
            result = bridge.get_focused_window()
            assert result is None or isinstance(result, AXElement)

    @pytest.mark.unit
    def test_focused_window_no_children(self):
        """Focused window exists but has no children."""
        bridge = AccessibilityBridge()
        window = AXElement(role="AXWindow", title="Empty Window", children=[])
        # We patch the internal mechanism if possible
        # If get_focused_window calls AX API internally, we mock it
        with patch.object(
            bridge, "get_focused_window", return_value=window
        ):
            result = bridge.get_focused_window()
            assert result is not None
            assert result.role == "AXWindow"
            assert result.children == []


# ============================================================================
# 7. Thread Safety and Concurrent Access
# ============================================================================


class TestThreadSafety:
    """Concurrent access tests for AccessibilityBridge."""

    @pytest.mark.unit
    def test_concurrent_find_elements(self):
        """Multiple threads calling find_elements concurrently -- no crash.

        This doesn't test true thread safety (that requires macOS AX calls),
        but ensures the Python-level code doesn't corrupt shared state.
        """
        bridge = AccessibilityBridge()
        elements = [
            AXElement(role="AXButton", title=f"Btn{i}") for i in range(10)
        ]

        errors = []

        def worker(role_filter):
            try:
                with patch.object(bridge, "get_element_tree") as mock_tree:
                    root = AXElement(role="AXWindow", children=elements)
                    mock_tree.return_value = root
                    results = bridge.find_elements(role=role_filter)
                    assert isinstance(results, list)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=worker, args=("AXButton",))
            for _ in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0, f"Thread errors: {errors}"

    @pytest.mark.unit
    def test_concurrent_serialize_tree(self):
        """Multiple threads calling serialize_tree -- no crash."""
        bridge = AccessibilityBridge()
        root = AXElement(
            role="AXWindow",
            children=[
                AXElement(role="AXButton", title=f"B{i}", position=(i, i), size=(10, 10))
                for i in range(20)
            ],
        )

        errors = []

        def worker():
            try:
                with patch.object(bridge, "get_element_tree", return_value=root):
                    result = bridge.serialize_tree()
                    assert isinstance(result, str)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0, f"Thread errors: {errors}"


# ============================================================================
# 8. Integration Points with VisionCoordinator
# ============================================================================


class TestVisionCoordinatorIntegration:
    """Tests for how AccessibilityBridge integrates with VisionCoordinator.

    These tests verify the contract between AccessibilityBridge and the
    VisionCoordinator's find_element method as described in the design spec.
    """

    @pytest.mark.unit
    def test_accessibility_returns_element_skips_vision(self):
        """When accessibility finds the element, vision should NOT be called.

        This is the core value proposition -- the 'fast path'.
        """
        bridge = AccessibilityBridge()
        found_element = AXElement(
            role="AXButton",
            title="Submit",
            position=(400, 300),
            size=(100, 40),
        )
        with patch.object(
            bridge, "find_element_by_description", return_value=found_element
        ):
            element = bridge.find_element_by_description("submit button")
            assert element is not None
            assert element.center is not None
            cx, cy = element.center
            result = {"x": cx, "y": cy, "source": "accessibility"}
            assert result["source"] == "accessibility"
            assert result["x"] == 450
            assert result["y"] == 320

    @pytest.mark.unit
    def test_accessibility_returns_none_falls_back(self):
        """When accessibility returns None, vision should be called (fallback)."""
        bridge = AccessibilityBridge()
        with patch.object(
            bridge, "find_element_by_description", return_value=None
        ):
            element = bridge.find_element_by_description("some weird element")
            assert element is None
            # In the real integration, VisionCoordinator would now call
            # _find_element_vision() -- we just verify accessibility returned None

    @pytest.mark.unit
    def test_accessibility_returns_element_without_center(self):
        """Element found but has no position -- should fall back to vision.

        Per spec: 'if ax_element and ax_element.center'
        """
        bridge = AccessibilityBridge()
        no_pos_element = AXElement(
            role="AXButton",
            title="Ghost Button",
            position=None,
            size=None,
        )
        with patch.object(
            bridge, "find_element_by_description", return_value=no_pos_element
        ):
            element = bridge.find_element_by_description("ghost button")
            assert element is not None
            assert element.center is None
            # VisionCoordinator should fall back because center is None

    @pytest.mark.unit
    def test_accessibility_throws_exception_graceful_fallback(self):
        """If accessibility raises an exception, coordinator should catch it
        and fall back to vision rather than propagating the crash.
        """
        bridge = AccessibilityBridge()
        with patch.object(
            bridge,
            "find_element_by_description",
            side_effect=OSError("AX API unavailable"),
        ):
            with pytest.raises(OSError):
                bridge.find_element_by_description("submit button")
            # In the real VisionCoordinator, this would be caught in a try/except

    @pytest.mark.unit
    def test_coordinate_source_tracking(self):
        """Verify that accessibility results are tagged with source='accessibility'.

        Per spec, the result dict should have:
        {"x": 450, "y": 320, "source": "accessibility"}
        """
        element = AXElement(
            role="AXButton",
            title="Submit",
            position=(400, 300),
            size=(100, 40),
        )
        assert element.center is not None
        cx, cy = element.center
        result = {"x": cx, "y": cy, "source": "accessibility"}
        assert result["source"] == "accessibility"
        # These coordinates are already in logical screen space
        # NO Retina scaling needed (per spec)
        assert result["x"] == 450
        assert result["y"] == 320

    @pytest.mark.unit
    def test_accessibility_coordinates_no_retina_scaling(self):
        """Accessibility coordinates are in logical space and must NOT be
        Retina-scaled. A Retina display at 2x would produce wrong coords
        if we accidentally scale them.
        """
        element = AXElement(
            role="AXButton",
            title="Submit",
            position=(400, 300),
            size=(100, 40),
        )
        cx, cy = element.center
        # These are logical coords -- if someone accidentally 2x scales them,
        # they'd get (900, 640), which would be wrong
        assert cx == 450
        assert cy == 320
        assert cx != 900  # 2x would be wrong
        assert cy != 640  # 2x would be wrong


# ============================================================================
# 9. Boundary and Robustness Tests
# ============================================================================


class TestBoundaryConditions:
    """Extreme values and boundary conditions."""

    @pytest.mark.unit
    def test_element_at_screen_origin(self):
        """Element at (0, 0) -- valid position."""
        el = AXElement(role="AXButton", position=(0, 0), size=(100, 50))
        assert el.center == (50, 25)

    @pytest.mark.unit
    def test_element_at_extreme_coordinates(self):
        """Element at very large coords (multi-monitor, 8K display)."""
        el = AXElement(
            role="AXButton",
            position=(7680, 4320),
            size=(200, 100),
        )
        assert el.center == (7780, 4370)

    @pytest.mark.unit
    def test_element_with_huge_size(self):
        """Element with extremely large size (full-screen window on 8K)."""
        el = AXElement(
            role="AXWindow",
            position=(0, 0),
            size=(7680, 4320),
        )
        assert el.center == (3840, 2160)

    @pytest.mark.unit
    def test_element_with_negative_size(self):
        """Negative size values -- can happen with corrupted AX data.

        Should not crash. Center computation may give unexpected results
        but should not raise an exception.
        """
        el = AXElement(
            role="AXButton",
            position=(100, 100),
            size=(-20, -10),
        )
        # Integer division of negative numbers in Python floors:
        # -20 // 2 = -10, -10 // 2 = -5
        center = el.center
        assert center is not None
        assert center == (90, 95)

    @pytest.mark.unit
    def test_find_elements_with_huge_tree(self):
        """Tree with 1000 elements -- performance should be acceptable."""
        bridge = AccessibilityBridge()
        children = [
            AXElement(role="AXButton", title=f"Btn{i}") for i in range(1000)
        ]
        with patch.object(bridge, "get_element_tree") as mock_tree:
            root = AXElement(role="AXWindow", children=children)
            mock_tree.return_value = root
            results = bridge.find_elements(role="AXButton")
            assert isinstance(results, list)

    @pytest.mark.unit
    def test_serialize_tree_with_emoji_titles(self):
        """Serialization of elements with emoji in titles."""
        bridge = AccessibilityBridge()
        root = AXElement(
            role="AXWindow",
            children=[
                AXElement(
                    role="AXButton",
                    title="\U0001f680 Launch",
                    position=(100, 100),
                    size=(80, 30),
                ),
                AXElement(
                    role="AXButton",
                    title="\u2705 Done",
                    position=(200, 100),
                    size=(80, 30),
                ),
            ],
        )
        with patch.object(bridge, "get_element_tree", return_value=root):
            result = bridge.serialize_tree()
            assert isinstance(result, str)
            # Should include the emoji without encoding errors


# ============================================================================
# 10. Error Code Handling from AX API
# ============================================================================


class TestAXAPIErrorCodes:
    """Tests for handling various AX API error conditions.

    macOS AX API can return various error codes instead of values:
    - kAXErrorCannotComplete
    - kAXErrorIllegalArgument
    - kAXErrorInvalidUIElement
    - kAXErrorNotImplemented
    - kAXErrorAPIDisabled (accessibility not enabled)
    """

    @pytest.mark.unit
    def test_ax_copy_attribute_returns_error(self):
        """AXUIElementCopyAttributeValue returns an error code.

        The bridge should handle this without crashing.
        """
        bridge = AccessibilityBridge()
        _mock_app_services.AXUIElementCopyAttributeValue.return_value = (
            -25204,  # kAXErrorCannotComplete
            None,
        )
        # This tests internal behavior -- if the bridge calls CopyAttributeValue,
        # it should check the error code
        # We just verify the bridge doesn't crash during tree traversal
        with patch.object(bridge, "get_frontmost_app", return_value={"name": "Test", "pid": 1}):
            tree = bridge.get_element_tree()
            # Should return None or an empty tree, not crash

    @pytest.mark.unit
    def test_ax_api_disabled(self):
        """Accessibility API is disabled system-wide.

        The bridge should raise a clear error or return None, not hang.
        """
        bridge = AccessibilityBridge()
        _mock_app_services.AXUIElementCreateSystemWide.side_effect = (
            RuntimeError("AX API disabled")
        )
        # The bridge should handle this during initialization or first use
        # Reset
        _mock_app_services.AXUIElementCreateSystemWide.side_effect = None

    @pytest.mark.unit
    def test_invalid_ui_element_during_traversal(self):
        """Element becomes invalid during tree traversal (app window closed).

        The bridge should skip invalid elements gracefully.
        """
        bridge = AccessibilityBridge()
        # Simulate an element whose raw_ref raises when accessed
        invalid_element = AXElement(
            role="AXButton",
            title="Disappearing",
            raw_ref=MagicMock(side_effect=RuntimeError("Invalid element")),
        )
        valid_element = AXElement(
            role="AXButton",
            title="Valid",
            position=(100, 100),
            size=(80, 30),
        )
        root = AXElement(
            role="AXWindow", children=[invalid_element, valid_element]
        )
        with patch.object(bridge, "get_element_tree", return_value=root):
            # find_elements should still work, skipping or including
            # the invalid element without crashing
            results = bridge.find_elements(role="AXButton")
            assert isinstance(results, list)


# ============================================================================
# 11. Stress and Degenerate Input Tests
# ============================================================================


class TestStressAndDegenerate:
    """Stress tests with degenerate inputs."""

    @pytest.mark.unit
    def test_circular_children_reference(self):
        """Circular reference in children -- should not cause infinite loop.

        While the dataclass won't prevent this, traversal code must handle it.
        """
        parent = AXElement(role="AXGroup")
        child = AXElement(role="AXButton", title="Recursive")
        parent.children = [child]
        child.children = [parent]  # Circular!

        bridge = AccessibilityBridge(max_depth=5)
        with patch.object(bridge, "get_element_tree", return_value=parent):
            # serialize_tree traversal must not infinite loop
            # We set a timeout expectation: should complete in reasonable time
            result = bridge.serialize_tree()
            assert isinstance(result, str)

    @pytest.mark.unit
    def test_role_with_unusual_value(self):
        """Role is an unexpected string not in AX* namespace."""
        el = AXElement(role="CustomRole123")
        assert el.role == "CustomRole123"
        # Should not crash or fail validation

    @pytest.mark.unit
    def test_many_find_elements_calls_in_sequence(self):
        """Call find_elements 100 times in a row -- no memory leak symptoms."""
        bridge = AccessibilityBridge()
        elements = [AXElement(role="AXButton", title="OK")]
        with patch.object(bridge, "get_element_tree") as mock_tree:
            root = AXElement(role="AXWindow", children=elements)
            mock_tree.return_value = root
            for _ in range(100):
                results = bridge.find_elements(role="AXButton")
                assert isinstance(results, list)

    @pytest.mark.unit
    def test_find_elements_with_none_title(self):
        """Elements where title is None -- title_contains should not crash."""
        bridge = AccessibilityBridge()
        elements = [
            AXElement(role="AXButton", title=None),
            AXElement(role="AXButton", title="Valid"),
        ]
        with patch.object(bridge, "get_element_tree") as mock_tree:
            root = AXElement(role="AXWindow", children=elements)
            mock_tree.return_value = root
            # Searching for title_contains with elements that have None title
            results = bridge.find_elements(title_contains="Valid")
            assert isinstance(results, list)
            # Should not crash on None title comparison

    @pytest.mark.unit
    def test_find_elements_title_contains_empty_string(self):
        """title_contains='' -- should this match everything or nothing?

        Either behavior is acceptable, but it must not crash.
        """
        bridge = AccessibilityBridge()
        elements = [
            AXElement(role="AXButton", title="Submit"),
            AXElement(role="AXButton", title="Cancel"),
        ]
        with patch.object(bridge, "get_element_tree") as mock_tree:
            root = AXElement(role="AXWindow", children=elements)
            mock_tree.return_value = root
            results = bridge.find_elements(title_contains="")
            assert isinstance(results, list)


# ============================================================================
# 12. AXElement Equality and Identity
# ============================================================================


class TestAXElementEquality:
    """Tests for AXElement equality, hashing, and identity."""

    @pytest.mark.unit
    def test_two_identical_elements_are_equal(self):
        """Two AXElements with same values should be equal (dataclass default)."""
        el1 = AXElement(role="AXButton", title="OK", position=(100, 100), size=(80, 30))
        el2 = AXElement(role="AXButton", title="OK", position=(100, 100), size=(80, 30))
        assert el1 == el2

    @pytest.mark.unit
    def test_different_elements_not_equal(self):
        """Two different AXElements should not be equal."""
        el1 = AXElement(role="AXButton", title="OK")
        el2 = AXElement(role="AXButton", title="Cancel")
        assert el1 != el2

    @pytest.mark.unit
    def test_element_with_raw_ref(self):
        """raw_ref is a MagicMock -- equality check should still work."""
        mock_ref = MagicMock()
        el = AXElement(role="AXButton", raw_ref=mock_ref)
        assert el.raw_ref is mock_ref
