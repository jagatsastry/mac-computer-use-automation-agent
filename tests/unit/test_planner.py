"""Unit tests for the ActionPlannerImpl component.

All tests mock the _call_llm method — no real API calls are made.
"""

import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from automation_agent.config import AgentConfig
from automation_agent.planner.planner import ActionPlannerImpl
from automation_agent.shared_models import ActionPlan, ActionStep, StepResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config():
    """Create a test AgentConfig."""
    return AgentConfig(_env_file=None, anthropic_api_key="test-key-not-real")


@pytest.fixture
def planner(config):
    """Create an ActionPlannerImpl with test config."""
    return ActionPlannerImpl(config)


def _make_llm_response(steps_data: list, wrap_in_markdown: bool = False) -> dict:
    """Helper: build a mock LLM response dict from steps data."""
    payload = json.dumps({"steps": steps_data})
    if wrap_in_markdown:
        content = f"Here is the plan:\n```json\n{payload}\n```\n"
    else:
        content = payload
    return {
        "content": content,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }


VALID_STEPS = [
    {
        "action": "activate_app",
        "params": {"app_name": "Calculator"},
        "verify": "Calculator is the frontmost application",
        "on_fail": "retry_different",
        "max_retries": 3,
    },
    {
        "action": "done",
        "params": {},
        "verify": "",
        "on_fail": "abort",
    },
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPlanBasic:
    """Tests for the plan() method — basic happy-path scenarios."""

    async def test_plan_returns_action_plan_with_verify_fields(self, planner):
        """1. plan() returns ActionPlan with all steps having verify fields."""
        planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))

        plan = await planner.plan("Open Calculator")

        assert isinstance(plan, ActionPlan)
        assert len(plan.steps) == 2
        assert plan.steps[0].verify == "Calculator is the frontmost application"
        assert plan.goal == "Open Calculator"
        # done step is allowed to have empty verify
        assert plan.steps[1].action == "done"

    async def test_plan_with_skill_context(self, planner):
        """2. plan() with skill context injects context into LLM prompt."""
        planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))

        await planner.plan(
            "Open Calculator", skill_context="Use Spotlight to launch apps"
        )

        # Verify the prompt that was sent to the LLM contains the skill context
        call_args = planner._call_llm.call_args
        prompt = call_args[0][0]
        assert "Use Spotlight to launch apps" in prompt

    async def test_plan_without_skill_context(self, planner):
        """3. plan() without skill context works and uses default text."""
        planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))

        await planner.plan("Open Calculator")

        call_args = planner._call_llm.call_args
        prompt = call_args[0][0]
        assert "No skill context available" in prompt

    async def test_plan_rejects_empty_verify(self, planner):
        """4. plan() rejects LLM response where any step has empty verify."""
        steps_missing_verify = [
            {
                "action": "activate_app",
                "params": {"app_name": "Calculator"},
                "verify": "",  # Invalid — non-terminal step with empty verify
                "on_fail": "retry_different",
            },
            {
                "action": "done",
                "params": {},
                "verify": "",
                "on_fail": "abort",
            },
        ]
        planner._call_llm = AsyncMock(
            return_value=_make_llm_response(steps_missing_verify)
        )

        with pytest.raises(ValueError, match="Plan validation failed"):
            await planner.plan("Open Calculator")

    async def test_plan_measures_token_usage(self, planner):
        """12. plan() measures and returns token usage in plan."""
        planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))

        plan = await planner.plan("Open Calculator")

        assert plan.token_usage is not None
        assert plan.token_usage["input_tokens"] == 100
        assert plan.token_usage["output_tokens"] == 50

    async def test_plan_with_screen_description(self, planner):
        """14. plan() with screen_description populates prompt."""
        planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))

        await planner.plan("Open Calculator", screen_description="Desktop with Dock visible")

        call_args = planner._call_llm.call_args
        prompt = call_args[0][0]
        assert "Desktop with Dock visible" in prompt
        # Ensure the default fallback is NOT present
        assert "Not available" not in prompt


class TestReplan:
    """Tests for the replan() method."""

    async def test_replan_receives_history_and_screen(self, planner):
        """5. replan() receives history + screen description."""
        planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))

        history = [
            StepResult(
                step=ActionStep(
                    action="activate_app",
                    params={"app_name": "Calculator"},
                    verify="Calculator is frontmost",
                ),
                success=False,
                evidence="Calculator did not open",
                verification_method="vision",
            ),
        ]

        plan = await planner.replan(
            "Open Calculator",
            "Desktop showing Finder",
            history,
            ["click_center"],
        )

        assert isinstance(plan, ActionPlan)
        # Verify the prompt contains history info
        call_args = planner._call_llm.call_args
        prompt = call_args[0][0]
        assert "Calculator did not open" in prompt
        assert "Desktop showing Finder" in prompt

    async def test_replan_includes_retry_strategies(self, planner):
        """6. replan() includes retry strategies used in prompt."""
        planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))

        history = [
            StepResult(
                step=ActionStep(
                    action="click",
                    params={"element": "button"},
                    verify="Button clicked",
                ),
                success=False,
                evidence="Button not found",
                verification_method="vision",
            ),
        ]

        await planner.replan(
            "Click the button",
            "Screen with form",
            history,
            ["click_center", "keyboard_shortcut"],
        )

        call_args = planner._call_llm.call_args
        prompt = call_args[0][0]
        assert "click_center" in prompt
        assert "keyboard_shortcut" in prompt


