"""Integration tests for Speed Phase 1 — all three changes working together.

These tests validate that the JS injection batching (E1), AX confidence
calibration (E2), and page content token matcher (E1) work correctly when
composed with real StepVerifier, GroundingRouter, and AppleScriptActuator
(mocked subprocess / AX bridge).

Tests are written against the spec interfaces (docs/speed-phase1-spec.md
sections 2.1-2.3, 3.1-3.3, 5.3). They will FAIL until Engineers 1 and 2
land their changes, then pass once merged.

AC coverage:
  AC-4:  test_type_text_tier1_resolves_no_vision, test_type_text_empty_falls_to_tier2
  AC-5:  test_batch_js_single_subprocess_call
  AC-6:  test_js_disabled_suppresses_all_fields
  AC-7:  test_ax_exact_match_high_confidence
  AC-8:  test_ax_partial_match_lower_confidence
  AC-11: test_page_heading_tier1_resolves_no_vision, test_page_title_tier1_resolves_no_vision,
         test_page_token_match_needs_two_tokens, test_page_token_match_single_long_token
  AC-12: test_page_fields_none_falls_to_tier2
  AC-15: test_subprocess_timeout_graceful
"""

import json
import subprocess as sp
from unittest.mock import MagicMock, patch

import pytest  # noqa: F401 — used by pytest marker collection

from automation_agent.config import AgentConfig
from automation_agent.orchestrator.grounding_router import (
    GroundingRouter,
    GroundingStrategy,
)
from automation_agent.orchestrator.verifier import StepVerifier
from automation_agent.shared_models import ActionStep

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> AgentConfig:
    defaults = {"model_provider": "local"}
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_subprocess_result(stdout: str = "", returncode: int = 0):
    """Create a mock subprocess.CompletedProcess."""
    mock = MagicMock()
    mock.stdout = stdout
    mock.returncode = returncode
    mock.stderr = ""
    return mock


def _make_mock_actuator_with_state(state: dict) -> MagicMock:
    """Create a mock actuator whose get_state() returns *state*."""
    act = MagicMock()
    act.get_state = MagicMock(return_value=state)
    return act


def _make_ax_element(center=(500, 300), role="AXButton", title="Submit"):
    """Create a mock AX element with expected attributes."""
    elem = MagicMock()
    elem.center = center
    elem.role = role
    elem.title = title
    elem.value = None
    elem.description = None
    elem.position = (center[0] - 40, center[1] - 15)
    elem.size = (80, 30)
    elem.focused = False
    return elem


# ---------------------------------------------------------------------------
# Tests 1-2: Tier 1 type_text resolution (AC-4)
# ---------------------------------------------------------------------------


class TestTypeTextTier1:
    def test_type_text_tier1_resolves_no_vision(self):
        """AC-4: type_text with populated focused_value resolves at Tier 1.

        Real StepVerifier, mock actuator returning focused_value="hello world".
        Vision (coordinator) is never called.
        """
        state = {
            "app_name": "Safari",
            "app_bundle": "com.apple.Safari",
            "window_title": "Google",
            "browser_url": "https://google.com",
            "focused_value": "hello world",
            "selected_text": None,
            "page_title": None,
            "page_heading": None,
        }
        actuator = _make_mock_actuator_with_state(state)

        step = ActionStep(
            action="type_text",
            params={"text": "hello"},
            verify="text typed",
        )

        verifier = StepVerifier(actuator=actuator)
        result = verifier._verify_tier1(step, actuator, {"success": True})

        assert result is not None, "Tier 1 should be conclusive when focused_value is populated"
        assert result[0] is True, "Should pass: 'hello' is in 'hello world'"
        assert "focused_value" in result[1], f"Evidence should cite focused_value, got: {result[1]}"

    def test_type_text_empty_falls_to_tier2(self):
        """AC-4: type_text with focused_value=None -> Tier 1 inconclusive (None).

        When focused_value is None and no other text fields are populated,
        _verify_tier1() returns None so verification escalates to Tier 2 vision.
        """
        state = {
            "app_name": "Safari",
            "app_bundle": "com.apple.Safari",
            "window_title": "Google",
            "browser_url": "https://google.com",
            "focused_value": None,
            "selected_text": None,
            "page_title": None,
            "page_heading": None,
        }
        actuator = _make_mock_actuator_with_state(state)

        step = ActionStep(
            action="type_text",
            params={"text": "hello"},
            verify="text typed",
        )

        verifier = StepVerifier(actuator=actuator)
        result = verifier._verify_tier1(step, actuator, {"success": True})

        assert result is None, "Tier 1 should be inconclusive when focused_value is None"


