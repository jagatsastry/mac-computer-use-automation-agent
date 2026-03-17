"""Unit tests for JS injection browser state verification.

Tests cover:
- _get_browser_js_batch (Safari/Chrome dispatch, timeout, parse error, non-browser)
- get_state() populating focused_value, selected_text, page_title, page_heading
- Config flag gating (js_verification_enabled)
- Empty string -> None conversion
- get_page_title / get_page_heading standalone methods
- Tier 1 page content token matcher in verifier
- _get_browser_js helper (Safari/Chrome dispatch, timeout, error)
- get_active_element_value / get_selected_text standalone methods
"""

import json
from unittest.mock import MagicMock, patch
import subprocess as sp

import pytest

from automation_agent.actuator.applescript_actuator import AppleScriptActuator
from automation_agent.config import AgentConfig
from automation_agent.logging.event_logger import EventLogger
from automation_agent.orchestrator.verifier import StepVerifier
from automation_agent.shared_models import ActionStep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> AgentConfig:
    defaults = dict(model_provider="local")
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_subprocess_result(stdout: str = "", returncode: int = 0):
    """Create a mock subprocess.CompletedProcess."""
    mock = MagicMock()
    mock.stdout = stdout
    mock.returncode = returncode
    mock.stderr = ""
    return mock


# ---------------------------------------------------------------------------
# Tests: _get_browser_js (Safari / Chrome dispatch)
# ---------------------------------------------------------------------------


class TestGetBrowserJs:
    @patch("automation_agent.actuator.applescript_actuator.subprocess.run")
    def test_safari_js(self, mock_run):
        """_get_browser_js dispatches to Safari AppleScript."""
        mock_run.return_value = _make_subprocess_result("hello world")
        actuator = AppleScriptActuator(config=_make_config())
        result = actuator._get_browser_js("Safari", "document.activeElement.value")
        assert result == "hello world"
        call_args = mock_run.call_args
        script = call_args[0][0][2]  # ["osascript", "-e", script]
        assert "Safari" in script
        assert "do JavaScript" in script

    @patch("automation_agent.actuator.applescript_actuator.subprocess.run")
    def test_chrome_js(self, mock_run):
        """_get_browser_js dispatches to Chrome AppleScript."""
        mock_run.return_value = _make_subprocess_result("typed text")
        actuator = AppleScriptActuator(config=_make_config())
        result = actuator._get_browser_js("Google Chrome", "document.activeElement.value")
        assert result == "typed text"
        call_args = mock_run.call_args
        script = call_args[0][0][2]
        assert "Google Chrome" in script
        assert "javascript" in script

    @patch("automation_agent.actuator.applescript_actuator.subprocess.run")
    def test_non_browser_returns_none(self, mock_run):
        """_get_browser_js returns None for non-browser apps."""
        actuator = AppleScriptActuator(config=_make_config())
        result = actuator._get_browser_js("Calculator", "document.activeElement.value")
        assert result is None
        mock_run.assert_not_called()

    @patch("automation_agent.actuator.applescript_actuator.subprocess.run")
    def test_timeout_returns_none(self, mock_run):
        """_get_browser_js returns None on subprocess timeout."""
        mock_run.side_effect = sp.TimeoutExpired(cmd="osascript", timeout=2)
        actuator = AppleScriptActuator(config=_make_config())
        result = actuator._get_browser_js("Safari", "document.activeElement.value")
        assert result is None

    @patch("automation_agent.actuator.applescript_actuator.subprocess.run")
    def test_error_returns_none(self, mock_run):
        """_get_browser_js returns None on non-zero exit."""
        mock_run.return_value = _make_subprocess_result("", returncode=1)
        actuator = AppleScriptActuator(config=_make_config())
        result = actuator._get_browser_js("Safari", "document.activeElement.value")
        assert result is None


# ---------------------------------------------------------------------------
# Tests: get_active_element_value / get_selected_text (patch get_state)
# ---------------------------------------------------------------------------


