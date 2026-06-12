"""Precondition semantics: inconclusive checks must not abort steps.

Found via sandbox e2e: on a fresh/blank desktop every verification tier
returns inconclusive for step-0 preconditions like "a browser is open",
and the verifier's actuator-result fallback coerced that to False —
aborting perfectly executable plans before their first action.

Contract:
- Precondition CONCLUSIVELY denied (a tier produced evidence) -> step fails
  with precondition_failed, action never dispatched.
- Precondition INCONCLUSIVE (no tier conclusive) -> proceed with the action.
- Precondition passed -> proceed.
"""

from unittest.mock import AsyncMock

import pytest

from automation_agent.config import AgentConfig
from automation_agent.orchestrator import AutomationAgent
from automation_agent.shared_models import ActionPlan, ActionStep, StepResult


def _make_agent(mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_path):
    config = AgentConfig(
        model_provider="local",
        log_dir=tmp_path / "logs",
        event_log_dir=tmp_path / "events",
    )
    return AutomationAgent(
        planner=mock_planner,
        skill_registry=mock_skill_registry,
        coordinator=mock_coordinator,
        actuator=mock_actuator,
        config=config,
    )


def _step_with_precondition():
    return ActionStep(
        action="open_url",
        params={"url": "http://localhost:8000/form.html"},
        precondition="A browser window is open and visible",
        verify="The contact form page is shown",
    )


def _result(step, success, method, evidence):
    return StepResult(
        step=step,
        success=success,
        verification_method=method,
        evidence=evidence,
    )


@pytest.fixture
def agent(mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_path):
    return _make_agent(
        mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_path
    )


async def _run_step(agent, step):
    plan = ActionPlan(steps=[step], goal="test")
    result, _ = await agent._execute_step_inner(0, step, [], "test", plan)
    return result


class TestInconclusivePrecondition:
    async def test_inconclusive_precondition_proceeds_with_action(
        self, agent, mock_actuator
    ):
        step = _step_with_precondition()
        pre = _result(step, False, "", "No verifier conclusive. Actuator: {}")
        post = _result(step, True, "actuator_state", "URL matches destination")
        agent.verifier = AsyncMock()
        agent.verifier.verify = AsyncMock(side_effect=[pre, post])

        result = await _run_step(agent, step)

        mock_actuator.open_url.assert_called_once()
        assert result.success is True
        assert result.error is None


class TestConclusiveDenial:
    async def test_denied_precondition_on_nondestructive_step_proceeds(
        self, agent, mock_actuator
    ):
        """Scenery denials ("the macOS desktop is visible") must not veto a
        reversible action — the step's own mandatory verify judges it."""
        step = _step_with_precondition()
        pre = _result(step, False, "vision", "Vision denies: no macOS desktop visible")
        post = _result(step, True, "actuator_state", "URL matches destination")
        agent.verifier = AsyncMock()
        agent.verifier.verify = AsyncMock(side_effect=[pre, post])

        result = await _run_step(agent, step)

        mock_actuator.open_url.assert_called_once()
        assert result.success is True

    async def test_denied_precondition_on_destructive_step_fails_without_dispatching(
        self, agent, mock_actuator
    ):
        """Destructive steps keep the hard gate: denied precondition = no dispatch."""
        step = ActionStep(
            action="click",
            params={"element": "Delete all items button"},
            precondition="The trash review page is shown",
            verify="Items deleted",
            destructive=True,
        )
        pre = _result(step, False, "vision", "Vision denies: trash page not shown")
        agent.verifier = AsyncMock()
        agent.verifier.verify = AsyncMock(side_effect=[pre])

        result = await _run_step(agent, step)

        mock_actuator.click.assert_not_called()
        assert result.success is False
        assert result.error is not None
        assert result.error.startswith("precondition_failed:")


class TestPassingPrecondition:
    async def test_passing_precondition_proceeds(self, agent, mock_actuator):
        step = _step_with_precondition()
        pre = _result(step, True, "vision", "Vision confirms: browser visible")
        post = _result(step, True, "actuator_state", "URL matches destination")
        agent.verifier = AsyncMock()
        agent.verifier.verify = AsyncMock(side_effect=[pre, post])

        result = await _run_step(agent, step)

        mock_actuator.open_url.assert_called_once()
        assert result.success is True
