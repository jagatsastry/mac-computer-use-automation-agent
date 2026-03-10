"""Unit tests for the Accessibility Bridge.

All tests are fully mocked — no real macOS AX API calls.
"""

import sys
from typing import List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module-level mocks for pyobjc frameworks.
# Must be set up BEFORE importing the accessibility module so that
# sys.modules lookups return mocks instead of real frameworks.
#
# We use setdefault so that if another test file (e.g. the adversarial suite)
# has already registered its own mocks, we reuse those.  All test code then
# references the objects actually in sys.modules via _shared_appkit /
# _shared_app_services so there is no object identity mismatch.
# ---------------------------------------------------------------------------
def _ensure_mock_module(name: str) -> MagicMock:
    module = sys.modules.get(name)
    if isinstance(module, MagicMock):
        return module
    mocked = MagicMock(name=name)
    sys.modules[name] = mocked
    return mocked


_ensure_mock_module("AppKit")
_ensure_mock_module("ApplicationServices")

_shared_appkit = sys.modules["AppKit"]
_shared_app_services = sys.modules["ApplicationServices"]

from automation_agent.perception.accessibility import (  # noqa: E402
    AXElement,
    AccessibilityBridge,
    _INTERACTIVE_ROLES,
    _NL_ROLE_MAP,
    _ax_element_from_ref,
    _get_ax_attr,
)


@pytest.fixture(autouse=True)
def _reset_shared_mocks():
    """Reset shared module-level mocks before and after every test.

    This prevents test-order-dependent state leaks when this file runs
    alongside the adversarial test suite (both share the same
    sys.modules["AppKit"] and sys.modules["ApplicationServices"]).
    """
    # Reset before test
    _shared_appkit.reset_mock()
    _shared_app_services.reset_mock()
    yield
    # Reset after test
    _shared_appkit.reset_mock()
    _shared_app_services.reset_mock()


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


def _make_element(
    role: str = "AXButton",
    title: Optional[str] = "OK",
    value: Optional[str] = None,
    description: Optional[str] = None,
    position: Optional[Tuple[int, int]] = (100, 200),
    size: Optional[Tuple[int, int]] = (80, 30),
    enabled: bool = True,
    focused: bool = False,
    children: Optional[List[AXElement]] = None,
) -> AXElement:
    """Create an AXElement with sensible defaults for testing."""
    return AXElement(
        role=role,
        title=title,
        value=value,
        description=description,
        position=position,
        size=size,
        enabled=enabled,
        focused=focused,
        children=children or [],
    )


def _make_tree() -> AXElement:
    """Build a small sample element tree for testing."""
    return _make_element(
        role="AXWindow",
        title="MyApp",
        position=(0, 25),
        size=(1440, 875),
        children=[
            _make_element(
                role="AXButton",
                title="Submit",
                position=(450, 320),
                size=(120, 40),
            ),
            _make_element(
                role="AXTextField",
                title="Search",
                value="query",
                position=(100, 50),
                size=(300, 30),
            ),
            _make_element(
                role="AXButton",
                title="Cancel",
                position=(600, 320),
                size=(120, 40),
                enabled=False,
            ),
            _make_element(
                role="AXStaticText",
                title="Status: OK",
                position=(100, 400),
                size=(200, 20),
            ),
            _make_element(
                role="AXCheckBox",
                title="Remember me",
                position=(100, 450),
                size=(150, 20),
            ),
        ],
    )


@pytest.fixture
def bridge():
    """Create an AccessibilityBridge with default settings."""
    return AccessibilityBridge(max_depth=8)


@pytest.fixture
def sample_tree():
    """Provide a pre-built sample element tree."""
    return _make_tree()


# ---------------------------------------------------------------------------
# Test AXElement.center property
# ---------------------------------------------------------------------------


