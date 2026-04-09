"""Unit tests for browser interchangeability and substitution features.

Covers:
  1. Verifier._matches_expected_app — browser interchangeability logic
  2. Actuator.activate_app — browser substitution (use frontmost browser)
  3. Actuator._js_disabled_browsers — per-session blocklist
  4. Agent type_text — fail when click-to-focus element not found

All external calls are mocked — no real AppleScript, no Anthropic API.
"""

import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.config import AgentConfig
from automation_agent.orchestrator.verifier import StepVerifier
from automation_agent.shared_models import ActionStep, FindElementResult


# ---------------------------------------------------------------------------
# Helpers / Factories
# ---------------------------------------------------------------------------


def _make_config(**overrides):
    """Return an AgentConfig for tests. Pin model_provider='local' to avoid
    API-key validation errors from .env leaking into pydantic-settings."""
    defaults = dict(
        model_provider="local",
        vision_model="molmo",
        log_dir="/tmp/test_browser_agnostic_logs",
    )
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_actuator(config=None):
    """Return a real AppleScriptActuator with all subprocess calls mocked."""
    from automation_agent.actuator.applescript_actuator import AppleScriptActuator

    if config is None:
        config = _make_config()
    return AppleScriptActuator(config)


def _make_agent(tmp_path, actuator=None, coordinator=None, extra_kwargs=None):
    """Construct a minimal AutomationAgent with fully mocked dependencies."""
    from automation_agent.orchestrator.agent import AutomationAgent
    from automation_agent.shared_models import ActionPlan

    config = _make_config(
        log_dir=str(tmp_path),
        max_iterations=10,
        action_delay=0.0,
    )

    if actuator is None:
        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.type_text = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(
            return_value={"app_name": "Safari", "window_title": "Test"}
        )

    if coordinator is None:
        coordinator = AsyncMock()
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(x=400, y=300, confidence=0.8, source="vision")
        )
        coordinator.describe_screen = AsyncMock(return_value="Test screen")
        coordinator.verify_condition = AsyncMock(return_value=True)
        coordinator.capture_screenshot = AsyncMock(return_value="fakebase64")
        coordinator.capabilities = MagicMock(return_value=frozenset())

    planner = AsyncMock()
    planner.plan = AsyncMock(
        return_value=ActionPlan(
            steps=[ActionStep(action="done", params={}, verify="")],
            goal="test",
        )
    )
    planner.replan = AsyncMock(
        return_value=ActionPlan(
            steps=[ActionStep(action="done", params={}, verify="")],
            goal="test",
        )
    )

    skill_registry = MagicMock()
    skill_registry.match = AsyncMock(return_value=None)
    skill_registry.list_skills = MagicMock(return_value=[])
    skill_registry.expand = MagicMock(return_value=None)

    kwargs = dict(
        planner=planner,
        skill_registry=skill_registry,
        coordinator=coordinator,
        actuator=actuator,
        config=config,
    )
    if extra_kwargs:
        kwargs.update(extra_kwargs)

    return AutomationAgent(**kwargs)


# ===========================================================================
# 1. Verifier: _matches_expected_app — browser interchangeability
# ===========================================================================


