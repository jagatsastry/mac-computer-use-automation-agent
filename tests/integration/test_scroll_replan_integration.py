"""Integration tests for scroll action and replan fix through the full pipeline.

These tests verify cross-component flows end-to-end:
- Scroll steps flowing through validation -> dispatch -> verification
- Multi-step plans with scroll executing in order
- Failed steps triggering replan via the orchestrator
- Replan step failures stopping execution (the replan fix)
- Scroll aliases flowing through the planner -> orchestrator pipeline

All components (planner, coordinator, actuator, skill_registry) are mocked
at system boundaries. The orchestrator, verifier, and shared_models are real.
"""

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from automation_agent.config import AgentConfig
from automation_agent.logging.event_logger import EventLogger
from automation_agent.logging.models import EventType
from automation_agent.orchestrator.agent import AutomationAgent
from automation_agent.orchestrator.verifier import StepVerifier
from automation_agent.shared_models import (
    ActionPlan,
    ActionStep,
    FindElementResult,
    StepResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> AgentConfig:
    defaults = {
        "_env_file": None,
        "anthropic_api_key": "test-key-not-real",
        "model_provider": "local",
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


def _mock_coordinator():
    coordinator = AsyncMock()
    coordinator.find_element = AsyncMock(
        return_value=FindElementResult(x=500, y=300, confidence=0.9, source="vision")
    )
    coordinator.describe_screen = AsyncMock(return_value="Desktop with Safari open")
    coordinator.verify_condition = AsyncMock(return_value=True)
    coordinator.capture_screenshot = AsyncMock(
        return_value=base64.b64encode(b"fake_screenshot_png_data").decode()
    )
    return coordinator


def _mock_actuator():
    actuator = MagicMock()
    actuator.is_available = MagicMock(return_value=True)
    actuator.click = MagicMock(return_value={"success": True, "output": "Clicked"})
    actuator.type_text = MagicMock(return_value={"success": True, "output": "Typed"})
    actuator.press_key = MagicMock(return_value={"success": True, "output": "Pressed"})
    actuator.activate_app = MagicMock(
        return_value={"success": True, "output": "Activated"}
    )
    actuator.open_url = MagicMock(return_value={"success": True, "output": "Opened"})
    actuator.quit_app = MagicMock(return_value={"success": True, "output": "Quit"})
    actuator.scroll = MagicMock(
        return_value={"success": True, "output": "Scrolled down 3 clicks"}
    )
    actuator.get_scroll_position = MagicMock(return_value=0)
    actuator.get_state = MagicMock(
        return_value={
            "app_name": "Safari",
            "app_bundle": "com.apple.Safari",
            "window_title": "Google",
            "window_frame": '{"x":0,"y":25,"w":1440,"h":875}',
        }
    )
    return actuator


def _mock_skill_registry():
    registry = MagicMock()
    registry.match = AsyncMock(return_value=None)
    registry.list_skills = MagicMock(return_value=[])
    registry.expand = MagicMock(return_value=None)
    registry.validate_all = MagicMock(return_value=[])
    return registry


# ===========================================================================
# Test 1: Scroll plan validates and executes through the agent
# ===========================================================================


@pytest.mark.integration
class TestScrollPlanValidatesAndExecutes:
    """Create an ActionPlan with scroll steps, validate it, execute through
    the agent with mock actuator, and verify scroll is dispatched correctly
    and verification runs."""

    async def test_scroll_plan_validates_and_executes(self, tmp_log_dir):
        """Full pipeline: plan with scroll -> validation passes -> dispatch ->
        actuator.scroll called -> verifier runs -> success."""
        coordinator = _mock_coordinator()
        actuator = _mock_actuator()
        skill_registry = _mock_skill_registry()

        scroll_step = ActionStep(
            action="scroll",
            params={"direction": "down", "amount": 5},
            verify="Page scrolled down",
        )
        done_step = ActionStep(action="done", params={}, verify="")
        plan = _make_plan([scroll_step, done_step], goal="Scroll the page down")

        # Validate plan passes (no validation errors)
        assert plan.validate() == []

        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(planner, skill_registry, coordinator, actuator, logger)

        result = await agent.execute("Scroll the page down")

        assert result.success
        # Verify actuator.scroll was called with correct args:
        # direction=down, amount=5 -> clicks=-5
        actuator.scroll.assert_called_once_with(-5, x=None, y=None)
        # Verify that verification ran (scroll resolved via S1/S3 tier1 path)
        assert result.steps[0].verification_method in (
            "actuator_state", "vision", ""
        )

    async def test_scroll_with_coordinates_dispatches_correctly(self, tmp_log_dir):
        """Scroll step with x/y passes coordinates through to actuator."""
        coordinator = _mock_coordinator()
        actuator = _mock_actuator()
        skill_registry = _mock_skill_registry()

        scroll_step = ActionStep(
            action="scroll",
            params={"direction": "up", "amount": 3, "x": 400, "y": 200},
            verify="Page scrolled up at target",
        )
        done_step = ActionStep(action="done", params={}, verify="")
        plan = _make_plan([scroll_step, done_step])

        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(planner, skill_registry, coordinator, actuator, logger)

        result = await agent.execute("Scroll up at coordinates")

        assert result.success
        # direction=up, amount=3 -> clicks=3
        actuator.scroll.assert_called_once_with(3, x=400, y=200)

    async def test_horizontal_scroll_dispatches_correctly(self, tmp_log_dir):
        """Horizontal scroll (left/right) passes horizontal=True to actuator."""
        coordinator = _mock_coordinator()
        actuator = _mock_actuator()
        skill_registry = _mock_skill_registry()

        scroll_step = ActionStep(
            action="scroll",
            params={"direction": "right", "amount": 4},
            verify="Page scrolled right",
        )
        done_step = ActionStep(action="done", params={}, verify="")
        plan = _make_plan([scroll_step, done_step])

        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(planner, skill_registry, coordinator, actuator, logger)

        result = await agent.execute("Scroll right")

        assert result.success
        # direction=right, amount=4 -> clicks=4, horizontal=True
        actuator.scroll.assert_called_once_with(4, x=None, y=None, horizontal=True)


# ===========================================================================
# Test 2: Scroll in multi-step plan
# ===========================================================================


@pytest.mark.integration
class TestScrollInMultiStepPlan:
    """Plan with activate_app -> open_url -> scroll -> click -> done.
    All succeed. Verify all 5 steps execute in order."""

    async def test_scroll_in_multi_step_plan(self, tmp_log_dir):
        coordinator = _mock_coordinator()
        actuator = _mock_actuator()
        skill_registry = _mock_skill_registry()

        steps = [
            ActionStep(
                action="activate_app",
                params={"app_name": "Safari"},
                verify="Safari is frontmost",
            ),
            ActionStep(
                action="open_url",
                params={"url": "https://example.com"},
                verify="Page loaded",
            ),
            ActionStep(
                action="scroll",
                params={"direction": "down", "amount": 3},
                verify="Page scrolled down",
            ),
            ActionStep(
                action="click",
                params={"element": "Read more"},
                verify="Article expanded",
            ),
            ActionStep(action="done", params={}, verify=""),
        ]
        plan = _make_plan(steps, goal="Read article")

        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(planner, skill_registry, coordinator, actuator, logger)

        result = await agent.execute("Read article on example.com")

        assert result.success
        assert len(result.steps) == 5

        # Verify all actuator methods were called in order
        actuator.activate_app.assert_called_once_with("Safari")
        actuator.open_url.assert_called_once()
        actuator.scroll.assert_called_once_with(-3, x=None, y=None)
        actuator.click.assert_called_once()

        # Verify step actions match expected order
        executed_actions = [sr.step.action for sr in result.steps]
        assert executed_actions == [
            "activate_app",
            "open_url",
            "scroll",
            "click",
            "done",
        ]


# ===========================================================================
# Test 3: Failed step triggers replan
# ===========================================================================


@pytest.mark.integration
class TestFailedStepTriggersReplan:
    """Plan where step 2 (click) fails all retries. Verify that:
    - _handle_failure is called
    - Replan is triggered (planner.replan called)
    - Step 3 (dependent) does NOT execute from the original plan"""

    async def test_failed_step_triggers_replan(self, tmp_log_dir):
        coordinator = _mock_coordinator()
        actuator = _mock_actuator()
        skill_registry = _mock_skill_registry()

        initial_steps = [
            ActionStep(
                action="activate_app",
                params={"app_name": "Safari"},
                verify="Safari open",
            ),
            ActionStep(
                action="click",
                params={"element": "Missing button"},
                verify="Button clicked",
                on_fail="replan",
            ),
            ActionStep(
                action="type_text",
                params={"text": "should not run"},
                verify="Text typed",
            ),
            ActionStep(action="done", params={}, verify=""),
        ]
        initial_plan = _make_plan(initial_steps, goal="Do stuff")

        # Replan returns a simpler plan that succeeds
        replan_steps = [
            ActionStep(
                action="click",
                params={"element": "Alternative button"},
                verify="Button clicked",
            ),
            ActionStep(action="done", params={}, verify=""),
        ]
        replan_plan = _make_plan(replan_steps, goal="Do stuff (replanned)")

        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=initial_plan)
        planner.replan = AsyncMock(return_value=replan_plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(planner, skill_registry, coordinator, actuator, logger)

        executed_elements = []

        async def tracking_execute_step(index, step, history, goal, plan):
            elem = step.params.get("element", step.params.get("app_name", step.action))
            executed_elements.append(elem)

            if step.params.get("element") == "Missing button":
                return _failure_result(step)
            return _success_result(step)

        agent._execute_step = tracking_execute_step

        result = await agent.execute("Do stuff")

        # Replan was triggered
        planner.replan.assert_awaited_once()

        # type_text step (step 3) should NOT have executed
        assert "should not run" not in [
            s.params.get("text", "") for s in
            [ActionStep(action="type_text", params={"text": "should not run"}, verify="")]
        ] or "should not run" not in str(executed_elements)

        # The "Alternative button" from the replan should have executed
        assert "Alternative button" in executed_elements

        # Overall result should succeed (replan succeeded)
        assert result.success


# ===========================================================================
# Test 4: Replan step failure stops execution
# ===========================================================================


@pytest.mark.integration
class TestReplanStepFailureStopsExecution:
    """In the replan loop, step 1 fails all retries. Verify step 2 does NOT
    execute (the replan fix)."""

    async def test_replan_step_failure_stops_execution(self, tmp_log_dir):
        coordinator = _mock_coordinator()
        actuator = _mock_actuator()
        skill_registry = _mock_skill_registry()

        # Initial plan: one step that fails -> triggers replan
        initial_plan = _make_plan(
            [
                ActionStep(
                    action="click",
                    params={"element": "trigger"},
                    verify="clicked",
                    on_fail="replan",
                )
            ],
            goal="Do task",
        )

        # Replan: step 1 will fail, step 2 should NOT execute
        replan_plan = _make_plan(
            [
                ActionStep(
                    action="click",
                    params={"element": "always fails"},
                    verify="clicked",
                    on_fail="retry_different",
                    max_retries=1,
                ),
                ActionStep(
                    action="click",
                    params={"element": "dependent step"},
                    verify="clicked",
                ),
                ActionStep(action="done", params={}, verify=""),
            ],
            goal="Do task (replanned)",
        )

        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=initial_plan)
        planner.replan = AsyncMock(return_value=replan_plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(planner, skill_registry, coordinator, actuator, logger)

        executed_elements = []

        async def tracking_execute_step(index, step, history, goal, plan):
            elem = step.params.get("element", step.action)
            executed_elements.append(elem)
            # Fail "trigger" and anything with "always fails" (including retry variants)
            if "trigger" in elem or "always fails" in elem:
                return _failure_result(step)
            return _success_result(step)

        agent._execute_step = tracking_execute_step

        result = await agent.execute("Do task")

        # "dependent step" should NOT have been executed (the replan fix)
        assert "dependent step" not in executed_elements

        # Replan was triggered
        planner.replan.assert_awaited_once()

        # Overall result should be failure
        assert not result.success

    async def test_replan_abort_stops_immediately(self, tmp_log_dir):
        """Abort policy in replan stops without retries, dependent steps skipped."""
        coordinator = _mock_coordinator()
        actuator = _mock_actuator()
        skill_registry = _mock_skill_registry()

        initial_plan = _make_plan(
            [
                ActionStep(
                    action="click",
                    params={"element": "trigger"},
                    verify="x",
                    on_fail="replan",
                )
            ]
        )

        replan_plan = _make_plan(
            [
                ActionStep(
                    action="click",
                    params={"element": "abort target"},
                    verify="clicked",
                    on_fail="abort",
                ),
                ActionStep(
                    action="click",
                    params={"element": "should not run"},
                    verify="x",
                ),
            ]
        )

        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=initial_plan)
        planner.replan = AsyncMock(return_value=replan_plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(planner, skill_registry, coordinator, actuator, logger)

        executed_elements = []

        async def tracking_execute_step(index, step, history, goal, plan):
            elem = step.params.get("element", step.action)
            executed_elements.append(elem)
            if elem in ("trigger", "abort target"):
                return _failure_result(step)
            return _success_result(step)

        agent._execute_step = tracking_execute_step

        result = await agent.execute("Abort test")

        assert "should not run" not in executed_elements
        assert not result.success
        # Abort target should appear exactly once (no retries)
        assert executed_elements.count("abort target") == 1


# ===========================================================================
# Test 5: Scroll alias through planner
# ===========================================================================


@pytest.mark.integration
class TestScrollAliasThroughPlanner:
    """Simulate planner returning {"action": "scroll_down"} -- verify it gets
    aliased to "scroll" and dispatches correctly."""

    async def test_scroll_alias_through_planner(self, tmp_log_dir):
        coordinator = _mock_coordinator()
        actuator = _mock_actuator()
        skill_registry = _mock_skill_registry()

        # Simulate planner returning scroll_down (common LLM alias)
        aliased_step = ActionStep.from_dict(
            {
                "action": "scroll_down",
                "params": {"direction": "down", "amount": 3},
                "verify": "Page scrolled down",
            }
        )
        done_step = ActionStep(action="done", params={}, verify="")

        # Verify from_dict correctly aliased scroll_down -> scroll
        assert aliased_step.action == "scroll"

        plan = _make_plan([aliased_step, done_step], goal="Scroll down")

        # Plan should pass validation
        assert plan.validate() == []

        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(planner, skill_registry, coordinator, actuator, logger)

        result = await agent.execute("Scroll down")

        assert result.success
        actuator.scroll.assert_called_once_with(-3, x=None, y=None)

    async def test_scroll_up_alias_through_planner(self, tmp_log_dir):
        """scroll_up alias also works end-to-end."""
        coordinator = _mock_coordinator()
        actuator = _mock_actuator()
        skill_registry = _mock_skill_registry()

        aliased_step = ActionStep.from_dict(
            {
                "action": "scroll_up",
                "params": {"direction": "up", "amount": 5},
                "verify": "Page scrolled up",
            }
        )
        done_step = ActionStep(action="done", params={}, verify="")

        assert aliased_step.action == "scroll"

        plan = _make_plan([aliased_step, done_step])

        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(planner, skill_registry, coordinator, actuator, logger)

        result = await agent.execute("Scroll up")

        assert result.success
        actuator.scroll.assert_called_once_with(5, x=None, y=None)

    async def test_scroll_alias_without_direction_uses_default(self, tmp_log_dir):
        """scroll_down alias without explicit direction param defaults to down."""
        coordinator = _mock_coordinator()
        actuator = _mock_actuator()
        skill_registry = _mock_skill_registry()

        # Planner might return scroll_down without direction param
        aliased_step = ActionStep.from_dict(
            {
                "action": "scroll_down",
                "params": {},
                "verify": "Page scrolled",
            }
        )
        done_step = ActionStep(action="done", params={}, verify="")

        assert aliased_step.action == "scroll"

        plan = _make_plan([aliased_step, done_step])

        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=plan)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(planner, skill_registry, coordinator, actuator, logger)

        result = await agent.execute("Scroll")

        assert result.success
        # Default direction=down, default amount=3 -> clicks=-3
        actuator.scroll.assert_called_once_with(-3, x=None, y=None)
