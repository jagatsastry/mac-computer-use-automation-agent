"""Unit tests for multi-skill planner context (Slice 3).

Tests that the planner correctly handles:
- Skill priors guidance in plan prompts
- Derived procedure context in replan prompts
- Parsing of derived_skill_patch from replan responses
- Backward compatibility with existing planner behavior
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from automation_agent.config import AgentConfig
from automation_agent.planner.planner import ActionPlannerImpl
from automation_agent.shared_models import (
    ActionPlan,
    ActionStep,
    ReplanPatch,
    StepResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config():
    return AgentConfig(
        _env_file=None,
        anthropic_api_key="test-key-not-real",
        model_provider="local",
    )


@pytest.fixture
def planner(config):
    return ActionPlannerImpl(config)


VALID_STEPS = [
    {
        "action": "activate_app",
        "params": {"app_name": "Safari"},
        "verify": "Safari is the frontmost application",
        "on_fail": "retry_different",
    },
    {"action": "done", "params": {}, "verify": "", "on_fail": "abort"},
]


def _make_llm_response(data: dict) -> dict:
    """Build a mock LLM response dict from arbitrary data."""
    return {
        "content": json.dumps(data),
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }


def _make_history():
    """Build a minimal execution history for replan tests."""
    return [
        StepResult(
            step=ActionStep(
                action="click",
                params={"element": "Submit"},
                verify="Form submitted",
            ),
            success=False,
            evidence="Submit button not found",
            verification_method="vision",
        ),
    ]


# ---------------------------------------------------------------------------
# Plan Prompt Tests
# ---------------------------------------------------------------------------


class TestPlanPromptSkillPriors:
    """Tests for skill priors guidance in plan prompts."""

    def test_plan_prompt_includes_skill_priors_guidance(self, planner):
        """Skill context with [direct] label appears and guidance section is present."""
        skill_ctx = (
            "## Skill: open_safari [direct]\n"
            "1. Press Cmd+Space\n2. Type Safari\n3. Press Enter"
        )
        prompt = planner._build_plan_prompt("Open Safari", "", skill_ctx)

        assert "[direct]" in prompt
        assert "direct" in prompt.lower()
        assert "analogical" in prompt.lower()
        assert "generic" in prompt.lower()
        assert "Guidance for Skill Priors" in prompt
        assert "Derived Procedure" in prompt

    def test_plan_prompt_no_skill_context(self, planner):
        """'No skill context available' appears when skill_context is None."""
        prompt = planner._build_plan_prompt("Open Safari", "", None)
        assert "No skill context available" in prompt
        # Guidance section should still be present in the template
        assert "Guidance for Skill Priors" in prompt


# ---------------------------------------------------------------------------
# Replan Prompt Tests
# ---------------------------------------------------------------------------


class TestReplanPrompt:
    """Tests for replan prompt template changes."""

    def test_replan_prompt_includes_derived_procedure(self, planner):
        """Derived procedure text appears in replan prompt via skill_context."""
        derived_ctx = (
            "## Derived Procedure\n"
            "Correction: Use 'Send' instead of 'Submit'"
        )
        prompt = planner._build_replan_prompt(
            "Submit form",
            "Form page visible",
            _make_history(),
            ["click_center"],
            skill_context=derived_ctx,
        )
        assert "Derived Procedure" in prompt
        assert "Use 'Send' instead of 'Submit'" in prompt

    def test_replan_prompt_no_same_format_line(self, planner):
        """Verify the replan template does NOT contain 'Same JSON format as before'."""
        template = planner._load_prompt("replan_from_state.md")
        assert "Same JSON format as before" not in template


# ---------------------------------------------------------------------------
# Replan Patch Parsing Tests
# ---------------------------------------------------------------------------


class TestReplanPatchParsing:
    """Tests for parsing derived_skill_patch from replan LLM responses."""

    async def test_parse_replan_with_patch(self, planner):
        """Response with derived_skill_patch parsed to ActionPlan.replan_patch."""
        data = {
            "steps": VALID_STEPS,
            "derived_skill_patch": {
                "replace_labels": [
                    {"old": "Submit", "new": "Send", "reason": "Label changed"}
                ],
                "add_landmarks": ["confirmation banner"],
                "verify_improvements": ["check for success toast"],
                "failed_assumptions": ["Submit button does not exist"],
                "successful_adaptations": ["Used Send button instead"],
            },
        }
        planner._call_llm = AsyncMock(return_value=_make_llm_response(data))
        plan = await planner.plan("Submit form")

        assert plan.replan_patch is not None
        assert isinstance(plan.replan_patch, ReplanPatch)
        assert len(plan.replan_patch.replace_labels) == 1
        assert plan.replan_patch.replace_labels[0]["old"] == "Submit"
        assert plan.replan_patch.add_landmarks == ["confirmation banner"]
        assert plan.replan_patch.failed_assumptions == [
            "Submit button does not exist"
        ]

    async def test_parse_replan_without_patch(self, planner):
        """Response without patch produces ActionPlan.replan_patch=None."""
        data = {"steps": VALID_STEPS}
        planner._call_llm = AsyncMock(return_value=_make_llm_response(data))
        plan = await planner.plan("Open Safari")

        assert plan.replan_patch is None

    async def test_parse_replan_non_dict_patch(self, planner):
        """Non-dict patch value produces None (AC-9: malformed -> None)."""
        data = {
            "steps": VALID_STEPS,
            "derived_skill_patch": "not a dict",
        }
        planner._call_llm = AsyncMock(return_value=_make_llm_response(data))
        plan = await planner.plan("Open Safari")

        assert plan.replan_patch is None

    async def test_parse_replan_malformed_dict_patch(self, planner):
        """Dict with wrong-typed fields produces None (AC-9: empty -> None)."""
        data = {
            "steps": VALID_STEPS,
            "derived_skill_patch": {
                "replace_labels": "not a list",
                "add_landmarks": 42,
                "unknown_field": True,
            },
        }
        planner._call_llm = AsyncMock(return_value=_make_llm_response(data))
        plan = await planner.plan("Open Safari")

        assert plan.replan_patch is None

    async def test_parse_replan_patch_missing_fields(self, planner):
        """Partial patch dict handled gracefully — missing fields default to empty."""
        data = {
            "steps": VALID_STEPS,
            "derived_skill_patch": {
                "replace_labels": [
                    {"old": "A", "new": "B", "reason": "test"}
                ],
                # Other fields omitted
            },
        }
        planner._call_llm = AsyncMock(return_value=_make_llm_response(data))
        plan = await planner.plan("Open Safari")

        assert plan.replan_patch is not None
        assert len(plan.replan_patch.replace_labels) == 1
        assert plan.replan_patch.add_landmarks == []
        assert plan.replan_patch.verify_improvements == []
        assert plan.replan_patch.failed_assumptions == []
        assert plan.replan_patch.successful_adaptations == []

    async def test_parse_replan_patch_only_no_steps(self, planner):
        """Response with patch but missing steps raises ValueError."""
        data = {
            "derived_skill_patch": {
                "replace_labels": [
                    {"old": "X", "new": "Y", "reason": "z"}
                ],
            },
        }
        planner._call_llm = AsyncMock(return_value=_make_llm_response(data))

        with pytest.raises(ValueError, match="missing 'steps' key"):
            await planner.plan("Open Safari")

    async def test_parse_replan_extracts_patch_before_steps_check(self, planner):
        """Patch is captured even if steps validation fails."""
        data = {
            "derived_skill_patch": {
                "add_landmarks": ["some landmark"],
            },
        }
        planner._call_llm = AsyncMock(return_value=_make_llm_response(data))

        with patch.object(
            ReplanPatch, "from_dict", wraps=ReplanPatch.from_dict
        ) as mock_from_dict:
            with pytest.raises(ValueError, match="missing 'steps' key"):
                await planner.plan("Open Safari")

            # Prove from_dict was called BEFORE the ValueError for missing steps
            mock_from_dict.assert_called_once_with({"add_landmarks": ["some landmark"]})


# ---------------------------------------------------------------------------
# Backward Compatibility Tests
# ---------------------------------------------------------------------------


class TestReplanMethodWithPatch:
    """Tests that replan() (not just plan()) correctly returns replan_patch."""

    async def test_replan_returns_patch(self, planner):
        """replan() with derived_skill_patch in response populates replan_patch."""
        data = {
            "steps": VALID_STEPS,
            "derived_skill_patch": {
                "replace_labels": [
                    {"old": "Submit", "new": "Send", "reason": "renamed"}
                ],
                "failed_assumptions": ["Submit button missing"],
            },
        }
        planner._call_llm = AsyncMock(return_value=_make_llm_response(data))

        plan = await planner.replan(
            "Submit form",
            "Form page visible",
            _make_history(),
            ["click_center"],
            skill_context="## Skill: submit_form [direct]\n1. Click Submit",
        )

        assert isinstance(plan, ActionPlan)
        assert plan.replan_patch is not None
        assert len(plan.replan_patch.replace_labels) == 1
        assert plan.replan_patch.replace_labels[0]["new"] == "Send"
        assert plan.replan_patch.failed_assumptions == ["Submit button missing"]


class TestBackwardCompatibility:
    """Tests that existing plan behavior is unchanged."""

    async def test_plan_still_works_without_multiskill(self, planner):
        """Existing plan behavior unchanged when skill_context is a simple string."""
        data = {"steps": VALID_STEPS}
        planner._call_llm = AsyncMock(return_value=_make_llm_response(data))

        plan = await planner.plan(
            "Open Calculator",
            skill_context="Use Spotlight to launch apps",
        )

        assert isinstance(plan, ActionPlan)
        assert len(plan.steps) == 2
        assert plan.replan_patch is None
        assert plan.goal == "Open Calculator"