class TestMatchesExpectedApp:
    """StepVerifier._matches_expected_app treats all browsers as interchangeable."""

    @pytest.mark.unit
    def test_safari_expected_chrome_actual(self):
        """Safari expected, Chrome actual -> True (both browsers)."""
        assert StepVerifier._matches_expected_app("Safari", "Google Chrome") is True

    @pytest.mark.unit
    def test_chrome_expected_safari_actual(self):
        """Chrome expected, Safari actual -> True (symmetric)."""
        assert StepVerifier._matches_expected_app("Google Chrome", "Safari") is True

    @pytest.mark.unit
    def test_safari_expected_firefox_actual(self):
        """Safari expected, Firefox actual -> True."""
        assert StepVerifier._matches_expected_app("Safari", "Firefox") is True

    @pytest.mark.unit
    def test_chrome_expected_arc_actual(self):
        """Chrome expected, Arc actual -> True."""
        assert StepVerifier._matches_expected_app("Google Chrome", "Arc") is True

    @pytest.mark.unit
    def test_safari_expected_calculator_actual(self):
        """Safari expected, Calculator actual -> False (non-browser)."""
        assert StepVerifier._matches_expected_app("Safari", "Calculator") is False

    @pytest.mark.unit
    def test_calculator_expected_safari_actual(self):
        """Calculator expected, Safari actual -> False (non-browser expected)."""
        assert StepVerifier._matches_expected_app("Calculator", "Safari") is False

    @pytest.mark.unit
    def test_empty_expected(self):
        """Empty expected string -> False."""
        assert StepVerifier._matches_expected_app("", "Safari") is False

    @pytest.mark.unit
    def test_empty_actual(self):
        """Empty actual string -> False."""
        assert StepVerifier._matches_expected_app("Safari", "") is False

    @pytest.mark.unit
    def test_both_empty(self):
        """Both empty -> False."""
        assert StepVerifier._matches_expected_app("", "") is False

    @pytest.mark.unit
    def test_same_browser(self):
        """Same browser -> True (trivially via substring check)."""
        assert StepVerifier._matches_expected_app("Safari", "Safari") is True

    @pytest.mark.unit
    def test_same_browser_chrome(self):
        """Same browser Chrome -> True."""
        assert StepVerifier._matches_expected_app("Google Chrome", "Google Chrome") is True

    @pytest.mark.unit
    def test_google_chrome_full_name_vs_safari(self):
        """'Google Chrome' (full name) expected, 'safari' actual -> True."""
        assert StepVerifier._matches_expected_app("Google Chrome", "safari") is True

    @pytest.mark.unit
    def test_case_insensitive(self):
        """Case-insensitive matching -> True."""
        assert StepVerifier._matches_expected_app("SAFARI", "google chrome") is True

    @pytest.mark.unit
    def test_edge_expected_brave_actual(self):
        """Edge expected, Brave actual -> True (both browsers)."""
        assert StepVerifier._matches_expected_app("Microsoft Edge", "Brave") is True

    @pytest.mark.unit
    def test_opera_expected_safari_actual(self):
        """Opera expected, Safari actual -> True (both browsers)."""
        assert StepVerifier._matches_expected_app("Opera", "Safari") is True

    @pytest.mark.unit
    def test_finder_expected_finder_actual(self):
        """Non-browser apps match when exact name matches."""
        assert StepVerifier._matches_expected_app("Finder", "Finder") is True

    @pytest.mark.unit
    def test_finder_expected_calculator_actual(self):
        """Two different non-browser apps -> False."""
        assert StepVerifier._matches_expected_app("Finder", "Calculator") is False

    @pytest.mark.unit
    def test_chrome_substring_match(self):
        """'Chrome' (short) expected, 'Google Chrome' actual -> True via substring."""
        assert StepVerifier._matches_expected_app("Chrome", "Google Chrome") is True

    @pytest.mark.unit
    def test_safari_expected_none_actual(self):
        """None-like inputs: None would cause an error so we test empty instead."""
        # The method checks `not expected_app` first, so empty is False.
        assert StepVerifier._matches_expected_app("Safari", "") is False


# ===========================================================================
# 2. Actuator: browser substitution in activate_app
# ===========================================================================