class TestAXElementCenter:
    """Tests for AXElement.center property computation."""

    @pytest.mark.unit
    def test_center_with_position_and_size(self):
        elem = _make_element(position=(100, 200), size=(80, 30))
        assert elem.center == (140, 215)

    @pytest.mark.unit
    def test_center_at_origin(self):
        elem = _make_element(position=(0, 0), size=(100, 100))
        assert elem.center == (50, 50)

    @pytest.mark.unit
    def test_center_odd_size(self):
        elem = _make_element(position=(10, 20), size=(51, 31))
        # 10 + 51//2 = 10 + 25 = 35, 20 + 31//2 = 20 + 15 = 35
        assert elem.center == (35, 35)

    @pytest.mark.unit
    def test_center_none_when_no_position(self):
        elem = _make_element(position=None, size=(80, 30))
        assert elem.center is None

    @pytest.mark.unit
    def test_center_none_when_no_size(self):
        elem = _make_element(position=(100, 200), size=None)
        assert elem.center is None

    @pytest.mark.unit
    def test_center_none_when_both_none(self):
        elem = _make_element(position=None, size=None)
        assert elem.center is None

    @pytest.mark.unit
    def test_center_large_element(self):
        elem = _make_element(position=(0, 0), size=(2560, 1440))
        assert elem.center == (1280, 720)

    @pytest.mark.unit
    def test_center_zero_size(self):
        elem = _make_element(position=(100, 200), size=(0, 0))
        assert elem.center == (100, 200)


# ---------------------------------------------------------------------------
# Test find_elements with various role/title filters
# ---------------------------------------------------------------------------


class TestFindElements:
    """Tests for AccessibilityBridge.find_elements()."""

    @pytest.mark.unit
    def test_find_by_role(self, bridge, sample_tree):
        with patch.object(bridge, "get_element_tree", return_value=sample_tree):
            results = bridge.find_elements(role="AXButton")
        # 2 buttons: Submit (enabled) and Cancel (disabled)
        # enabled_only defaults to True, so Cancel is excluded
        assert len(results) == 1
        assert results[0].title == "Submit"

    @pytest.mark.unit
    def test_find_by_role_disabled_included(self, bridge, sample_tree):
        with patch.object(bridge, "get_element_tree", return_value=sample_tree):
            results = bridge.find_elements(role="AXButton", enabled_only=False)
        assert len(results) == 2
        titles = {r.title for r in results}
        assert titles == {"Submit", "Cancel"}

    @pytest.mark.unit
    def test_find_by_title_contains(self, bridge, sample_tree):
        with patch.object(bridge, "get_element_tree", return_value=sample_tree):
            results = bridge.find_elements(title_contains="search")
        assert len(results) == 1
        assert results[0].role == "AXTextField"

    @pytest.mark.unit
    def test_find_by_title_case_insensitive(self, bridge, sample_tree):
        with patch.object(bridge, "get_element_tree", return_value=sample_tree):
            results = bridge.find_elements(title_contains="SUBMIT")
        assert len(results) == 1
        assert results[0].title == "Submit"

    @pytest.mark.unit
    def test_find_by_role_and_title(self, bridge, sample_tree):
        with patch.object(bridge, "get_element_tree", return_value=sample_tree):
            results = bridge.find_elements(
                role="AXButton", title_contains="submit"
            )
        assert len(results) == 1
        assert results[0].title == "Submit"

    @pytest.mark.unit
    def test_find_no_match(self, bridge, sample_tree):
        with patch.object(bridge, "get_element_tree", return_value=sample_tree):
            results = bridge.find_elements(role="AXSlider")
        assert results == []

    @pytest.mark.unit
    def test_find_all_elements_no_filter(self, bridge, sample_tree):
        with patch.object(bridge, "get_element_tree", return_value=sample_tree):
            results = bridge.find_elements(enabled_only=False)
        # 1 window + 5 children = 6
        assert len(results) == 6

    @pytest.mark.unit
    def test_find_elements_returns_empty_when_tree_is_none(self, bridge):
        with patch.object(bridge, "get_element_tree", return_value=None):
            results = bridge.find_elements(role="AXButton")
        assert results == []

    @pytest.mark.unit
    def test_find_elements_graceful_on_exception(self, bridge):
        with patch.object(
            bridge, "get_element_tree", side_effect=RuntimeError("AX error")
        ):
            results = bridge.find_elements(role="AXButton")
        assert results == []