class TestGetActiveElementValue:
    def test_returns_value_for_safari(self):
        """get_active_element_value returns JS result for Safari."""
        actuator = AppleScriptActuator(config=_make_config())
        actuator.get_state = MagicMock(return_value={
            "app_name": "Safari",
            "browser_url": "https://google.com",
        })
        actuator._get_browser_js = MagicMock(return_value="hello world")

        result = actuator.get_active_element_value()
        assert result == "hello world"
        actuator._get_browser_js.assert_called_once()

    def test_non_browser_returns_none(self):
        """get_active_element_value returns None for non-browser apps."""
        actuator = AppleScriptActuator(config=_make_config())
        actuator.get_state = MagicMock(return_value={
            "app_name": "Calculator",
        })
        actuator._get_browser_js = MagicMock()

        result = actuator.get_active_element_value()
        assert result is None
        actuator._get_browser_js.assert_not_called()


class TestGetSelectedText:
    def test_returns_selection(self):
        """get_selected_text returns JS selection result."""
        actuator = AppleScriptActuator(config=_make_config())
        actuator.get_state = MagicMock(return_value={
            "app_name": "Safari",
            "browser_url": "https://google.com",
        })
        actuator._get_browser_js = MagicMock(return_value="selected text")

        result = actuator.get_selected_text()
        assert result == "selected text"
        actuator._get_browser_js.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: _get_browser_js_batch (AC-1, AC-2, AC-5, AC-10, AC-15)
# ---------------------------------------------------------------------------


class TestGetBrowserJsBatch:
    @patch("automation_agent.actuator.applescript_actuator.subprocess.run")
    def test_get_browser_js_batch_safari(self, mock_run):
        """AC-1,2,10: Batch JS call parses JSON result for Safari."""
        batch_json = json.dumps({
            "focused_value": "search term",
            "selected_text": "sel",
            "page_title": "Google",
            "page_heading": "Welcome to Google",
        })
        mock_run.return_value = _make_subprocess_result(batch_json)
        actuator = AppleScriptActuator(config=_make_config())
        result = actuator._get_browser_js_batch("Safari")

        assert result["focused_value"] == "search term"
        assert result["selected_text"] == "sel"
        assert result["page_title"] == "Google"
        assert result["page_heading"] == "Welcome to Google"
        # Verify Safari AppleScript pattern
        script = mock_run.call_args[0][0][2]
        assert "Safari" in script
        assert "do JavaScript" in script
        assert "JSON.stringify" in script

    @patch("automation_agent.actuator.applescript_actuator.subprocess.run")
    def test_get_browser_js_batch_chrome(self, mock_run):
        """AC-1,2,10: Batch JS call parses JSON result for Chrome."""
        batch_json = json.dumps({
            "focused_value": "typed",
            "selected_text": "",
            "page_title": "Example",
            "page_heading": "Main Heading",
        })
        mock_run.return_value = _make_subprocess_result(batch_json)
        actuator = AppleScriptActuator(config=_make_config())
        result = actuator._get_browser_js_batch("Google Chrome")

        assert result["focused_value"] == "typed"
        assert result["page_title"] == "Example"
        assert result["page_heading"] == "Main Heading"
        script = mock_run.call_args[0][0][2]
        assert "Google Chrome" in script
        assert "javascript" in script
        assert "JSON.stringify" in script

    @patch("automation_agent.actuator.applescript_actuator.subprocess.run")
    def test_get_browser_js_batch_non_browser(self, mock_run):
        """AC-15: Non-browser app returns empty dict."""
        actuator = AppleScriptActuator(config=_make_config())
        result = actuator._get_browser_js_batch("Calculator")
        assert result == {}
        mock_run.assert_not_called()

    @patch("automation_agent.actuator.applescript_actuator.subprocess.run")
    def test_get_browser_js_batch_timeout(self, mock_run):
        """AC-5,15: Timeout returns empty dict."""
        mock_run.side_effect = sp.TimeoutExpired(cmd="osascript", timeout=2)
        actuator = AppleScriptActuator(config=_make_config())
        result = actuator._get_browser_js_batch("Safari")
        assert result == {}

    @patch("automation_agent.actuator.applescript_actuator.subprocess.run")
    def test_get_browser_js_batch_invalid_json(self, mock_run):
        """AC-15: Malformed JSON returns empty dict."""
        mock_run.return_value = _make_subprocess_result("missing value")
        actuator = AppleScriptActuator(config=_make_config())
        result = actuator._get_browser_js_batch("Safari")
        assert result == {}