class TestActivateAppBrowserSubstitution:
    """AppleScriptActuator.activate_app substitutes browsers when another
    browser is already frontmost."""

    @pytest.mark.unit
    def test_safari_requested_chrome_frontmost_uses_chrome(self):
        """activate_app('Safari') when Chrome is frontmost -> activates Chrome."""
        actuator = _make_actuator()
        # Mock get_state to return Chrome as frontmost
        with patch.object(
            actuator, "get_state", return_value={"app_name": "Google Chrome"}
        ), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = actuator.activate_app("Safari")
            # The subprocess.run call should use "Google Chrome" not "Safari"
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args == ["open", "-a", "Google Chrome"]
            assert result["success"] is True

    @pytest.mark.unit
    def test_safari_requested_safari_frontmost_no_substitution(self):
        """activate_app('Safari') when Safari is frontmost -> activates Safari."""
        actuator = _make_actuator()
        with patch.object(
            actuator, "get_state", return_value={"app_name": "Safari"}
        ), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = actuator.activate_app("Safari")
            # Same browser — no substitution, uses Safari
            args = mock_run.call_args[0][0]
            assert args == ["open", "-a", "Safari"]
            assert result["success"] is True

    @pytest.mark.unit
    def test_calculator_requested_chrome_frontmost_no_substitution(self):
        """activate_app('Calculator') when Chrome is frontmost -> activates Calculator.
        Non-browser apps are never substituted."""
        actuator = _make_actuator()
        with patch.object(
            actuator, "get_state", return_value={"app_name": "Google Chrome"}
        ), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = actuator.activate_app("Calculator")
            args = mock_run.call_args[0][0]
            assert args == ["open", "-a", "Calculator"]
            assert result["success"] is True

    @pytest.mark.unit
    def test_safari_requested_get_state_fails_uses_safari(self):
        """activate_app('Safari') when get_state raises -> fallback to Safari."""
        actuator = _make_actuator()
        with patch.object(
            actuator, "get_state", side_effect=Exception("osascript failed")
        ), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = actuator.activate_app("Safari")
            args = mock_run.call_args[0][0]
            assert args == ["open", "-a", "Safari"]
            assert result["success"] is True

    @pytest.mark.unit
    def test_google_chrome_requested_safari_frontmost_uses_safari(self):
        """activate_app('Google Chrome') when Safari is frontmost -> activates Safari."""
        actuator = _make_actuator()
        with patch.object(
            actuator, "get_state", return_value={"app_name": "Safari"}
        ), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = actuator.activate_app("Google Chrome")
            args = mock_run.call_args[0][0]
            assert args == ["open", "-a", "Safari"]
            assert result["success"] is True

    @pytest.mark.unit
    def test_chrome_requested_arc_frontmost_uses_arc(self):
        """activate_app('Chrome') when Arc is frontmost -> activates Arc."""
        actuator = _make_actuator()
        with patch.object(
            actuator, "get_state", return_value={"app_name": "Arc"}
        ), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = actuator.activate_app("Chrome")
            args = mock_run.call_args[0][0]
            assert args == ["open", "-a", "Arc"]
            assert result["success"] is True

    @pytest.mark.unit
    def test_firefox_requested_edge_frontmost_uses_edge(self):
        """activate_app('Firefox') when Edge is frontmost -> activates Edge."""
        actuator = _make_actuator()
        with patch.object(
            actuator, "get_state", return_value={"app_name": "Edge"}
        ), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = actuator.activate_app("Firefox")
            args = mock_run.call_args[0][0]
            assert args == ["open", "-a", "Edge"]
            assert result["success"] is True

    @pytest.mark.unit
    def test_safari_requested_get_state_returns_empty_app(self):
        """activate_app('Safari') when get_state returns empty app_name -> uses Safari."""
        actuator = _make_actuator()
        with patch.object(
            actuator, "get_state", return_value={"app_name": ""}
        ), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = actuator.activate_app("Safari")
            args = mock_run.call_args[0][0]
            assert args == ["open", "-a", "Safari"]

    @pytest.mark.unit
    def test_subprocess_failure_returns_error(self):
        """activate_app returns success=False when open -a fails."""
        actuator = _make_actuator()
        with patch.object(
            actuator, "get_state", return_value={"app_name": "Safari"}
        ), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="Unable to find application"
            )
            result = actuator.activate_app("Safari")
            assert result["success"] is False

    @pytest.mark.unit
    def test_subprocess_timeout_returns_error(self):
        """activate_app returns success=False on subprocess timeout."""
        actuator = _make_actuator()
        with patch.object(
            actuator, "get_state", return_value={"app_name": "Safari"}
        ), patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="open", timeout=10)):
            result = actuator.activate_app("Safari")
            assert result["success"] is False


