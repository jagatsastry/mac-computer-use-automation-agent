"""Unit tests for the new AutomationAgent orchestrator.

All components (planner, coordinator, actuator, skill_registry) are mocked.
The EventLogger is real (writes to tmp_log_dir).
"""

import base64
import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.config import AgentConfig
from automation_agent.logging.event_logger import EventLogger
from automation_agent.logging.models import EventType
from automation_agent.orchestrator.agent import AutomationAgent
from automation_agent.shared_models import (
    ActionPlan,
    ActionStep,
    ExecutionResult,
    FindElementResult,
    StepResult,
)


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


class _AutoApproveHandler:
    """Auto-approve all destructive confirmations in tests."""

    async def confirm(self, step):
        return True


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
        confirmation_handler=_AutoApproveHandler(),
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

    async def test_trivial_done_skill_plan_falls_back_to_compiled_steps(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """A matched skill should execute compiled fallback steps when planner returns only done."""
        mock_skill_registry.match.return_value = {
            "skill_name": "return-amazon-order",
            "params": {"item": "blue headphones"},
            "expanded_steps": (
                "1. Use open_url to navigate to https://www.amazon.com/gp/your-account/order-history\n"
                "   - verify: Amazon orders page or login page visible\n"
                "2. If login page is visible, wait for user to sign in\n"
                "   - verify: Orders page loaded with search functionality\n"
            ),
        }
        mock_planner.plan = AsyncMock(
            return_value=_make_plan([ActionStep(action="done", params={}, verify="")])
        )
        mock_actuator.get_state.return_value = {
            "app_name": "Safari",
            "window_title": "Blank Start Page",
        }
        mock_coordinator.verify_condition = AsyncMock(
            side_effect=[False, False, False, True]
        )

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute(
            "Return my blue headphones on Amazon, but stop as soon as the Amazon orders page or sign-in page is visible."
        )

        assert result.success is True
        mock_actuator.open_url.assert_called_once_with(
            "https://www.amazon.com/gp/your-account/order-history"
        )

    async def test_trivial_done_skill_plan_is_accepted_when_target_already_visible(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """A done-only plan is acceptable when the compiled skill target already verifies."""
        mock_skill_registry.match.return_value = {
            "skill_name": "open-app-and-navigate",
            "params": {"app_name": "Safari"},
            "expanded_steps": "1. Open Safari\n   - verify: Safari is frontmost app\n",
        }
        mock_planner.plan = AsyncMock(
            return_value=_make_plan([ActionStep(action="done", params={}, verify="")])
        )
        mock_actuator.get_state.return_value = {"app_name": "Safari"}

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Open Safari")

        assert result.success is True
        mock_actuator.activate_app.assert_not_called()


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

        mock_coordinator.find_element.assert_awaited_once()
        call_args = mock_coordinator.find_element.call_args
        assert call_args.args[0] == "the submit button"

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
        # Check that the step result used actuator_state method
        activate_step_results = [
            sr for sr in result.steps if sr.step.action == "activate_app"
        ]
        assert len(activate_step_results) == 1
        assert activate_step_results[0].verification_method == "actuator_state"

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
        mock_coordinator.verify_condition = AsyncMock(
            side_effect=lambda cond, **kwargs: next(verify_results)
        )

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
        mock_skill_registry.match.return_value = {
            "skill_name": "return-amazon-order",
            "params": {"item": "Tylenol"},
            "skill_context": (
                "## Recovery Heuristics\n"
                "- If Return is absent, use View item on the same order card first"
            ),
        }
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
        replan_kwargs = mock_planner.replan.call_args.kwargs
        assert "View item on the same order card first" in replan_kwargs["skill_context"]
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

    async def test_wait_for_user_skips_when_condition_is_not_present(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Conditional waits should skip when their precondition is absent."""
        mock_coordinator.verify_condition = AsyncMock(return_value=False)
        mock_planner.plan = AsyncMock(
            return_value=_make_plan([
                ActionStep(
                    action="wait_for_user",
                    params={
                        "message": "Please sign in to Amazon",
                        "condition": "Amazon login page is visible",
                    },
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
        assert "Skipped wait" in wait_results[0].evidence
        mock_coordinator.capture_screenshot.assert_not_awaited()

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


class TestBugFixes:
    """Tests verifying fixes for bugs found in adversary review."""

    async def test_element_not_found_triggers_replan(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """BUG 1: When find_element returns None, replan is called (not just failure returned)."""
        mock_coordinator.find_element = AsyncMock(return_value=None)
        mock_planner.plan = AsyncMock(
            return_value=_make_plan([
                ActionStep(
                    action="click",
                    params={"element": "nonexistent button"},
                    verify="Button clicked",
                    on_fail="replan",
                ),
                ActionStep(action="done", params={}, verify=""),
            ])
        )
        mock_planner.replan = AsyncMock(
            return_value=_make_plan([
                ActionStep(action="done", params={}, verify=""),
            ])
        )

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Click nonexistent")

        # The actuator click should NOT have been called (element wasn't found)
        mock_actuator.click.assert_not_called()
        # Replan should have been triggered
        mock_planner.replan.assert_awaited_once()

    async def test_retry_params_differ_from_original(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """BUG 6: retry_different actually produces different params/strategy name."""
        call_count = 0
        captured_params = []

        original_execute_step = None

        # Verification always fails to force retries
        mock_coordinator.verify_condition = AsyncMock(return_value=False)

        mock_planner.plan = AsyncMock(
            return_value=_make_plan([
                ActionStep(
                    action="press_key",
                    params={"keys": ["return"]},
                    verify="Key pressed",
                    on_fail="retry_different",
                    max_retries=2,
                ),
                ActionStep(action="done", params={}, verify=""),
            ])
        )
        # Replan returns done to terminate
        mock_planner.replan = AsyncMock(
            return_value=_make_plan([
                ActionStep(action="done", params={}, verify=""),
            ])
        )

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Press enter")

        # Extract retry events and their strategies
        retry_events = [
            e for e in logger.events if e.event_type == EventType.STEP_RETRY
        ]
        strategies = [e.data.get("strategy", "") for e in retry_events]

        # Strategies should be non-empty and differ from each other
        assert len(strategies) >= 2
        assert strategies[0] != strategies[1], (
            f"Retry strategies should differ: {strategies}"
        )

    async def test_click_retry_can_change_action_type(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Second click retry uses a real alternate action instead of ignored params."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        step = ActionStep(
            action="click",
            params={"element": "Continue button"},
            verify="Next screen shown",
            on_fail="retry_different",
            max_retries=3,
        )
        prev = StepResult(
            step=step,
            success=False,
            evidence="not found",
            retry_count=1,
        )

        strategy, retry_step = agent._vary_strategy(step, prev)

        assert strategy == "keyboard_fallback_enter"
        assert retry_step.action == "press_key"
        assert retry_step.params == {"keys": ["return"]}

    async def test_missing_target_retry_uses_suggested_visible_affordance(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """A missing target should retry with the suggested visible control before keyboard fallback."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        step = ActionStep(
            action="click",
            params={"element": "Return or Replace Items button"},
            verify="Return options page is visible",
            on_fail="retry_different",
            max_retries=3,
        )
        prev = StepResult(
            step=step,
            success=False,
            evidence="Action failed: Element not found: Return or Replace Items button. Suggested visible alternative: View item",
            error="Element not found: Return or Replace Items button",
            suggested_element="View item",
        )

        strategy, retry_step = agent._vary_strategy(step, prev)

        assert strategy == "visible_alternative_affordance"
        assert retry_step.action == "click"
        assert retry_step.params["element"] == "View item"

    async def test_execute_step_missing_target_captures_visible_alternative(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Missing click targets should attach a suggested visible alternative when available."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )
        mock_coordinator.capture_screenshot = AsyncMock(return_value="c2NyZWVuc2hvdA==")
        mock_coordinator.suggest_alternative_affordance = AsyncMock(
            return_value={
                "affordance": "View item",
                "reason": "The same order card shows View item instead of Return or Replace Items.",
                "safe_to_try": "yes",
            }
        )

        step = ActionStep(
            action="click",
            params={"element": "Return or Replace Items button"},
            verify="Return options page is visible",
            expected_observation="Return options page is visible",
        )
        mock_coordinator.find_element = AsyncMock(return_value=None)
        plan = _make_plan([step], goal="Return the most recent Tylenol order on Amazon")

        result, _tf = await agent._execute_step(0, step, [], plan.goal, plan)

        assert result.success is False
        assert result.error == "Element not found: Return or Replace Items button"
        assert result.suggested_element == "View item"
        assert "suggested visible alternative" in result.evidence.lower()
        mock_coordinator.suggest_alternative_affordance.assert_awaited_once()

    async def test_search_click_retry_scrolls_to_top_before_retrying(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Search-field retries should recover viewport before another click."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        step = ActionStep(
            action="click",
            params={"element": "search orders text field"},
            verify="Search bar is focused",
            on_fail="retry_different",
            max_retries=3,
        )
        prev = StepResult(
            step=step,
            success=False,
            evidence="not found",
            retry_count=1,
        )

        strategy, retry_step = agent._vary_strategy(step, prev)

        assert strategy == "jump_to_page_top_and_retry_click"
        assert retry_step.action == "click"
        assert retry_step.params["_pre_keys"] == ["cmd", "up"]

    # -----------------------------------------------------------------------
    # _vary_strategy: non-click action retry strategies
    # -----------------------------------------------------------------------

    async def test_type_text_retry_clears_field_first(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """First type_text retry should select-all before retyping."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )
        step = ActionStep(
            action="type_text", params={"text": "hello"}, verify="Text visible",
            on_fail="retry_different", max_retries=3,
        )
        prev = StepResult(step=step, success=False, evidence="typing failed", retry_count=0)

        strategy, retry_step = agent._vary_strategy(step, prev)

        assert strategy == "select_all_then_type"
        assert retry_step.action == "type_text"
        assert retry_step.params.get("_clear_first") is True
        assert retry_step.params.get("text") == "hello"

    async def test_type_text_second_retry_uses_slow_type(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Second type_text retry should type character-by-character."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )
        step = ActionStep(
            action="type_text", params={"text": "hello"}, verify="Text visible",
            on_fail="retry_different", max_retries=3,
        )
        prev = StepResult(step=step, success=False, evidence="typing failed", retry_count=1)

        strategy, retry_step = agent._vary_strategy(step, prev)

        assert strategy == "slow_type_retry"
        assert retry_step.params.get("_slow_type") is True

    async def test_type_text_reflection_refocus_clears_first(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Reflection hint 'refocus_text_field' should clear and retype."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )
        step = ActionStep(
            action="type_text", params={"text": "hello"}, verify="Text visible",
            on_fail="retry_different", max_retries=3,
        )
        prev = StepResult(
            step=step, success=False, evidence="wrong field",
            retry_count=0, reflection_hint="refocus_text_field",
        )

        strategy, retry_step = agent._vary_strategy(step, prev)

        assert strategy == "reflection_refocus_then_type"
        assert retry_step.params.get("_clear_first") is True

    async def test_press_key_retry_adds_delay(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """First press_key retry should add a 0.5s delay."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )
        step = ActionStep(
            action="press_key", params={"keys": ["cmd", "c"]}, verify="Copied",
            on_fail="retry_different", max_retries=3,
        )
        prev = StepResult(step=step, success=False, evidence="key press failed", retry_count=0)

        strategy, retry_step = agent._vary_strategy(step, prev)

        assert strategy == "delayed_key_press"
        assert retry_step.params.get("_pre_delay") == 0.5

    async def test_press_key_second_retry_extends_delay(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Second press_key retry should use 2.0s delay (1.0 * attempt)."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )
        step = ActionStep(
            action="press_key", params={"keys": ["return"]}, verify="Submitted",
            on_fail="retry_different", max_retries=3,
        )
        prev = StepResult(step=step, success=False, evidence="failed", retry_count=1)

        strategy, retry_step = agent._vary_strategy(step, prev)

        assert "extended_delay" in strategy
        assert retry_step.params.get("_pre_delay") == 2.0

    async def test_press_key_keyboard_submit_reflection(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Reflection hint 'keyboard_submit' should retry with return key."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )
        step = ActionStep(
            action="press_key", params={"keys": ["tab"]}, verify="Submitted",
            on_fail="retry_different", max_retries=3,
        )
        prev = StepResult(
            step=step, success=False, evidence="failed",
            retry_count=0, reflection_hint="keyboard_submit",
        )

        strategy, retry_step = agent._vary_strategy(step, prev)

        assert strategy == "reflection_keyboard_submit"
        assert retry_step.params == {"keys": ["return"]}

    async def test_open_url_retry_uses_address_bar_fallback(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """First open_url retry should use browser address bar fallback."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )
        step = ActionStep(
            action="open_url", params={"url": "https://example.com"}, verify="Page loaded",
            on_fail="retry_different", max_retries=3,
        )
        prev = StepResult(step=step, success=False, evidence="URL open failed", retry_count=0)

        strategy, retry_step = agent._vary_strategy(step, prev)

        assert strategy == "browser_address_bar_fallback"
        assert retry_step.params.get("_address_bar_fallback") is True
        assert retry_step.params.get("url") == "https://example.com"

    async def test_open_url_second_retry_adds_delay(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Second open_url retry should add extended delay."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )
        step = ActionStep(
            action="open_url", params={"url": "https://example.com"}, verify="Page loaded",
            on_fail="retry_different", max_retries=3,
        )
        prev = StepResult(step=step, success=False, evidence="failed", retry_count=1)

        strategy, retry_step = agent._vary_strategy(step, prev)

        assert "extended_delay" in strategy
        assert retry_step.params.get("_pre_delay") == 4.0

    async def test_activate_app_retry_quits_first(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """First activate_app retry should quit and relaunch."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )
        step = ActionStep(
            action="activate_app", params={"app_name": "Safari"}, verify="Safari active",
            on_fail="retry_different", max_retries=3,
        )
        prev = StepResult(step=step, success=False, evidence="app not found", retry_count=0)

        strategy, retry_step = agent._vary_strategy(step, prev)

        assert strategy == "quit_and_relaunch"
        assert retry_step.params.get("_quit_first") is True
        assert retry_step.params.get("app_name") == "Safari"

    async def test_activate_app_second_retry_uses_spotlight(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Second activate_app retry should use Spotlight."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )
        step = ActionStep(
            action="activate_app", params={"app_name": "Safari"}, verify="Safari active",
            on_fail="retry_different", max_retries=3,
        )
        prev = StepResult(step=step, success=False, evidence="still failed", retry_count=1)

        strategy, retry_step = agent._vary_strategy(step, prev)

        assert strategy == "spotlight_launch"
        assert retry_step.params.get("_spotlight") is True

    async def test_quit_app_retry_adds_delay(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """First quit_app retry should add delay."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )
        step = ActionStep(
            action="quit_app", params={"app_name": "Finder"}, verify="Finder closed",
            on_fail="retry_different", max_retries=3,
        )
        prev = StepResult(step=step, success=False, evidence="quit failed", retry_count=0)

        strategy, retry_step = agent._vary_strategy(step, prev)

        assert strategy == "delayed_quit"
        assert retry_step.params.get("_pre_delay") == 0.5

    async def test_scroll_retry_uses_generic_fallback(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Scroll retry should use generic delay-based retry."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )
        step = ActionStep(
            action="scroll", params={"direction": "down", "amount": 3}, verify="Scrolled",
            on_fail="retry_different", max_retries=3,
        )
        prev = StepResult(step=step, success=False, evidence="scroll failed", retry_count=0)

        strategy, retry_step = agent._vary_strategy(step, prev)

        assert "generic_retry" in strategy
        assert retry_step.params.get("_pre_delay") == 0.5

    async def test_missing_target_no_alternative_triggers_replan_on_attempt_3(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Missing target with no suggested element should replan after refine attempts.

        Strategy order: attempt 1 = refine_missing_element_query, attempt 2 = refine_missing_target_query,
        attempt 3+ = replan_missing_target.
        """
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )
        step = ActionStep(
            action="click", params={"element": "Submit"}, verify="Submitted",
            on_fail="retry_different", max_retries=3,
        )
        prev = StepResult(
            step=step, success=False, evidence="not found",
            error="Element not found: Submit", retry_count=2,
        )

        strategy, retry_step = agent._vary_strategy(step, prev)

        assert strategy == "replan_missing_target"
        assert retry_step is None

    async def test_dismiss_modal_reflection_hint(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Reflection hint 'dismiss_modal' should press Escape."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )
        step = ActionStep(
            action="click", params={"element": "Save"}, verify="Saved",
            on_fail="retry_different", max_retries=3,
        )
        prev = StepResult(
            step=step, success=False, evidence="modal blocking",
            retry_count=0, reflection_hint="dismiss_modal",
        )

        strategy, retry_step = agent._vary_strategy(step, prev)

        assert strategy == "dismiss_modal_then_retry"
        assert retry_step.action == "press_key"
        assert retry_step.params == {"keys": ["escape"]}

    async def test_dispatch_press_key_accepts_legacy_key_param(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Legacy planner output with key='Return' should still execute."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        step = ActionStep(action="press_key", params={"key": "Return"}, verify="")
        result = await agent._dispatch_action(step)

        assert result["success"] is True
        mock_actuator.press_key.assert_called_once_with(["return"])

    async def test_validate_candidate_uses_multiscale_verification(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Pre-click validation should send both detail and context crops."""
        from PIL import Image

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )
        img = Image.new("RGB", (800, 600), color=(240, 240, 240))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        screenshot_b64 = base64.b64encode(buf.getvalue()).decode()
        mock_coordinator.verify_multiscale_target = AsyncMock(return_value=True)

        result = await agent._validate_candidate(200, 120, "search box", screenshot_b64=screenshot_b64)

        assert result is True
        mock_coordinator.verify_multiscale_target.assert_awaited_once()
        args = mock_coordinator.verify_multiscale_target.await_args.args
        assert args[0] == "search box"
        assert args[1] != args[2]

    async def test_execute_step_reflects_on_failed_verification_with_visible_effect(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """When the screen changed but verify failed, reflection metadata is attached."""
        logger = EventLogger(tmp_log_dir)
        screenshot_diff = MagicMock()
        screenshot_diff.capture_before = MagicMock()
        screenshot_diff.region_changed = MagicMock(return_value=False)
        screenshot_diff.screen_changed = MagicMock(return_value=True)
        agent = _make_agent(
            mock_planner,
            mock_skill_registry,
            mock_coordinator,
            mock_actuator,
            logger,
        )
        agent.screenshot_diff = screenshot_diff
        agent.verifier.verify = AsyncMock(
            return_value=StepResult(
                step=ActionStep(
                    action="click",
                    params={"x": 100, "y": 200},
                    verify="Search box is focused",
                    expected_observation="The search box is focused and cursor is visible",
                ),
                success=False,
                verification_method="vision",
                evidence="Vision denies: Search box is focused",
            )
        )
        mock_coordinator.reflect_action_outcome = AsyncMock(
            return_value={
                "worked": "no",
                "observed": "The page is scrolled to the footer.",
                "hint": "scroll_to_top",
            }
        )
        mock_coordinator.capture_screenshot = AsyncMock(
            return_value=base64.b64encode(b"fake_screenshot_png_data").decode()
        )

        step = ActionStep(
            action="click",
            params={"x": 100, "y": 200},
            verify="Search box is focused",
            expected_observation="The search box is focused and cursor is visible",
        )
        plan = _make_plan([step])

        result, _tf = await agent._execute_step(0, step, [], "Focus the search box", plan)

        assert result.success is False
        assert result.reflection_hint == "scroll_to_top"
        assert "footer" in result.evidence.lower()

    def test_compile_conditional_wait_extracts_visibility_condition(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Conditional wait instructions should carry a machine-checkable condition."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        steps = agent._compile_skill_instruction(
            "If login page is visible, wait for user to sign in",
            "",
        )

        assert steps is not None
        assert len(steps) == 1
        assert steps[0].action == "wait_for_user"
        assert steps[0].params["condition"] == "login page is visible"

    def test_compile_navigate_url_becomes_open_url(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Navigate-to URL instructions should produce open_url, not a click."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        steps = agent._compile_skill_instruction(
            "Navigate to https://www.amazon.com/gp/your-account/order-history",
            "Amazon orders page visible",
        )

        assert steps is not None
        assert len(steps) == 1
        assert steps[0].action == "open_url"
        assert steps[0].params == {"url": "https://www.amazon.com/gp/your-account/order-history"}
        assert steps[0].expected_observation != ""

    async def test_verify_start_and_action_start_events_logged(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """VERIFY_START and ACTION_START events are in the event log."""
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

        await agent.execute("Click something")

        event_types = {e.event_type for e in logger.events}
        assert EventType.VERIFY_START in event_types, (
            f"VERIFY_START missing from events: {event_types}"
        )
        assert EventType.ACTION_START in event_types, (
            f"ACTION_START missing from events: {event_types}"
        )

    async def test_max_retries_actually_retries_multiple_times(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """BUG 4: With max_retries=3, the step is attempted 3+ times before escalating."""
        # Verification always fails to force retries
        mock_coordinator.verify_condition = AsyncMock(return_value=False)

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
        # Replan returns done to terminate
        mock_planner.replan = AsyncMock(
            return_value=_make_plan([
                ActionStep(action="done", params={}, verify=""),
            ])
        )

        logger = EventLogger(tmp_log_dir)
        config = _make_config(max_iterations=20)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger, config
        )

        result = await agent.execute("Click button")

        # Count retry events
        retry_events = [
            e for e in logger.events if e.event_type == EventType.STEP_RETRY
        ]
        # Should have 3 retry attempts (matching max_retries=3)
        assert len(retry_events) >= 3, (
            f"Expected at least 3 retries but got {len(retry_events)}: "
            f"{[e.data for e in retry_events]}"
        )
        # After retries exhausted, should have escalated to replan
        mock_planner.replan.assert_awaited_once()

    async def test_empty_verify_rejected_at_execution(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """BUG 5: Plan with empty verify on non-terminal step -> execute() returns error."""
        mock_planner.plan = AsyncMock(
            return_value=_make_plan([
                ActionStep(
                    action="click",
                    params={"x": 100, "y": 200},
                    verify="",  # EMPTY verify on non-terminal action
                ),
                ActionStep(action="done", params={}, verify=""),
            ])
        )

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Click something")

        assert result.success is False
        assert "validation failed" in result.message.lower() or "verify" in result.message.lower()


# ---------------------------------------------------------------------------
# New tests: features from parallel agent work
# ---------------------------------------------------------------------------


class TestCoerceKeySequence:
    """Tests for _coerce_key_sequence() edge cases."""

    def test_dict_with_keys_list(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Dict with 'keys' list should be unwrapped."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )
        result = agent._coerce_key_sequence({"keys": ["cmd", "a"]})
        assert result == ["cmd", "a"]

    def test_dict_with_key_string(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Dict with 'key' string (legacy) should be parsed."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )
        result = agent._coerce_key_sequence({"key": "Return"})
        assert result == ["return"]

    def test_tuple_input(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Tuple input should be converted to list and processed."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )
        result = agent._coerce_key_sequence(("cmd+c",))
        assert result == ["cmd", "c"]

    def test_none_input(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """None input should return empty list."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )
        result = agent._coerce_key_sequence(None)
        assert result == []

    def test_string_combo(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """String combo like 'cmd+shift+s' should be split."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )
        result = agent._coerce_key_sequence("cmd+shift+s")
        assert result == ["cmd", "shift", "s"]


class TestExtractWaitCondition:
    """Tests for _extract_wait_condition() parsing."""

    def test_if_login_page_appears(self):
        """'If login page appears, wait for user' -> 'login page is visible'."""
        result = AutomationAgent._extract_wait_condition(
            "If login page appears, wait for user to sign in"
        )
        assert result == "login page is visible"

    def test_if_captcha_shown(self):
        """Condition with 'shown' keyword should not append redundant 'is visible'."""
        result = AutomationAgent._extract_wait_condition(
            "If captcha is shown, wait for user"
        )
        assert "visible" in result or "shown" in result

    def test_non_conditional_text_returns_empty(self):
        """Non-matching text should return empty string."""
        result = AutomationAgent._extract_wait_condition("Click the submit button")
        assert result == ""

    def test_case_insensitive(self):
        """Should work case-insensitively."""
        result = AutomationAgent._extract_wait_condition(
            "IF 2FA prompt appears, WAIT FOR USER"
        )
        assert "2fa prompt" in result.lower()
        assert "visible" in result.lower()


class TestConditionsOverlap:
    """Tests for _conditions_overlap() fuzzy matching."""

    def test_exact_match(self):
        """Identical conditions should overlap."""
        assert AutomationAgent._conditions_overlap(
            "Amazon login page is visible",
            "Amazon login page is visible",
        )

    def test_synonym_match_sign_in_login(self):
        """'sign in' and 'login' should be treated as equivalent."""
        assert AutomationAgent._conditions_overlap(
            "Amazon sign-in page is visible",
            "Amazon login page is visible",
        )

    def test_substring_match(self):
        """One condition contained in the other should overlap."""
        assert AutomationAgent._conditions_overlap(
            "login page visible",
            "Amazon login page visible with search bar",
        )

    def test_no_overlap(self):
        """Completely different conditions should not overlap."""
        assert not AutomationAgent._conditions_overlap(
            "Calculator is frontmost",
            "Safari search bar is focused",
        )

    def test_empty_string(self):
        """Empty strings should never overlap."""
        assert not AutomationAgent._conditions_overlap("", "login page")
        assert not AutomationAgent._conditions_overlap("login page", "")


class TestNormalizeConditionText:
    """Tests for _normalize_condition_text() stop word removal and aliasing."""

    def test_stop_words_removed(self):
        """Stop words (the, a, an, is, are, to, be) should be removed."""
        result = AutomationAgent._normalize_condition_text(
            "The login page is visible"
        )
        assert "the" not in result.split()
        assert "is" not in result.split()
        assert "login" in result
        assert "page" in result
        assert "visible" in result

    def test_sign_in_aliased_to_login(self):
        """'sign in' and 'sign-in' should be normalized to 'login'."""
        result = AutomationAgent._normalize_condition_text("Sign in page visible")
        assert "login" in result
        assert "sign" not in result

    def test_log_in_aliased_to_login(self):
        """'log in' should also be normalized to 'login'."""
        result = AutomationAgent._normalize_condition_text("Log in required")
        assert "login" in result


class TestPreKeysExecution:
    """Tests for _pre_keys param being executed before the main action."""

    async def test_pre_keys_executed_before_click(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """_pre_keys should fire press_key before the click action."""
        mock_planner.plan = AsyncMock(
            return_value=_make_plan([
                ActionStep(
                    action="click",
                    params={
                        "element": "search field",
                        "_pre_keys": ["cmd", "up"],
                    },
                    verify="Search field focused",
                ),
                ActionStep(action="done", params={}, verify=""),
            ])
        )

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        await agent.execute("Click search field")

        # pre_keys should have called press_key with ["cmd", "up"]
        calls = mock_actuator.press_key.call_args_list
        assert len(calls) >= 1
        assert calls[0].args[0] == ["cmd", "up"]

    async def test_pre_keys_failure_aborts_action(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """If _pre_keys fails, the main action should not execute."""
        mock_actuator.press_key = MagicMock(return_value={"success": False, "error": "key fail"})
        mock_coordinator.verify_condition = AsyncMock(return_value=False)

        mock_planner.plan = AsyncMock(
            return_value=_make_plan([
                ActionStep(
                    action="type_text",
                    params={
                        "text": "hello",
                        "_pre_keys": ["cmd", "a"],
                    },
                    verify="Text typed",
                    on_fail="abort",
                ),
                ActionStep(action="done", params={}, verify=""),
            ])
        )

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Type hello")

        # type_text should NOT have been called since pre_keys failed
        mock_actuator.type_text.assert_not_called()


class TestScreenToImageCoords:
    """Tests for screen-space ↔ image-space coordinate mapping."""

    def test_screen_to_image_identity_when_same_resolution(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Same screen and image resolution -> identity mapping."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )
        # Patch logical screen size to match image size
        with patch.object(agent, "_get_logical_screen_size", return_value=(1024, 768)):
            ix, iy = agent._screen_to_image_coords(500, 400, 1024, 768)
        assert ix == 500
        assert iy == 400

    def test_screen_to_image_retina_scaling(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Retina: logical 1440x900 screen -> 2880x1800 image should double."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )
        with patch.object(agent, "_get_logical_screen_size", return_value=(1440, 900)):
            ix, iy = agent._screen_to_image_coords(720, 450, 2880, 1800)
        assert ix == 1440
        assert iy == 900

    def test_screen_to_image_clamped_at_boundaries(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Coordinates should be clamped to [0, image_dim - 1]."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )
        with patch.object(agent, "_get_logical_screen_size", return_value=(1440, 900)):
            ix, iy = agent._screen_to_image_coords(2000, 1200, 1024, 768)
        assert ix <= 1023
        assert iy <= 767


class TestClearFirstAndSlowType:
    """Tests for _clear_first and _slow_type params on type_text."""

    async def test_clear_first_sends_cmd_a(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """_clear_first=True should send cmd+a before typing."""
        mock_planner.plan = AsyncMock(
            return_value=_make_plan([
                ActionStep(
                    action="type_text",
                    params={"text": "new text", "_clear_first": True},
                    verify="Text field updated",
                ),
                ActionStep(action="done", params={}, verify=""),
            ])
        )

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        await agent.execute("Replace text")

        # press_key should have been called with cmd+a
        press_calls = mock_actuator.press_key.call_args_list
        assert any(c.args[0] == ["cmd", "a"] for c in press_calls)
        mock_actuator.type_text.assert_called_once_with("new text")


class TestQuitFirstAndSpotlight:
    """Tests for _quit_first and _spotlight params on activate_app."""

    async def test_quit_first_sends_quit_before_activate(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """_quit_first=True should send quit_app before activate_app."""
        mock_actuator.get_state.return_value = {
            "app_name": "Safari",
            "app_bundle": "com.apple.Safari",
            "window_title": "Google",
        }
        mock_planner.plan = AsyncMock(
            return_value=_make_plan([
                ActionStep(
                    action="activate_app",
                    params={"app_name": "Safari", "_quit_first": True},
                    verify="Safari is frontmost",
                ),
                ActionStep(action="done", params={}, verify=""),
            ])
        )

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        await agent.execute("Restart Safari")

        mock_actuator.quit_app.assert_called_once_with("Safari")
        mock_actuator.activate_app.assert_called_once_with("Safari")


class TestClickDispatchScreenCoords:
    """Tests for screen_x/screen_y propagation in click dispatch."""

    async def test_click_with_element_uses_screen_coords(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Click with element should pass screen_x/screen_y to actuator.click()."""
        mock_coordinator.find_element = AsyncMock(
            return_value=FindElementResult(
                x=200, y=150, confidence=0.95, source="vision",
            )
        )
        mock_planner.plan = AsyncMock(
            return_value=_make_plan([
                ActionStep(
                    action="click",
                    params={"element": "the OK button"},
                    verify="Dialog dismissed",
                ),
                ActionStep(action="done", params={}, verify=""),
            ])
        )

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        # Patch _normalize_find_result to inject known screen coords
        original_normalize = agent._normalize_find_result

        def patched_normalize(result, image_size):
            normalized = original_normalize(result, image_size)
            return FindElementResult(
                x=normalized.x,
                y=normalized.y,
                confidence=normalized.confidence,
                source=normalized.source,
                screen_x=400,
                screen_y=300,
            )

        with patch.object(agent, "_normalize_find_result", side_effect=patched_normalize):
            await agent.execute("Click OK")

        # actuator.click should receive screen coords (400, 300), not image coords (200, 150)
        mock_actuator.click.assert_called_once_with(400, 300)

    async def test_click_with_element_falls_back_to_image_coords(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """When screen_x/screen_y are None, should fall back to image x/y."""
        mock_coordinator.find_element = AsyncMock(
            return_value=FindElementResult(
                x=200, y=150, confidence=0.95, source="vision",
                screen_x=None, screen_y=None,
            )
        )
        mock_planner.plan = AsyncMock(
            return_value=_make_plan([
                ActionStep(
                    action="click",
                    params={"element": "the OK button"},
                    verify="Dialog dismissed",
                ),
                ActionStep(action="done", params={}, verify=""),
            ])
        )

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        await agent.execute("Click OK")

        # Falls back to image coords
        mock_actuator.click.assert_called_once_with(200, 150)


class TestAddressBarFallback:
    """Tests for _address_bar_fallback param on open_url."""

    async def test_address_bar_fallback_calls_spotlight_method(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """_address_bar_fallback=True should call _open_url_via_address_bar."""
        mock_planner.plan = AsyncMock(
            return_value=_make_plan([
                ActionStep(
                    action="open_url",
                    params={
                        "url": "https://example.com",
                        "_address_bar_fallback": True,
                    },
                    verify="Page loaded",
                ),
                ActionStep(action="done", params={}, verify=""),
            ])
        )

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        with patch.object(
            agent, "_open_url_via_address_bar",
            new_callable=AsyncMock,
            return_value={"success": True},
        ) as mock_addr_bar:
            await agent.execute("Open example.com via address bar")

            mock_addr_bar.assert_awaited_once_with("https://example.com")
            # Standard open_url should NOT have been called
            mock_actuator.open_url.assert_not_called()