class TestParsingErrors:
    """Tests for error handling in LLM response parsing."""

    async def test_malformed_json_raises_value_error(self, planner):
        """7. Malformed JSON from LLM raises ValueError."""
        planner._call_llm = AsyncMock(
            return_value={
                "content": "This is not JSON at all {{{",
                "usage": {"input_tokens": 50, "output_tokens": 10},
            }
        )

        with pytest.raises(ValueError, match="Failed to parse LLM response as JSON"):
            await planner.plan("Open Calculator")

    async def test_empty_steps_raises_value_error(self, planner):
        """8. Empty steps from LLM raises ValueError."""
        planner._call_llm = AsyncMock(
            return_value={
                "content": json.dumps({"steps": []}),
                "usage": {"input_tokens": 50, "output_tokens": 10},
            }
        )

        with pytest.raises(ValueError, match="LLM returned empty steps list"):
            await planner.plan("Open Calculator")

    async def test_json_wrapped_in_markdown_extracted(self, planner):
        """13. JSON wrapped in ```json ``` is extracted correctly."""
        planner._call_llm = AsyncMock(
            return_value=_make_llm_response(VALID_STEPS, wrap_in_markdown=True)
        )

        plan = await planner.plan("Open Calculator")

        assert isinstance(plan, ActionPlan)
        assert len(plan.steps) == 2
        assert plan.steps[0].action == "activate_app"

    async def test_missing_steps_key_raises_value_error(self, planner):
        """15. _parse_plan_response handles missing 'steps' key."""
        planner._call_llm = AsyncMock(
            return_value={
                "content": json.dumps({"actions": []}),
                "usage": {"input_tokens": 50, "output_tokens": 10},
            }
        )

        with pytest.raises(ValueError, match="missing 'steps' key"):
            await planner.plan("Open Calculator")