# ---------------------------------------------------------------------------
# Tests 3-5: Tier 1 page content token matcher (AC-11, AC-12)
# ---------------------------------------------------------------------------


class TestPageContentTier1:
    def test_page_heading_tier1_resolves_no_vision(self):
        """AC-11: page_heading tokens match verify -> Tier 1 resolves (True).

        "Shopping Cart - Your Items" vs verify "shopping cart page is displayed"
        shares tokens "shopping" and "cart" (2 tokens, both 4+ chars) -> match.
        """
        state = {
            "app_name": "Safari",
            "app_bundle": "com.apple.Safari",
            "window_title": "Cart",
            "browser_url": "https://example.com/cart",
            "focused_value": None,
            "selected_text": None,
            "page_title": None,
            "page_heading": "Shopping Cart - Your Items",
        }
        actuator = _make_mock_actuator_with_state(state)

        step = ActionStep(
            action="click",
            params={"x": 100, "y": 200},
            verify="shopping cart page is displayed",
        )

        verifier = StepVerifier(actuator=actuator)
        result = verifier._verify_tier1(step, actuator, {"success": True})

        assert result is not None, "Tier 1 should resolve when page_heading tokens match"
        assert result[0] is True
        assert "page_heading" in result[1]

    def test_page_title_tier1_resolves_no_vision(self):
        """AC-11: page_title tokens match verify -> Tier 1 resolves (True).

        "Amazon.com: Shopping Cart" vs verify "shopping cart is visible"
        shares tokens "shopping" and "cart" -> match.
        """
        state = {
            "app_name": "Safari",
            "app_bundle": "com.apple.Safari",
            "window_title": "Amazon",
            "browser_url": "https://amazon.com/gp/cart",
            "focused_value": None,
            "selected_text": None,
            "page_title": "Amazon.com: Shopping Cart",
            "page_heading": None,
        }
        actuator = _make_mock_actuator_with_state(state)

        step = ActionStep(
            action="click",
            params={"x": 100, "y": 200},
            verify="shopping cart is visible",
        )

        verifier = StepVerifier(actuator=actuator)
        result = verifier._verify_tier1(step, actuator, {"success": True})

        assert result is not None, "Tier 1 should resolve when page_title tokens match"
        assert result[0] is True
        assert "page_title" in result[1]

    def test_page_fields_none_falls_to_tier2(self):
        """AC-12: page_title=None, page_heading=None -> Tier 1 inconclusive.

        When both page fields are absent (non-browser or JS failure), the
        page content matcher returns None, preserving Tier 2 fallback.
        """
        state = {
            "app_name": "Safari",
            "app_bundle": "com.apple.Safari",
            "window_title": "Some Page",
            "browser_url": "https://example.com",
            "focused_value": None,
            "selected_text": None,
            # page_title and page_heading intentionally absent
        }
        actuator = _make_mock_actuator_with_state(state)

        step = ActionStep(
            action="click",
            params={"x": 100, "y": 200},
            verify="shopping cart page is displayed",
        )

        verifier = StepVerifier(actuator=actuator)
        result = verifier._verify_tier1(step, actuator, {"success": True})

        assert result is None, "Tier 1 should be inconclusive when page fields are absent"


# ---------------------------------------------------------------------------
# Tests 6-7: AX confidence calibration (AC-7, AC-8, AC-9)
# ---------------------------------------------------------------------------


