"""Unit tests for the replan-as-plan-patch feature.

Tests cover:
- Annotated plan builder
- Replan prompt with annotated plan and current_step_index
- resume_from_step parsing and step merging
- Plan materialization to files
- Screenshot passthrough to replan
- Image support in _call_llm
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from automation_agent.config import AgentConfig
from automation_agent.planner.planner import ActionPlannerImpl
from automation_agent.shared_models import ActionPlan, ActionStep, StepResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config():
    return AgentConfig(
        _env_file=None,
        model_provider="local",
        anthropic_api_key="test-key-not-real",
    )


@pytest.fixture
def planner(config):
    return ActionPlannerImpl(config)


def _make_llm_response(data: dict, wrap_markdown: bool = False) -> dict:
    payload = json.dumps(data)
    if wrap_markdown:
        content = f"```json\n{payload}\n```"
    else:
        content = payload
    return {
        "content": content,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }


STEP_OPEN = ActionStep(
    action="open_url",
    params={"url": "https://amazon.com/orders"},
    verify="Browser URL matches amazon.com/orders",
)
STEP_TYPE = ActionStep(
    action="type_text",
    params={"text": "listerine", "element": "Search input"},
    verify="Search input contains listerine",
)
STEP_CLICK = ActionStep(
    action="click",
    params={"element": "Return button"},
    verify="Return flow visible",
)
STEP_DONE = ActionStep(action="done", params={}, verify="")


# ---------------------------------------------------------------------------
# _build_annotated_plan
# ---------------------------------------------------------------------------


class TestAnnotatedPlanBuilder:
    """Tests for _build_annotated_plan()."""

    def test_all_steps_annotated(self, planner):
        """All original steps get checkmark, cross, or dash annotation."""
        original = [STEP_OPEN, STEP_TYPE, STEP_CLICK, STEP_DONE]
        results = [
            StepResult(step=STEP_OPEN, success=True, evidence="URL matches"),
            StepResult(step=STEP_TYPE, success=False, evidence="Vision denies"),
        ]

        text = planner._build_annotated_plan(original, results, failed_step_index=1)

        assert "\u2713" in text.split("\n")[0]  # step 0 passed
        assert "\u2717" in text.split("\n")[1]  # step 1 failed
        assert "not attempted" in text.split("\n")[2]  # step 2
        assert "not attempted" in text.split("\n")[3]  # step 3

    def test_evidence_from_step_results(self, planner):
        """Evidence text from StepResults appears in annotations."""
        original = [STEP_OPEN, STEP_TYPE]
        results = [
            StepResult(step=STEP_OPEN, success=True, evidence="Browser URL matches"),
            StepResult(step=STEP_TYPE, success=False, evidence="Vision denies: field empty"),
        ]

        text = planner._build_annotated_plan(original, results, failed_step_index=1)

        assert "Browser URL matches" in text
        assert "Vision denies" in text

    def test_failed_at_first_step(self, planner):
        """When first step fails, it gets cross, rest are not attempted."""
        original = [STEP_OPEN, STEP_TYPE, STEP_CLICK]
        results = [
            StepResult(step=STEP_OPEN, success=False, evidence="Timeout"),
        ]

        text = planner._build_annotated_plan(original, results, failed_step_index=0)

        lines = text.strip().split("\n")
        assert "\u2717" in lines[0]  # step 0 failed
        assert "not attempted" in lines[1]
        assert "not attempted" in lines[2]

    def test_all_steps_passed(self, planner):
        """When all steps passed (e.g., done gate failure), all get checkmarks."""
        original = [STEP_OPEN, STEP_TYPE, STEP_DONE]
        results = [
            StepResult(step=STEP_OPEN, success=True, evidence="OK"),
            StepResult(step=STEP_TYPE, success=True, evidence="OK"),
            StepResult(step=STEP_DONE, success=True, evidence="Done"),
        ]

        text = planner._build_annotated_plan(
            original, results, failed_step_index=3,
        )

        for line in text.strip().split("\n"):
            assert "\u2713" in line

    def test_params_displayed(self, planner):
        """Step params are shown in the annotated plan text."""
        original = [STEP_OPEN]
        results = [
            StepResult(step=STEP_OPEN, success=False, evidence="failed"),
        ]

        text = planner._build_annotated_plan(original, results, failed_step_index=0)

        assert "amazon.com/orders" in text
        assert "open_url" in text

    def test_empty_results(self, planner):
        """Empty step_results: all steps are not attempted (except failed step)."""
        original = [STEP_OPEN, STEP_TYPE]

        text = planner._build_annotated_plan(original, [], failed_step_index=0)

        lines = text.strip().split("\n")
        assert "\u2717" in lines[0]  # failed step
        assert "not attempted" in lines[1]


# ---------------------------------------------------------------------------
# _build_replan_prompt with annotated plan
# ---------------------------------------------------------------------------


class TestReplanPromptWithAnnotatedPlan:
    """Tests for _build_replan_prompt() with original_steps/current_step_index."""

    def test_annotated_plan_in_prompt(self, planner):
        """Prompt contains annotated plan when original_steps provided."""
        original = [STEP_OPEN, STEP_TYPE, STEP_CLICK]
        results = [
            StepResult(step=STEP_OPEN, success=True, evidence="URL matches"),
            StepResult(step=STEP_TYPE, success=False, evidence="field empty"),
        ]

        prompt = planner._build_replan_prompt(
            "Buy listerine", "Screen desc", results, ["retry"],
            original_steps=original, current_step_index=1,
        )

        assert "\u2713" in prompt  # checkmark for passed step
        assert "\u2717" in prompt  # cross for failed step
        assert "not attempted" in prompt  # click step
        assert "step 1" in prompt.lower() or "Step 1" in prompt

    def test_current_step_index_in_prompt(self, planner):
        """current_step_index appears in the prompt text."""
        prompt = planner._build_replan_prompt(
            "Goal", "Screen", [], [],
            original_steps=[STEP_OPEN, STEP_TYPE],
            current_step_index=1,
        )

        # The template uses {{current_step_index}} which should be replaced
        assert "{{current_step_index}}" not in prompt
        # Should contain the literal "1" as step index
        assert "step 1" in prompt.lower() or "STEP 1" in prompt

    def test_fallback_without_original_steps(self, planner):
        """Without original_steps, falls back to flat history format."""
        results = [
            StepResult(step=STEP_OPEN, success=False, evidence="failed"),
        ]

        prompt = planner._build_replan_prompt(
            "Goal", "Screen", results, [],
        )

        # Should still contain the history
        assert "open_url" in prompt
        assert "failed" in prompt
        # Flat history uses -> SUCCESS/FAILED format, not Step N: action() checkmark format
        assert "-> FAILED" in prompt or "FAILED:" in prompt

    def test_all_placeholders_filled(self, planner):
        """No raw template placeholders remain in the prompt."""
        original = [STEP_OPEN, STEP_TYPE]
        results = [
            StepResult(step=STEP_OPEN, success=True, evidence="OK"),
        ]

        prompt = planner._build_replan_prompt(
            "Goal", "Screen", results, ["s1"],
            desktop_context="Desktop state",
            skill_context="Skill context",
            absent_elements=["Missing Button"],
            original_steps=original,
            current_step_index=1,
        )

        assert "{{" not in prompt


# ---------------------------------------------------------------------------
# replan() with resume_from_step parsing
# ---------------------------------------------------------------------------


class TestReplanResumeFromStep:
    """Tests for resume_from_step parsing and step merging."""

    async def test_resume_from_step_does_not_prepend_completed(self, planner):
        """Replan with resume_from_step does NOT prepend completed steps.

        Completed steps are tracked in completed_steps metadata, not re-included
        in the executable steps list (which would cause re-execution with stale
        preconditions).
        """
        original = [STEP_OPEN, STEP_TYPE, STEP_CLICK]
        new_steps = [
            {"action": "press_key", "params": {"keys": ["return"]}, "verify": "Search submitted"},
            {"action": "done", "params": {}, "verify": ""},
        ]
        response_data = {
            "resume_from_step": 1,
            "steps": new_steps,
        }

        planner._call_llm = AsyncMock(return_value=_make_llm_response(response_data))
        history = [
            StepResult(step=STEP_OPEN, success=True, evidence="OK"),
            StepResult(step=STEP_TYPE, success=False, evidence="Failed"),
        ]

        plan = await planner.replan(
            "Buy listerine", "Screen", history, [],
            original_steps=original, current_step_index=1,
        )

        # Only new steps — completed steps NOT prepended
        assert len(plan.steps) == 2
        assert plan.steps[0].action == "press_key"
        assert plan.steps[1].action == "done"
        assert plan.resume_from_step == 1

    async def test_no_resume_from_step_uses_current_index(self, planner):
        """Without resume_from_step in response, uses current_step_index."""
        response_data = {
            "steps": [
                {"action": "click", "params": {"element": "Button"}, "verify": "clicked"},
            ],
        }

        planner._call_llm = AsyncMock(return_value=_make_llm_response(response_data))

        plan = await planner.replan(
            "Goal", "Screen", [], [],
            original_steps=[STEP_OPEN], current_step_index=1,
        )

        assert plan.resume_from_step == 1

    async def test_resume_from_step_zero_means_full_replan(self, planner):
        """resume_from_step=0 means the entire plan is replaced."""
        response_data = {
            "resume_from_step": 0,
            "steps": [
                {"action": "open_url", "params": {"url": "https://new.com"}, "verify": "loaded"},
            ],
        }

        planner._call_llm = AsyncMock(return_value=_make_llm_response(response_data))

        plan = await planner.replan(
            "Goal", "Screen", [], [],
            original_steps=[STEP_OPEN, STEP_TYPE], current_step_index=0,
        )

        # No completed steps prepended
        assert len(plan.steps) == 1
        assert plan.steps[0].action == "open_url"
        assert plan.resume_from_step == 0

    async def test_resume_from_step_clamped_to_bounds(self, planner):
        """resume_from_step > len(original_steps) is clamped."""
        response_data = {
            "resume_from_step": 99,
            "steps": [
                {"action": "done", "params": {}, "verify": ""},
            ],
        }

        planner._call_llm = AsyncMock(return_value=_make_llm_response(response_data))

        plan = await planner.replan(
            "Goal", "Screen", [], [],
            original_steps=[STEP_OPEN, STEP_TYPE], current_step_index=1,
        )

        # Clamped to 2 but completed steps NOT prepended — only new steps
        assert len(plan.steps) == 1
        assert plan.steps[0].action == "done"
        assert plan.resume_from_step == 2

    async def test_backward_compat_without_original_steps(self, planner):
        """replan() without original_steps still works (backward compat)."""
        response_data = {
            "steps": [
                {"action": "click", "params": {"element": "Btn"}, "verify": "clicked"},
            ],
        }

        planner._call_llm = AsyncMock(return_value=_make_llm_response(response_data))
        history = [
            StepResult(step=STEP_OPEN, success=False, evidence="fail"),
        ]

        plan = await planner.replan("Goal", "Screen", history, [])

        assert len(plan.steps) == 1
        assert plan.steps[0].action == "click"


# ---------------------------------------------------------------------------
# Screenshot passthrough
# ---------------------------------------------------------------------------


class TestScreenshotPassthrough:
    """Tests for screenshot_b64 being passed through to _call_llm."""

    async def test_screenshot_passed_to_call_llm(self, planner):
        """screenshot_b64 is forwarded to _call_llm as image_b64."""
        response_data = {
            "steps": [{"action": "done", "params": {}, "verify": ""}],
        }
        planner._call_llm = AsyncMock(return_value=_make_llm_response(response_data))

        await planner.replan(
            "Goal", "Screen", [], [],
            screenshot_b64="base64data",
        )

        call_kwargs = planner._call_llm.call_args
        assert call_kwargs.kwargs.get("image_b64") == "base64data"

    async def test_no_screenshot_passes_none(self, planner):
        """Without screenshot_b64, image_b64 is None."""
        response_data = {
            "steps": [{"action": "done", "params": {}, "verify": ""}],
        }
        planner._call_llm = AsyncMock(return_value=_make_llm_response(response_data))

        await planner.replan("Goal", "Screen", [], [])

        call_kwargs = planner._call_llm.call_args
        assert call_kwargs.kwargs.get("image_b64") is None


# ---------------------------------------------------------------------------
# _call_llm image routing
# ---------------------------------------------------------------------------


class TestCallLLMImageRouting:
    """Tests for image_b64 routing in _call_llm."""

    async def test_anthropic_receives_image(self, config):
        """Anthropic provider passes image_b64 to _call_anthropic_llm."""
        config.model_provider = "anthropic"
        planner = ActionPlannerImpl(config)
        planner._call_anthropic_llm = AsyncMock(
            return_value={"content": "{}", "usage": {}}
        )

        await planner._call_llm("test prompt", image_b64="img123")

        call_kwargs = planner._call_anthropic_llm.call_args
        assert call_kwargs.kwargs.get("image_b64") == "img123"

    async def test_gemini_receives_image(self, config):
        """Gemini provider passes image_b64 to _call_gemini_llm."""
        config.model_provider = "gemini"
        planner = ActionPlannerImpl(config)
        planner._call_gemini_llm = AsyncMock(
            return_value={"content": "{}", "usage": {}}
        )

        await planner._call_llm("test prompt", image_b64="img456")

        call_kwargs = planner._call_gemini_llm.call_args
        assert call_kwargs.kwargs.get("image_b64") == "img456"

    async def test_local_does_not_receive_image(self, config):
        """Local provider does not get image_b64 parameter."""
        config.model_provider = "local"
        planner = ActionPlannerImpl(config)
        planner._call_local_llm = AsyncMock(
            return_value={"content": "{}", "usage": {}}
        )

        await planner._call_llm("test prompt", image_b64="img789")

        # local provider should NOT receive image_b64
        call_kwargs = planner._call_local_llm.call_args
        assert "image_b64" not in (call_kwargs.kwargs or {})

    async def test_openai_does_not_receive_image(self, config):
        """OpenAI provider does not get image_b64 (not yet supported)."""
        config.model_provider = "openai"
        planner = ActionPlannerImpl(config)
        planner._call_openai_llm = AsyncMock(
            return_value={"content": "{}", "usage": {}}
        )

        await planner._call_llm("test prompt", image_b64="imgabc")

        call_kwargs = planner._call_openai_llm.call_args
        assert "image_b64" not in (call_kwargs.kwargs or {})


# ---------------------------------------------------------------------------
# Plan materialization
# ---------------------------------------------------------------------------


class TestPlanMaterialization:
    """Tests for _materialize_plan() writing plan files."""

    def _make_agent(self, run_dir):
        """Create a minimal agent with a real logger pointing to tmp dir."""
        from automation_agent.orchestrator.agent import AutomationAgent

        logger = MagicMock()
        logger.run_dir = run_dir
        logger.run_id = "test-run"
        logger.log_event = MagicMock()

        config = AgentConfig(
            _env_file=None,
            model_provider="local",
            anthropic_api_key="test-key",
        )

        agent = AutomationAgent.__new__(AutomationAgent)
        agent.config = config
        agent.logger = logger
        return agent

    def test_initial_plan_materialized(self):
        """Initial plan (v0) is saved as plan_v0_*.json."""
        from automation_agent.orchestrator.agent import PlanRecord

        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            agent = self._make_agent(run_dir)

            record = PlanRecord(
                version=0,
                timestamp="12:00:00",
                is_replan=False,
                steps=[
                    {"action": "open_url", "params": {"url": "https://x.com"}, "verify": "loaded"},
                ],
                raw_llm_response='{"steps": [...]}',
            )
            agent._materialize_plan(record)

            plans_dir = run_dir / "plans"
            assert plans_dir.exists()
            files = list(plans_dir.glob("plan_v0_*.json"))
            assert len(files) == 1

            data = json.loads(files[0].read_text())
            assert data["version"] == 0
            assert data["is_replan"] is False
            assert len(data["steps"]) == 1

    def test_replan_materialized(self):
        """Replan (v1) is saved with trigger and completed_steps."""
        from automation_agent.orchestrator.agent import PlanRecord

        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            agent = self._make_agent(run_dir)

            record = PlanRecord(
                version=1,
                timestamp="12:05:00",
                is_replan=True,
                replan_reason="Step 1 type_text verify failed",
                resume_from_step=1,
                completed_steps=[
                    {"index": 0, "action": "open_url", "result": "PASS", "evidence": "URL matches"},
                ],
                steps=[
                    {"action": "press_key", "params": {"keys": ["return"]}, "verify": "submitted"},
                ],
                raw_llm_response='{"resume_from_step": 1, "steps": [...]}',
            )
            agent._materialize_plan(record)

            plans_dir = run_dir / "plans"
            files = list(plans_dir.glob("plan_v1_*.json"))
            assert len(files) == 1

            data = json.loads(files[0].read_text())
            assert data["version"] == 1
            assert data["is_replan"] is True
            assert data["trigger"] == "Step 1 type_text verify failed"
            assert data["resume_from_step"] == 1
            assert len(data["completed_steps"]) == 1

    def test_multiple_plans_materialized(self):
        """Multiple plan versions create separate files."""
        from automation_agent.orchestrator.agent import PlanRecord

        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            agent = self._make_agent(run_dir)

            for v in range(3):
                record = PlanRecord(
                    version=v,
                    timestamp=f"12:{v:02d}:00",
                    is_replan=v > 0,
                    steps=[{"action": "done", "params": {}, "verify": ""}],
                )
                agent._materialize_plan(record)

            plans_dir = run_dir / "plans"
            files = sorted(plans_dir.glob("plan_v*.json"))
            assert len(files) == 3

    def test_materialize_tolerates_missing_logger(self):
        """_materialize_plan does not crash when logger.run_dir is inaccessible."""
        from automation_agent.orchestrator.agent import AutomationAgent, PlanRecord

        agent = AutomationAgent.__new__(AutomationAgent)
        agent.logger = MagicMock()
        # Simulate run_dir access raising
        type(agent.logger).run_dir = property(lambda self: (_ for _ in ()).throw(AttributeError))

        record = PlanRecord(
            version=0,
            steps=[{"action": "done", "params": {}, "verify": ""}],
        )
        # Should not raise
        agent._materialize_plan(record)


# ---------------------------------------------------------------------------
# ActionPlan.resume_from_step field
# ---------------------------------------------------------------------------


class TestActionPlanResumeField:
    """Tests for the new resume_from_step field on ActionPlan."""

    def test_default_none(self):
        """resume_from_step defaults to None."""
        plan = ActionPlan(steps=[STEP_DONE], goal="test")
        assert plan.resume_from_step is None

    def test_can_set(self):
        """resume_from_step can be set."""
        plan = ActionPlan(steps=[STEP_DONE], goal="test", resume_from_step=2)
        assert plan.resume_from_step == 2


# ---------------------------------------------------------------------------
# Consultant-requested: retry param mutation robustness
# ---------------------------------------------------------------------------


class TestRetryParamMutationMatching:
    """Consultant finding P1 #1: retries that mutate params must still match."""

    def test_annotated_plan_matches_despite_retry_params(self, planner):
        """Step with _pre_delay retry still matches original for annotation."""
        original = [STEP_OPEN, STEP_TYPE]
        # Simulate a retry that added _pre_delay to the step's params
        mutated_step = ActionStep(
            action="type_text",
            params={"text": "listerine", "element": "Search input", "_pre_delay": 0.5},
            verify="Search input contains listerine",
        )
        results = [
            StepResult(step=STEP_OPEN, success=True, evidence="URL matches"),
            StepResult(step=STEP_TYPE, success=False, evidence="first attempt failed"),
            StepResult(step=mutated_step, success=True, evidence="retry succeeded"),
        ]

        text = planner._build_annotated_plan(original, results, failed_step_index=2)

        # Step 1 should show the LAST result (retry succeeded), not first attempt
        lines = text.strip().split("\n")
        assert "\u2713" in lines[1]  # step 1 passed (via retry)
        assert "retry succeeded" in lines[1]

    def test_params_match_ignoring_retry_keys(self, planner):
        """_params_match_ignoring_retry_keys strips transient keys."""
        assert planner._params_match_ignoring_retry_keys(
            {"text": "hello", "_pre_delay": 0.5, "_clear_first": True},
            {"text": "hello"},
        )
        assert not planner._params_match_ignoring_retry_keys(
            {"text": "hello"},
            {"text": "world"},
        )