# ---------------------------------------------------------------------------
# Test find_element_by_description NL parsing
# ---------------------------------------------------------------------------


class TestFindElementByDescription:
    """Tests for natural language -> AX element lookup."""

    @pytest.mark.unit
    def test_search_button(self, bridge, sample_tree):
        with patch.object(bridge, "get_element_tree", return_value=sample_tree):
            result = bridge.find_element_by_description("search button")
        # Should not find — no button with title containing "search".
        # "Search" is a TextField, not a Button.
        assert result is None

    @pytest.mark.unit
    def test_submit_button(self, bridge, sample_tree):
        with patch.object(bridge, "get_element_tree", return_value=sample_tree):
            result = bridge.find_element_by_description("submit button")
        assert result is not None
        assert result.role == "AXButton"
        assert result.title == "Submit"

    @pytest.mark.unit
    def test_text_field(self, bridge, sample_tree):
        with patch.object(bridge, "get_element_tree", return_value=sample_tree):
            result = bridge.find_element_by_description("search text field")
        assert result is not None
        assert result.role == "AXTextField"

    @pytest.mark.unit
    def test_checkbox(self, bridge, sample_tree):
        with patch.object(bridge, "get_element_tree", return_value=sample_tree):
            result = bridge.find_element_by_description("remember checkbox")
        assert result is not None
        assert result.role == "AXCheckBox"

    @pytest.mark.unit
    def test_no_role_keyword_uses_title_search(self, bridge, sample_tree):
        with patch.object(bridge, "get_element_tree", return_value=sample_tree):
            result = bridge.find_element_by_description("Submit")
        assert result is not None
        assert result.title == "Submit"

    @pytest.mark.unit
    def test_description_not_found(self, bridge, sample_tree):
        with patch.object(bridge, "get_element_tree", return_value=sample_tree):
            result = bridge.find_element_by_description("nonexistent widget")
        assert result is None

    @pytest.mark.unit
    def test_description_strips_filler_words(self, bridge, sample_tree):
        with patch.object(bridge, "get_element_tree", return_value=sample_tree):
            result = bridge.find_element_by_description("the submit button")
        assert result is not None
        assert result.title == "Submit"

    @pytest.mark.unit
    def test_graceful_on_exception(self, bridge):
        with patch.object(
            bridge, "get_element_tree", side_effect=RuntimeError("fail")
        ):
            result = bridge.find_element_by_description("some button")
        assert result is None


# ---------------------------------------------------------------------------
# Test get_interactive_elements
# ---------------------------------------------------------------------------


class TestGetInteractiveElements:
    """Tests for get_interactive_elements filtering."""

    @pytest.mark.unit
    def test_returns_only_interactive(self, bridge, sample_tree):
        with patch.object(bridge, "get_element_tree", return_value=sample_tree):
            results = bridge.get_interactive_elements()
        roles = {r.role for r in results}
        # Submit button (enabled), TextField, CheckBox
        # Cancel button is disabled, StaticText is not interactive, Window is not interactive
        assert roles == {"AXButton", "AXTextField", "AXCheckBox"}
        assert len(results) == 3

    @pytest.mark.unit
    def test_excludes_disabled(self, bridge, sample_tree):
        with patch.object(bridge, "get_element_tree", return_value=sample_tree):
            results = bridge.get_interactive_elements()
        titles = {r.title for r in results}
        assert "Cancel" not in titles

    @pytest.mark.unit
    def test_excludes_no_position(self, bridge):
        tree = _make_element(
            role="AXWindow",
            children=[
                _make_element(role="AXButton", title="NoPos", position=None),
                _make_element(role="AXButton", title="HasPos", position=(10, 10)),
            ],
        )
        with patch.object(bridge, "get_element_tree", return_value=tree):
            results = bridge.get_interactive_elements()
        assert len(results) == 1
        assert results[0].title == "HasPos"

    @pytest.mark.unit
    def test_empty_when_tree_is_none(self, bridge):
        with patch.object(bridge, "get_element_tree", return_value=None):
            results = bridge.get_interactive_elements()
        assert results == []

    @pytest.mark.unit
    def test_graceful_on_exception(self, bridge):
        with patch.object(
            bridge, "get_element_tree", side_effect=RuntimeError("fail")
        ):
            results = bridge.get_interactive_elements()
        assert results == []


