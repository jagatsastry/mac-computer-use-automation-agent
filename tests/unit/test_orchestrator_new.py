"""Unit tests for the new AutomationAgent orchestrator.

All components (planner, coordinator, actuator, skill_registry) are mocked.
The EventLogger is real (writes to tmp_log_dir).
"""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.config import AgentConfig
from automation_agent.logging.event_logger import EventLogger
from automation_agent.logging.models import EventType
from automation_agent.orchestrator.agent import AutomationAgent
from automation_agent.shared_models import ActionPlan, ActionStep, ExecutionResult, StepResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> AgentConfig:
    """Create an AgentConfig for tests with sensible defaults."""
    defaults = {"_env_file": None, "anthropic_api_key": "test-key-not-real"}
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_plan(steps, goal="Test goal"):
    """Shortcut to create an ActionPlan."""
    return ActionPlan(steps=steps, goal=goal)


def _make_agent(
    planner,
    skill_registry,
    coordinator,
    actuator,
    logger,
    config=None,
):
    """Create an AutomationAgent with the given mocks."""
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSkillMatching:
    """Tests for skill matching in the orchestrator."""

    async def test_skill_matched_context_passed_to_planner(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """1. When a skill matches, its context is passed to planner.plan()."""
        mock_skill_registry.match.return_value = {
            "skill_name": "open_app",
            "params": {"app_name": "Calculator"},
        }
        mock_skill_registry.expand.return_value = "Use Spotlight to open Calculator"

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        await agent.execute("Open Calculator")

        # Verify planner.plan was called with skill_context
        mock_planner.plan.assert_awaited_once()
        call_kwargs = mock_planner.plan.call_args
        assert call_kwargs.kwargs.get("skill_context") == "Use Spotlight to open Calculator"

    async def test_no_skill_planner_called_without_context(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """2. When no skill matches, planner is called with skill_context=None."""
        mock_skill_registry.match.return_value = None

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        await agent.execute("Do something custom")

        mock_planner.plan.assert_awaited_once()
        call_kwargs = mock_planner.plan.call_args
        assert call_kwargs.kwargs.get("skill_context") is None


class TestElementFinding:
    """Tests for element finding in click actions."""

    async def test_element_param_triggers_find_element(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """3. Click step with 'element' param calls coordinator.find_element."""
        mock_planner.plan = AsyncMock(
            return_value=_make_plan([
                ActionStep(
                    action="click",
                    params={"element": "the submit button"},
                    verify="Submit button clicked",
                ),
                ActionStep(action="done", params={}, verify=""),
            ])
        )

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        await agent.execute("Click submit")

        mock_coordinator.find_element.assert_awaited_with("the submit button")

    async def test_element_not_found_returns_failure(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """4. Element not found returns failure (does not blindly retry)."""
        mock_coordinator.find_element = AsyncMock(return_value=None)
        mock_planner.plan = AsyncMock(
            return_value=_make_plan([
                ActionStep(
                    action="click",
                    params={"element": "nonexistent button"},
                    verify="Button clicked",
                    on_fail="abort",
                ),
                ActionStep(action="done", params={}, verify=""),
            ])
        )
        # Make vision verification fail too since element wasn't found
        mock_coordinator.verify_condition = AsyncMock(return_value=False)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Click nonexistent")

        assert result.success is False
        # The actuator click should NOT have been called (element wasn't found)
        mock_actuator.click.assert_not_called()


class TestVerificationTiers:
    """Tests for verification tier behavior in orchestrator context."""

    async def test_verify_tier1_pass_skips_tier2(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """5. Verify tier 1 pass means tier 2 (vision) is not called."""
        # Set up actuator to return Calculator as frontmost
        mock_actuator.get_state.return_value = {
            "app_name": "Calculator",
            "app_bundle": "com.apple.Calculator",
            "window_title": "Calculator",
        }

        mock_planner.plan = AsyncMock(
            return_value=_make_plan([
                ActionStep(
                    action="activate_app",
                    params={"app_name": "Calculator"},
                    verify="Calculator is the frontmost application",
                ),
                ActionStep(action="done", params={}, verify=""),
            ])
        )

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Open Calculator")

        assert result.success is True
        # verify_condition (tier 2) should NOT have been called for the activate_app step
        # It might be called 0 or more times for other reasons, but the verify for
        # the activate_app step should have been resolved by tier 1.
        # Check that the step result used hammerspoon_state method
        activate_step_results = [
            sr for sr in result.steps if sr.step.action == "activate_app"
        ]
        assert len(activate_step_results) == 1
        assert activate_step_results[0].verification_method == "hammerspoon_state"

    async def test_verify_tier1_ambiguous_escalates_to_tier2(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """6. Verify tier 1 ambiguous for click actions escalates to tier 2."""
        mock_planner.plan = AsyncMock(
            return_value=_make_plan([
                ActionStep(
                    action="click",
                    params={"x": 500, "y": 300},
                    verify="Button appears pressed",
                ),
                ActionStep(action="done", params={}, verify=""),
            ])
        )

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Click button")

        assert result.success is True
        click_results = [sr for sr in result.steps if sr.step.action == "click"]
        assert len(click_results) == 1
        assert click_results[0].verification_method == "vision"

    async def test_verify_tier2_pass(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """7. Verify tier 2 pass succeeds."""
        mock_coordinator.verify_condition = AsyncMock(return_value=True)
        mock_planner.plan = AsyncMock(
            return_value=_make_plan([
                ActionStep(
                    action="type_text",
                    params={"text": "hello"},
                    verify="Text field contains hello",
                ),
                ActionStep(action="done", params={}, verify=""),
            ])
        )

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Type hello")

        assert result.success is True
        type_results = [sr for sr in result.steps if sr.step.action == "type_text"]
        assert len(type_results) == 1
        assert type_results[0].verification_method == "vision"
        assert type_results[0].success is True


class TestFailureHandling:
    """Tests for failure handling policies."""

    async def test_verify_fail_retry_different(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """8. Verify fail + on_fail=retry_different triggers retry."""
        # First verify fails, second succeeds
        verify_results = iter([False, True])
        mock_coordinator.verify_condition = AsyncMock(side_effect=lambda cond: next(verify_results))

        mock_planner.plan = AsyncMock(
            return_value=_make_plan([
                ActionStep(
                    action="click",
                    params={"x": 500, "y": 300},
                    verify="Button clicked",
                    on_fail="retry_different",
                    max_retries=3,
                ),
                ActionStep(action="done", params={}, verify=""),
            ])
        )

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Click button")

        # Should have retried and eventually succeeded
        event_types = [e.event_type for e in logger.events]
        assert EventType.STEP_RETRY in event_types

    async def test_verify_fail_replan(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """9. Verify fail + on_fail=replan triggers replanning."""
        mock_coordinator.verify_condition = AsyncMock(return_value=False)

        mock_planner.plan = AsyncMock(
            return_value=_make_plan([
                ActionStep(
                    action="click",
                    params={"x": 500, "y": 300},
                    verify="Button clicked",
                    on_fail="replan",
                ),
                ActionStep(action="done", params={}, verify=""),
            ])
        )
        # Replan returns a new plan that succeeds
        mock_planner.replan = AsyncMock(
            return_value=_make_plan([
                ActionStep(action="done", params={}, verify=""),
            ])
        )

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Click button")

        mock_planner.replan.assert_awaited_once()
        event_types = [e.event_type for e in logger.events]
        assert EventType.STEP_REPLAN in event_types

    async def test_verify_fail_abort(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """10. Verify fail + on_fail=abort returns failure immediately."""
        mock_coordinator.verify_condition = AsyncMock(return_value=False)

        mock_planner.plan = AsyncMock(
            return_value=_make_plan([
                ActionStep(
                    action="click",
                    params={"x": 500, "y": 300},
                    verify="Button clicked",
                    on_fail="abort",
                ),
                ActionStep(action="done", params={}, verify=""),
            ])
        )

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Click button")

        assert result.success is False

    async def test_max_retries_exhausted(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """11. Max retries exhausted escalates (no infinite loop)."""
        # Verification always fails
        mock_coordinator.verify_condition = AsyncMock(return_value=False)

        mock_planner.plan = AsyncMock(
            return_value=_make_plan([
                ActionStep(
                    action="click",
                    params={"x": 500, "y": 300},
                    verify="Button clicked",
                    on_fail="retry_different",
                    max_retries=2,
                ),
                ActionStep(action="done", params={}, verify=""),
            ])
        )

        logger = EventLogger(tmp_log_dir)
        config = _make_config(max_iterations=10)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger, config
        )

        result = await agent.execute("Click button")

        # Should not run forever -- retries are bounded
        assert result.iterations <= 10


class TestMaxIterations:
    """Tests for max iterations limit."""

    async def test_max_iterations_returns_failure(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """12. Max iterations reached returns failure with evidence."""
        # Plan with many steps
        steps = [
            ActionStep(
                action="click",
                params={"x": i, "y": i},
                verify="Something",
            )
            for i in range(30)
        ]
        steps.append(ActionStep(action="done", params={}, verify=""))

        mock_planner.plan = AsyncMock(return_value=_make_plan(steps))

        logger = EventLogger(tmp_log_dir)
        config = _make_config(max_iterations=3)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger, config
        )

        result = await agent.execute("Do many things")

        assert result.success is False
        assert "Max iterations" in result.message
        assert result.iterations <= 3


class TestSpecialSteps:
    """Tests for special step types (done, wait_for_user)."""

    async def test_wait_for_user_step(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """13. wait_for_user step returns with waiting message."""
        mock_planner.plan = AsyncMock(
            return_value=_make_plan([
                ActionStep(
                    action="wait_for_user",
                    params={"message": "Please log in"},
                    verify="",
                ),
                ActionStep(action="done", params={}, verify=""),
            ])
        )

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Do something requiring login")

        assert result.success is True
        wait_results = [sr for sr in result.steps if sr.step.action == "wait_for_user"]
        assert len(wait_results) == 1
        assert "Waiting for user" in wait_results[0].evidence

    async def test_done_step_returns_success(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """14. done step returns success."""
        mock_planner.plan = AsyncMock(
            return_value=_make_plan([
                ActionStep(action="done", params={}, verify=""),
            ])
        )

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Nothing to do")

        assert result.success is True
        done_results = [sr for sr in result.steps if sr.step.action == "done"]
        assert len(done_results) == 1
        assert "done" in done_results[0].evidence.lower()


class TestStepResultEvidence:
    """Tests for StepResult evidence quality."""

    async def test_all_step_results_have_nonempty_evidence(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """15. All StepResults have non-empty evidence."""
        mock_planner.plan = AsyncMock(
            return_value=_make_plan([
                ActionStep(
                    action="activate_app",
                    params={"app_name": "Safari"},
                    verify="Safari is frontmost app",
                ),
                ActionStep(
                    action="click",
                    params={"x": 100, "y": 200},
                    verify="Element visible",
                ),
                ActionStep(
                    action="wait_for_user",
                    params={"message": "Check something"},
                    verify="",
                ),
                ActionStep(action="done", params={}, verify=""),
            ])
        )
        # Make sure activate_app uses tier 1 by matching the app
        mock_actuator.get_state.return_value = {
            "app_name": "Safari",
            "app_bundle": "com.apple.Safari",
            "window_title": "Google",
        }

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Multi-step task")

        assert len(result.steps) >= 3
        for sr in result.steps:
            assert sr.evidence != "", f"Step {sr.step.action} has empty evidence"


class TestLogging:
    """Tests for event logging."""

    async def test_events_logged_for_every_phase(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """16. Events logged for every action/decision/verification."""
        mock_planner.plan = AsyncMock(
            return_value=_make_plan([
                ActionStep(
                    action="activate_app",
                    params={"app_name": "Safari"},
                    verify="Safari is frontmost app",
                ),
                ActionStep(action="done", params={}, verify=""),
            ])
        )
        mock_actuator.get_state.return_value = {
            "app_name": "Safari",
            "app_bundle": "com.apple.Safari",
        }

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        await agent.execute("Open Safari")

        event_types = {e.event_type for e in logger.events}

        # Must have logged: task start, plan start/complete, step start/complete, task complete
        assert EventType.TASK_START in event_types
        assert EventType.PLAN_START in event_types
        assert EventType.PLAN_COMPLETE in event_types
        assert EventType.STEP_START in event_types
        assert EventType.STEP_COMPLETE in event_types
        assert EventType.TASK_COMPLETE in event_types

    async def test_screenshots_saved_at_verification(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """17. Screenshots saved at verification steps."""
        mock_coordinator.capture_screenshot = AsyncMock(
            return_value=base64.b64encode(b"fake_png_data").decode()
        )
        mock_planner.plan = AsyncMock(
            return_value=_make_plan([
                ActionStep(
                    action="click",
                    params={"x": 100, "y": 200},
                    verify="Element visible",
                ),
                ActionStep(action="done", params={}, verify=""),
            ])
        )

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Click something")

        # The click step should go to tier 2 (vision) which captures screenshots
        click_results = [sr for sr in result.steps if sr.step.action == "click"]
        assert len(click_results) == 1
        assert click_results[0].screenshot_path is not None


class TestExceptionHandling:
    """Tests for exception handling."""

    async def test_exception_during_execution_graceful_failure(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """18. Exception during execution returns graceful failure with error."""
        mock_planner.plan = AsyncMock(side_effect=RuntimeError("LLM connection failed"))

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Something that fails")

        assert result.success is False
        assert "LLM connection failed" in result.message
        assert result.error is not None
        assert "LLM connection failed" in result.error