# ===========================================================================
# 3. Actuator: _js_disabled_browsers per-session blocklist
# ===========================================================================


class TestJsDisabledBrowsers:
    """AppleScriptActuator._js_disabled_browsers tracks per-session failures."""

    @pytest.mark.unit
    def test_safari_permission_error_adds_to_blocklist(self):
        """First Safari JS call with permission error -> adds 'safari' to blocklist."""
        actuator = _make_actuator()
        assert len(actuator._js_disabled_browsers) == 0

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Allow JavaScript from Apple Events"

        with patch("subprocess.run", return_value=mock_result):
            result = actuator._get_browser_js("Safari", "document.title")
            assert result is None
            assert "safari" in actuator._js_disabled_browsers

    @pytest.mark.unit
    def test_second_safari_js_call_skipped(self):
        """Second Safari JS call -> skipped immediately (in blocklist)."""
        actuator = _make_actuator()
        actuator._js_disabled_browsers.add("safari")

        with patch("subprocess.run") as mock_run:
            result = actuator._get_browser_js("Safari", "document.title")
            assert result is None
            # subprocess.run should NOT have been called
            mock_run.assert_not_called()

    @pytest.mark.unit
    def test_chrome_works_when_safari_blocked(self):
        """Chrome JS call when Safari is blocked -> still works (independent)."""
        actuator = _make_actuator()
        actuator._js_disabled_browsers.add("safari")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Some Title"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = actuator._get_browser_js("Google Chrome", "document.title")
            assert result == "Some Title"

    @pytest.mark.unit
    def test_new_actuator_fresh_blocklist(self):
        """New actuator instance -> fresh blocklist (not shared)."""
        actuator1 = _make_actuator()
        actuator1._js_disabled_browsers.add("safari")

        actuator2 = _make_actuator()
        assert len(actuator2._js_disabled_browsers) == 0
        assert "safari" not in actuator2._js_disabled_browsers

    @pytest.mark.unit
    def test_non_permission_error_does_not_block(self):
        """A regular failure (not 'Allow JavaScript') does NOT add to blocklist."""
        actuator = _make_actuator()
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "execution error: some other error"

        with patch("subprocess.run", return_value=mock_result):
            result = actuator._get_browser_js("Safari", "document.title")
            assert result is None
            assert "safari" not in actuator._js_disabled_browsers

    @pytest.mark.unit
    def test_blocklist_is_case_insensitive_via_lower(self):
        """Blocklist uses lowered names — 'Safari' matches 'safari' entry."""
        actuator = _make_actuator()
        actuator._js_disabled_browsers.add("safari")

        with patch("subprocess.run") as mock_run:
            # "Safari" -> lower -> "safari" which is in blocklist
            result = actuator._get_browser_js("Safari", "document.title")
            assert result is None
            mock_run.assert_not_called()

    @pytest.mark.unit
    def test_chrome_permission_error_blocks_chrome_only(self):
        """Chrome permission error blocks Chrome but not Safari."""
        actuator = _make_actuator()
        # Simulate Chrome blocking by directly adding to blocklist
        # (Chrome doesn't have the same permission error, but the mechanism works)
        actuator._js_disabled_browsers.add("chrome")

        # Chrome should be blocked
        with patch("subprocess.run") as mock_run:
            result = actuator._get_browser_js("Google Chrome", "document.title")
            assert result is None
            mock_run.assert_not_called()

        # Safari should still work
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Safari Title"
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result):
            result = actuator._get_browser_js("Safari", "document.title")
            assert result == "Safari Title"

    @pytest.mark.unit
    def test_unsupported_browser_returns_none_without_blocklist(self):
        """Firefox (unsupported for JS) returns None without adding to blocklist."""
        actuator = _make_actuator()
        result = actuator._get_browser_js("Firefox", "document.title")
        assert result is None
        assert len(actuator._js_disabled_browsers) == 0

    @pytest.mark.unit
    def test_empty_stdout_returns_none(self):
        """Successful returncode but empty stdout -> returns None."""
        actuator = _make_actuator()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = actuator._get_browser_js("Safari", "document.title")
            assert result is None
            # Should NOT be blocklisted (no permission error)
            assert "safari" not in actuator._js_disabled_browsers