class TestPromptFiles:
    """Tests for prompt loading from disk."""

    def test_prompt_files_load_from_disk(self, planner):
        """9. Prompt files load from disk correctly."""
        plan_prompt = planner._load_prompt("plan_from_prompt.md")
        replan_prompt = planner._load_prompt("replan_from_state.md")

        assert "{{goal}}" in plan_prompt
        assert "{{screen_description}}" in plan_prompt
        assert "{{skill_context}}" in plan_prompt

        assert "{{goal}}" in replan_prompt
        assert "{{history}}" in replan_prompt
        assert "{{retry_strategies}}" in replan_prompt

    def test_missing_prompt_file_raises_error(self, planner):
        """10. Missing prompt file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="Prompt file not found"):
            planner._load_prompt("nonexistent_prompt.md")


class TestCLI:
    """Tests for the __main__.py CLI."""

    def test_dry_run_prints_prompt(self, planner, config, capsys):
        """11. --dry-run prints prompt without calling LLM."""
        from automation_agent.planner.__main__ import main

        with patch(
            "automation_agent.planner.__main__.AgentConfig",
            return_value=config,
        ), patch(
            "automation_agent.planner.__main__.ActionPlannerImpl",
        ) as MockPlanner:
            mock_instance = MockPlanner.return_value
            # _build_plan_prompt should be called, _call_llm should NOT
            mock_instance._build_plan_prompt.return_value = "TEST PROMPT CONTENT"
            mock_instance._call_llm = AsyncMock()

            main(["--dry-run", "Open Calculator"])

            # Verify prompt was printed
            captured = capsys.readouterr()
            assert "TEST PROMPT CONTENT" in captured.out
            assert "DRY RUN" in captured.out

            # Verify _call_llm was NOT called
            mock_instance._call_llm.assert_not_called()


class TestPlannerIntegration:
    """Integration-style tests using real prompt files with mocked LLM."""

    async def test_plan_prompt_contains_goal(self, planner):
        """Verify the built prompt contains the goal text."""
        prompt = planner._build_plan_prompt("Open Safari", "", None)
        assert "Open Safari" in prompt
        assert "Available Actions" in prompt

    async def test_replan_prompt_contains_all_placeholders_filled(self, planner):
        """Verify the replan prompt has all placeholders replaced."""
        history = [
            StepResult(
                step=ActionStep(
                    action="activate_app",
                    params={"app_name": "Safari"},
                    verify="Safari is frontmost",
                ),
                success=False,
                evidence="Safari not found",
                verification_method="vision",
            ),
        ]
        prompt = planner._build_replan_prompt(
            "Open Safari", "Desktop visible", history, ["direct_launch"]
        )
        # All placeholders should be replaced
        assert "{{goal}}" not in prompt
        assert "{{screen_description}}" not in prompt
        assert "{{history}}" not in prompt
        assert "{{retry_strategies}}" not in prompt
        # Content should be present
        assert "Open Safari" in prompt
        assert "Desktop visible" in prompt
        assert "Safari not found" in prompt
        assert "direct_launch" in prompt

    async def test_plan_duration_is_recorded(self, planner):
        """Verify planning_duration_ms is set on the returned plan."""
        planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))

        plan = await planner.plan("Open Calculator")

        assert plan.planning_duration_ms >= 0


class TestActionAliases:
    """Tests for LLM action name auto-correction."""

    def test_key_press_corrected_to_press_key(self):
        """key_press (common Haiku misspelling) corrected to press_key."""
        step = ActionStep.from_dict({
            "action": "key_press",
            "params": {"keys": ["cmd", "c"]},
            "verify": "Text copied",
        })
        assert step.action == "press_key"

    def test_open_app_corrected_to_activate_app(self):
        """open_app corrected to activate_app."""
        step = ActionStep.from_dict({
            "action": "open_app",
            "params": {"app_name": "Safari"},
            "verify": "Safari open",
        })
        assert step.action == "activate_app"

    def test_type_corrected_to_type_text(self):
        """bare 'type' corrected to type_text."""
        step = ActionStep.from_dict({
            "action": "type",
            "params": {"text": "hello"},
            "verify": "Text visible",
        })
        assert step.action == "type_text"

    def test_navigate_corrected_to_open_url(self):
        """navigate corrected to open_url."""
        step = ActionStep.from_dict({
            "action": "navigate",
            "params": {"url": "https://example.com"},
            "verify": "Page loaded",
        })
        assert step.action == "open_url"

    def test_close_app_corrected_to_quit_app(self):
        """close_app corrected to quit_app."""
        step = ActionStep.from_dict({
            "action": "close_app",
            "params": {"app_name": "Safari"},
            "verify": "Safari closed",
        })
        assert step.action == "quit_app"

    def test_finish_corrected_to_done(self):
        """finish corrected to done."""
        step = ActionStep.from_dict({
            "action": "finish",
            "params": {},
            "verify": "",
        })
        assert step.action == "done"

    def test_valid_action_unchanged(self):
        """Valid action names are not modified."""
        step = ActionStep.from_dict({
            "action": "click",
            "params": {"x": 100, "y": 200},
            "verify": "Button clicked",
        })
        assert step.action == "click"

    def test_unknown_action_still_raises(self):
        """Truly unknown action (not in aliases) still raises ValueError."""
        with pytest.raises(ValueError, match="Unknown action"):
            ActionStep.from_dict({
                "action": "hover_over",
                "params": {},
                "verify": "Hovering",
            })

    async def test_plan_with_aliased_actions_accepted(self, planner):
        """Plan with aliased action names parses successfully."""
        steps_data = [
            {
                "action": "key_press",
                "params": {"keys": ["cmd", "space"]},
                "verify": "Spotlight open",
            },
            {"action": "done", "params": {}, "verify": ""},
        ]
        planner._call_llm = AsyncMock(return_value=_make_llm_response(steps_data))

        plan = await planner.plan("Open Spotlight")
        assert plan.steps[0].action == "press_key"


class TestAPIRetry:
    """Tests for API retry with exponential backoff."""

    async def test_retry_on_529_overloaded(self, planner):
        """_call_llm retries on 529 (overloaded) errors."""
        import anthropic
        import httpx

        # Build a realistic mock response for the APIStatusError
        mock_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        mock_response = httpx.Response(529, request=mock_request)
        error_529 = anthropic.APIStatusError(
            message="Overloaded",
            response=mock_response,
            body=None,
        )

        call_count = 0

        async def mock_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise error_529
            return type("Message", (), {
                "content": [type("Block", (), {"text": json.dumps({"steps": VALID_STEPS})})()],
                "usage": type("Usage", (), {"input_tokens": 100, "output_tokens": 50})(),
            })()

        with patch("anthropic.AsyncAnthropic") as MockClient:
            mock_instance = MockClient.return_value
            mock_instance.messages.create = mock_create

            result = await planner._call_llm("test prompt")

        assert "content" in result
        assert call_count == 3  # 2 failures + 1 success
