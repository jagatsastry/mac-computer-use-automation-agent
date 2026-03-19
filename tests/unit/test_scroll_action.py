"""Exhaustive unit tests for the scroll action.

Tests cover all four layers:
1. shared_models — ActionStep validation and aliases
2. actuator — AppleScriptActuator.scroll() with pyautogui mocks
3. orchestrator — _dispatch_action() direction→clicks mapping
4. prompts — plan_from_prompt.md and replan_from_state.md mention scroll
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.config import AgentConfig
from automation_agent.logging.event_logger import EventLogger
from automation_agent.orchestrator.agent import AutomationAgent
from automation_agent.shared_models import ActionPlan, ActionStep, _ACTION_ALIASES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> AgentConfig:
    defaults = {"_env_file": None, "anthropic_api_key": "test-key-not-real"}
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_agent(actuator, logger, config=None):
    """Create an AutomationAgent with minimal mocks for dispatch testing."""
    planner = AsyncMock()
    skill_registry = MagicMock()
    coordinator = AsyncMock()
    if config is None:
        config = _make_config()
    return AutomationAgent(
        planner=planner,
        skill_registry=skill_registry,
        coordinator=coordinator,
        actuator=actuator,
        config=config,
        logger=logger,
    )


def _mock_actuator():
    """Create a mock actuator with scroll method."""
    actuator = MagicMock()
    actuator.is_available = MagicMock(return_value=True)
    actuator.scroll = MagicMock(
        return_value={"success": True, "output": "Scrolled down 3 clicks"}
    )
    actuator.get_state = MagicMock(
        return_value={
            "app_name": "Safari",
            "app_bundle": "com.apple.Safari",
            "window_title": "Google",
        }
    )
    return actuator


# ===========================================================================
# 1. shared_models tests
# ===========================================================================


class TestScrollSharedModels:
    """ActionStep validation and alias resolution for scroll."""

    def test_scroll_is_valid_action(self):
        """ActionStep(action='scroll') should not raise."""
        step = ActionStep(
            action="scroll", params={"direction": "down"}, verify="Page scrolled"
        )
        assert step.action == "scroll"

    def test_scroll_down_alias_via_from_dict(self):
        """from_dict should alias 'scroll_down' -> 'scroll'."""
        step = ActionStep.from_dict(
            {"action": "scroll_down", "params": {}, "verify": "scrolled"}
        )
        assert step.action == "scroll"

    def test_scroll_up_alias_via_from_dict(self):
        """from_dict should alias 'scroll_up' -> 'scroll'."""
        step = ActionStep.from_dict(
            {"action": "scroll_up", "params": {}, "verify": "scrolled"}
        )
        assert step.action == "scroll"

    def test_scroll_down_direct_is_invalid(self):
        """Using 'scroll_down' directly (not via from_dict alias) should raise."""
        with pytest.raises(ValueError, match="Unknown action 'scroll_down'"):
            ActionStep(action="scroll_down", params={}, verify="x")

    def test_scroll_up_direct_is_invalid(self):
        """Using 'scroll_up' directly (not via from_dict alias) should raise."""
        with pytest.raises(ValueError, match="Unknown action 'scroll_up'"):
            ActionStep(action="scroll_up", params={}, verify="x")

    def test_scroll_aliases_in_alias_dict(self):
        """_ACTION_ALIASES must map scroll_down and scroll_up to scroll."""
        assert _ACTION_ALIASES["scroll_down"] == "scroll"
        assert _ACTION_ALIASES["scroll_up"] == "scroll"

    def test_scroll_step_with_all_params(self):
        """ActionStep with full scroll params should validate."""
        step = ActionStep(
            action="scroll",
            params={"direction": "up", "amount": 5, "x": 100, "y": 200},
            verify="Scrolled up",
        )
        assert step.params["direction"] == "up"
        assert step.params["amount"] == 5
        assert step.params["x"] == 100

    def test_scroll_step_empty_verify_fails_plan_validation(self):
        """Plan validation rejects scroll steps with empty verify."""
        plan = ActionPlan(
            steps=[ActionStep(action="scroll", params={"direction": "down"}, verify="")],
            goal="Scroll page",
        )
        errors = plan.validate()
        assert len(errors) == 1
        assert "verify" in errors[0].lower()

    def test_scroll_step_with_verify_passes_plan_validation(self):
        """Plan validation accepts scroll steps with non-empty verify."""
        plan = ActionPlan(
            steps=[
                ActionStep(
                    action="scroll",
                    params={"direction": "down"},
                    verify="Page scrolled down",
                ),
                ActionStep(action="done", params={}, verify=""),
            ],
            goal="Scroll page",
        )
        errors = plan.validate()
        assert errors == []

    def test_scroll_down_alias_injects_direction(self):
        """from_dict(scroll_down) should alias to scroll AND set direction=down."""
        step = ActionStep.from_dict(
            {"action": "scroll_down", "params": {}, "verify": "scrolled"}
        )
        assert step.action == "scroll"
        assert step.params.get("direction") == "down"

    def test_scroll_up_alias_injects_direction(self):
        """from_dict(scroll_up) should alias to scroll AND set direction=up."""
        step = ActionStep.from_dict(
            {"action": "scroll_up", "params": {}, "verify": "scrolled"}
        )
        assert step.action == "scroll"
        assert step.params.get("direction") == "up"

    def test_scroll_alias_does_not_override_explicit_direction(self):
        """If params already has direction, alias should not override it."""
        step = ActionStep.from_dict(
            {"action": "scroll_down", "params": {"direction": "left"}, "verify": "scrolled"}
        )
        assert step.action == "scroll"
        assert step.params["direction"] == "left"


# ===========================================================================
# 2. Actuator tests
# ===========================================================================


class TestScrollActuator:
    """AppleScriptActuator.scroll() with pyautogui mocked."""

    @patch("pyautogui.scroll")
    def test_scroll_down(self, mock_scroll):
        from automation_agent.actuator.applescript_actuator import AppleScriptActuator

        actuator = AppleScriptActuator()
        result = actuator.scroll(clicks=-3)
        mock_scroll.assert_called_once_with(-3, x=None, y=None)
        assert result["success"] is True
        assert "down" in result["output"]

    @patch("pyautogui.scroll")
    def test_scroll_up(self, mock_scroll):
        from automation_agent.actuator.applescript_actuator import AppleScriptActuator

        actuator = AppleScriptActuator()
        result = actuator.scroll(clicks=3)
        mock_scroll.assert_called_once_with(3, x=None, y=None)
        assert result["success"] is True
        assert "up" in result["output"]

    @patch("pyautogui.hscroll")
    def test_scroll_horizontal_right(self, mock_hscroll):
        from automation_agent.actuator.applescript_actuator import AppleScriptActuator

        actuator = AppleScriptActuator()
        result = actuator.scroll(clicks=3, horizontal=True)
        mock_hscroll.assert_called_once_with(3, x=None, y=None)
        assert result["success"] is True
        assert "right" in result["output"]

    @patch("pyautogui.hscroll")
    def test_scroll_horizontal_left(self, mock_hscroll):
        from automation_agent.actuator.applescript_actuator import AppleScriptActuator

        actuator = AppleScriptActuator()
        result = actuator.scroll(clicks=-3, horizontal=True)
        mock_hscroll.assert_called_once_with(-3, x=None, y=None)
        assert result["success"] is True
        assert "left" in result["output"]

    @patch("pyautogui.scroll")
    def test_scroll_with_coordinates(self, mock_scroll):
        from automation_agent.actuator.applescript_actuator import AppleScriptActuator

        actuator = AppleScriptActuator()
        result = actuator.scroll(clicks=-5, x=100, y=200)
        mock_scroll.assert_called_once_with(-5, x=100, y=200)
        assert result["success"] is True

    @patch("pyautogui.hscroll")
    def test_scroll_horizontal_with_coordinates(self, mock_hscroll):
        from automation_agent.actuator.applescript_actuator import AppleScriptActuator

        actuator = AppleScriptActuator()
        result = actuator.scroll(clicks=2, x=300, y=400, horizontal=True)
        mock_hscroll.assert_called_once_with(2, x=300, y=400)
        assert result["success"] is True

    @patch("pyautogui.scroll", side_effect=Exception("display not available"))
    def test_scroll_failure(self, mock_scroll):
        from automation_agent.actuator.applescript_actuator import AppleScriptActuator

        actuator = AppleScriptActuator()
        result = actuator.scroll(clicks=-3)
        assert result["success"] is False
        assert "display not available" in result["error"]

    @patch("pyautogui.scroll")
    def test_scroll_restores_failsafe(self, mock_scroll):
        import pyautogui

        from automation_agent.actuator.applescript_actuator import AppleScriptActuator

        # Set a known value and verify it's restored after scroll
        pyautogui.FAILSAFE = True
        actuator = AppleScriptActuator()
        actuator.scroll(clicks=-1)
        assert pyautogui.FAILSAFE is True

    @patch("pyautogui.scroll")
    def test_scroll_click_count_in_output(self, mock_scroll):
        from automation_agent.actuator.applescript_actuator import AppleScriptActuator

        actuator = AppleScriptActuator()
        result = actuator.scroll(clicks=-7)
        assert "7" in result["output"]

    @patch("pyautogui.scroll")
    def test_scroll_zero_clicks(self, mock_scroll):
        """Scrolling zero clicks should still succeed (edge case)."""
        from automation_agent.actuator.applescript_actuator import AppleScriptActuator

        actuator = AppleScriptActuator()
        result = actuator.scroll(clicks=0)
        mock_scroll.assert_called_once_with(0, x=None, y=None)
        assert result["success"] is True
        # clicks=0 is not > 0, so direction label is "down"; 0 clicks reported
        assert "0" in result["output"]

    @patch("pyautogui.hscroll", side_effect=Exception("hscroll unavailable"))
    def test_hscroll_failure(self, mock_hscroll):
        """Horizontal scroll failure should return success=False with error."""
        from automation_agent.actuator.applescript_actuator import AppleScriptActuator

        actuator = AppleScriptActuator()
        result = actuator.scroll(clicks=3, horizontal=True)
        assert result["success"] is False
        assert "hscroll unavailable" in result["error"]

    @patch("pyautogui.scroll")
    def test_scroll_large_amount(self, mock_scroll):
        """Large scroll amounts pass through to pyautogui unclamped."""
        from automation_agent.actuator.applescript_actuator import AppleScriptActuator

        actuator = AppleScriptActuator()
        result = actuator.scroll(clicks=-99999)
        mock_scroll.assert_called_once_with(-99999, x=None, y=None)
        assert result["success"] is True
        assert "99999" in result["output"]


# ===========================================================================
# 3. Orchestrator dispatch tests
# ===========================================================================


class TestScrollOrchestratorDispatch:
    """_dispatch_action() maps direction and amount to actuator.scroll() calls."""

    async def test_dispatch_scroll_down(self, tmp_log_dir):
        """direction='down', amount=3 -> clicks=-3, horizontal=False."""
        actuator = _mock_actuator()
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(actuator, logger)

        step = ActionStep(
            action="scroll",
            params={"direction": "down", "amount": 3},
            verify="scrolled",
        )
        result = await agent._dispatch_action(step)
        actuator.scroll.assert_called_once_with(-3, x=None, y=None)
        assert result["success"] is True

    async def test_dispatch_scroll_up(self, tmp_log_dir):
        """direction='up', amount=5 -> clicks=5, horizontal=False."""
        actuator = _mock_actuator()
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(actuator, logger)

        step = ActionStep(
            action="scroll",
            params={"direction": "up", "amount": 5},
            verify="scrolled",
        )
        await agent._dispatch_action(step)
        actuator.scroll.assert_called_once_with(5, x=None, y=None)

    async def test_dispatch_scroll_default_amount(self, tmp_log_dir):
        """When amount is not specified, default to 3."""
        actuator = _mock_actuator()
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(actuator, logger)

        step = ActionStep(
            action="scroll",
            params={"direction": "down"},
            verify="scrolled",
        )
        await agent._dispatch_action(step)
        actuator.scroll.assert_called_once_with(-3, x=None, y=None)

    async def test_dispatch_scroll_left(self, tmp_log_dir):
        """direction='left', amount=2 -> clicks=-2, horizontal=True."""
        actuator = _mock_actuator()
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(actuator, logger)

        step = ActionStep(
            action="scroll",
            params={"direction": "left", "amount": 2},
            verify="scrolled",
        )
        await agent._dispatch_action(step)
        actuator.scroll.assert_called_once_with(-2, x=None, y=None, horizontal=True)

    async def test_dispatch_scroll_right(self, tmp_log_dir):
        """direction='right', amount=2 -> clicks=2, horizontal=True."""
        actuator = _mock_actuator()
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(actuator, logger)

        step = ActionStep(
            action="scroll",
            params={"direction": "right", "amount": 2},
            verify="scrolled",
        )
        await agent._dispatch_action(step)
        actuator.scroll.assert_called_once_with(2, x=None, y=None, horizontal=True)

    async def test_dispatch_scroll_with_coordinates(self, tmp_log_dir):
        """Scroll dispatch passes x, y coordinates through."""
        actuator = _mock_actuator()
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(actuator, logger)

        step = ActionStep(
            action="scroll",
            params={"direction": "down", "amount": 3, "x": 500, "y": 300},
            verify="scrolled",
        )
        await agent._dispatch_action(step)
        actuator.scroll.assert_called_once_with(-3, x=500, y=300)

    async def test_dispatch_scroll_horizontal_with_coordinates(self, tmp_log_dir):
        """Horizontal scroll dispatch passes x, y coordinates through."""
        actuator = _mock_actuator()
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(actuator, logger)

        step = ActionStep(
            action="scroll",
            params={"direction": "right", "amount": 4, "x": 200, "y": 100},
            verify="scrolled",
        )
        await agent._dispatch_action(step)
        actuator.scroll.assert_called_once_with(4, x=200, y=100, horizontal=True)

    async def test_dispatch_scroll_default_direction_is_down(self, tmp_log_dir):
        """When direction is not specified, default to 'down'."""
        actuator = _mock_actuator()
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(actuator, logger)

        step = ActionStep(
            action="scroll",
            params={},
            verify="scrolled",
        )
        await agent._dispatch_action(step)
        # Default direction "down", default amount 3 -> clicks=-3
        actuator.scroll.assert_called_once_with(-3, x=None, y=None)

    async def test_dispatch_scroll_string_amount_converted(self, tmp_log_dir):
        """Amount passed as string (from JSON) should be converted to int."""
        actuator = _mock_actuator()
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(actuator, logger)

        step = ActionStep(
            action="scroll",
            params={"direction": "up", "amount": "7"},
            verify="scrolled",
        )
        await agent._dispatch_action(step)
        actuator.scroll.assert_called_once_with(7, x=None, y=None)

    async def test_dispatch_scroll_negative_amount_down(self, tmp_log_dir):
        """Negative amount with direction='down' should still scroll down.

        abs() normalizes the amount so direction is the sole authority.
        """
        actuator = _mock_actuator()
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(actuator, logger)

        step = ActionStep(
            action="scroll",
            params={"direction": "down", "amount": -5},
            verify="scrolled",
        )
        await agent._dispatch_action(step)
        # abs(-5) = 5, direction=down -> clicks=-5
        actuator.scroll.assert_called_once_with(-5, x=None, y=None)

    async def test_dispatch_scroll_large_amount(self, tmp_log_dir):
        """Large scroll amounts pass through unclamped."""
        actuator = _mock_actuator()
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(actuator, logger)

        step = ActionStep(
            action="scroll",
            params={"direction": "down", "amount": 99999},
            verify="scrolled",
        )
        await agent._dispatch_action(step)
        actuator.scroll.assert_called_once_with(-99999, x=None, y=None)

    async def test_dispatch_vertical_scroll_no_horizontal_kwarg(self, tmp_log_dir):
        """Vertical scroll dispatch must NOT pass horizontal=True."""
        actuator = _mock_actuator()
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(actuator, logger)

        step = ActionStep(
            action="scroll",
            params={"direction": "down", "amount": 3},
            verify="scrolled",
        )
        await agent._dispatch_action(step)
        # Verify horizontal kwarg was NOT passed
        call_kwargs = actuator.scroll.call_args
        assert "horizontal" not in call_kwargs.kwargs

    async def test_dispatch_scroll_non_numeric_amount_defaults_to_3(self, tmp_log_dir):
        """Non-numeric amount string (from LLM) should fall back to 3."""
        actuator = _mock_actuator()
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(actuator, logger)

        step = ActionStep(
            action="scroll",
            params={"direction": "down", "amount": "lots"},
            verify="scrolled",
        )
        await agent._dispatch_action(step)
        # "lots" can't be int()-ed, falls back to 3; direction=down -> clicks=-3
        actuator.scroll.assert_called_once_with(-3, x=None, y=None)

    async def test_dispatch_scroll_empty_string_amount_defaults_to_3(self, tmp_log_dir):
        """Empty string amount should fall back to 3."""
        actuator = _mock_actuator()
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(actuator, logger)

        step = ActionStep(
            action="scroll",
            params={"direction": "up", "amount": ""},
            verify="scrolled",
        )
        await agent._dispatch_action(step)
        # "" can't be int()-ed, falls back to 3; direction=up -> clicks=3
        actuator.scroll.assert_called_once_with(3, x=None, y=None)

    async def test_dispatch_scroll_float_amount_truncated(self, tmp_log_dir):
        """Float amount should be truncated to int via int()."""
        actuator = _mock_actuator()
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(actuator, logger)

        step = ActionStep(
            action="scroll",
            params={"direction": "down", "amount": 3.7},
            verify="scrolled",
        )
        await agent._dispatch_action(step)
        # int(3.7) = 3, abs(3) = 3, direction=down -> clicks=-3
        actuator.scroll.assert_called_once_with(-3, x=None, y=None)

    async def test_dispatch_scroll_unknown_direction_defaults_vertical(self, tmp_log_dir):
        """Unknown direction (e.g., 'diagonal') falls through to vertical path.

        Documents current behavior: unknown direction is treated as down
        (clicks = -amount, no horizontal flag).
        """
        actuator = _mock_actuator()
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(actuator, logger)

        step = ActionStep(
            action="scroll",
            params={"direction": "diagonal", "amount": 3},
            verify="scrolled",
        )
        await agent._dispatch_action(step)
        # "diagonal" is not "up" -> clicks = -3; not in ("left","right") -> vertical
        actuator.scroll.assert_called_once_with(-3, x=None, y=None)


# ===========================================================================
# 4. Prompt tests
# ===========================================================================


class TestScrollPrompts:
    """Planner prompts must document the scroll action."""

    # Anchor prompt paths to project root via __file__ so tests work from any CWD
    _PROMPTS_DIR = (
        Path(__file__).resolve().parent.parent.parent
        / "src"
        / "automation_agent"
        / "planner"
        / "prompts"
    )

    def test_plan_prompt_mentions_scroll(self):
        text = (self._PROMPTS_DIR / "plan_from_prompt.md").read_text()
        assert "scroll" in text.lower()
        assert "direction" in text

    def test_plan_prompt_scroll_params(self):
        """plan_from_prompt.md should document scroll params: direction, amount."""
        text = (self._PROMPTS_DIR / "plan_from_prompt.md").read_text()
        assert "direction" in text
        assert "amount" in text

    def test_plan_prompt_scroll_directions(self):
        """plan_from_prompt.md should list all four scroll directions."""
        text = (self._PROMPTS_DIR / "plan_from_prompt.md").read_text()
        for direction in ("up", "down", "left", "right"):
            assert direction in text

    def test_replan_prompt_mentions_scroll(self):
        """replan_from_state.md must document scroll as an available action."""
        text = (self._PROMPTS_DIR / "replan_from_state.md").read_text()
        assert "scroll" in text.lower()

    def test_replan_prompt_has_available_actions(self):
        """replan_from_state.md must have an Available Actions section."""
        text = (self._PROMPTS_DIR / "replan_from_state.md").read_text()
        assert "Available Actions" in text

    def test_replan_prompt_scroll_params(self):
        """replan_from_state.md should document scroll params: direction, amount."""
        text = (self._PROMPTS_DIR / "replan_from_state.md").read_text()
        assert "direction" in text
        assert "amount" in text

    def test_replan_available_actions_match_plan(self):
        """replan_from_state.md Available Actions must match plan_from_prompt.md exactly."""
        import re

        plan_text = (self._PROMPTS_DIR / "plan_from_prompt.md").read_text()
        replan_text = (self._PROMPTS_DIR / "replan_from_state.md").read_text()

        def _extract_actions_section(text):
            match = re.search(
                r"## Available Actions\n((?:- .+\n)+)", text
            )
            assert match, "Available Actions section not found"
            return match.group(1)

        plan_actions = _extract_actions_section(plan_text)
        replan_actions = _extract_actions_section(replan_text)
        # Replan may have extra guidance (e.g., type_text verify clarification)
        # so check that all plan action names are present in replan
        for line in plan_actions.strip().split("\n"):
            action_name = line.split("`")[1] if "`" in line else line[:30]
            assert action_name in replan_actions, (
                f"Plan action '{action_name}' missing from replan actions"
            )
