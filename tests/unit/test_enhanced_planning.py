"""Unit tests for enhanced planning with structured desktop context.

Tests that the planner correctly accepts and integrates desktop_context
from the ContextMonitor into planning and replanning prompts.

All tests mock the _call_llm method — no real API calls are made.
"""

import json
from unittest.mock import AsyncMock

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


def _make_llm_response(steps_data: list) -> dict:
    """Helper: build a mock LLM response dict from steps data."""
    payload = json.dumps({"steps": steps_data})
    return {
        "content": payload,
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

SAMPLE_DESKTOP_CONTEXT = (
    "## Desktop State\n"
    "App: Safari\n"
    "Window: OpenTable - Book restaurants\n"
    "Interactive elements (3):\n"
    '  - [Button] "Find a Table"\n'
    '  - [TextField] "Restaurant or Cuisine" value="sushi"\n'
    '  - [Button] "Let\'s go"\n'
    "\n"
    "## Form Progress\n"
    "  Restaurant: sushi"
)


# ---------------------------------------------------------------------------
# Tests: plan() with desktop_context
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPlanWithDesktopContext:
    """Tests for plan() with the desktop_context parameter."""

    async def test_plan_with_desktop_context_includes_in_prompt(self, planner):
        """plan() with desktop_context includes it in the LLM prompt."""
        planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))

        await planner.plan("Book a table", desktop_context=SAMPLE_DESKTOP_CONTEXT)

        prompt = planner._call_llm.call_args[0][0]
        assert "## Desktop State" in prompt
        assert "App: Safari" in prompt
        assert "Window: OpenTable - Book restaurants" in prompt
        assert '[Button] "Find a Table"' in prompt
        assert "## Form Progress" in prompt
        assert "Restaurant: sushi" in prompt

    async def test_plan_without_desktop_context_no_regression(self, planner):
        """plan() without desktop_context works as before."""
        planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))

        plan = await planner.plan("Open Calculator")

        assert isinstance(plan, ActionPlan)
        assert len(plan.steps) == 2
        assert plan.steps[0].action == "activate_app"

        # Prompt should not contain desktop state header
        prompt = planner._call_llm.call_args[0][0]
        assert "## Desktop State" not in prompt

    async def test_desktop_context_appears_before_screen_description(self, planner):
        """desktop_context appears before screen_description in the prompt."""
        planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))

        await planner.plan(
            "Book a table",
            screen_description="Safari showing OpenTable website",
            desktop_context=SAMPLE_DESKTOP_CONTEXT,
        )

        prompt = planner._call_llm.call_args[0][0]
        context_pos = prompt.index("## Desktop State")
        screen_pos = prompt.index("Safari showing OpenTable website")
        assert context_pos < screen_pos, (
            "desktop_context must appear before screen_description"
        )

    async def test_empty_desktop_context_no_extra_whitespace(self, planner):
        """Empty desktop_context doesn't add extra sections to the prompt."""
        planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))

        await planner.plan("Open Calculator", desktop_context="")

        prompt = planner._call_llm.call_args[0][0]
        # Should not have stray desktop context placeholders or headers
        assert "{{desktop_context}}" not in prompt
        assert "## Desktop State" not in prompt

    async def test_plan_with_desktop_context_returns_valid_plan(self, planner):
        """plan() with desktop_context still returns a valid ActionPlan."""
        planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))

        plan = await planner.plan(
            "Book a table",
            desktop_context=SAMPLE_DESKTOP_CONTEXT,
        )

        assert isinstance(plan, ActionPlan)
        assert plan.goal == "Book a table"
        assert len(plan.steps) == 2

    async def test_desktop_context_with_skill_context_both_present(self, planner):
        """Both desktop_context and skill_context appear in the prompt."""
        planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))

        await planner.plan(
            "Book a table",
            skill_context="Use OpenTable skill template",
            desktop_context=SAMPLE_DESKTOP_CONTEXT,
        )

        prompt = planner._call_llm.call_args[0][0]
        assert "## Desktop State" in prompt
        assert "Use OpenTable skill template" in prompt

    async def test_large_desktop_context_handled(self, planner):
        """Very long desktop_context (100+ elements) is handled without error."""
        planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))

        elements = "\n".join(
            f'  - [Button] "Element {i}"' for i in range(150)
        )
        large_context = (
            "## Desktop State\n"
            "App: Safari\n"
            "Window: Complex Page\n"
            f"Interactive elements (150):\n{elements}"
        )

        plan = await planner.plan(
            "Click element 42",
            desktop_context=large_context,
        )

        assert isinstance(plan, ActionPlan)
        prompt = planner._call_llm.call_args[0][0]
        assert '[Button] "Element 0"' in prompt
        assert '[Button] "Element 149"' in prompt


