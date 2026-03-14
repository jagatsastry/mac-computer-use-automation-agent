"""Tests for replan failure handling in AutomationAgent.

Covers the fix for the bug where _replan_and_continue() would continue
executing subsequent steps even after a step failed. Also covers the
main loop's failure handling when recovery_result is non-None but failed.

All components (planner, coordinator, actuator, skill_registry) are mocked.
"""

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
    defaults = {
        "_env_file": None,
        "anthropic_api_key": "test-key-not-real",
        "model_provider": "local",
        "grounding_model": "",
        "grounding_server_url": "",
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_plan(steps, goal="Test goal"):
    return ActionPlan(steps=steps, goal=goal)


def _make_agent(planner, skill_registry, coordinator, actuator, logger, config=None):
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


def _success_result(step):
    return StepResult(
        step=step,
        success=True,
        verification_method="actuator_state",
        evidence="Step succeeded",
    )


def _failure_result(step, retry_count=0, strategies=None):
    return StepResult(
        step=step,
        success=False,
        verification_method="vision",
        evidence="Step failed",
        retry_count=retry_count,
        retry_strategies_used=strategies or [],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_actuator():
    actuator = MagicMock()
    actuator.is_available = MagicMock(return_value=True)
    actuator.click = MagicMock(return_value={"success": True, "output": ""})
    actuator.type_text = MagicMock(return_value={"success": True, "output": ""})
    actuator.press_key = MagicMock(return_value={"success": True, "output": ""})
    actuator.activate_app = MagicMock(return_value={"success": True, "output": ""})
    actuator.open_url = MagicMock(return_value={"success": True, "output": ""})
    actuator.quit_app = MagicMock(return_value={"success": True, "output": ""})
    actuator.scroll = MagicMock(return_value={"success": True, "output": ""})
    actuator.get_state = MagicMock(
        return_value={
            "app_name": "Safari",
            "app_bundle": "com.apple.Safari",
            "window_title": "Google",
            "window_frame": '{"x":0,"y":25,"w":1440,"h":875}',
        }
    )
    return actuator


@pytest.fixture
def mock_coordinator():
    coordinator = AsyncMock()
    coordinator.find_element = AsyncMock(
        return_value=FindElementResult(x=500, y=300, confidence=0.9, source="vision")
    )
    coordinator.describe_screen = AsyncMock(return_value="Desktop with Safari open")
    coordinator.verify_condition = AsyncMock(return_value=True)
    import base64

    coordinator.capture_screenshot = AsyncMock(
        return_value=base64.b64encode(b"fake_screenshot_png_data").decode()
    )
    return coordinator


@pytest.fixture
def mock_skill_registry():
    registry = MagicMock()
    registry.match = AsyncMock(return_value=None)
    registry.list_skills = MagicMock(return_value=[])
    registry.expand = MagicMock(return_value=None)
    registry.validate_all = MagicMock(return_value=[])
    return registry


# ---------------------------------------------------------------------------
# Test: Replan retries failed step
# ---------------------------------------------------------------------------


class TestReplanFailureHandling:
    """Tests for _replan_and_continue failure handling."""

    async def test_replan_retries_failed_step(
        self, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Replan loop should call _handle_failure which retries failed steps."""
        step1 = ActionStep(
            action="click",
            params={"element": "Return button"},
            verify="Return button clicked",
            on_fail="retry_different",
            max_retries=2,
        )
        step2 = ActionStep(action="done", params={}, verify="")

        # Initial plan succeeds step 0, fails step 1 -> triggers replan
        initial_plan = _make_plan(
            [
                ActionStep(
                    action="activate_app",
                    params={"app_name": "Safari"},
                    verify="Safari open",
                ),
                ActionStep(
                    action="click",
                    params={"element": "Orders"},
                    verify="Orders visible",
                    on_fail="replan",
                ),
            ]
        )

        # Replan returns new steps: click Return button (will fail then succeed on retry)
        replan_plan = _make_plan([step1, step2])

        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=initial_plan)
        planner.replan = AsyncMock(return_value=replan_plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        # Track _execute_step calls to count retries
        call_count = 0
        return_button_calls = 0

        async def tracked_execute_step(index, step, history, goal, plan):
            nonlocal call_count, return_button_calls
            call_count += 1
            # Initial plan: step 0 succeeds, step 1 fails (triggers replan)
            if plan == initial_plan:
                if index == 0:
                    return (_success_result(step), False)
                else:
                    return (_failure_result(step), False)
            # Replan: track calls related to "Return button" (including retry variants)
            elem = step.params.get("element", "")
            if "Return button" in elem:
                return_button_calls += 1
                if return_button_calls == 1:
                    # First attempt fails
                    return (_failure_result(step, retry_count=0), False)
                else:
                    # Retry succeeds
                    return (_success_result(step), False)
            return (_success_result(step), False)

        agent._execute_step = tracked_execute_step

        result = await agent.execute("Return an order")

        # The replan should have retried the failed step
        assert call_count >= 3  # initial steps + replan steps + retry
        assert return_button_calls >= 2  # original attempt + at least one retry
        assert result.success  # overall task should succeed after retry

    async def test_replan_stops_after_exhausted_retries(
        self, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """After retries exhaust in replan, should stop -- not continue to next step."""
        failing_step = ActionStep(
            action="click",
            params={"element": "Return button"},
            verify="Return button clicked",
            on_fail="retry_different",
            max_retries=1,  # Only 1 retry allowed
        )
        # This step should NEVER execute if the previous one fails
        next_step = ActionStep(
            action="click",
            params={"element": "Select reason"},
            verify="Reason selected",
        )
        done_step = ActionStep(action="done", params={}, verify="")

        replan_plan = _make_plan([failing_step, next_step, done_step])

        planner = AsyncMock()
        planner.plan = AsyncMock(
            return_value=_make_plan(
                [
                    ActionStep(
                        action="click",
                        params={"element": "X"},
                        verify="X",
                        on_fail="replan",
                    )
                ]
            )
        )
        planner.replan = AsyncMock(return_value=replan_plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        executed_steps = []

        async def tracking_execute_step(index, step, history, goal, plan):
            elem = step.params.get("element", "")
            executed_steps.append((step.action, elem))
            # Fail anything related to "Return button" (including retry variants)
            if "Return button" in elem or "return" in elem.lower():
                return (_failure_result(step), False)
            if elem == "X":
                return (_failure_result(step), False)
            return (_success_result(step), False)

        agent._execute_step = tracking_execute_step

        result = await agent.execute("Return an order")

        # "Select reason" should NOT have been executed
        executed_elements = [e[1] for e in executed_steps]
        assert "Select reason" not in executed_elements
        assert not result.success

    async def test_replan_stops_on_abort(
        self, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """on_fail='abort' in replan should stop immediately without retries."""
        abort_step = ActionStep(
            action="click",
            params={"element": "Confirm delete"},
            verify="Deleted",
            on_fail="abort",  # No retries, just abort
        )
        next_step = ActionStep(
            action="click",
            params={"element": "Next thing"},
            verify="Done",
        )

        replan_plan = _make_plan([abort_step, next_step])

        planner = AsyncMock()
        planner.plan = AsyncMock(
            return_value=_make_plan(
                [
                    ActionStep(
                        action="click",
                        params={"element": "trigger"},
                        verify="x",
                        on_fail="replan",
                    )
                ]
            )
        )
        planner.replan = AsyncMock(return_value=replan_plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        executed_steps = []

        async def tracking_execute_step(index, step, history, goal, plan):
            executed_steps.append(step.params.get("element", ""))
            if step.params.get("element") == "Confirm delete":
                return (_failure_result(step), False)
            if step.params.get("element") == "trigger":
                return (_failure_result(step), False)
            return (_success_result(step), False)

        agent._execute_step = tracking_execute_step

        result = await agent.execute("Delete item")

        assert "Next thing" not in executed_steps
        assert not result.success
        # Abort should not retry — "Confirm delete" should appear exactly once
        assert executed_steps.count("Confirm delete") == 1

    async def test_replan_stops_on_wait_for_user_failure(
        self, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """on_fail='wait_for_user' in replan should stop — recovery returns failed StepResult."""
        wait_step = ActionStep(
            action="click",
            params={"element": "broken thing"},
            verify="fixed",
            on_fail="wait_for_user",
        )
        next_step = ActionStep(
            action="click",
            params={"element": "should not run"},
            verify="x",
        )

        replan_plan = _make_plan([wait_step, next_step])

        planner = AsyncMock()
        planner.plan = AsyncMock(
            return_value=_make_plan(
                [
                    ActionStep(
                        action="click",
                        params={"element": "trigger"},
                        verify="x",
                        on_fail="replan",
                    )
                ]
            )
        )
        planner.replan = AsyncMock(return_value=replan_plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        executed_elements = []

        async def tracking_execute_step(index, step, history, goal, plan):
            executed_elements.append(step.params.get("element", ""))
            if step.params.get("element") in ("broken thing", "trigger"):
                return (_failure_result(step), False)
            return (_success_result(step), False)

        agent._execute_step = tracking_execute_step

        result = await agent.execute("Fix thing")

        assert "should not run" not in executed_elements
        assert not result.success

    async def test_replan_no_infinite_recursion(
        self, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Replan inside replan should NOT trigger another replan -- it should stop."""
        # Step with on_fail="replan" inside a replan loop should NOT recurse
        replan_step = ActionStep(
            action="click",
            params={"element": "problematic"},
            verify="x",
            on_fail="replan",  # Would normally trigger replan, but we're already in one
        )

        replan_plan = _make_plan([replan_step])

        planner = AsyncMock()
        planner.plan = AsyncMock(
            return_value=_make_plan(
                [
                    ActionStep(
                        action="click",
                        params={"element": "trigger"},
                        verify="x",
                        on_fail="replan",
                    )
                ]
            )
        )
        # replan should only be called ONCE (from main loop), not again from replan loop
        planner.replan = AsyncMock(return_value=replan_plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        async def always_fail(index, step, history, goal, plan):
            return (_failure_result(step), False)

        agent._execute_step = always_fail

        result = await agent.execute("Do something")

        # replan should have been called exactly once (from the main loop)
        assert planner.replan.await_count == 1
        assert not result.success

    async def test_replan_success_path_unchanged(
        self, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Successful replan steps still work as before -- no regression."""
        step1 = ActionStep(
            action="click",
            params={"element": "alternative button"},
            verify="clicked",
        )
        done_step = ActionStep(action="done", params={}, verify="")

        replan_plan = _make_plan([step1, done_step])

        planner = AsyncMock()
        planner.plan = AsyncMock(
            return_value=_make_plan(
                [
                    ActionStep(
                        action="click",
                        params={"element": "original"},
                        verify="x",
                        on_fail="replan",
                    )
                ]
            )
        )
        planner.replan = AsyncMock(return_value=replan_plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        executed_steps = []

        async def tracking_execute_step(index, step, history, goal, plan):
            executed_steps.append(step.params.get("element", step.action))
            if step.params.get("element") == "original":
                return (_failure_result(step), False)
            return (_success_result(step), False)

        agent._execute_step = tracking_execute_step

        result = await agent.execute("Do task")

        assert result.success
        assert "alternative button" in executed_steps

    async def test_replan_continues_after_recovery_success(
        self, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """When retry recovery succeeds in replan, execution continues to next step."""
        step1 = ActionStep(
            action="click",
            params={"element": "flaky button"},
            verify="clicked",
            on_fail="retry_different",
            max_retries=3,
        )
        step2 = ActionStep(
            action="click",
            params={"element": "next button"},
            verify="next clicked",
        )
        done_step = ActionStep(action="done", params={}, verify="")

        replan_plan = _make_plan([step1, step2, done_step])

        planner = AsyncMock()
        planner.plan = AsyncMock(
            return_value=_make_plan(
                [
                    ActionStep(
                        action="click",
                        params={"element": "trigger"},
                        verify="x",
                        on_fail="replan",
                    )
                ]
            )
        )
        planner.replan = AsyncMock(return_value=replan_plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        attempt_count = 0

        async def tracking_execute_step(index, step, history, goal, plan):
            nonlocal attempt_count
            if step.params.get("element") == "trigger":
                return (_failure_result(step), False)
            if step.params.get("element") == "flaky button":
                attempt_count += 1
                if attempt_count == 1:
                    return (_failure_result(step, retry_count=0), False)
                # Succeed on retry
                return (_success_result(step), False)
            return (_success_result(step), False)

        agent._execute_step = tracking_execute_step

        result = await agent.execute("Do task")

        # Should have reached "next button" since flaky button succeeded on retry
        assert result.success

    async def test_replan_stops_when_handle_failure_returns_none(
        self, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """When _handle_failure returns None (wants replan) in replan loop, stop execution."""
        step1 = ActionStep(
            action="click",
            params={"element": "always fails"},
            verify="clicked",
            on_fail="retry_different",
            max_retries=0,  # No retries -> _handle_failure escalates to replan (returns None)
        )
        step2 = ActionStep(
            action="click",
            params={"element": "should not execute"},
            verify="x",
        )

        replan_plan = _make_plan([step1, step2])

        planner = AsyncMock()
        planner.plan = AsyncMock(
            return_value=_make_plan(
                [
                    ActionStep(
                        action="click",
                        params={"element": "trigger"},
                        verify="x",
                        on_fail="replan",
                    )
                ]
            )
        )
        planner.replan = AsyncMock(return_value=replan_plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        executed_elements = []

        async def tracking_execute_step(index, step, history, goal, plan):
            executed_elements.append(step.params.get("element", ""))
            return (_failure_result(step), False)

        agent._execute_step = tracking_execute_step

        result = await agent.execute("Do task")

        assert "should not execute" not in executed_elements
        assert not result.success


# ---------------------------------------------------------------------------
# Test: Main loop failure handling
# ---------------------------------------------------------------------------


class TestMainLoopFailureHandling:
    """Tests for the main execute() loop's failure handling."""

    async def test_main_loop_triggers_replan_on_failure(
        self, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Main execute loop triggers replan when retry_different is exhausted."""
        step = ActionStep(
            action="click",
            params={"element": "missing button"},
            verify="clicked",
            on_fail="retry_different",
            max_retries=1,
        )
        done_step = ActionStep(action="done", params={}, verify="")

        initial_plan = _make_plan([step, done_step])
        replan_plan = _make_plan(
            [
                ActionStep(
                    action="click",
                    params={"element": "alt button"},
                    verify="clicked",
                ),
                done_step,
            ]
        )

        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=initial_plan)
        planner.replan = AsyncMock(return_value=replan_plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        async def selective_execute(index, step, history, goal, plan):
            elem = step.params.get("element", "")
            # Fail anything related to "missing button" (including retry variants)
            if "missing" in elem.lower():
                return (_failure_result(step), False)
            return (_success_result(step), False)

        agent._execute_step = selective_execute

        result = await agent.execute("Click button")

        # Replan should have been called
        planner.replan.assert_awaited_once()
        assert result.success

    async def test_main_loop_stops_on_non_abort_recovery_failure(
        self, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Main loop stops (returns failure) when recovery fails, even if on_fail != abort.

        This tests the fix for the main loop fallthrough bug where non-abort
        failed recovery would continue to the next step.
        """
        step1 = ActionStep(
            action="click",
            params={"element": "broken"},
            verify="clicked",
            on_fail="wait_for_user",  # Not abort, not replan
        )
        step2 = ActionStep(
            action="click",
            params={"element": "should not run"},
            verify="x",
        )
        done_step = ActionStep(action="done", params={}, verify="")

        initial_plan = _make_plan([step1, step2, done_step])

        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=initial_plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        executed_elements = []

        async def tracking_execute_step(index, step, history, goal, plan):
            executed_elements.append(step.params.get("element", step.action))
            if step.params.get("element") == "broken":
                return (_failure_result(step), False)
            return (_success_result(step), False)

        agent._execute_step = tracking_execute_step

        result = await agent.execute("Do stuff")

        # "should not run" must NOT have been executed
        assert "should not run" not in executed_elements
        assert not result.success

    async def test_main_loop_abort_returns_failure(
        self, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Main loop returns failure immediately for on_fail=abort steps."""
        step = ActionStep(
            action="click",
            params={"element": "critical"},
            verify="clicked",
            on_fail="abort",
        )
        done_step = ActionStep(action="done", params={}, verify="")

        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=_make_plan([step, done_step]))

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        async def fail_step(index, step, history, goal, plan):
            return (_failure_result(step), False)

        agent._execute_step = fail_step

        result = await agent.execute("Critical task")

        assert not result.success
        # Should not have tried replan
        assert not hasattr(planner.replan, "await_count") or planner.replan.await_count == 0

    async def test_main_loop_replan_on_failure_returns_replan_result(
        self, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """When on_fail=replan and step fails, main loop calls replan and returns its result."""
        step = ActionStep(
            action="click",
            params={"element": "missing"},
            verify="clicked",
            on_fail="replan",
        )

        replan_done = ActionStep(action="done", params={}, verify="")
        replan_plan = _make_plan([replan_done])

        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=_make_plan([step]))
        planner.replan = AsyncMock(return_value=replan_plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        async def execute_step(index, step, history, goal, plan):
            if step.params.get("element") == "missing":
                return (_failure_result(step), False)
            return (_success_result(step), False)

        agent._execute_step = execute_step

        result = await agent.execute("Find element")

        planner.replan.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test: Edge cases
# ---------------------------------------------------------------------------


class TestReplanEdgeCases:
    """Edge case tests for replan handling."""

    async def test_replan_max_iterations_respected(
        self, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Replan loop respects max_iterations config."""
        steps = [
            ActionStep(
                action="click",
                params={"element": f"step{i}"},
                verify="clicked",
            )
            for i in range(10)
        ]

        replan_plan = _make_plan(steps)

        planner = AsyncMock()
        planner.plan = AsyncMock(
            return_value=_make_plan(
                [
                    ActionStep(
                        action="click",
                        params={"element": "trigger"},
                        verify="x",
                        on_fail="replan",
                    )
                ]
            )
        )
        planner.replan = AsyncMock(return_value=replan_plan)

        config = _make_config(max_iterations=3)
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger, config=config
        )

        executed_count = 0

        async def counting_execute_step(index, step, history, goal, plan):
            nonlocal executed_count
            executed_count += 1
            if step.params.get("element") == "trigger":
                return (_failure_result(step), False)
            return (_success_result(step), False)

        agent._execute_step = counting_execute_step

        result = await agent.execute("Do many things")

        # Should not have executed all 10 replan steps due to max_iterations
        assert executed_count <= 4  # 1 initial + at most 3 replan (but iterations capped)

    async def test_replan_empty_plan_returns_failure(
        self, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Replan with empty plan should return failure."""
        replan_plan = _make_plan([])  # Empty plan

        planner = AsyncMock()
        planner.plan = AsyncMock(
            return_value=_make_plan(
                [
                    ActionStep(
                        action="click",
                        params={"element": "trigger"},
                        verify="x",
                        on_fail="replan",
                    )
                ]
            )
        )
        planner.replan = AsyncMock(return_value=replan_plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        async def fail_trigger(index, step, history, goal, plan):
            return (_failure_result(step), False)

        agent._execute_step = fail_trigger

        result = await agent.execute("Empty replan")

        assert not result.success

    async def test_replan_done_step_succeeds(
        self, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Replan that immediately returns done should succeed."""
        replan_plan = _make_plan(
            [ActionStep(action="done", params={}, verify="")]
        )

        planner = AsyncMock()
        planner.plan = AsyncMock(
            return_value=_make_plan(
                [
                    ActionStep(
                        action="click",
                        params={"element": "trigger"},
                        verify="x",
                        on_fail="replan",
                    )
                ]
            )
        )
        planner.replan = AsyncMock(return_value=replan_plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        async def execute_step(index, step, history, goal, plan):
            if step.params.get("element") == "trigger":
                return (_failure_result(step), False)
            return (_success_result(step), False)

        agent._execute_step = execute_step

        result = await agent.execute("Quick finish")

        assert result.success
        assert "replan" in result.message.lower()