# ===========================================================================
# 4. Type_text: fail when click-to-focus element not found
# ===========================================================================


class TestTypeTextClickToFocus:
    """Agent type_text step fails gracefully when element lookup fails.

    Tests call _dispatch_action() which handles the type_text action dispatch
    including click-to-focus logic. Returns a dict with success/error keys.
    """

    @pytest.mark.unit
    async def test_type_text_element_not_found_returns_failure(self, tmp_path):
        """type_text with element that _find_element returns None -> success=False."""
        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.type_text = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(
            return_value={"app_name": "Safari", "window_title": "Test"}
        )

        coordinator = AsyncMock()
        coordinator.find_element = AsyncMock(return_value=None)
        coordinator.capabilities = MagicMock(return_value=frozenset())

        agent = _make_agent(tmp_path, actuator=actuator, coordinator=coordinator)
        # Mock _find_element to return None
        agent._find_element = AsyncMock(return_value=None)

        step = ActionStep(
            action="type_text",
            params={"text": "hello", "element": "search box"},
            verify="search box contains hello",
        )
        result = await agent._dispatch_action(step)
        assert result["success"] is False
        assert "not found" in result.get("error", "").lower()
        # type_text on the actuator should NOT have been called
        actuator.type_text.assert_not_called()

    @pytest.mark.unit
    async def test_type_text_element_find_raises_returns_failure(self, tmp_path):
        """type_text with element that _find_element raises -> success=False."""
        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.type_text = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(
            return_value={"app_name": "Safari", "window_title": "Test"}
        )

        coordinator = AsyncMock()
        coordinator.capabilities = MagicMock(return_value=frozenset())

        agent = _make_agent(tmp_path, actuator=actuator, coordinator=coordinator)
        agent._find_element = AsyncMock(
            side_effect=RuntimeError("Vision server down")
        )

        step = ActionStep(
            action="type_text",
            params={"text": "hello", "element": "search box"},
            verify="search box contains hello",
        )
        result = await agent._dispatch_action(step)
        assert result["success"] is False
        assert "failed" in result.get("error", "").lower()
        actuator.type_text.assert_not_called()

    @pytest.mark.unit
    async def test_type_text_without_element_types_normally(self, tmp_path):
        """type_text without element param -> types normally (no click-to-focus)."""
        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.type_text = MagicMock(return_value={"success": True, "output": "typed"})
        actuator.press_key = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(
            return_value={"app_name": "Safari", "window_title": "Test"}
        )

        coordinator = AsyncMock()
        coordinator.capabilities = MagicMock(return_value=frozenset())

        agent = _make_agent(tmp_path, actuator=actuator, coordinator=coordinator)
        agent._find_element = AsyncMock(return_value=None)

        step = ActionStep(
            action="type_text",
            params={"text": "hello"},
            verify="text entered",
        )
        result = await agent._dispatch_action(step)
        assert result["success"] is True
        actuator.type_text.assert_called_once_with("hello")
        # _find_element should NOT have been called (no element param)
        agent._find_element.assert_not_called()

    @pytest.mark.unit
    async def test_type_text_with_element_found_clicks_then_types(self, tmp_path):
        """type_text with element found -> clicks at location, then types."""
        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.type_text = MagicMock(return_value={"success": True, "output": "typed"})
        actuator.press_key = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(
            return_value={"app_name": "Safari", "window_title": "Test"}
        )

        coordinator = AsyncMock()
        coordinator.capabilities = MagicMock(return_value=frozenset())

        agent = _make_agent(tmp_path, actuator=actuator, coordinator=coordinator)
        agent._find_element = AsyncMock(
            return_value=FindElementResult(
                x=200, y=150, confidence=0.9, source="vision",
                screen_x=200, screen_y=150,
            )
        )

        step = ActionStep(
            action="type_text",
            params={"text": "hello", "element": "search box"},
            verify="search box contains hello",
        )
        result = await agent._dispatch_action(step)
        assert result["success"] is True
        # Should have clicked at the element location first
        actuator.click.assert_called_once_with(200, 150)
        # Then typed the text
        actuator.type_text.assert_called_once_with("hello")

    @pytest.mark.unit
    async def test_type_text_element_with_skip_focus_skips_find(self, tmp_path):
        """type_text with _skip_focus=True -> does not call _find_element."""
        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.type_text = MagicMock(return_value={"success": True, "output": "typed"})
        actuator.press_key = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(
            return_value={"app_name": "Safari", "window_title": "Test"}
        )

        coordinator = AsyncMock()
        coordinator.capabilities = MagicMock(return_value=frozenset())

        agent = _make_agent(tmp_path, actuator=actuator, coordinator=coordinator)
        agent._find_element = AsyncMock(return_value=None)

        step = ActionStep(
            action="type_text",
            params={"text": "hello", "element": "search box", "_skip_focus": True},
            verify="text entered",
        )
        result = await agent._dispatch_action(step)
        assert result["success"] is True
        # _find_element should NOT have been called (_skip_focus bypasses it)
        agent._find_element.assert_not_called()
        actuator.type_text.assert_called_once_with("hello")