# ---------------------------------------------------------------------------
# Tests: replan() with desktop_context
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReplanWithDesktopContext:
    """Tests for replan() with the desktop_context parameter."""

    def _make_history(self) -> list:
        return [
            StepResult(
                step=ActionStep(
                    action="click",
                    params={"element": "Find a Table"},
                    verify="Table search started",
                ),
                success=False,
                evidence="Button not found",
                verification_method="vision",
            ),
        ]

    async def test_replan_with_desktop_context_includes_in_prompt(self, planner):
        """replan() with desktop_context includes it in the LLM prompt."""
        planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))

        await planner.replan(
            "Book a table",
            "Safari showing OpenTable",
            self._make_history(),
            ["click_center"],
            desktop_context=SAMPLE_DESKTOP_CONTEXT,
        )

        prompt = planner._call_llm.call_args[0][0]
        assert "## Desktop State" in prompt
        assert "App: Safari" in prompt
        assert '[Button] "Find a Table"' in prompt

    async def test_replan_without_desktop_context_no_regression(self, planner):
        """replan() without desktop_context works as before."""
        planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))

        plan = await planner.replan(
            "Book a table",
            "Safari showing OpenTable",
            self._make_history(),
            ["click_center"],
        )

        assert isinstance(plan, ActionPlan)
        prompt = planner._call_llm.call_args[0][0]
        assert "## Desktop State" not in prompt
        # History should still be present
        assert "Button not found" in prompt

    async def test_replan_desktop_context_before_screen_description(self, planner):
        """desktop_context appears before screen_description in replan prompt."""
        planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))

        await planner.replan(
            "Book a table",
            "Safari showing OpenTable",
            self._make_history(),
            ["click_center"],
            desktop_context=SAMPLE_DESKTOP_CONTEXT,
        )

        prompt = planner._call_llm.call_args[0][0]
        context_pos = prompt.index("## Desktop State")
        screen_pos = prompt.index("Safari showing OpenTable")
        assert context_pos < screen_pos

    async def test_replan_empty_desktop_context_clean(self, planner):
        """Empty desktop_context in replan doesn't add artifacts."""
        planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))

        await planner.replan(
            "Book a table",
            "Safari showing OpenTable",
            self._make_history(),
            ["click_center"],
            desktop_context="",
        )

        prompt = planner._call_llm.call_args[0][0]
        assert "{{desktop_context}}" not in prompt


# ---------------------------------------------------------------------------
# Tests: Prompt template integration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPromptTemplateIntegration:
    """Tests verifying prompt templates contain the desktop_context placeholder."""

    def test_plan_prompt_has_desktop_context_placeholder(self, planner):
        """Plan prompt template contains the {{desktop_context}} placeholder."""
        template = planner._load_prompt("plan_from_prompt.md")
        assert "{{desktop_context}}" in template

    def test_replan_prompt_has_desktop_context_placeholder(self, planner):
        """Replan prompt template contains the {{desktop_context}} placeholder."""
        template = planner._load_prompt("replan_from_state.md")
        assert "{{desktop_context}}" in template

    def test_plan_prompt_has_element_reference_guidance(self, planner):
        """Plan prompt includes guidance to reference elements by exact name."""
        template = planner._load_prompt("plan_from_prompt.md")
        assert "reference them by exact name" in template

    def test_plan_prompt_has_form_progress_guidance(self, planner):
        """Plan prompt includes guidance about checking form progress."""
        template = planner._load_prompt("plan_from_prompt.md")
        assert "form progress" in template.lower()

    def test_replan_prompt_has_element_reference_guidance(self, planner):
        """Replan prompt includes guidance to reference elements by exact name."""
        template = planner._load_prompt("replan_from_state.md")
        assert "reference them by exact name" in template

    def test_desktop_context_placeholder_before_screen_in_plan_template(
        self, planner
    ):
        """In plan template, desktop_context placeholder is before screen_description."""
        template = planner._load_prompt("plan_from_prompt.md")
        ctx_pos = template.index("{{desktop_context}}")
        screen_pos = template.index("{{screen_description}}")
        assert ctx_pos < screen_pos

    def test_desktop_context_placeholder_before_screen_in_replan_template(
        self, planner
    ):
        """In replan template, desktop_context placeholder is before screen_description."""
        template = planner._load_prompt("replan_from_state.md")
        ctx_pos = template.index("{{desktop_context}}")
        screen_pos = template.index("{{screen_description}}")
        assert ctx_pos < screen_pos


# ---------------------------------------------------------------------------
# Tests: Backward compatibility
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBackwardCompatibility:
    """Verify that adding desktop_context doesn't break existing behavior."""

    async def test_plan_signature_backward_compatible(self, planner):
        """plan() can be called without desktop_context (default empty string)."""
        planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))

        # Call without desktop_context — should work like before
        plan = await planner.plan("Open Calculator")
        assert isinstance(plan, ActionPlan)

    async def test_plan_with_positional_args_still_works(self, planner):
        """plan() with positional args for goal and screen_description still works."""
        planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))

        plan = await planner.plan("Open Calculator", "Desktop visible")
        assert isinstance(plan, ActionPlan)

    async def test_plan_with_keyword_args_still_works(self, planner):
        """plan() with keyword args still works."""
        planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))

        plan = await planner.plan(
            goal="Open Calculator",
            screen_description="Desktop visible",
            skill_context="Use Spotlight",
        )
        assert isinstance(plan, ActionPlan)

    async def test_replan_signature_backward_compatible(self, planner):
        """replan() can be called without desktop_context."""
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

        plan = await planner.replan(
            "Click the button",
            "Screen with form",
            history,
            ["click_center"],
        )
        assert isinstance(plan, ActionPlan)

    async def test_build_plan_prompt_without_desktop_context(self, planner):
        """_build_plan_prompt works without desktop_context argument."""
        prompt = planner._build_plan_prompt("Open Safari", "", None)
        assert "Open Safari" in prompt
        assert "{{desktop_context}}" not in prompt