# ---------------------------------------------------------------------------
# Tests: get_state() JS integration (batched)
# ---------------------------------------------------------------------------


class TestGetStateJSFields:
    @patch("automation_agent.actuator.applescript_actuator.subprocess.run")
    def test_get_state_populates_page_title(self, mock_run):
        """AC-10: State dict has page_title for Safari."""
        batch_json = json.dumps({
            "focused_value": "",
            "selected_text": "",
            "page_title": "Google Search",
            "page_heading": "",
        })
        mock_run.side_effect = [
            # _run_osascript for get_state
            _make_subprocess_result("Safari|com.apple.Safari|Google|0|0|1440|900"),
            # _get_browser_url
            _make_subprocess_result("https://google.com"),
            # _get_browser_js_batch
            _make_subprocess_result(batch_json),
        ]
        actuator = AppleScriptActuator(config=_make_config())
        state = actuator.get_state()
        assert state["page_title"] == "Google Search"

    @patch("automation_agent.actuator.applescript_actuator.subprocess.run")
    def test_get_state_populates_page_heading(self, mock_run):
        """AC-10: State dict has page_heading for Chrome."""
        batch_json = json.dumps({
            "focused_value": "",
            "selected_text": "",
            "page_title": "",
            "page_heading": "Shopping Cart",
        })
        mock_run.side_effect = [
            _make_subprocess_result(
                "Google Chrome|com.google.Chrome|Cart|0|0|1440|900"
            ),
            _make_subprocess_result("https://target.com/cart"),
            _make_subprocess_result(batch_json),
        ]
        actuator = AppleScriptActuator(config=_make_config())
        state = actuator.get_state()
        assert state["page_heading"] == "Shopping Cart"

    @patch("automation_agent.actuator.applescript_actuator.subprocess.run")
    def test_get_state_js_disabled(self, mock_run):
        """AC-6: js_verification_enabled=False suppresses all 4 JS fields."""
        mock_run.side_effect = [
            _make_subprocess_result("Safari|com.apple.Safari|Google|0|0|1440|900"),
            _make_subprocess_result("https://google.com"),
        ]
        config = _make_config(js_verification_enabled=False)
        actuator = AppleScriptActuator(config=config)
        state = actuator.get_state()
        assert "focused_value" not in state
        assert "selected_text" not in state
        assert "page_title" not in state
        assert "page_heading" not in state

    @patch("automation_agent.actuator.applescript_actuator.subprocess.run")
    def test_get_state_batch_populates_all_four(self, mock_run):
        """AC-3,10: Single subprocess call populates all four JS fields."""
        batch_json = json.dumps({
            "focused_value": "search term",
            "selected_text": "selected",
            "page_title": "Google",
            "page_heading": "Main Heading",
        })
        mock_run.side_effect = [
            _make_subprocess_result("Safari|com.apple.Safari|Google|0|0|1440|900"),
            _make_subprocess_result("https://google.com"),
            _make_subprocess_result(batch_json),
        ]
        actuator = AppleScriptActuator(config=_make_config())
        state = actuator.get_state()
        assert state["focused_value"] == "search term"
        assert state["selected_text"] == "selected"
        assert state["page_title"] == "Google"
        assert state["page_heading"] == "Main Heading"
        # 3 subprocess calls total: get_state osascript, browser_url, batch JS
        assert mock_run.call_count == 3

    @patch("automation_agent.actuator.applescript_actuator.subprocess.run")
    def test_get_state_empty_string_becomes_none(self, mock_run):
        """AC-15: Empty JS values become None in state dict."""
        batch_json = json.dumps({
            "focused_value": "",
            "selected_text": "",
            "page_title": "",
            "page_heading": "",
        })
        mock_run.side_effect = [
            _make_subprocess_result("Safari|com.apple.Safari|Google|0|0|1440|900"),
            _make_subprocess_result("https://google.com"),
            _make_subprocess_result(batch_json),
        ]
        actuator = AppleScriptActuator(config=_make_config())
        state = actuator.get_state()
        assert state["focused_value"] is None
        assert state["selected_text"] is None
        assert state["page_title"] is None
        assert state["page_heading"] is None

    @patch("automation_agent.actuator.applescript_actuator.subprocess.run")
    def test_skips_js_for_non_browser(self, mock_run):
        """get_state does NOT call JS for non-browser apps."""
        mock_run.return_value = _make_subprocess_result(
            "Calculator|com.apple.Calculator|Calculator|0|0|400|300"
        )
        actuator = AppleScriptActuator(config=_make_config())
        state = actuator.get_state()
        assert "focused_value" not in state
        assert "selected_text" not in state
        assert "page_title" not in state
        assert "page_heading" not in state
        assert mock_run.call_count == 1