# ---------------------------------------------------------------------------
# Test serialize_tree format
# ---------------------------------------------------------------------------


class TestSerializeTree:
    """Tests for serialize_tree text output."""

    @pytest.mark.unit
    def test_basic_serialization(self, bridge, sample_tree):
        with patch.object(bridge, "get_element_tree", return_value=sample_tree):
            output = bridge.serialize_tree()
        assert '[Window "MyApp"' in output
        assert '[Button "Submit"' in output
        assert '[TextField "Search" value="query"' in output
        assert "@ (450, 320)" in output
        assert "120x40" in output

    @pytest.mark.unit
    def test_disabled_element_shows_disabled(self, bridge, sample_tree):
        with patch.object(bridge, "get_element_tree", return_value=sample_tree):
            output = bridge.serialize_tree()
        # The Cancel button is disabled
        assert "DISABLED" in output

    @pytest.mark.unit
    def test_focused_element_shows_focused(self, bridge):
        tree = _make_element(
            role="AXWindow",
            children=[_make_element(role="AXTextField", focused=True)],
        )
        with patch.object(bridge, "get_element_tree", return_value=tree):
            output = bridge.serialize_tree()
        assert "FOCUSED" in output

    @pytest.mark.unit
    def test_indentation(self, bridge, sample_tree):
        with patch.object(bridge, "get_element_tree", return_value=sample_tree):
            output = bridge.serialize_tree()
        lines = output.split("\n")
        # First line (window) should have no indent
        assert lines[0].startswith("[")
        # Children should be indented
        assert lines[1].startswith("  [")

    @pytest.mark.unit
    def test_long_value_truncated(self, bridge):
        long_val = "a" * 100
        tree = _make_element(
            role="AXWindow",
            children=[
                _make_element(
                    role="AXTextField", title="Field", value=long_val
                ),
            ],
        )
        with patch.object(bridge, "get_element_tree", return_value=tree):
            output = bridge.serialize_tree()
        assert "..." in output
        # Should not contain the full 100-char value
        assert long_val not in output

    @pytest.mark.unit
    def test_empty_when_tree_is_none(self, bridge):
        with patch.object(bridge, "get_element_tree", return_value=None):
            output = bridge.serialize_tree()
        assert output == ""

    @pytest.mark.unit
    def test_graceful_on_exception(self, bridge):
        with patch.object(
            bridge, "get_element_tree", side_effect=RuntimeError("fail")
        ):
            output = bridge.serialize_tree()
        assert output == ""


# ---------------------------------------------------------------------------
# Test graceful handling when AX APIs fail
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    """Tests that all methods degrade gracefully when AX APIs are unavailable."""

    @pytest.mark.unit
    def test_get_frontmost_app_handles_appkit_unavailable(self, bridge):
        # Simulate AppKit completely unavailable via sys.modules
        saved = sys.modules.get("AppKit")
        try:
            sys.modules["AppKit"] = None  # type: ignore[assignment]
            result = bridge.get_frontmost_app()
            assert result is None
        finally:
            if saved is not None:
                sys.modules["AppKit"] = saved
            else:
                sys.modules.pop("AppKit", None)

    @pytest.mark.unit
    def test_get_frontmost_app_handles_none_active_app(self, bridge):
        _shared_appkit.NSWorkspace.sharedWorkspace.return_value.activeApplication.return_value = (
            None
        )
        result = bridge.get_frontmost_app()
        assert result is None
        # Reset
        _shared_appkit.NSWorkspace.sharedWorkspace.return_value.activeApplication.return_value = (
            MagicMock()
        )

    @pytest.mark.unit
    def test_get_element_tree_handles_no_frontmost_app(self, bridge):
        with patch.object(bridge, "get_frontmost_app", return_value=None):
            result = bridge.get_element_tree()
        assert result is None

    @pytest.mark.unit
    def test_get_element_tree_handles_no_pid(self, bridge):
        with patch.object(
            bridge, "get_frontmost_app", return_value={"name": "App", "pid": 0}
        ):
            result = bridge.get_element_tree()
        assert result is None

    @pytest.mark.unit
    def test_get_focused_window_handles_no_frontmost_app(self, bridge):
        with patch.object(bridge, "get_frontmost_app", return_value=None):
            result = bridge.get_focused_window()
        assert result is None

    @pytest.mark.unit
    def test_find_elements_empty_on_error(self, bridge):
        with patch.object(bridge, "get_element_tree", return_value=None):
            result = bridge.find_elements(role="AXButton")
        assert result == []

    @pytest.mark.unit
    def test_get_interactive_elements_empty_on_error(self, bridge):
        with patch.object(bridge, "get_element_tree", return_value=None):
            result = bridge.get_interactive_elements()
        assert result == []

    @pytest.mark.unit
    def test_serialize_tree_empty_on_error(self, bridge):
        with patch.object(bridge, "get_element_tree", return_value=None):
            result = bridge.serialize_tree()
        assert result == ""