# ===========================================================================
# 5. Integration: Verifier + browser interchangeability in Tier 0 / Tier 1
# ===========================================================================


class TestVerifierBrowserInterchangeabilityIntegration:
    """Verify that browser interchangeability works end-to-end in Tier 0 and Tier 1."""

    @pytest.mark.unit
    def test_tier1_activate_app_safari_when_chrome_frontmost(self):
        """Tier 1 verify for activate_app('Safari') passes when Chrome is frontmost."""
        verifier = StepVerifier()
        step = ActionStep(
            action="activate_app",
            params={"app_name": "Safari"},
            verify="Safari is frontmost",
        )
        # Mock actuator: Chrome is the frontmost app
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Google Chrome"})

        result = verifier._verify_tier1(step, actuator, {})
        assert result is not None
        passed, evidence = result
        assert passed is True
        assert "Google Chrome" in evidence

    @pytest.mark.unit
    def test_tier1_activate_app_chrome_when_safari_frontmost(self):
        """Tier 1 verify for activate_app('Chrome') passes when Safari is frontmost."""
        verifier = StepVerifier()
        step = ActionStep(
            action="activate_app",
            params={"app_name": "Chrome"},
            verify="Chrome is frontmost",
        )
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})

        result = verifier._verify_tier1(step, actuator, {})
        assert result is not None
        passed, evidence = result
        assert passed is True

    @pytest.mark.unit
    def test_tier1_activate_app_calculator_when_safari_frontmost_fails(self):
        """Tier 1 verify for activate_app('Calculator') fails when Safari is frontmost."""
        verifier = StepVerifier()
        step = ActionStep(
            action="activate_app",
            params={"app_name": "Calculator"},
            verify="Calculator is frontmost",
        )
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})

        result = verifier._verify_tier1(step, actuator, {})
        assert result is not None
        passed, evidence = result
        assert passed is False

    @pytest.mark.unit
    def test_tier0_activate_app_safari_when_arc_frontmost(self):
        """Tier 0 (AX) verify for activate_app('Safari') passes when Arc is frontmost."""
        verifier = StepVerifier()
        step = ActionStep(
            action="activate_app",
            params={"app_name": "Safari"},
            verify="Safari is frontmost",
        )
        accessibility = MagicMock()
        accessibility.get_frontmost_app = MagicMock(return_value={"name": "Arc"})
        accessibility.get_focused_element = MagicMock(return_value=None)

        result = verifier._verify_tier0(step, accessibility)
        assert result is not None
        passed, evidence = result
        assert passed is True
        assert "Arc" in evidence

    @pytest.mark.unit
    def test_tier0_activate_app_finder_when_chrome_frontmost_fails(self):
        """Tier 0: activate_app('Finder') fails when Chrome is frontmost."""
        verifier = StepVerifier()
        step = ActionStep(
            action="activate_app",
            params={"app_name": "Finder"},
            verify="Finder is frontmost",
        )
        accessibility = MagicMock()
        accessibility.get_frontmost_app = MagicMock(
            return_value={"name": "Google Chrome"}
        )
        accessibility.get_focused_element = MagicMock(return_value=None)

        result = verifier._verify_tier0(step, accessibility)
        assert result is not None
        passed, evidence = result
        assert passed is False

    @pytest.mark.unit
    def test_tier1_verify_text_about_app_with_browser_swap(self):
        """Tier 1 verify 'Safari is the active app' passes when Chrome is frontmost."""
        verifier = StepVerifier()
        step = ActionStep(
            action="click",
            params={"app_name": "Safari", "element": "some button"},
            verify="Safari is the active app",
        )
        actuator = MagicMock()
        actuator.get_state = MagicMock(
            return_value={"app_name": "Google Chrome", "page_title": None, "page_heading": None}
        )

        result = verifier._verify_tier1(step, actuator, {})
        assert result is not None
        passed, evidence = result
        assert passed is True


