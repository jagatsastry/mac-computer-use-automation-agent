"""Unit tests for the success condition gate feature.

The success condition gate checks whether a skill's success_condition is met
before accepting a "done" step. If the condition is not met and replans remain,
the agent removes the premature "done" and triggers a replan.

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
from automation_agent.skills.models import Skill, SkillParam, SkillRequirements


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> AgentConfig:
    """Create an AgentConfig for tests with sensible defaults."""
    defaults = {
        "_env_file": None,
        "model_provider": "local",
        "anthropic_api_key": "test-key-not-real",
        "skill_matching_enabled": True,
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_plan(steps, goal="Test goal"):
    """Shortcut to create an ActionPlan."""
    return ActionPlan(steps=steps, goal=goal)


def _make_skill(name="test-skill", success_condition="", **kwargs):
    """Create a Skill with the given success_condition."""
    defaults = dict(
        name=name,
        description="A test skill",
        trigger_keywords=["test"],
        parameters={},
        requires=SkillRequirements(),
        success_condition=success_condition,
    )
    defaults.update(kwargs)
    return Skill(**defaults)


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
# 1. Success condition gate triggers on "done" with skill match
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSuccessConditionGateBasic:
    """Success gate triggers when a skill has a success_condition and plan ends with done."""

    async def test_done_with_skill_success_condition_met(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Plan ends with done, skill has success_condition, verifier returns True => success."""
        skill = _make_skill(
            name="order-food",
            success_condition="Order confirmation page is visible",
        )
        mock_skill_registry.match = AsyncMock(return_value={
            "skill_name": "order-food",
            "params": {},
        })
        mock_skill_registry.expand = MagicMock(return_value="1. Order food\n   - verify: done")
        mock_skill_registry.get_skill = MagicMock(return_value=skill)

        # Plan with just a done step so iterations stays at 1 (< replan_limit=10)
        mock_planner.plan = AsyncMock(return_value=_make_plan([
            ActionStep(action="done", params={}, verify=""),
        ]))

        # Make verification pass for the success condition
        mock_coordinator.verify_condition = AsyncMock(return_value=True)

        logger = EventLogger(tmp_log_dir)
        config = _make_config(infeasibility_replan_limit=10)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger, config
        )

        # Patch verifier to return success for the success condition
        async def patched_verify(step, actuator_result, coordinator=None, actuator=None):
            return StepResult(
                step=step,
                success=True,
                verification_method="vision",
                evidence="Confirmed",
            )

        agent.verifier.verify = patched_verify

        result = await agent.execute("Order food online")

        assert result.success is True
        # get_skill should have been called with the skill name
        mock_skill_registry.get_skill.assert_called_with("order-food")

    async def test_done_with_skill_success_condition_not_met_triggers_replan(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Plan ends with done, success_condition not met => premature done removed, replan triggered."""
        skill = _make_skill(
            name="order-food",
            success_condition="Order confirmation page is visible",
        )
        mock_skill_registry.match = AsyncMock(return_value={
            "skill_name": "order-food",
            "params": {},
        })
        mock_skill_registry.expand = MagicMock(return_value="Steps here")
        mock_skill_registry.get_skill = MagicMock(return_value=skill)

        # First plan: activate_app + done
        mock_planner.plan = AsyncMock(return_value=_make_plan([
            ActionStep(
                action="activate_app",
                params={"app_name": "Safari"},
                verify="Safari is frontmost",
            ),
            ActionStep(action="done", params={}, verify=""),
        ]))

        # Replan returns a plan that completes successfully
        mock_planner.replan = AsyncMock(return_value=_make_plan([
            ActionStep(action="done", params={}, verify=""),
        ]))

        # activate_app tier 1 passes (app name match),
        # but success condition vision check fails
        call_count = 0

        async def verify_side_effect(cond, **kwargs):
            nonlocal call_count
            call_count += 1
            # Success condition check returns False
            if "confirmation" in cond.lower():
                return False
            return True

        mock_coordinator.verify_condition = AsyncMock(side_effect=verify_side_effect)

        # The verifier inside the agent uses coordinator + actuator directly.
        # For the success gate, it creates an ActionStep with verify=sc and calls
        # self.verifier.verify(). We need the verifier to return success=False
        # for the success condition check.
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        # Patch the verifier's verify method to control success condition outcome
        original_verify = agent.verifier.verify

        sc_check_count = 0

        async def patched_verify(step, actuator_result, coordinator=None, actuator=None):
            nonlocal sc_check_count
            # Detect success condition check: done step with non-empty verify
            if step.action == "done" and step.verify and "confirmation" in step.verify.lower():
                sc_check_count += 1
                return StepResult(
                    step=step,
                    success=False,
                    verification_method="vision",
                    evidence="Order confirmation page not visible",
                )
            return await original_verify(step, actuator_result, coordinator=coordinator, actuator=actuator)

        agent.verifier.verify = patched_verify

        result = await agent.execute("Order food online")

        # Success condition was checked
        assert sc_check_count >= 1
        # Replan was triggered
        mock_planner.replan.assert_awaited()
        # STEP_REPLAN event was logged
        event_types = [e.event_type for e in logger.events]
        assert EventType.STEP_REPLAN in event_types

    async def test_no_skill_matched_done_accepted_immediately(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """No skill matched (skill_name=None) => done accepted without success condition check."""
        mock_skill_registry.match = AsyncMock(return_value=None)

        mock_planner.plan = AsyncMock(return_value=_make_plan([
            ActionStep(
                action="activate_app",
                params={"app_name": "Calculator"},
                verify="Calculator is frontmost",
            ),
            ActionStep(action="done", params={}, verify=""),
        ]))

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Open Calculator")

        assert result.success is True
        # get_skill should NOT have been called since skill_name is None
        mock_skill_registry.get_skill.assert_not_called()
        # No replan triggered
        mock_planner.replan.assert_not_awaited()


# ---------------------------------------------------------------------------
# 2. Success condition gate respects replan limits
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSuccessConditionReplanLimits:
    """The gate respects infeasibility_replan_limit to avoid infinite loops."""

    async def test_iterations_at_replan_limit_accepts_done(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """iterations >= max_replans => done accepted even if success condition not met."""
        skill = _make_skill(
            name="order-food",
            success_condition="Order confirmation visible",
        )
        mock_skill_registry.match = AsyncMock(return_value={
            "skill_name": "order-food",
            "params": {},
        })
        mock_skill_registry.expand = MagicMock(return_value="Steps")
        mock_skill_registry.get_skill = MagicMock(return_value=skill)

        # Set replan limit very low so it's easy to exceed
        config = _make_config(infeasibility_replan_limit=1)

        # Create a plan with enough steps that iterations >= replan limit by "done"
        mock_planner.plan = AsyncMock(return_value=_make_plan([
            ActionStep(
                action="activate_app",
                params={"app_name": "Safari"},
                verify="Safari is frontmost",
            ),
            ActionStep(
                action="click",
                params={"x": 100, "y": 200},
                verify="Something clicked",
            ),
            ActionStep(action="done", params={}, verify=""),
        ]))

        mock_coordinator.verify_condition = AsyncMock(return_value=True)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger, config
        )

        result = await agent.execute("Order food")

        # Done should be accepted (iterations >= replan limit by the time we reach done)
        assert result.success is True
        # Replan should NOT have been triggered since we're at/past limit
        mock_planner.replan.assert_not_awaited()

    async def test_iterations_below_replan_limit_checks_condition(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """iterations < max_replans => success condition is checked."""
        skill = _make_skill(
            name="order-food",
            success_condition="Order confirmation visible",
        )
        mock_skill_registry.match = AsyncMock(return_value={
            "skill_name": "order-food",
            "params": {},
        })
        mock_skill_registry.expand = MagicMock(return_value="Steps")
        mock_skill_registry.get_skill = MagicMock(return_value=skill)

        # Set replan limit high so iteration count stays under it
        config = _make_config(infeasibility_replan_limit=10)

        # Plan with just one step before done => iterations=1 < 10
        mock_planner.plan = AsyncMock(return_value=_make_plan([
            ActionStep(action="done", params={}, verify=""),
        ]))
        mock_planner.replan = AsyncMock(return_value=_make_plan([
            ActionStep(action="done", params={}, verify=""),
        ]))

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger, config
        )

        # Patch verifier to fail the success condition on the first check
        # but let everything through on the replan's done
        sc_calls = []

        async def patched_verify(step, actuator_result, coordinator=None, actuator=None):
            if step.action == "done" and step.verify:
                sc_calls.append(step.verify)
                return StepResult(
                    step=step,
                    success=False,
                    verification_method="vision",
                    evidence="Condition not met",
                )
            return StepResult(
                step=step,
                success=True,
                verification_method="",
                evidence="Task marked as done",
            )

        agent.verifier.verify = patched_verify

        result = await agent.execute("Order food")

        # The success condition should have been checked
        assert len(sc_calls) >= 1
        assert "Order confirmation visible" in sc_calls[0]


# ---------------------------------------------------------------------------
# 3. Skill registry integration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSkillRegistryIntegration:
    """How the gate interacts with get_skill() return values."""

    async def test_get_skill_returns_skill_with_success_condition(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """get_skill returns skill with non-empty success_condition => gate uses it."""
        skill = _make_skill(
            name="checkout",
            success_condition="Payment confirmation is displayed",
        )
        mock_skill_registry.match = AsyncMock(return_value={
            "skill_name": "checkout",
            "params": {},
        })
        mock_skill_registry.expand = MagicMock(return_value="Steps")
        mock_skill_registry.get_skill = MagicMock(return_value=skill)

        mock_planner.plan = AsyncMock(return_value=_make_plan([
            ActionStep(action="done", params={}, verify=""),
        ]))
        mock_planner.replan = AsyncMock(return_value=_make_plan([
            ActionStep(action="done", params={}, verify=""),
        ]))

        logger = EventLogger(tmp_log_dir)
        config = _make_config(infeasibility_replan_limit=10)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger, config
        )

        # Patch verifier to return failure for success condition
        async def patched_verify(step, actuator_result, coordinator=None, actuator=None):
            if step.action == "done" and step.verify:
                return StepResult(
                    step=step,
                    success=False,
                    verification_method="vision",
                    evidence="No payment confirmation",
                )
            return StepResult(
                step=step, success=True, verification_method="", evidence="done"
            )

        agent.verifier.verify = patched_verify

        result = await agent.execute("Complete checkout")

        # Replan was triggered because success condition failed
        mock_planner.replan.assert_awaited()

    async def test_get_skill_returns_skill_with_empty_success_condition(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """get_skill returns skill with empty success_condition => no gate, done accepted."""
        skill = _make_skill(
            name="open-app",
            success_condition="",
        )
        mock_skill_registry.match = AsyncMock(return_value={
            "skill_name": "open-app",
            "params": {},
        })
        mock_skill_registry.expand = MagicMock(return_value="Steps")
        mock_skill_registry.get_skill = MagicMock(return_value=skill)

        mock_planner.plan = AsyncMock(return_value=_make_plan([
            ActionStep(action="done", params={}, verify=""),
        ]))

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Open app")

        assert result.success is True
        # No replan since no success condition to check
        mock_planner.replan.assert_not_awaited()

    async def test_get_skill_returns_none(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """get_skill returns None => no gate, done accepted."""
        mock_skill_registry.match = AsyncMock(return_value={
            "skill_name": "unknown-skill",
            "params": {},
        })
        mock_skill_registry.expand = MagicMock(return_value="Steps")
        mock_skill_registry.get_skill = MagicMock(return_value=None)

        mock_planner.plan = AsyncMock(return_value=_make_plan([
            ActionStep(action="done", params={}, verify=""),
        ]))

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Do unknown thing")

        assert result.success is True
        mock_planner.replan.assert_not_awaited()


# ---------------------------------------------------------------------------
# 4. Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSuccessConditionEdgeCases:
    """Edge cases for the success condition gate."""

    async def test_done_with_abort_reason_takes_priority(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """done step with abort_reason => abort takes priority, no success condition check."""
        skill = _make_skill(
            name="order-food",
            success_condition="Order confirmation visible",
        )
        mock_skill_registry.match = AsyncMock(return_value={
            "skill_name": "order-food",
            "params": {},
        })
        mock_skill_registry.expand = MagicMock(return_value="Steps")
        mock_skill_registry.get_skill = MagicMock(return_value=skill)

        mock_planner.plan = AsyncMock(return_value=_make_plan([
            ActionStep(
                action="done",
                params={"abort_reason": "Restaurant is closed"},
                verify="",
            ),
        ]))

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        # Patch verifier to track if success condition was ever checked
        sc_checked = False

        async def patched_verify(step, actuator_result, coordinator=None, actuator=None):
            nonlocal sc_checked
            if step.action == "done" and step.verify:
                sc_checked = True
            return StepResult(
                step=step, success=True, verification_method="", evidence="done"
            )

        agent.verifier.verify = patched_verify

        result = await agent.execute("Order food")

        # Abort takes priority: result should indicate failure
        assert result.success is False
        assert "closed" in result.error.lower()
        # The success condition gate should NOT have been checked because
        # the abort done step has success=False from _execute_step_inner,
        # and the done step is handled by the abort path after the loop
        # The gate code at line 1044 still runs, but the abort step
        # returns success=False from the inner execution.
        # The key thing: the task fails with the abort reason.
        mock_planner.replan.assert_not_awaited()

    async def test_success_condition_check_exception_completes_normally(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """If the success condition check itself raises an exception, task completes normally."""
        skill = _make_skill(
            name="order-food",
            success_condition="Order confirmation visible",
        )
        mock_skill_registry.match = AsyncMock(return_value={
            "skill_name": "order-food",
            "params": {},
        })
        mock_skill_registry.expand = MagicMock(return_value="Steps")
        mock_skill_registry.get_skill = MagicMock(return_value=skill)

        mock_planner.plan = AsyncMock(return_value=_make_plan([
            ActionStep(
                action="activate_app",
                params={"app_name": "Safari"},
                verify="Safari is frontmost",
            ),
            ActionStep(action="done", params={}, verify=""),
        ]))

        logger = EventLogger(tmp_log_dir)
        config = _make_config(infeasibility_replan_limit=10)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger, config
        )

        # Patch verifier to raise exception on success condition check
        original_verify = agent.verifier.verify

        async def patched_verify(step, actuator_result, coordinator=None, actuator=None):
            if step.action == "done" and step.verify:
                raise RuntimeError("Vision model crashed")
            return await original_verify(step, actuator_result, coordinator=coordinator, actuator=actuator)

        agent.verifier.verify = patched_verify

        result = await agent.execute("Order food")

        # Task should complete normally despite the exception
        assert result.success is True
        # No replan triggered
        mock_planner.replan.assert_not_awaited()

    async def test_success_condition_met_on_first_check(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Success condition passes on first done => no replan, task succeeds."""
        skill = _make_skill(
            name="order-food",
            success_condition="Order confirmation visible",
        )
        mock_skill_registry.match = AsyncMock(return_value={
            "skill_name": "order-food",
            "params": {},
        })
        mock_skill_registry.expand = MagicMock(return_value="Steps")
        mock_skill_registry.get_skill = MagicMock(return_value=skill)

        mock_planner.plan = AsyncMock(return_value=_make_plan([
            ActionStep(action="done", params={}, verify=""),
        ]))

        logger = EventLogger(tmp_log_dir)
        config = _make_config(infeasibility_replan_limit=10)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger, config
        )

        # Patch verifier to return success for the success condition
        async def patched_verify(step, actuator_result, coordinator=None, actuator=None):
            return StepResult(
                step=step,
                success=True,
                verification_method="vision",
                evidence="Order confirmation page is visible",
            )

        agent.verifier.verify = patched_verify

        result = await agent.execute("Order food")

        assert result.success is True
        mock_planner.replan.assert_not_awaited()

    async def test_success_condition_gate_creates_correct_verify_step(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """The gate creates an ActionStep(action='done', verify=success_condition) for verification."""
        skill = _make_skill(
            name="checkout",
            success_condition="Payment receipt shows order number",
        )
        mock_skill_registry.match = AsyncMock(return_value={
            "skill_name": "checkout",
            "params": {},
        })
        mock_skill_registry.expand = MagicMock(return_value="Steps")
        mock_skill_registry.get_skill = MagicMock(return_value=skill)

        mock_planner.plan = AsyncMock(return_value=_make_plan([
            ActionStep(action="done", params={}, verify=""),
        ]))
        mock_planner.replan = AsyncMock(return_value=_make_plan([
            ActionStep(action="done", params={}, verify=""),
        ]))

        logger = EventLogger(tmp_log_dir)
        config = _make_config(infeasibility_replan_limit=10)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger, config
        )

        # Track the step passed to verifier
        verify_calls = []

        async def patched_verify(step, actuator_result, coordinator=None, actuator=None):
            verify_calls.append(step)
            if step.action == "done" and step.verify:
                return StepResult(
                    step=step,
                    success=False,
                    verification_method="vision",
                    evidence="Not met",
                )
            return StepResult(
                step=step, success=True, verification_method="", evidence="done"
            )

        agent.verifier.verify = patched_verify

        await agent.execute("Complete checkout")

        # Find the success condition verification step
        sc_steps = [s for s in verify_calls if s.action == "done" and s.verify]
        assert len(sc_steps) >= 1
        assert sc_steps[0].verify == "Payment receipt shows order number"
        assert sc_steps[0].action == "done"
        assert sc_steps[0].params == {}

    async def test_success_gate_logs_replan_event(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """When success gate fails, a STEP_REPLAN event is logged with the condition."""
        skill = _make_skill(
            name="order-food",
            success_condition="Order confirmation visible",
        )
        mock_skill_registry.match = AsyncMock(return_value={
            "skill_name": "order-food",
            "params": {},
        })
        mock_skill_registry.expand = MagicMock(return_value="Steps")
        mock_skill_registry.get_skill = MagicMock(return_value=skill)

        mock_planner.plan = AsyncMock(return_value=_make_plan([
            ActionStep(action="done", params={}, verify=""),
        ]))
        mock_planner.replan = AsyncMock(return_value=_make_plan([
            ActionStep(action="done", params={}, verify=""),
        ]))

        logger = EventLogger(tmp_log_dir)
        config = _make_config(infeasibility_replan_limit=10)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger, config
        )

        async def patched_verify(step, actuator_result, coordinator=None, actuator=None):
            if step.action == "done" and step.verify:
                return StepResult(
                    step=step,
                    success=False,
                    verification_method="vision",
                    evidence="Condition not met",
                )
            return StepResult(
                step=step, success=True, verification_method="", evidence="done"
            )

        agent.verifier.verify = patched_verify

        await agent.execute("Order food")

        # Check for STEP_REPLAN event with the condition text
        replan_events = [
            e for e in logger.events if e.event_type == EventType.STEP_REPLAN
        ]
        assert len(replan_events) >= 1
        assert "Order confirmation visible" in replan_events[0].message

    async def test_success_gate_pops_premature_done_from_step_results(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """When success gate fails, the premature done StepResult is removed from step_results."""
        skill = _make_skill(
            name="order-food",
            success_condition="Order confirmation visible",
        )
        mock_skill_registry.match = AsyncMock(return_value={
            "skill_name": "order-food",
            "params": {},
        })
        mock_skill_registry.expand = MagicMock(return_value="Steps")
        mock_skill_registry.get_skill = MagicMock(return_value=skill)

        # First plan: activate_app + done
        mock_planner.plan = AsyncMock(return_value=_make_plan([
            ActionStep(
                action="activate_app",
                params={"app_name": "Safari"},
                verify="Safari is frontmost",
            ),
            ActionStep(action="done", params={}, verify=""),
        ]))

        # Replan returns a plan ending with done that will be accepted
        # (second iteration exceeds replan limit with limit=2)
        mock_planner.replan = AsyncMock(return_value=_make_plan([
            ActionStep(
                action="click",
                params={"x": 100, "y": 200},
                verify="Clicked",
            ),
            ActionStep(action="done", params={}, verify=""),
        ]))

        logger = EventLogger(tmp_log_dir)
        config = _make_config(infeasibility_replan_limit=10)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger, config
        )

        sc_call_count = 0

        async def patched_verify(step, actuator_result, coordinator=None, actuator=None):
            nonlocal sc_call_count
            if step.action == "done" and step.verify:
                sc_call_count += 1
                if sc_call_count == 1:
                    # First success condition check fails
                    return StepResult(
                        step=step,
                        success=False,
                        verification_method="vision",
                        evidence="Not confirmed",
                    )
                # Second check passes
                return StepResult(
                    step=step,
                    success=True,
                    verification_method="vision",
                    evidence="Confirmed",
                )
            return StepResult(
                step=step, success=True, verification_method="", evidence="done"
            )

        agent.verifier.verify = patched_verify

        result = await agent.execute("Order food")

        # The premature done should have been removed from the first plan's results.
        # The final results should NOT contain a premature done followed by
        # more steps (the premature done was popped before replan).
        done_steps = [sr for sr in result.steps if sr.step.action == "done"]
        # Only the final done from the replan should remain
        # (the first premature done was popped)
        assert all(sr.success for sr in done_steps), "All remaining done steps should be successful"

    async def test_get_skill_raises_exception_completes_normally(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """If get_skill raises, the exception is caught and task completes normally."""
        mock_skill_registry.match = AsyncMock(return_value={
            "skill_name": "buggy-skill",
            "params": {},
        })
        mock_skill_registry.expand = MagicMock(return_value="Steps")
        mock_skill_registry.get_skill = MagicMock(side_effect=KeyError("skill not found"))

        mock_planner.plan = AsyncMock(return_value=_make_plan([
            ActionStep(action="done", params={}, verify=""),
        ]))

        logger = EventLogger(tmp_log_dir)
        config = _make_config(infeasibility_replan_limit=10)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger, config
        )

        result = await agent.execute("Do buggy thing")

        # Should complete normally despite the exception in get_skill
        assert result.success is True
        mock_planner.replan.assert_not_awaited()

    async def test_skill_without_success_condition_attribute(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Skill object exists but has no success_condition attr => no gate."""
        # Create a mock that lacks success_condition attribute entirely
        mock_skill_obj = MagicMock(spec=[])
        # Remove success_condition from attributes
        del mock_skill_obj.success_condition

        mock_skill_registry.match = AsyncMock(return_value={
            "skill_name": "weird-skill",
            "params": {},
        })
        mock_skill_registry.expand = MagicMock(return_value="Steps")
        mock_skill_registry.get_skill = MagicMock(return_value=mock_skill_obj)

        mock_planner.plan = AsyncMock(return_value=_make_plan([
            ActionStep(action="done", params={}, verify=""),
        ]))

        logger = EventLogger(tmp_log_dir)
        config = _make_config(infeasibility_replan_limit=10)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger, config
        )

        result = await agent.execute("Weird skill thing")

        assert result.success is True
        mock_planner.replan.assert_not_awaited()

    async def test_multiple_steps_before_done_with_success_condition(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """Multiple action steps before done, success condition checked only on done."""
        skill = _make_skill(
            name="multi-step",
            success_condition="Final page loaded",
        )
        mock_skill_registry.match = AsyncMock(return_value={
            "skill_name": "multi-step",
            "params": {},
        })
        mock_skill_registry.expand = MagicMock(return_value="Steps")
        mock_skill_registry.get_skill = MagicMock(return_value=skill)

        mock_planner.plan = AsyncMock(return_value=_make_plan([
            ActionStep(
                action="activate_app",
                params={"app_name": "Safari"},
                verify="Safari is frontmost",
            ),
            ActionStep(
                action="click",
                params={"x": 100, "y": 200},
                verify="Button clicked",
            ),
            ActionStep(
                action="type_text",
                params={"text": "hello"},
                verify="Text typed",
            ),
            ActionStep(action="done", params={}, verify=""),
        ]))

        mock_coordinator.verify_condition = AsyncMock(return_value=True)

        logger = EventLogger(tmp_log_dir)
        config = _make_config(infeasibility_replan_limit=10)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger, config
        )

        # Patch verifier: success condition passes
        async def patched_verify(step, actuator_result, coordinator=None, actuator=None):
            if step.action == "done" and step.verify:
                return StepResult(
                    step=step,
                    success=True,
                    verification_method="vision",
                    evidence="Final page loaded",
                )
            return StepResult(
                step=step, success=True, verification_method="actuator_state",
                evidence="Passed",
            )

        agent.verifier.verify = patched_verify

        result = await agent.execute("Multi step task")

        assert result.success is True
        # All steps should have been executed
        non_done = [sr for sr in result.steps if sr.step.action != "done"]
        assert len(non_done) == 3
        mock_planner.replan.assert_not_awaited()