# ---------------------------------------------------------------------------
# Tests: get_page_title / get_page_heading standalone methods (AC-10)
# ---------------------------------------------------------------------------


class TestGetPageTitleHeading:
    def test_get_page_title_delegates_to_get_state(self):
        """AC-10: get_page_title returns page_title from get_state."""
        actuator = AppleScriptActuator(config=_make_config())
        actuator.get_state = MagicMock(return_value={
            "app_name": "Safari",
            "page_title": "Google Search",
        })
        result = actuator.get_page_title()
        assert result == "Google Search"
        actuator.get_state.assert_called_once()

    def test_get_page_heading_delegates_to_get_state(self):
        """AC-10: get_page_heading returns page_heading from get_state."""
        actuator = AppleScriptActuator(config=_make_config())
        actuator.get_state = MagicMock(return_value={
            "app_name": "Safari",
            "page_heading": "Welcome",
        })
        result = actuator.get_page_heading()
        assert result == "Welcome"
        actuator.get_state.assert_called_once()

    def test_get_page_title_none_for_non_browser(self):
        """get_page_title returns None when page_title not in state."""
        actuator = AppleScriptActuator(config=_make_config())
        actuator.get_state = MagicMock(return_value={
            "app_name": "Calculator",
        })
        result = actuator.get_page_title()
        assert result is None

    def test_get_page_heading_none_for_non_browser(self):
        """get_page_heading returns None when page_heading not in state."""
        actuator = AppleScriptActuator(config=_make_config())
        actuator.get_state = MagicMock(return_value={
            "app_name": "Calculator",
        })
        result = actuator.get_page_heading()
        assert result is None


# ---------------------------------------------------------------------------
# Tests: Tier 1 page content token matcher (AC-11, AC-12)
# ---------------------------------------------------------------------------