# ---------------------------------------------------------------------------
# Test get_frontmost_app
# ---------------------------------------------------------------------------


class TestGetFrontmostApp:
    """Tests for get_frontmost_app()."""

    @pytest.mark.unit
    def test_returns_app_info(self, bridge):
        _shared_appkit.NSWorkspace.sharedWorkspace.return_value.activeApplication.return_value = {
            "NSApplicationName": "Safari",
            "NSApplicationProcessIdentifier": 1234,
            "NSApplicationBundleIdentifier": "com.apple.Safari",
        }
        result = bridge.get_frontmost_app()

        assert result is not None
        assert result["name"] == "Safari"
        assert result["pid"] == 1234
        assert result["bundle_id"] == "com.apple.Safari"

    @pytest.mark.unit
    def test_returns_none_on_no_active_app(self, bridge):
        _shared_appkit.NSWorkspace.sharedWorkspace.return_value.activeApplication.return_value = (
            None
        )
        result = bridge.get_frontmost_app()
        assert result is None
        # Reset
        _shared_appkit.NSWorkspace.sharedWorkspace.return_value.activeApplication.return_value = (
            MagicMock()
        )

    @pytest.mark.unit
    def test_handles_missing_keys(self, bridge):
        # Use a dict with at least one key so it's truthy
        _shared_appkit.NSWorkspace.sharedWorkspace.return_value.activeApplication.return_value = {
            "NSApplicationName": "",
        }
        result = bridge.get_frontmost_app()
        assert result is not None
        assert result["name"] == ""
        assert result["pid"] == 0
        assert result["bundle_id"] == ""


# ---------------------------------------------------------------------------
# Test _get_ax_attr helper
# ---------------------------------------------------------------------------


class TestGetAxAttr:
    """Tests for the _get_ax_attr helper function."""

    @pytest.mark.unit
    def test_returns_value_on_success(self):
        _shared_app_services.AXUIElementCopyAttributeValue.return_value = (0, "SomeValue")
        result = _get_ax_attr(MagicMock(), "AXRole")
        assert result == "SomeValue"

    @pytest.mark.unit
    def test_returns_none_on_error_code(self):
        _shared_app_services.AXUIElementCopyAttributeValue.return_value = (-25204, None)
        result = _get_ax_attr(MagicMock(), "AXRole")
        assert result is None

    @pytest.mark.unit
    def test_returns_none_on_exception(self):
        _shared_app_services.AXUIElementCopyAttributeValue.side_effect = RuntimeError(
            "AX unavailable"
        )
        try:
            result = _get_ax_attr(MagicMock(), "AXRole")
            assert result is None
        finally:
            _shared_app_services.AXUIElementCopyAttributeValue.side_effect = None


# ---------------------------------------------------------------------------
# Test _ax_element_from_ref
# ---------------------------------------------------------------------------


