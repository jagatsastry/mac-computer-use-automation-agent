"""Tests for P0-2 replan hardening: _harden_plan shared helper.

Verifies that _harden_plan applies all hardening checks (fallback replacement,
navigation enforcement, domain verification, click on_fail normalization, and
plan validation) and that _replan_and_continue uses it.

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


@pytest.fixture
def logger(tmp_log_dir):
    return EventLogger(tmp_log_dir)


# ---------------------------------------------------------------------------
# Tests: _harden_plan unit tests
# ---------------------------------------------------------------------------


class TestHardenPlan:
    """Direct tests for the _harden_plan shared helper."""

    async def test_harden_plan_replaces_truncated_plan_with_fallback(
        self, mock_coordinator, mock_actuator, mock_skill_registry, logger,
    ):
        """A navigation-only plan should be replaced by the fallback."""
        planner = AsyncMock()
        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger,
        )
        agent._expected_domain = None

        # Truncated plan: only open_url, no interactions
        truncated = _make_plan([
            ActionStep(
                action="open_url",
                params={"url": "https://target.com"},
                verify="Target.com loaded",
            ),
            ActionStep(action="done", params={}, verify=""),
        ])

        # Fallback with full interaction steps
        fallback = _make_plan([
            ActionStep(
                action="open_url",
                params={"url": "https://target.com"},
                verify="Target.com loaded",
            ),
            ActionStep(
                action="click",
                params={"element": "Search"},
                verify="Search focused",
            ),
            ActionStep(
                action="type_text",
                params={"text": "shoes"},
                verify="Shoes typed",
            ),
            ActionStep(
                action="click",
                params={"element": "Add to cart"},
                verify="Added to cart",
            ),
            ActionStep(action="done", params={}, verify=""),
        ])

        result = await agent._harden_plan(
            truncated, "buy shoes on target", "skill context", fallback,
        )

        # Should have replaced truncated with fallback
        assert len(result.steps) == len(fallback.steps)
        assert result.steps[1].action == "click"
        assert result.steps[1].params["element"] == "Search"

    async def test_harden_plan_normalizes_click_on_fail(
        self, mock_coordinator, mock_actuator, mock_skill_registry, logger,
    ):
        """Click steps with on_fail=abort should be normalized to retry_different
        when skill_context is present."""
        planner = AsyncMock()
        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger,
        )
        agent._expected_domain = None

        plan = _make_plan([
            ActionStep(
                action="click",
                params={"element": "Add to cart"},
                verify="Added to cart",
                on_fail="abort",
            ),
            ActionStep(action="done", params={}, verify=""),
        ])

        result = await agent._harden_plan(
            plan, "buy shoes", "skill context", None,
        )

        assert result.steps[0].on_fail == "retry_different"

    async def test_harden_plan_no_normalize_without_skill_context(
        self, mock_coordinator, mock_actuator, mock_skill_registry, logger,
    ):
        """Click on_fail=abort should NOT be changed when no skill_context."""
        planner = AsyncMock()
        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger,
        )
        agent._expected_domain = None

        plan = _make_plan([
            ActionStep(
                action="click",
                params={"element": "Delete"},
                verify="Deleted",
                on_fail="abort",
            ),
            ActionStep(action="done", params={}, verify=""),
        ])

        result = await agent._harden_plan(
            plan, "delete file", None, None,
        )

        assert result.steps[0].on_fail == "abort"

    async def test_harden_plan_injects_domain_verification(
        self, mock_coordinator, mock_actuator, mock_skill_registry, logger,
    ):
        """Domain verification should be injected into open_url steps."""
        planner = AsyncMock()
        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger,
        )
        agent._expected_domain = "target.com"

        plan = _make_plan([
            ActionStep(
                action="open_url",
                params={"url": "https://target.com/s?searchTerm=shoes"},
                verify="Target search results page loaded",
            ),
            ActionStep(action="done", params={}, verify=""),
        ])

        result = await agent._harden_plan(
            plan, "buy shoes on target", "skill context", None,
        )

        assert "browser domain is target.com" in result.steps[0].verify

    async def test_harden_plan_returns_plan_without_validating(
        self, mock_coordinator, mock_actuator, mock_skill_registry, logger,
    ):
        """_harden_plan should return the plan without running validation.
        Validation is the caller's responsibility (_execute hard-fails,
        replan soft-warns)."""
        planner = AsyncMock()
        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger,
        )
        agent._expected_domain = None

        # Plan with empty verify on a non-exempt action
        plan = _make_plan([
            ActionStep(
                action="click",
                params={"element": "Button"},
                verify="",  # Missing verify
            ),
            ActionStep(action="done", params={}, verify=""),
        ])

        result = await agent._harden_plan(
            plan, "click button", None, None,
        )

        # Plan should be returned as-is (validation is caller's job)
        assert result is not None
        assert len(result.steps) == 2
        # Caller can validate and decide what to do
        errors = result.validate()
        assert len(errors) > 0  # Proves there ARE validation issues
        assert "verify" in errors[0].lower()

    async def test_harden_plan_trivial_done_replaced_when_not_satisfied(
        self, mock_coordinator, mock_actuator, mock_skill_registry, logger,
    ):
        """A trivial done plan should be replaced by fallback when conditions
        are not yet satisfied."""
        planner = AsyncMock()
        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger,
        )
        agent._expected_domain = None

        # Mock _plan_already_satisfied to return False
        agent._plan_already_satisfied = AsyncMock(return_value=False)

        trivial_done = _make_plan([
            ActionStep(action="done", params={}, verify=""),
        ])

        fallback = _make_plan([
            ActionStep(
                action="open_url",
                params={"url": "https://target.com"},
                verify="Target loaded",
            ),
            ActionStep(
                action="click",
                params={"element": "Search"},
                verify="Search focused",
            ),
            ActionStep(
                action="type_text",
                params={"text": "shoes"},
                verify="Shoes typed",
            ),
            ActionStep(
                action="click",
                params={"element": "Add to cart"},
                verify="Cart updated",
            ),
            ActionStep(action="done", params={}, verify=""),
        ])

        result = await agent._harden_plan(
            trivial_done, "buy shoes on target", "skill context", fallback,
        )

        # Should be replaced with fallback
        assert len(result.steps) == len(fallback.steps)
        assert result.steps[0].action == "open_url"

    async def test_harden_plan_logs_truncated_plan_warning(
        self, mock_coordinator, mock_actuator, mock_skill_registry, logger,
    ):
        """Truncated plan detection should emit structured slog warning."""
        planner = AsyncMock()
        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger,
        )
        agent._expected_domain = None

        truncated = _make_plan([
            ActionStep(
                action="open_url",
                params={"url": "https://target.com"},
                verify="Target.com loaded",
            ),
            ActionStep(action="done", params={}, verify=""),
        ])

        fallback = _make_plan([
            ActionStep(
                action="open_url",
                params={"url": "https://target.com"},
                verify="Target.com loaded",
            ),
            ActionStep(
                action="click", params={"element": "Search"},
                verify="Search focused",
            ),
            ActionStep(
                action="type_text", params={"text": "shoes"},
                verify="Typed",
            ),
            ActionStep(
                action="click", params={"element": "Add to cart"},
                verify="Added",
            ),
            ActionStep(action="done", params={}, verify=""),
        ])

        with patch("automation_agent.orchestrator.agent.slog") as mock_slog:
            await agent._harden_plan(
                truncated, "buy shoes on target", "skill context", fallback,
            )
            # Should log truncated_plan_detected warning
            warning_calls = [
                str(c) for c in mock_slog.warning.call_args_list
            ]
            assert any(
                "truncated_plan_detected" in c for c in warning_calls
            ), f"Expected truncated_plan_detected warning, got: {warning_calls}"

    async def test_harden_plan_logs_skill_expand_event_on_fallback(
        self, mock_coordinator, mock_actuator, mock_skill_registry, logger,
    ):
        """SKILL_EXPAND event should be emitted when plan is replaced by fallback."""
        planner = AsyncMock()
        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger,
        )
        agent._expected_domain = None

        truncated = _make_plan([
            ActionStep(
                action="open_url",
                params={"url": "https://target.com"},
                verify="Target.com loaded",
            ),
            ActionStep(action="done", params={}, verify=""),
        ])

        fallback = _make_plan([
            ActionStep(
                action="open_url",
                params={"url": "https://target.com"},
                verify="Target.com loaded",
            ),
            ActionStep(
                action="click", params={"element": "S"},
                verify="Focused",
            ),
            ActionStep(
                action="type_text", params={"text": "shoes"},
                verify="Typed",
            ),
            ActionStep(
                action="click", params={"element": "Add"},
                verify="Added",
            ),
            ActionStep(action="done", params={}, verify=""),
        ])

        with patch.object(agent.logger, "log_event") as mock_log:
            await agent._harden_plan(
                truncated, "buy shoes", "skill context", fallback,
            )
            # Should emit SKILL_EXPAND event
            expand_calls = [
                c for c in mock_log.call_args_list
                if c.args[0] == EventType.SKILL_EXPAND
            ]
            assert len(expand_calls) == 1, (
                f"Expected 1 SKILL_EXPAND event, got {len(expand_calls)}"
            )

    async def test_replan_logs_warning_when_fallback_build_fails(
        self, mock_coordinator, mock_actuator, mock_skill_registry, logger,
    ):
        """When skill_context is truthy but _build_skill_fallback_plan returns
        None, a warning should be logged."""
        planner = AsyncMock()
        replan_result = _make_plan([
            ActionStep(
                action="click",
                params={"element": "OK"},
                verify="OK clicked",
            ),
            ActionStep(action="done", params={}, verify=""),
        ])
        planner.replan = AsyncMock(return_value=replan_result)

        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger,
        )
        agent._expected_domain = None
        agent.context_monitor = None

        # Unparseable skill_context that won't produce a fallback
        unparseable_context = "This is not valid skill step format at all"

        executed_steps = []

        async def capture_execute_step(index, step, history, goal, plan):
            executed_steps.append(step)
            return (
                StepResult(
                    step=step,
                    success=True,
                    verification_method="actuator_state",
                    evidence="ok",
                ),
                False,
            )

        agent._execute_step = capture_execute_step

        prior_results = [
            StepResult(
                step=ActionStep(
                    action="click", params={"element": "X"},
                    verify="X", on_fail="replan",
                ),
                success=False,
                verification_method="vision",
                evidence="Failed",
            ),
        ]

        with patch("automation_agent.orchestrator.agent.slog") as mock_slog:
            await agent._replan_and_continue(
                goal="do something",
                step_results=prior_results,
                iterations=1,
                start_time=0.0,
                skill_context=unparseable_context,
            )
            warning_calls = [
                str(c) for c in mock_slog.warning.call_args_list
            ]
            assert any(
                "replan_fallback_build_failed" in c for c in warning_calls
            ), f"Expected replan_fallback_build_failed warning, got: {warning_calls}"


# ---------------------------------------------------------------------------
# Tests: _replan_and_continue uses _harden_plan
# ---------------------------------------------------------------------------


class TestReplanUsesHardenPlan:
    """Tests that _replan_and_continue routes through _harden_plan."""

    async def test_replan_applies_fallback_on_truncated_plan(
        self, mock_coordinator, mock_actuator, mock_skill_registry, logger,
    ):
        """When planner returns a truncated replan, _harden_plan should
        replace it with fallback built from skill_context. Verify the
        executed plan has the fallback steps, not just that the function
        was invoked."""
        # Skill context that yields a fallback plan
        skill_context = """## Steps