# ===========================================================================
# 6. Actuator._is_browser helper
# ===========================================================================


class TestActuatorIsBrowser:
    """AppleScriptActuator._is_browser static method."""

    @pytest.mark.unit
    def test_safari_is_browser(self):
        from automation_agent.actuator.applescript_actuator import AppleScriptActuator
        assert AppleScriptActuator._is_browser("Safari") is True

    @pytest.mark.unit
    def test_google_chrome_is_browser(self):
        from automation_agent.actuator.applescript_actuator import AppleScriptActuator
        assert AppleScriptActuator._is_browser("Google Chrome") is True

    @pytest.mark.unit
    def test_arc_is_browser(self):
        from automation_agent.actuator.applescript_actuator import AppleScriptActuator
        assert AppleScriptActuator._is_browser("Arc") is True

    @pytest.mark.unit
    def test_calculator_not_browser(self):
        from automation_agent.actuator.applescript_actuator import AppleScriptActuator
        assert AppleScriptActuator._is_browser("Calculator") is False

    @pytest.mark.unit
    def test_empty_not_browser(self):
        from automation_agent.actuator.applescript_actuator import AppleScriptActuator
        assert AppleScriptActuator._is_browser("") is False

    @pytest.mark.unit
    def test_firefox_is_browser(self):
        from automation_agent.actuator.applescript_actuator import AppleScriptActuator
        assert AppleScriptActuator._is_browser("Firefox") is True

    @pytest.mark.unit
    def test_edge_is_browser(self):
        from automation_agent.actuator.applescript_actuator import AppleScriptActuator
        assert AppleScriptActuator._is_browser("Microsoft Edge") is True


# ===========================================================================
# 7. Verifier._is_browser_app helper
# ===========================================================================


class TestVerifierIsBrowserApp:
    """StepVerifier._is_browser_app static method."""

    @pytest.mark.unit
    def test_safari_is_browser(self):
        assert StepVerifier._is_browser_app("Safari") is True

    @pytest.mark.unit
    def test_chrome_is_browser(self):
        assert StepVerifier._is_browser_app("Google Chrome") is True

    @pytest.mark.unit
    def test_calculator_not_browser(self):
        assert StepVerifier._is_browser_app("Calculator") is False

    @pytest.mark.unit
    def test_brave_is_browser(self):
        assert StepVerifier._is_browser_app("Brave") is True

    @pytest.mark.unit
    def test_opera_is_browser(self):
        assert StepVerifier._is_browser_app("Opera") is True