class TestAxElementFromRef:
    """Tests for _ax_element_from_ref helper."""

    @pytest.mark.unit
    def test_builds_element(self):
        mock_pos = MagicMock()
        mock_pos.x = 100
        mock_pos.y = 200
        mock_size = MagicMock()
        mock_size.width = 80
        mock_size.height = 30

        def fake_attr(ref, attr, _=None):
            attrs = {
                "AXRole": (0, "AXButton"),
                "AXTitle": (0, "OK"),
                "AXValue": (0, None),
                "AXDescription": (0, "OK button"),
                "AXEnabled": (0, True),
                "AXFocused": (0, False),
                "AXPosition": (0, mock_pos),
                "AXSize": (0, mock_size),
                "AXChildren": (0, []),
            }
            return attrs.get(attr, (-1, None))

        _shared_app_services.AXUIElementCopyAttributeValue.side_effect = fake_attr
        try:
            elem = _ax_element_from_ref(MagicMock(), depth=0, max_depth=2)
        finally:
            _shared_app_services.AXUIElementCopyAttributeValue.side_effect = None

        assert elem is not None
        assert elem.role == "AXButton"
        assert elem.title == "OK"
        assert elem.position == (100, 200)
        assert elem.size == (80, 30)
        assert elem.enabled is True
        assert elem.center == (140, 215)

    @pytest.mark.unit
    def test_returns_none_on_outer_exception(self):
        """If _get_ax_attr itself is broken (not just the AX call), return None."""
        with patch(
            "automation_agent.perception.accessibility._get_ax_attr",
            side_effect=RuntimeError("catastrophic failure"),
        ):
            elem = _ax_element_from_ref(MagicMock(), depth=0, max_depth=2)
        assert elem is None

    @pytest.mark.unit
    def test_respects_max_depth(self):
        """At max_depth, children are not traversed."""
        def fake_attr(ref, attr, _=None):
            attrs = {
                "AXRole": (0, "AXGroup"),
                "AXTitle": (0, None),
                "AXValue": (0, None),
                "AXDescription": (0, None),
                "AXEnabled": (0, True),
                "AXFocused": (0, False),
                "AXPosition": (-1, None),
                "AXSize": (-1, None),
                "AXChildren": (0, [MagicMock()]),  # Has children
            }
            return attrs.get(attr, (-1, None))

        _shared_app_services.AXUIElementCopyAttributeValue.side_effect = fake_attr
        try:
            # depth == max_depth, so children should not be traversed
            elem = _ax_element_from_ref(MagicMock(), depth=3, max_depth=3)
        finally:
            _shared_app_services.AXUIElementCopyAttributeValue.side_effect = None

        assert elem is not None
        assert elem.children == []


# ---------------------------------------------------------------------------
# Test NL role mapping constants
# ---------------------------------------------------------------------------


class TestNLRoleMap:
    """Tests for the natural language role mapping."""

    @pytest.mark.unit
    def test_known_mappings_exist(self):
        assert _NL_ROLE_MAP["button"] == "AXButton"
        assert _NL_ROLE_MAP["text field"] == "AXTextField"
        assert _NL_ROLE_MAP["checkbox"] == "AXCheckBox"
        assert _NL_ROLE_MAP["link"] == "AXLink"
        assert _NL_ROLE_MAP["dropdown"] == "AXPopUpButton"

    @pytest.mark.unit
    def test_interactive_roles_includes_expected(self):
        assert "AXButton" in _INTERACTIVE_ROLES
        assert "AXTextField" in _INTERACTIVE_ROLES
        assert "AXTextArea" in _INTERACTIVE_ROLES
        assert "AXCheckBox" in _INTERACTIVE_ROLES
        assert "AXLink" in _INTERACTIVE_ROLES


# ---------------------------------------------------------------------------
# Test _collect_elements helper
# ---------------------------------------------------------------------------


class TestCollectElements:
    """Tests for the _collect_elements tree flattening."""

    @pytest.mark.unit
    def test_flat_collection(self, bridge, sample_tree):
        elements = bridge._collect_elements(sample_tree)
        # 1 window + 5 children
        assert len(elements) == 6

    @pytest.mark.unit
    def test_single_node(self, bridge):
        elem = _make_element(role="AXButton", children=[])
        elements = bridge._collect_elements(elem)
        assert len(elements) == 1

    @pytest.mark.unit
    def test_nested_tree(self, bridge):
        nested = _make_element(
            role="AXGroup",
            children=[
                _make_element(
                    role="AXGroup",
                    children=[
                        _make_element(role="AXButton"),
                    ],
                ),
            ],
        )
        elements = bridge._collect_elements(nested)
        assert len(elements) == 3


