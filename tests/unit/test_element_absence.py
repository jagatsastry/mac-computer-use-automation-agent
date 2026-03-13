"""Unit tests for element absence detection (AC-4, AC-5, AC-6).

Tests the absence counter in _handle_failure, absent_elements injection
into the replan prompt, and the done+abort_reason graceful abort handler.

All components are mocked. The EventLogger is real (writes to tmp_log_dir).
"""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.config import AgentConfig
from automation_agent.logging.event_logger import EventLogger
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
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_plan(steps, goal="Test goal"):
    return ActionPlan(steps=steps, goal=goal)


class _AutoApproveHandler:
    """Auto-approve all destructive confirmations in tests."""

    async def confirm(self, step):
        return True


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
        confirmation_handler=_AutoApproveHandler(),
    )


def _element_not_found_result(step, element="Return or Replace Items", retry_count=0):
    """Create a StepResult that simulates an element-not-found failure."""
    return StepResult(
        step=step,
        success=False,
        verification_method="",
        evidence=f"Action failed: Element not found: {element}",
        error=f"Element not found: {element}",
        retry_count=retry_count,
    )


# ---------------------------------------------------------------------------
# AC-4: Absence Counter Tests
# ---------------------------------------------------------------------------


class TestAbsenceCounter:
    """AC-4: Confirmed-absent vs. not-yet-found distinction."""

    async def test_absence_counter_triggers_after_two_not_found(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """1. Initial attempt + 1 retry with 'Element not found:' -> error becomes 'Element absent:'."""
        click_step = ActionStep(
            action="click",
            params={"element": "Return or Replace Items"},
            verify="Return options page visible",
            max_retries=3,
        )
        plan = _make_plan([click_step, ActionStep(action="done", params={}, verify="")])
        mock_planner.plan = AsyncMock(return_value=plan)

        # Every click attempt returns element-not-found
        mock_coordinator.find_element = AsyncMock(return_value=None)
        mock_coordinator.verify_condition = AsyncMock(return_value=True)

        # Replan returns a simple done plan
        replan_plan = _make_plan(
            [ActionStep(action="done", params={}, verify="")],
            goal="Replanned",
        )
        mock_planner.replan = AsyncMock(return_value=replan_plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Return my headphones on Amazon")

        # The replan should have been called (failure escalated)
        mock_planner.replan.assert_awaited()

        # Check that at least one step result has "Element absent:" prefix
        absent_results = [
            sr for sr in result.steps
            if sr.error and sr.error.startswith("Element absent:")
        ]
        assert len(absent_results) >= 1, (
            f"Expected at least one 'Element absent:' error, got errors: "
            f"{[sr.error for sr in result.steps if sr.error]}"
        )

    async def test_absence_counter_does_not_trigger_on_single_not_found(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """2. Only 1 'Element not found:' -> error stays 'Element not found:'."""
        click_step = ActionStep(
            action="click",
            params={"element": "Return or Replace Items"},
            verify="Return options page visible",
            max_retries=1,  # Only 1 retry allowed, so only initial attempt
        )
        plan = _make_plan([click_step, ActionStep(action="done", params={}, verify="")])
        mock_planner.plan = AsyncMock(return_value=plan)

        # First attempt: element not found. Then the single retry succeeds.
        call_count = 0

        async def find_element_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # First: not found
            return FindElementResult(x=500, y=300, confidence=0.9, source="vision")

        mock_coordinator.find_element = AsyncMock(side_effect=find_element_side_effect)
        mock_coordinator.verify_condition = AsyncMock(return_value=True)

        replan_plan = _make_plan(
            [ActionStep(action="done", params={}, verify="")],
            goal="Replanned",
        )
        mock_planner.replan = AsyncMock(return_value=replan_plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Return my headphones")

        # Should NOT have any "Element absent:" errors since only 1 not-found
        absent_results = [
            sr for sr in result.steps
            if sr.error and sr.error.startswith("Element absent:")
        ]
        assert len(absent_results) == 0, (
            f"Expected no 'Element absent:' errors, got: "
            f"{[sr.error for sr in result.steps if sr.error]}"
        )

    async def test_absence_counter_keys_on_original_element(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """3. _vary_strategy mutates element desc, but counter still reaches threshold."""
        # The original element description is "Return or Replace Items"
        # _vary_strategy will append "(look carefully, may be partially hidden)" etc
        # but the counter should key on the original element description
        click_step = ActionStep(
            action="click",
            params={"element": "Return or Replace Items"},
            verify="Return options page visible",
            max_retries=3,
        )
        plan = _make_plan([click_step, ActionStep(action="done", params={}, verify="")])
        mock_planner.plan = AsyncMock(return_value=plan)

        # All attempts: element not found
        mock_coordinator.find_element = AsyncMock(return_value=None)
        mock_coordinator.verify_condition = AsyncMock(return_value=True)

        replan_plan = _make_plan(
            [ActionStep(action="done", params={}, verify="")],
            goal="Replanned",
        )
        mock_planner.replan = AsyncMock(return_value=replan_plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Return my headphones")

        # Despite _vary_strategy mutating the element description, absence should still trigger
        absent_results = [
            sr for sr in result.steps
            if sr.error and sr.error.startswith("Element absent:")
        ]
        assert len(absent_results) >= 1, (
            f"Expected 'Element absent:' despite description mutations, got: "
            f"{[sr.error for sr in result.steps if sr.error]}"
        )

    async def test_absence_counter_resets_at_replan(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """4. After replan, absence counter starts fresh (not carried from previous plan).

        First plan: 1 element-not-found (below threshold). Replan triggered by
        on_fail='replan'. Second plan: 1 element-not-found (below threshold).
        Neither plan individually reaches the threshold of 2, so no 'Element absent:'
        errors should appear.
        """
        # First plan: click that fails once then triggers replan
        initial_step = ActionStep(
            action="click",
            params={"element": "Return or Replace Items"},
            verify="Return options page visible",
            max_retries=0,  # No retries -> immediate escalation
            on_fail="replan",
        )
        plan = _make_plan([initial_step, ActionStep(action="done", params={}, verify="")])
        mock_planner.plan = AsyncMock(return_value=plan)

        # Replan: another click that also fails once, then done
        replan_step = ActionStep(
            action="click",
            params={"element": "Return or Replace Items"},
            verify="Return options page visible",
            max_retries=0,  # No retries -> immediate escalation (but we're in replan, so stops)
            on_fail="retry_different",
        )
        replan_plan = _make_plan([replan_step, ActionStep(action="done", params={}, verify="")])
        mock_planner.replan = AsyncMock(return_value=replan_plan)

        # All find_element calls return None (element not found)
        mock_coordinator.find_element = AsyncMock(return_value=None)
        mock_coordinator.verify_condition = AsyncMock(return_value=True)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Return my headphones")

        # Neither plan hit the threshold of 2, so no "Element absent:" errors
        absent_results = [
            sr for sr in result.steps
            if sr.error and sr.error.startswith("Element absent:")
        ]
        assert len(absent_results) == 0, (
            f"Counter should reset at replan. Neither plan hit threshold of 2. "
            f"Got: {[sr.error for sr in result.steps if sr.error]}"
        )

    async def test_absence_ax_confirmation(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """5. AX tree queried when counter threshold met; AX confirms absence."""
        click_step = ActionStep(
            action="click",
            params={"element": "Return or Replace Items"},
            verify="Return options page visible",
            max_retries=3,
        )
        plan = _make_plan([click_step, ActionStep(action="done", params={}, verify="")])
        mock_planner.plan = AsyncMock(return_value=plan)

        mock_coordinator.find_element = AsyncMock(return_value=None)
        mock_coordinator.verify_condition = AsyncMock(return_value=True)

        # Set up accessibility mock that returns elements NOT matching the target
        mock_accessibility = MagicMock()
        mock_el = MagicMock()
        mock_el.title = "Manage your subscription"
        mock_el.description = "Subscription management"
        mock_accessibility.get_accessibility_elements = MagicMock(return_value=[mock_el])
        mock_coordinator.accessibility = mock_accessibility

        replan_plan = _make_plan(
            [ActionStep(action="done", params={}, verify="")],
            goal="Replanned",
        )
        mock_planner.replan = AsyncMock(return_value=replan_plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Return my headphones")

        # AX didn't find a match -> element confirmed absent
        absent_results = [
            sr for sr in result.steps
            if sr.error and sr.error.startswith("Element absent:")
        ]
        assert len(absent_results) >= 1

    async def test_absence_ax_finds_element_prevents_absent_classification(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """5b. AX tree finds a matching element -> stays 'Element not found:' (not absent)."""
        click_step = ActionStep(
            action="click",
            params={"element": "Return or Replace Items"},
            verify="Return options page visible",
            max_retries=3,
        )
        plan = _make_plan([click_step, ActionStep(action="done", params={}, verify="")])
        mock_planner.plan = AsyncMock(return_value=plan)

        mock_coordinator.find_element = AsyncMock(return_value=None)
        mock_coordinator.verify_condition = AsyncMock(return_value=True)

        # AX finds an element whose title contains the target text
        mock_accessibility = MagicMock()
        mock_el = MagicMock()
        mock_el.title = "Return or Replace Items"
        mock_el.description = "Button"
        mock_accessibility.get_accessibility_elements = MagicMock(return_value=[mock_el])
        mock_coordinator.accessibility = mock_accessibility

        replan_plan = _make_plan(
            [ActionStep(action="done", params={}, verify="")],
            goal="Replanned",
        )
        mock_planner.replan = AsyncMock(return_value=replan_plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Return my headphones")

        # AX found a match -> should NOT be classified as absent
        absent_results = [
            sr for sr in result.steps
            if sr.error and sr.error.startswith("Element absent:")
        ]
        assert len(absent_results) == 0, (
            f"AX found matching element, should not be absent. Got: "
            f"{[sr.error for sr in result.steps if sr.error]}"
        )


# ---------------------------------------------------------------------------
# AC-5: Replan Prompt Receives Absence Context
# ---------------------------------------------------------------------------


class TestReplanAbsenceContext:
    """AC-5: Replan prompt receives absence context."""

    async def test_replan_receives_absent_elements(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """6. planner.replan called with absent_elements=["Return or Replace Items"]."""
        click_step = ActionStep(
            action="click",
            params={"element": "Return or Replace Items"},
            verify="Return options page visible",
            max_retries=3,
        )
        plan = _make_plan([click_step, ActionStep(action="done", params={}, verify="")])
        mock_planner.plan = AsyncMock(return_value=plan)

        mock_coordinator.find_element = AsyncMock(return_value=None)
        mock_coordinator.verify_condition = AsyncMock(return_value=True)

        replan_plan = _make_plan(
            [ActionStep(action="done", params={}, verify="")],
            goal="Replanned",
        )
        mock_planner.replan = AsyncMock(return_value=replan_plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        await agent.execute("Return my headphones")

        # Verify replan was called with absent_elements
        mock_planner.replan.assert_awaited()
        call_kwargs = mock_planner.replan.call_args
        absent_elements = call_kwargs.kwargs.get("absent_elements") or (
            call_kwargs[1].get("absent_elements") if len(call_kwargs) > 1 else None
        )
        assert absent_elements is not None, "replan must be called with absent_elements kwarg"
        assert "Return or Replace Items" in absent_elements

    async def test_replan_no_absent_elements_when_none(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """8. No absent elements -> absent_elements=[] or omitted."""
        # This test uses a non-element failure (verification failure, not element-not-found)
        click_step = ActionStep(
            action="click",
            params={"element": "Submit button"},
            verify="Form submitted",
            max_retries=3,
        )
        plan = _make_plan([click_step, ActionStep(action="done", params={}, verify="")])
        mock_planner.plan = AsyncMock(return_value=plan)

        # Element IS found, but verification fails
        mock_coordinator.find_element = AsyncMock(
            return_value=FindElementResult(x=500, y=300, confidence=0.9, source="vision")
        )
        mock_coordinator.verify_condition = AsyncMock(return_value=False)

        replan_plan = _make_plan(
            [ActionStep(action="done", params={}, verify="")],
            goal="Replanned",
        )
        mock_planner.replan = AsyncMock(return_value=replan_plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        await agent.execute("Submit form")

        # If replan was called, absent_elements should be empty or not present
        if mock_planner.replan.called:
            call_kwargs = mock_planner.replan.call_args
            absent_elements = call_kwargs.kwargs.get("absent_elements", [])
            assert absent_elements == [] or absent_elements is None


# ---------------------------------------------------------------------------
# AC-6: Graceful Abort via done + abort_reason
# ---------------------------------------------------------------------------


class TestDoneAbortReason:
    """AC-6: Graceful abort on impossible task."""

    async def test_done_with_abort_reason_returns_failure(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """9. done step with abort_reason -> ExecutionResult(success=False, error=...)."""
        abort_reason = (
            "The 'Return or Replace Items' option is not available for this order, "
            "which may indicate the item is a subscription or digital purchase."
        )
        plan = _make_plan([
            ActionStep(
                action="done",
                params={"abort_reason": abort_reason},
                verify="",
            ),
        ])
        mock_planner.plan = AsyncMock(return_value=plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Return my subscription item")

        assert result.success is False
        assert result.error == abort_reason
        assert "Task aborted" in result.message

    async def test_done_without_abort_reason_returns_success(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """10. Normal done step -> ExecutionResult(success=True)."""
        plan = _make_plan([
            ActionStep(action="done", params={}, verify=""),
        ])
        mock_planner.plan = AsyncMock(return_value=plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Simple task")

        assert result.success is True

    async def test_done_abort_in_replan_returns_failure(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """11. done + abort_reason during _replan_and_continue() -> failure."""
        # First plan: a step that will fail and trigger replan
        click_step = ActionStep(
            action="click",
            params={"element": "Return or Replace Items"},
            verify="Return options page visible",
            max_retries=3,
        )
        plan = _make_plan([click_step, ActionStep(action="done", params={}, verify="")])
        mock_planner.plan = AsyncMock(return_value=plan)

        mock_coordinator.find_element = AsyncMock(return_value=None)
        mock_coordinator.verify_condition = AsyncMock(return_value=True)

        # Replan returns a done step with abort_reason
        abort_reason = "Item is a subscription and cannot be returned"
        replan_plan = _make_plan([
            ActionStep(
                action="done",
                params={"abort_reason": abort_reason},
                verify="",
            ),
        ])
        mock_planner.replan = AsyncMock(return_value=replan_plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Return my subscription item")

        assert result.success is False
        assert result.error == abort_reason