class TestTier1PageContentMatcher:
    def test_tier1_page_heading_token_match(self):
        """AC-11: Verifier returns (True, ...) when heading tokens overlap verify."""
        mock_act = MagicMock()
        mock_act.get_state = MagicMock(return_value={
            "app_name": "Safari",
            "browser_url": "https://example.com",
            "window_title": "Example",
            "page_title": None,
            "page_heading": "Shopping Cart Items",
        })
        step = ActionStep(
            action="click",
            params={"x": 100, "y": 200},
            verify="The shopping cart page is visible with items",
        )
        verifier = StepVerifier(actuator=mock_act)
        result = verifier._verify_tier1(step, mock_act, {"success": True})
        assert result is not None
        assert result[0] is True
        assert "page_heading" in result[1]

    def test_tier1_page_title_token_match(self):
        """AC-11: Verifier returns (True, ...) when title tokens overlap verify."""
        mock_act = MagicMock()
        mock_act.get_state = MagicMock(return_value={
            "app_name": "Safari",
            "browser_url": "https://example.com",
            "window_title": "Example",
            "page_title": "Product Details - Running Shoes",
            "page_heading": None,
        })
        step = ActionStep(
            action="click",
            params={"x": 100, "y": 200},
            verify="Product details page shows running shoes",
        )
        verifier = StepVerifier(actuator=mock_act)
        result = verifier._verify_tier1(step, mock_act, {"success": True})
        assert result is not None
        assert result[0] is True
        assert "page_title" in result[1]

    def test_tier1_page_none_returns_none(self):
        """AC-12: Both fields None -> returns None (inconclusive)."""
        mock_act = MagicMock()
        mock_act.get_state = MagicMock(return_value={
            "app_name": "Safari",
            "browser_url": "https://example.com",
            "window_title": "Example",
        })
        step = ActionStep(
            action="click",
            params={"x": 100, "y": 200},
            verify="The shopping cart page is visible",
        )
        verifier = StepVerifier(actuator=mock_act)
        result = verifier._verify_tier1(step, mock_act, {"success": True})
        assert result is None

    def test_tier1_page_no_token_overlap_returns_none(self):
        """AC-11: Fields populated but no token overlap -> None (inconclusive)."""
        mock_act = MagicMock()
        mock_act.get_state = MagicMock(return_value={
            "app_name": "Safari",
            "browser_url": "https://example.com",
            "window_title": "Example",
            "page_title": "Weather Forecast",
            "page_heading": "Today's Weather",
        })
        step = ActionStep(
            action="click",
            params={"x": 100, "y": 200},
            verify="The shopping cart page is visible",
        )
        verifier = StepVerifier(actuator=mock_act)
        result = verifier._verify_tier1(step, mock_act, {"success": True})
        assert result is None

    def test_tier1_page_single_short_token_no_match(self):
        """Single short token (3-4 chars) does NOT match."""
        mock_act = MagicMock()
        mock_act.get_state = MagicMock(return_value={
            "app_name": "Safari",
            "browser_url": "https://example.com",
            "window_title": "Example",
            "page_title": "Cart",
            "page_heading": None,
        })
        step = ActionStep(
            action="click",
            params={"x": 100, "y": 200},
            verify="The cart is open",
        )
        verifier = StepVerifier(actuator=mock_act)
        result = verifier._verify_tier1(step, mock_act, {"success": True})
        # "cart" is only 4 chars, single token -> should NOT match
        assert result is None

    def test_tier1_page_single_long_token_matches(self):
        """Single 5+ char token DOES match."""
        mock_act = MagicMock()
        mock_act.get_state = MagicMock(return_value={
            "app_name": "Safari",
            "browser_url": "https://example.com",
            "window_title": "Example",
            "page_title": "Checkout - Complete Purchase",
            "page_heading": None,
        })
        step = ActionStep(
            action="click",
            params={"x": 100, "y": 200},
            verify="The checkout page is visible",
        )
        verifier = StepVerifier(actuator=mock_act)
        result = verifier._verify_tier1(step, mock_act, {"success": True})
        assert result is not None
        assert result[0] is True
        assert "checkout" in result[1].lower() or "Checkout" in result[1]


# ---------------------------------------------------------------------------
# Tests: Tier 1 integration (verifier resolves with focused_value)
# ---------------------------------------------------------------------------


class TestTier1TypeTextResolution:
    async def test_tier1_resolves_when_focused_value_populated(self, tmp_log_dir):
        """Tier 1 type_text check returns (True, ...) when focused_value matches."""
        mock_act = MagicMock()
        mock_act.get_state = MagicMock(
            return_value={
                "app_name": "Safari",
                "app_bundle": "com.apple.Safari",
                "window_title": "Google",
                "browser_url": "https://google.com",
                "focused_value": "hello world",
                "selected_text": None,
            }
        )
        step = ActionStep(
            action="type_text",
            params={"text": "hello world"},
            verify="Text field contains hello world",
        )
        logger = EventLogger(tmp_log_dir)
        verifier = StepVerifier(actuator=mock_act, logger=logger)

        result = await verifier.verify(step, {"success": True, "output": ""})

        assert result.success is True
        assert result.verification_method == "actuator_state"
        assert "focused_value" in result.evidence