# ---------------------------------------------------------------------------
# Test coordinator integration (accessibility fast-path)
# ---------------------------------------------------------------------------


class TestCoordinatorAccessibilityFastPath:
    """Test that ScreenCoordinatorImpl uses accessibility when available."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_accessibility_fast_path_returns_coords(self):
        from unittest.mock import AsyncMock

        from automation_agent.config import AgentConfig
        from automation_agent.vision.capture import ScreenCapture
        from automation_agent.vision.coordinator import ScreenCoordinatorImpl

        config = AgentConfig(
            vision_model="molmo",
            model_provider="local",
            log_dir="/tmp/test_agent_logs",
        )
        mock_capture = MagicMock(spec=ScreenCapture)
        mock_capture.target_resolution = (1024, 768)

        mock_ax = MagicMock()
        mock_ax.find_element_by_description.return_value = _make_element(
            role="AXButton",
            title="OK",
            position=(100, 200),
            size=(80, 30),
        )

        coord = ScreenCoordinatorImpl(config, capture=mock_capture, accessibility=mock_ax)
        coord._call_vision_model = AsyncMock()

        result = await coord.find_element("OK button", screenshot_b64="fake")

        assert result is not None
        assert result["x"] == 140  # center x
        assert result["y"] == 215  # center y
        assert result["source"] == "accessibility"
        # Vision model should NOT have been called
        coord._call_vision_model.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_falls_back_to_vision_when_ax_returns_none(self):
        from unittest.mock import AsyncMock

        from automation_agent.config import AgentConfig
        from automation_agent.vision.capture import ScreenCapture
        from automation_agent.vision.coordinator import ScreenCoordinatorImpl

        config = AgentConfig(
            vision_model="molmo",
            model_provider="local",
            log_dir="/tmp/test_agent_logs",
        )
        mock_capture = MagicMock(spec=ScreenCapture)
        mock_capture.target_resolution = (1024, 768)

        mock_ax = MagicMock()
        mock_ax.find_element_by_description.return_value = None

        coord = ScreenCoordinatorImpl(config, capture=mock_capture, accessibility=mock_ax)
        coord._call_vision_model = AsyncMock(return_value="FOUND: x=0.5, y=0.25")

        result = await coord.find_element("OK button", screenshot_b64="fake")

        assert result is not None
        assert result["x"] == 5
        assert result["y"] == 1
        assert result["source"] == "vision"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_falls_back_to_vision_when_ax_raises(self):
        from unittest.mock import AsyncMock

        from automation_agent.config import AgentConfig
        from automation_agent.vision.capture import ScreenCapture
        from automation_agent.vision.coordinator import ScreenCoordinatorImpl

        config = AgentConfig(
            vision_model="molmo",
            model_provider="local",
            log_dir="/tmp/test_agent_logs",
        )
        mock_capture = MagicMock(spec=ScreenCapture)
        mock_capture.target_resolution = (1024, 768)

        mock_ax = MagicMock()
        mock_ax.find_element_by_description.side_effect = RuntimeError("AX error")

        coord = ScreenCoordinatorImpl(config, capture=mock_capture, accessibility=mock_ax)
        coord._call_vision_model = AsyncMock(return_value="FOUND: x=0.5, y=0.25")

        result = await coord.find_element("OK button", screenshot_b64="fake")

        assert result is not None
        assert result["source"] == "vision"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_no_accessibility_goes_straight_to_vision(self):
        from unittest.mock import AsyncMock

        from automation_agent.config import AgentConfig
        from automation_agent.vision.capture import ScreenCapture
        from automation_agent.vision.coordinator import ScreenCoordinatorImpl

        config = AgentConfig(
            vision_model="molmo",
            model_provider="local",
            log_dir="/tmp/test_agent_logs",
        )
        mock_capture = MagicMock(spec=ScreenCapture)
        mock_capture.target_resolution = (1024, 768)

        coord = ScreenCoordinatorImpl(config, capture=mock_capture)
        coord._call_vision_model = AsyncMock(return_value="FOUND: x=0.5, y=0.5")

        result = await coord.find_element("some element", screenshot_b64="fake")

        assert result is not None
        assert result["source"] == "vision"
        coord._call_vision_model.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_ax_element_with_no_center_falls_back(self):
        """If AX finds an element but it has no center (no position), fall back."""
        from unittest.mock import AsyncMock

        from automation_agent.config import AgentConfig
        from automation_agent.vision.capture import ScreenCapture
        from automation_agent.vision.coordinator import ScreenCoordinatorImpl

        config = AgentConfig(
            vision_model="molmo",
            model_provider="local",
            log_dir="/tmp/test_agent_logs",
        )
        mock_capture = MagicMock(spec=ScreenCapture)
        mock_capture.target_resolution = (1024, 768)

        mock_ax = MagicMock()
        # Element with no position -> center is None
        mock_ax.find_element_by_description.return_value = _make_element(
            role="AXButton", position=None, size=None
        )

        coord = ScreenCoordinatorImpl(config, capture=mock_capture, accessibility=mock_ax)
        coord._call_vision_model = AsyncMock(return_value="FOUND: x=0.3, y=0.7")

        result = await coord.find_element("OK button", screenshot_b64="fake")

        assert result is not None
        assert result["source"] == "vision"
        coord._call_vision_model.assert_called_once()


# ---------------------------------------------------------------------------
# Test config use_accessibility flag
# ---------------------------------------------------------------------------


class TestConfigUseAccessibility:
    """Tests for the use_accessibility configuration field."""

    @pytest.mark.unit
    def test_default_is_false(self):
        from automation_agent.config import AgentConfig

        config = AgentConfig(
            vision_model="molmo", log_dir="/tmp/test_agent_logs"
        )
        assert config.use_accessibility is False

    @pytest.mark.unit
    def test_can_enable(self):
        from automation_agent.config import AgentConfig

        config = AgentConfig(
            vision_model="molmo",
            log_dir="/tmp/test_agent_logs",
            use_accessibility=True,
        )
        assert config.use_accessibility is True

    @pytest.mark.unit
    def test_env_var(self, monkeypatch):
        from automation_agent.config import AgentConfig

        monkeypatch.setenv("AGENT_USE_ACCESSIBILITY", "true")
        config = AgentConfig(
            vision_model="molmo", log_dir="/tmp/test_agent_logs"
        )
        assert config.use_accessibility is True


# ---------------------------------------------------------------------------
# Test get_focused_window
# ---------------------------------------------------------------------------


class TestGetFocusedWindow:
    """Tests for get_focused_window()."""

    @pytest.mark.unit
    def test_returns_element_on_success(self, bridge):
        mock_window_ref = MagicMock()

        def fake_attr(ref, attr, _=None):
            attrs = {
                "AXFocusedWindow": (0, mock_window_ref),
                "AXRole": (0, "AXWindow"),
                "AXTitle": (0, "MyWindow"),
                "AXValue": (0, None),
                "AXDescription": (0, None),
                "AXEnabled": (0, True),
                "AXFocused": (0, True),
                "AXPosition": (-1, None),
                "AXSize": (-1, None),
                "AXChildren": (0, []),
            }
            return attrs.get(attr, (-1, None))

        _shared_app_services.AXUIElementCreateApplication.return_value = MagicMock()
        _shared_app_services.AXUIElementCopyAttributeValue.side_effect = fake_attr
        try:
            with patch.object(bridge, "get_frontmost_app", return_value={
                "name": "App", "pid": 1234, "bundle_id": "com.test",
            }):
                result = bridge.get_focused_window()
        finally:
            _shared_app_services.AXUIElementCopyAttributeValue.side_effect = None

        assert result is not None
        assert result.role == "AXWindow"

    @pytest.mark.unit
    def test_returns_none_when_no_app(self, bridge):
        with patch.object(bridge, "get_frontmost_app", return_value=None):
            result = bridge.get_focused_window()
        assert result is None