# ---------------------------------------------------------------------------
# Consultant-requested: malformed resume_from_step
# ---------------------------------------------------------------------------


class TestMalformedResumeFromStep:
    """Consultant finding P1 #3: malformed resume_from_step must not crash."""

    async def test_string_resume_from_step_falls_back(self, planner):
        """resume_from_step='bad' falls back to current_step_index."""
        response_data = {
            "resume_from_step": "bad",
            "steps": [
                {"action": "done", "params": {}, "verify": ""},
            ],
        }

        planner._call_llm = AsyncMock(return_value=_make_llm_response(response_data))

        plan = await planner.replan(
            "Goal", "Screen", [], [],
            original_steps=[STEP_OPEN], current_step_index=1,
        )

        # Should fall back to current_step_index, not crash
        assert plan.resume_from_step == 1
        assert len(plan.steps) == 1

    async def test_list_resume_from_step_falls_back(self, planner):
        """resume_from_step=[] falls back to current_step_index."""
        response_data = {
            "resume_from_step": [],
            "steps": [
                {"action": "click", "params": {"element": "X"}, "verify": "clicked"},
            ],
        }

        planner._call_llm = AsyncMock(return_value=_make_llm_response(response_data))

        plan = await planner.replan(
            "Goal", "Screen", [], [],
            original_steps=[STEP_OPEN, STEP_TYPE], current_step_index=0,
        )

        assert plan.resume_from_step == 0
        assert len(plan.steps) == 1

    async def test_none_resume_from_step_falls_back(self, planner):
        """resume_from_step=null in JSON falls back to current_step_index."""
        response_data = {
            "resume_from_step": None,
            "steps": [
                {"action": "done", "params": {}, "verify": ""},
            ],
        }

        planner._call_llm = AsyncMock(return_value=_make_llm_response(response_data))

        plan = await planner.replan(
            "Goal", "Screen", [], [],
            original_steps=[STEP_OPEN], current_step_index=1,
        )

        assert plan.resume_from_step == 1


# ---------------------------------------------------------------------------
# Consultant-requested: OpenAI gets real screen description
# ---------------------------------------------------------------------------


class TestOpenAIScreenDescription:
    """Consultant finding P1 #2: OpenAI must get real text, not image placeholder."""

    async def test_openai_replan_gets_text_description(self, planner):
        """When provider is openai, screenshot_b64 is None in _call_llm."""
        response_data = {
            "steps": [{"action": "done", "params": {}, "verify": ""}],
        }
        planner._call_llm = AsyncMock(return_value=_make_llm_response(response_data))

        # Even if screenshot_b64 is provided, OpenAI path ignores it
        await planner.replan(
            "Goal", "Real screen description here", [], [],
            screenshot_b64="base64data",
        )

        # The prompt should contain the real screen description
        call_args = planner._call_llm.call_args
        prompt_text = call_args[0][0]
        assert "Real screen description here" in prompt_text
