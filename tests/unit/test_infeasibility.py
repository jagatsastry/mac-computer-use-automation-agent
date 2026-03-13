"""Unit tests for Gap 5: Infeasibility Detection (AC-1 through AC-5)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.config import AgentConfig, ConfirmMode
from automation_agent.logging.event_logger import EventLogger
from automation_agent.logging.models import EventType
from automation_agent.orchestrator.agent import (
    AutomationAgent,
    FrustrationScore,
)
from automation_agent.orchestrator.confirmation import AutoDenyConfirmationHandler
from automation_agent.shared_models import (
    ActionPlan,
    ActionStep,
    ExecutionResult,
    StepResult,
)


def _make_config(**overrides) -> AgentConfig:
    """Create an AgentConfig for tests with sensible defaults."""
    defaults = {
        "_env_file": None,
        "anthropic_api_key": "test-key-not-real",
        "model_provider": "local",
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_agent(
    planner=None,
    skill_registry=None,
    coordinator=None,
    actuator=None,
    logger=None,
    config=None,
    confirmation_handler=None,
):
    """Create an AutomationAgent with mocked dependencies."""
    if planner is None:
        planner = AsyncMock()
    if skill_registry is None:
        skill_registry = AsyncMock()
        skill_registry.match = AsyncMock(return_value=None)
    if coordinator is None:
        coordinator = AsyncMock()
        coordinator.describe_screen = AsyncMock(return_value="Screen desc")
        coordinator.capture_screenshot = AsyncMock(return_value="base64img")
        coordinator.verify_condition = AsyncMock(return_value=True)
    if actuator is None:
        actuator = MagicMock()
        actuator.get_state.return_value = {"frontmost_app": "TestApp"}
    if config is None:
        config = _make_config()
    if logger is None:
        logger = MagicMock(spec=EventLogger)
        logger.run_id = "test-run"
    if confirmation_handler is None:
        confirmation_handler = AutoDenyConfirmationHandler()

    return AutomationAgent(
        planner=planner,
        skill_registry=skill_registry,
        coordinator=coordinator,
        actuator=actuator,
        config=config,
        logger=logger,
        confirmation_handler=confirmation_handler,
    )


# ---------------------------------------------------------------------------
# FrustrationScore unit tests
# ---------------------------------------------------------------------------


class TestFrustrationScore:
    """Tests for FrustrationScore dataclass."""

    def test_frustration_score_resets_on_progress(self):
        """AC-1: same_state_count resets to 0 on visible progress."""
        fs = FrustrationScore(same_state_count=5, identical_action_count=3)
        fs.reset_on_progress()
        assert fs.same_state_count == 0
        assert fs.identical_action_count == 0
        # replan_count should NOT reset
        fs2 = FrustrationScore(
            same_state_count=5, identical_action_count=3, replan_count=2
        )
        fs2.reset_on_progress()
        assert fs2.replan_count == 2

    def test_frustration_score_or_trigger(self):
        """AC-2: Triggers on same-state=3 OR replan=2."""
        fs = FrustrationScore(same_state_count=3, replan_count=0)
        assert fs.is_triggered(same_state_limit=3, replan_limit=2)

        fs2 = FrustrationScore(same_state_count=0, replan_count=2)
        assert fs2.is_triggered(same_state_limit=3, replan_limit=2)

        fs3 = FrustrationScore(same_state_count=2, replan_count=1)
        assert not fs3.is_triggered(same_state_limit=3, replan_limit=2)

    def test_hard_abort(self):
        """Hard abort after N advisory checks."""
        fs = FrustrationScore(advisory_checks_used=2)
        assert fs.is_hard_abort(max_advisory=2)
        assert not FrustrationScore(advisory_checks_used=1).is_hard_abort(2)

    def test_config_thresholds(self):
        """AC-5: Custom limits override defaults."""
        fs = FrustrationScore(same_state_count=5, replan_count=0)
        # Custom limit of 10 — should NOT trigger with count=5
        assert not fs.is_triggered(same_state_limit=10, replan_limit=5)
        # Custom limit of 3 — SHOULD trigger
        assert fs.is_triggered(same_state_limit=3, replan_limit=5)


# ---------------------------------------------------------------------------
# _check_infeasibility unit tests
# ---------------------------------------------------------------------------


class TestCheckInfeasibility:
    """Tests for _check_infeasibility() method."""

    @pytest.mark.asyncio
    async def test_infeasibility_check_returns_structured_result(self):
        """AC-3: ExecutionResult has infeasibility_reason."""
        planner = AsyncMock()
        planner.plan = AsyncMock(
            return_value=ActionPlan(
                steps=[ActionStep(action="done", params={}, verify="")],
                goal="test",
            )
        )
        planner.check_infeasibility = AsyncMock(
            return_value={"infeasible": True, "reason": "Button not on page"}
        )
        agent = _make_agent(planner=planner)
        frustration = FrustrationScore(same_state_count=3)
        result = await agent._check_infeasibility(
            "Click Buy", frustration, [], force=False
        )
        assert result is not None
        assert isinstance(result, ExecutionResult)
        assert result.infeasibility_reason == "Button not on page"
        assert result.success is False

    @pytest.mark.asyncio
    async def test_planner_says_achievable_resets_counter(self):
        """AC-2: Counter reset on 'still achievable'."""
        planner = AsyncMock()
        planner.check_infeasibility = AsyncMock(
            return_value={"infeasible": False, "reason": "Still possible"}
        )
        agent = _make_agent(planner=planner)
        frustration = FrustrationScore(same_state_count=5)
        config = _make_config(infeasibility_same_state_limit=3)
        agent.config = config

        result = await agent._check_infeasibility(
            "test goal", frustration, [], force=False
        )
        assert result is None  # Not infeasible
        assert frustration.advisory_checks_used == 1
        # same_state_count should reset since it was >= limit
        assert frustration.same_state_count == 0

    @pytest.mark.asyncio
    async def test_advisory_check_cap(self):
        """AC-2+AC-5: Hard abort after max_advisory_checks."""
        fs = FrustrationScore(advisory_checks_used=2)
        config = _make_config(infeasibility_max_advisory_checks=2)
        # Once advisory_checks_used >= max, is_hard_abort returns True
        assert fs.is_hard_abort(config.infeasibility_max_advisory_checks)

    @pytest.mark.asyncio
    async def test_critical_path_absence_immediate_trigger(self):
        """AC-4: Click step with absent element triggers immediately (force=True)."""
        planner = AsyncMock()
        planner.check_infeasibility = AsyncMock(
            return_value={
                "infeasible": True,
                "reason": "Element 'Buy Now' not on page",
            }
        )
        agent = _make_agent(planner=planner)

        # Simulate step result with absent element error
        step = ActionStep(
            action="click",
            params={"element": "Buy Now"},
            verify="Buy page visible",
        )
        sr = StepResult(
            step=step,
            success=False,
            error="Element absent: Buy Now",
            evidence="Element not found",
        )

        result = await agent._check_infeasibility(
            "Purchase item", FrustrationScore(), [sr], force=True
        )
        assert result is not None
        assert result.infeasibility_reason == "Element 'Buy Now' not on page"

    @pytest.mark.asyncio
    async def test_planner_without_check_infeasibility_hard_aborts(self):
        """AC-2: Planner with default protocol method -> returns infeasible."""
        # Use a planner that has no check_infeasibility override
        # (the protocol default returns infeasible=True)
        class MinimalPlanner:
            async def plan(self, goal, **kwargs):
                return ActionPlan(
                    steps=[ActionStep(action="done", params={}, verify="")],
                    goal=goal,
                )

            async def replan(self, goal, screen_description, history, **kw):
                return ActionPlan(
                    steps=[ActionStep(action="done", params={}, verify="")],
                    goal=goal,
                )

            async def check_infeasibility(
                self, goal, absent_elements, failure_history,
                frustration_summary,
            ):
                # Default protocol behavior
                return {
                    "infeasible": True,
                    "reason": "Planner does not support infeasibility"
                    " assessment",
                }

        agent = _make_agent(planner=MinimalPlanner())
        frustration = FrustrationScore(same_state_count=3)
        result = await agent._check_infeasibility(
            "test", frustration, [], force=False
        )
        assert result is not None
        assert result.infeasibility_reason is not None
        assert "does not support" in result.infeasibility_reason

    @pytest.mark.asyncio
    async def test_frustration_score_is_fresh_per_execute(self):
        """AC-1: Two consecutive execute() calls start with zero counters.

        We verify that the FrustrationScore is NOT stored on the agent
        and is created fresh each time by checking the class has no
        persistent frustration attribute.
        """
        # FrustrationScore should be a local variable in execute(),
        # not an instance attribute
        agent = _make_agent()
        assert not hasattr(agent, "frustration")
        assert not hasattr(agent, "_frustration")

        # Also verify fresh creation
        fs1 = FrustrationScore()
        fs1.same_state_count = 10
        fs1.replan_count = 5

        fs2 = FrustrationScore()
        assert fs2.same_state_count == 0
        assert fs2.replan_count == 0

    @pytest.mark.asyncio
    async def test_infeasibility_timeout_returns_infeasible(self):
        """AC-2: Timeout on infeasibility check -> returns ExecutionResult."""
        async def _slow_check(**kwargs):
            await asyncio.sleep(10)  # Will be interrupted by timeout

        planner = AsyncMock()
        planner.check_infeasibility = _slow_check
        config = _make_config(infeasibility_timeout_s=0.01)
        agent = _make_agent(planner=planner, config=config)
        frustration = FrustrationScore(same_state_count=3)

        result = await agent._check_infeasibility(
            "test", frustration, [], force=False
        )
        assert result is not None
        assert result.success is False
        assert "timed out" in result.infeasibility_reason


# ---------------------------------------------------------------------------
# Integration: frustration accumulation in execute()
# ---------------------------------------------------------------------------


class TestFrustrationInExecute:
    """Integration test: verify frustration tracking works end-to-end in execute()."""

    @pytest.mark.asyncio
    async def test_frustration_accumulates_and_aborts(self):
        """AC-2: Repeated failures in execute() trigger infeasibility abort."""
        # Plan that has 5 identical click steps that will all fail
        failing_steps = [
            ActionStep(
                action="click",
                params={"element": "Missing Button"},
                verify="Button clicked",
            )
            for _ in range(5)
        ]
        failing_steps.append(
            ActionStep(action="done", params={}, verify="")
        )

        planner = AsyncMock()
        planner.plan = AsyncMock(
            return_value=ActionPlan(steps=failing_steps, goal="test goal")
        )
        planner.check_infeasibility = AsyncMock(
            return_value={"infeasible": True, "reason": "Element never appears"}
        )

        coordinator = AsyncMock()
        coordinator.describe_screen = AsyncMock(return_value="Screen desc")
        coordinator.capture_screenshot = AsyncMock(return_value="base64img")
        coordinator.find_element = AsyncMock(return_value=None)
        coordinator.verify_condition = AsyncMock(return_value=False)

        actuator = MagicMock()
        actuator.get_state.return_value = {"frontmost_app": "TestApp"}

        agent = _make_agent(
            planner=planner,
            coordinator=coordinator,
            actuator=actuator,
        )

        result = await agent.execute("test goal")
        assert result.success is False
        assert result.infeasibility_reason is not None
        assert "never appears" in result.infeasibility_reason
