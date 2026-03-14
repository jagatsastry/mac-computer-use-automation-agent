"""Tests for P1-3: Scroll verification via JavaScript scrollY.

Tests cover:
- Tier S1: JS scrollY delta confirms scroll direction
- Tier S2: Screenshot pixel-diff fallback
- Tier S3: Actuator success fallback
- get_scroll_position behavior for non-browser apps and timeouts
- AppleScript escaping (_escape_for_applescript)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import subprocess

import pytest

from automation_agent.actuator.applescript_actuator import AppleScriptActuator
from automation_agent.config import AgentConfig
from automation_agent.orchestrator.verifier import StepVerifier
from automation_agent.shared_models import ActionStep


def _make_config(**overrides) -> AgentConfig:
    defaults = {
        "_env_file": None,
        "anthropic_api_key": "test-key-not-real",
        "model_provider": "local",
        "grounding_model": "",
        "grounding_server_url": "",
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


# ---------------------------------------------------------------------------
# get_scroll_position tests
# ---------------------------------------------------------------------------


class TestGetScrollPosition:
    """Tests for AppleScriptActuator.get_scroll_position()."""

    def test_safari_returns_scrolly(self):
        """get_scroll_position returns int for Safari."""
        actuator = AppleScriptActuator()
        with patch.object(actuator, "get_state", return_value={"app_name": "Safari"}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout="500\n", stderr=""
                )
                result = actuator.get_scroll_position()
        assert result == 500

    def test_chrome_returns_scrolly(self):
        """get_scroll_position returns int for Google Chrome."""
        actuator = AppleScriptActuator()
        with patch.object(
            actuator, "get_state", return_value={"app_name": "Google Chrome"}
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout="1200\n", stderr=""
                )
                result = actuator.get_scroll_position()
        assert result == 1200

    def test_non_browser_returns_none(self):
        """get_scroll_position returns None for non-browser apps."""
        actuator = AppleScriptActuator()
        with patch.object(
            actuator, "get_state", return_value={"app_name": "Finder"}
        ):
            result = actuator.get_scroll_position()
        assert result is None

    def test_timeout_returns_none(self):
        """get_scroll_position returns None on timeout."""
        actuator = AppleScriptActuator()
        with patch.object(actuator, "get_state", return_value={"app_name": "Safari"}):
            with patch(
                "subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 3)
            ):
                result = actuator.get_scroll_position()
        assert result is None

    def test_error_returns_none(self):
        """get_scroll_position returns None on osascript error."""
        actuator = AppleScriptActuator()
        with patch.object(actuator, "get_state", return_value={"app_name": "Safari"}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=1, stdout="", stderr="error"
                )
                result = actuator.get_scroll_position()
        assert result is None

    def test_permission_denied_returns_none(self):
        """Safari JS permission error -> returns None (not crash)."""
        actuator = AppleScriptActuator()
        with patch.object(actuator, "get_state", return_value={"app_name": "Safari"}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=1,
                    stdout="",
                    stderr=(
                        "execution error: Safari got an error: "
                        "Allow JavaScript from Apple Events is not enabled. "
                        "(-1743)"
                    ),
                )
                result = actuator.get_scroll_position()
        assert result is None


# ---------------------------------------------------------------------------
# Scroll verification in verifier (Tier S1, S2, S3)
# ---------------------------------------------------------------------------


class TestScrollVerification:
    """Tests for scroll verification tiers in StepVerifier._verify_tier1."""

    def _make_scroll_step(self, direction="down"):
        return ActionStep(
            action="scroll",
            params={"direction": direction, "amount": 3},
            verify="Page scrolled " + direction,
        )

    def test_scroll_down_js_confirmed(self):
        """scrollY 0->500 -> Tier S1 pass for scroll down."""
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})
        actuator.get_scroll_position = MagicMock(return_value=500)

        verifier = StepVerifier(actuator=actuator)
        step = self._make_scroll_step("down")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={"success": True, "_scroll_before": {"axis": "y", "value": 0}},
        )
        assert result is not None
        assert result[0] is True
        assert "scroll" in result[1].lower()

    def test_scroll_up_js_confirmed(self):
        """scrollY 500->200 -> Tier S1 pass for scroll up."""
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})
        actuator.get_scroll_position = MagicMock(return_value=200)

        verifier = StepVerifier(actuator=actuator)
        step = self._make_scroll_step("up")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={"success": True, "_scroll_before": {"axis": "y", "value": 500}},
        )
        assert result is not None
        assert result[0] is True

    def test_scroll_boundary_falls_to_pixel(self):
        """delta=0, pixel_changed=True -> Tier S2 pass."""
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})
        actuator.get_scroll_position = MagicMock(return_value=0)

        verifier = StepVerifier(actuator=actuator)
        step = self._make_scroll_step("down")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={
                "success": True,
                "_scroll_before": {"axis": "y", "value": 0},
                "_scroll_pixel_changed": True,
            },
        )
        assert result is not None
        assert result[0] is True
        assert "pixel" in result[1].lower() or "screenshot" in result[1].lower()

    def test_scroll_no_js_falls_to_pixel(self):
        """get_scroll_position=None, pixel_changed=True -> Tier S2."""
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Finder"})
        actuator.get_scroll_position = MagicMock(return_value=None)

        verifier = StepVerifier(actuator=actuator)
        step = self._make_scroll_step("down")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={
                "success": True,
                "_scroll_pixel_changed": True,
            },
        )
        assert result is not None
        assert result[0] is True

    def test_scroll_no_signal_actuator_fallback(self):
        """No JS, no pixel diff, actuator success -> Tier S3 pass."""
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Finder"})
        actuator.get_scroll_position = MagicMock(return_value=None)

        verifier = StepVerifier(actuator=actuator)
        step = self._make_scroll_step("down")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={
                "success": True,
                "_scroll_pixel_changed": None,
            },
        )
        assert result is not None
        assert result[0] is True
        assert "actuator" in result[1].lower()

    def test_scroll_no_screenshot_diff(self):
        """screenshot_diff=None -> Tier S2 skipped, falls to Tier S3."""
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Finder"})
        actuator.get_scroll_position = MagicMock(return_value=None)

        verifier = StepVerifier(actuator=actuator)
        step = self._make_scroll_step("down")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={
                "success": True,
                # No _scroll_before or _scroll_pixel_changed key at all
            },
        )
        assert result is not None
        assert result[0] is True

    def test_scroll_hammerspoon_skips_s1(self):
        """Actuator without get_scroll_position -> Tier S1 skipped."""
        actuator = MagicMock(spec=["get_state", "scroll"])
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})
        # No get_scroll_position attribute

        verifier = StepVerifier(actuator=actuator)
        step = self._make_scroll_step("down")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={
                "success": True,
                "_scroll_pixel_changed": True,
            },
        )
        assert result is not None
        assert result[0] is True

    def test_scroll_wrong_direction(self):
        """Scroll down but delta < 0 -> S1 inconclusive, falls to S2/S3."""
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})
        # scrollY went from 500 to 400 (wrong direction for "down")
        actuator.get_scroll_position = MagicMock(return_value=400)

        verifier = StepVerifier(actuator=actuator)
        step = self._make_scroll_step("down")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={
                "success": True,
                "_scroll_before": {"axis": "y", "value": 500},
                "_scroll_pixel_changed": True,
            },
        )
        # S1 is inconclusive (wrong direction), falls to S2 (pixel diff)
        assert result is not None
        assert result[0] is True
        assert "pixel" in result[1].lower() or "screenshot" in result[1].lower()

    def test_scroll_pixel_diff_false_positive(self):
        """pixel_changed=True but scrollY delta=0 (animation) -> S2 pass."""
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})
        # scrollY stayed at 0 (at boundary), but pixels changed (CSS animation)
        actuator.get_scroll_position = MagicMock(return_value=0)

        verifier = StepVerifier(actuator=actuator)
        step = self._make_scroll_step("down")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={
                "success": True,
                "_scroll_before": {"axis": "y", "value": 0},
                "_scroll_pixel_changed": True,
            },
        )
        assert result is not None
        assert result[0] is True
        assert "pixel" in result[1].lower() or "screenshot" in result[1].lower()

    def test_scroll_at_bottom_boundary(self):
        """At page bottom: delta=0, no pixel change -> S3 actuator fallback.

        When the user is at the bottom and scrolls down, scrollY stays
        the same (delta=0) and pixels don't change. S1 is inconclusive,
        S2 has no signal, S3 returns True because the actuator command
        was successfully executed. This is the intended behavior: the
        scroll action was performed (pyautogui.scroll fired), even though
        the page didn't move. Failing it would trigger unnecessary
        retries for a legitimate boundary condition.
        """
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})
        # scrollY stayed at 5000 (page bottom)
        actuator.get_scroll_position = MagicMock(return_value=5000)

        verifier = StepVerifier(actuator=actuator)
        step = self._make_scroll_step("down")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={
                "success": True,
                "_scroll_before": {"axis": "y", "value": 5000},
                "_scroll_pixel_changed": False,
            },
        )
        # S3 fallback: actuator success accepted
        assert result is not None
        assert result[0] is True
        assert "actuator" in result[1].lower()


# ---------------------------------------------------------------------------
# Scroll data capture in agent
# ---------------------------------------------------------------------------


class TestScrollDataCapture:
    """Tests that scroll before/after data is captured in actuator_result."""

    @pytest.mark.asyncio
    async def test_scroll_before_captured(self):
        """Result contains _scroll_before structured dict from pre-scroll capture."""
        from automation_agent.orchestrator.agent import AutomationAgent

        planner = AsyncMock()
        skill_registry = MagicMock()
        skill_registry.match = AsyncMock(return_value=None)
        skill_registry.learn_from_run = AsyncMock(return_value=[])
        skill_registry.promote_from_run = AsyncMock(return_value=None)
        coordinator = AsyncMock()
        coordinator.capabilities = MagicMock(return_value=frozenset())
        coordinator.capture_screenshot = AsyncMock(return_value="base64data")
        actuator = MagicMock()
        actuator.scroll = MagicMock(
            return_value={"success": True, "output": "Scrolled down 3"}
        )
        actuator.get_scroll_position = MagicMock(return_value=100)
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})
        config = _make_config()

        agent = AutomationAgent(
            planner, skill_registry, coordinator, actuator, config
        )

        step = ActionStep(
            action="scroll",
            params={"direction": "down", "amount": 3},
            verify="Page scrolled down",
        )

        result = await agent._dispatch_action(step)
        assert result.get("_scroll_before") == {"axis": "y", "value": 100}

    @pytest.mark.asyncio
    async def test_scroll_pixel_diff_captured(self):
        """Result contains _scroll_pixel_changed when screenshot_diff exists."""
        from automation_agent.orchestrator.agent import AutomationAgent

        planner = AsyncMock()
        skill_registry = MagicMock()
        skill_registry.match = AsyncMock(return_value=None)
        skill_registry.learn_from_run = AsyncMock(return_value=[])
        skill_registry.promote_from_run = AsyncMock(return_value=None)
        coordinator = AsyncMock()
        coordinator.capabilities = MagicMock(return_value=frozenset())
        coordinator.capture_screenshot = AsyncMock(return_value="base64data")
        actuator = MagicMock()
        actuator.scroll = MagicMock(
            return_value={"success": True, "output": "Scrolled down 3"}
        )
        actuator.get_scroll_position = MagicMock(return_value=None)
        actuator.get_state = MagicMock(return_value={"app_name": "Finder"})
        config = _make_config()

        agent = AutomationAgent(
            planner, skill_registry, coordinator, actuator, config
        )

        screenshot_diff = MagicMock()
        screenshot_diff.capture_before = MagicMock()
        screenshot_diff.screen_changed = MagicMock(return_value=True)
        agent.screenshot_diff = screenshot_diff

        step = ActionStep(
            action="scroll",
            params={"direction": "down", "amount": 3},
            verify="Page scrolled down",
        )

        result = await agent._dispatch_action(step)
        assert result.get("_scroll_pixel_changed") is True


# ---------------------------------------------------------------------------
# AppleScript escaping
# ---------------------------------------------------------------------------


class TestEscapeForAppleScript:
    """Tests for _escape_for_applescript static method."""

    def test_escape_double_quote(self):
        result = AppleScriptActuator._escape_for_applescript('hello "world"')
        assert result == 'hello \\"world\\"'

    def test_escape_backslash(self):
        result = AppleScriptActuator._escape_for_applescript("path\\to\\file")
        assert result == "path\\\\to\\\\file"

    def test_strip_newline(self):
        result = AppleScriptActuator._escape_for_applescript("line1\nline2")
        assert result == "line1line2"

    def test_tab_to_space(self):
        result = AppleScriptActuator._escape_for_applescript("col1\tcol2")
        assert result == "col1 col2"

    def test_strip_carriage_return(self):
        result = AppleScriptActuator._escape_for_applescript("line1\rline2")
        assert result == "line1line2"

    def test_no_escape_needed(self):
        result = AppleScriptActuator._escape_for_applescript("hello world")
        assert result == "hello world"

    def test_combined_special_chars(self):
        result = AppleScriptActuator._escape_for_applescript(
            'say "hi"\nand\\bye\r\t'
        )
        assert result == 'say \\"hi\\"and\\\\bye '