class TestAXConfidenceCalibration:
    def test_ax_exact_match_high_confidence(self):
        """AC-7,9: Exact AX match (score=1.0) -> confidence=0.95.

        GroundingRouter._ground_accessibility_match() with match_score=1.0
        should produce confidence = 0.6 + 0.35 * 1.0 = 0.95.
        """
        router = GroundingRouter(config=_make_config())
        elem = _make_ax_element(center=(500, 300), role="AXButton", title="Submit")

        result = router._ground_accessibility_match(elem, match_score=1.0)

        assert result is not None
        assert result.strategy_used == GroundingStrategy.ACCESSIBILITY
        assert result.x == 500
        assert result.y == 300
        expected_confidence = 0.6 + 0.35 * 1.0  # = 0.95
        assert abs(result.confidence - expected_confidence) < 1e-6, (
            f"Expected confidence {expected_confidence}, got {result.confidence}"
        )

    def test_ax_partial_match_lower_confidence(self):
        """AC-8: Partial AX match (score=0.7) -> confidence=0.845 (< 0.9).

        This confidence is below the 0.9 critical threshold, so partial
        matches should NOT skip pre-click validation.
        """
        router = GroundingRouter(config=_make_config())
        elem = _make_ax_element(center=(200, 400), role="AXLink", title="Add to Cart")

        result = router._ground_accessibility_match(elem, match_score=0.7)

        assert result is not None
        expected_confidence = 0.6 + 0.35 * 0.7  # = 0.845
        assert abs(result.confidence - expected_confidence) < 1e-6, (
            f"Expected confidence {expected_confidence}, got {result.confidence}"
        )
        assert result.confidence < 0.9, (
            "Partial match confidence should be below 0.9 critical threshold"
        )


# ---------------------------------------------------------------------------
# Tests 8-10: AppleScriptActuator JS batching and config gating (AC-5, AC-6, AC-15)
# ---------------------------------------------------------------------------


class TestActuatorJSBatching:
    @patch("automation_agent.actuator.applescript_actuator.subprocess.run")
    def test_js_disabled_suppresses_all_fields(self, mock_run):
        """AC-6: js_verification_enabled=False -> no JS fields in state.

        When the config flag is False, get_state() must not contain
        focused_value, selected_text, page_title, or page_heading.
        """
        from automation_agent.actuator.applescript_actuator import AppleScriptActuator

        mock_run.side_effect = [
            # Base get_state osascript call
            _make_subprocess_result("Safari|com.apple.Safari|Google|0|0|1440|900"),
            # _get_browser_url call
            _make_subprocess_result("https://google.com"),
        ]
        config = _make_config(js_verification_enabled=False)
        actuator = AppleScriptActuator(config=config)
        state = actuator.get_state()

        assert state.get("app_name") == "Safari"
        assert "focused_value" not in state, "focused_value should be suppressed"
        assert "selected_text" not in state, "selected_text should be suppressed"
        assert "page_title" not in state, "page_title should be suppressed"
        assert "page_heading" not in state, "page_heading should be suppressed"

    @patch("automation_agent.actuator.applescript_actuator.subprocess.run")
    def test_batch_js_single_subprocess_call(self, mock_run):
        """AC-5: All 4 JS fields populated via 1 batched subprocess call.

        For a Safari browser, get_state() should make exactly 3 subprocess
        calls total: (1) base get_state, (2) browser_url, (3) batch JS.
        NOT 6 calls (which would happen with unbatched individual JS calls).
        """
        from automation_agent.actuator.applescript_actuator import AppleScriptActuator

        batch_json = json.dumps({
            "focused_value": "search query",
            "selected_text": "",
            "page_title": "Google Search",
            "page_heading": "Google",
        })
        mock_run.side_effect = [
            # 1. Base get_state osascript
            _make_subprocess_result("Safari|com.apple.Safari|Google|0|0|1440|900"),
            # 2. _get_browser_url
            _make_subprocess_result("https://google.com"),
            # 3. _get_browser_js_batch (single call for all 4 fields)
            _make_subprocess_result(batch_json),
        ]

        actuator = AppleScriptActuator(config=_make_config())
        state = actuator.get_state()

        assert state["focused_value"] == "search query"
        assert state["page_title"] == "Google Search"
        assert state["page_heading"] == "Google"
        # Exactly 3 subprocess calls, NOT 6
        assert mock_run.call_count == 3, (
            f"Expected 3 subprocess calls (base + URL + batch JS), got {mock_run.call_count}"
        )

    @patch("automation_agent.actuator.applescript_actuator.subprocess.run")
    def test_subprocess_timeout_graceful(self, mock_run):
        """AC-15: JS batch timeout -> state still has app_name, JS fields are None.

        When the batch JS subprocess call raises TimeoutExpired, get_state()
        must NOT raise. It should return a dict with app_name populated but
        all JS fields as None.
        """
        from automation_agent.actuator.applescript_actuator import AppleScriptActuator

        def side_effect_fn(*args, **kwargs):
            # First call: base get_state succeeds
            if side_effect_fn.call_count == 0:
                side_effect_fn.call_count += 1
                return _make_subprocess_result(
                    "Safari|com.apple.Safari|Google|0|0|1440|900"
                )
            # Second call: browser_url succeeds
            if side_effect_fn.call_count == 1:
                side_effect_fn.call_count += 1
                return _make_subprocess_result("https://google.com")
            # Third call: batch JS times out
            side_effect_fn.call_count += 1
            raise sp.TimeoutExpired(cmd="osascript", timeout=2)

        side_effect_fn.call_count = 0
        mock_run.side_effect = side_effect_fn

        actuator = AppleScriptActuator(config=_make_config())
        state = actuator.get_state()

        # App name should still be populated (from the base call)
        assert state["app_name"] == "Safari"
        # JS fields should be None (graceful degradation)
        assert state.get("focused_value") is None
        assert state.get("selected_text") is None
        assert state.get("page_title") is None
        assert state.get("page_heading") is None