1. Open `https://target.com/s?searchTerm=shoes` | Target search results page loaded
2. Click the first product result | Product detail page is shown
3. Click "Add to cart" button | Item added to cart confirmation shown
4. Done
"""

        # Truncated replan from planner: navigation only
        truncated_replan = _make_plan([
            ActionStep(
                action="open_url",
                params={"url": "https://target.com/s?searchTerm=shoes"},
                verify="Target search page loaded",
            ),
            ActionStep(action="done", params={}, verify=""),
        ])

        planner = AsyncMock()
        planner.replan = AsyncMock(return_value=truncated_replan)

        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger,
        )
        agent._expected_domain = "target.com"
        agent.context_monitor = None

        # Track what plan gets executed after hardening
        executed_plans = []
        original_harden = agent._harden_plan

        async def tracking_harden(plan, goal, sc, fb):
            result = await original_harden(plan, goal, sc, fb)
            executed_plans.append(result)
            return result

        agent._harden_plan = tracking_harden

        # Prior step results that triggered the replan
        prior_results = [
            StepResult(
                step=ActionStep(
                    action="click",
                    params={"element": "Search"},
                    verify="Search focused",
                    on_fail="replan",
                ),
                success=False,
                verification_method="vision",
                evidence="Element not found",
            ),
        ]

        await agent._replan_and_continue(
            goal="buy shoes on target",
            step_results=prior_results,
            iterations=1,
            start_time=0.0,
            skill_name="target_buy",
            skill_context=skill_context,
        )

        assert len(executed_plans) == 1, "_harden_plan was not called during replan"
        hardened = executed_plans[0]

        # Verify the hardened plan is NOT the truncated plan (which had only
        # open_url + done = 0 interaction steps)
        assert hardened is not truncated_replan, (
            "Truncated plan should have been replaced, not passed through"
        )

        # The hardened plan should have interaction steps from the fallback
        interaction_actions = {"click", "type_text", "scroll"}
        hardened_interactions = sum(
            1 for s in hardened.steps if s.action in interaction_actions
        )
        assert hardened_interactions >= 2, (
            f"Truncated plan should have been replaced by fallback with interactions, "
            f"got {hardened_interactions} interaction steps: "
            f"{[s.action for s in hardened.steps]}"
        )

        # Verify the truncated plan's original steps are not present unchanged
        truncated_actions = [s.action for s in truncated_replan.steps]
        hardened_actions = [s.action for s in hardened.steps]
        assert hardened_actions != truncated_actions, (
            f"Hardened plan should differ from truncated plan, "
            f"both have: {hardened_actions}"
        )

    async def test_replan_normalizes_click_on_fail(
        self, mock_coordinator, mock_actuator, mock_skill_registry, logger,
    ):
        """Replan with click on_fail=abort should be normalized to retry_different
        when skill_context is present."""
        replan_result = _make_plan([
            ActionStep(
                action="click",
                params={"element": "Add to cart"},
                verify="Added to cart",
                on_fail="abort",
            ),
            ActionStep(action="done", params={}, verify=""),
        ])

        planner = AsyncMock()
        planner.replan = AsyncMock(return_value=replan_result)

        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger,
        )
        agent._expected_domain = None
        agent.context_monitor = None

        # Track what plan gets executed after hardening
        executed_steps = []
        original_execute_step = agent._execute_step

        async def capture_execute_step(index, step, history, goal, plan):
            executed_steps.append(step)
            return (
                StepResult(
                    step=step,
                    success=True,
                    verification_method="actuator_state",
                    evidence="ok",
                ),
                False,
            )

        agent._execute_step = capture_execute_step

        prior_results = [
            StepResult(
                step=ActionStep(
                    action="click",
                    params={"element": "X"},
                    verify="X clicked",
                    on_fail="replan",
                ),
                success=False,
                verification_method="vision",
                evidence="Failed",
            ),
        ]

        await agent._replan_and_continue(
            goal="buy shoes",
            step_results=prior_results,
            iterations=1,
            start_time=0.0,
            skill_name="test_skill",
            skill_context="some skill context",
        )

        # The click step should have been normalized
        click_steps = [s for s in executed_steps if s.action == "click"]
        assert len(click_steps) > 0
        for step in click_steps:
            assert step.on_fail == "retry_different", (
                f"Click step on_fail should be retry_different, got {step.on_fail}"
            )

    async def test_replan_injects_domain_verification(
        self, mock_coordinator, mock_actuator, mock_skill_registry, logger,
    ):
        """Replan steps should get domain verification injected via _harden_plan."""
        replan_result = _make_plan([
            ActionStep(
                action="open_url",
                params={"url": "https://target.com/search"},
                verify="Search page loaded",
            ),
            ActionStep(action="done", params={}, verify=""),
        ])

        planner = AsyncMock()
        planner.replan = AsyncMock(return_value=replan_result)

        agent = _make_agent(
            planner, mock_skill_registry, mock_coordinator, mock_actuator, logger,
        )
        agent._expected_domain = "target.com"
        agent.context_monitor = None

        executed_steps = []

        async def capture_execute_step(index, step, history, goal, plan):
            executed_steps.append(step)
            return (
                StepResult(
                    step=step,
                    success=True,
                    verification_method="actuator_state",
                    evidence="ok",
                ),
                False,
            )

        agent._execute_step = capture_execute_step

        prior_results = [
            StepResult(
                step=ActionStep(
                    action="click",
                    params={"element": "X"},
                    verify="X",
                    on_fail="replan",
                ),
                success=False,
                verification_method="vision",
                evidence="Failed",
            ),
        ]

        await agent._replan_and_continue(
            goal="buy shoes on target",
            step_results=prior_results,
            iterations=1,
            start_time=0.0,
        )

        open_url_steps = [s for s in executed_steps if s.action == "open_url"]
        assert len(open_url_steps) > 0
        for step in open_url_steps:
            assert "browser domain is target.com" in step.verify, (
                f"Domain verification not injected: {step.verify}"
            )