# ---------------------------------------------------------------------------
# Tests 11-12: Page token matcher edge cases (AC-11)
# ---------------------------------------------------------------------------


class TestPageTokenMatcherEdgeCases:
    def test_page_token_match_needs_two_tokens(self):
        """AC-11: Single short token (4 chars) does NOT match.

        page_heading="Cart", verify="shopping cart items".
        Only 1 overlapping token "cart" (4 chars, < 5) -> no match.
        Returns None (inconclusive), falls through to Tier 2.
        """
        state = {
            "app_name": "Safari",
            "app_bundle": "com.apple.Safari",
            "window_title": "Cart",
            "browser_url": "https://example.com/cart",
            "focused_value": None,
            "selected_text": None,
            "page_title": None,
            "page_heading": "Cart",
        }
        actuator = _make_mock_actuator_with_state(state)

        step = ActionStep(
            action="click",
            params={"x": 100, "y": 200},
            verify="shopping cart items",
        )

        verifier = StepVerifier(actuator=actuator)
        result = verifier._verify_tier1(step, actuator, {"success": True})

        assert result is None, (
            "Single 4-char token 'cart' should NOT match (only 1 token, < 5 chars)"
        )

    def test_page_token_match_single_long_token(self):
        """AC-11: Single 8-char token "shopping" DOES match.

        page_heading="Shopping", verify="shopping cart".
        1 overlapping token "shopping" (8 chars, >= 5) -> match.
        """
        state = {
            "app_name": "Safari",
            "app_bundle": "com.apple.Safari",
            "window_title": "Shop",
            "browser_url": "https://example.com/shop",
            "focused_value": None,
            "selected_text": None,
            "page_title": None,
            "page_heading": "Shopping",
        }
        actuator = _make_mock_actuator_with_state(state)

        step = ActionStep(
            action="click",
            params={"x": 100, "y": 200},
            verify="shopping cart",
        )

        verifier = StepVerifier(actuator=actuator)
        result = verifier._verify_tier1(step, actuator, {"success": True})

        assert result is not None, (
            "Single 8-char token 'shopping' should match (>= 5 chars)"
        )
        assert result[0] is True
        assert "page_heading" in result[1]
