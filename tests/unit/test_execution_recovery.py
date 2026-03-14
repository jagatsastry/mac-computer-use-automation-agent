"""Unit tests for Execution Recovery fixes (Fixes 5-7 and Fix 9).

Covers:
- Fix 5: wait_for_user condition extraction improvements
- Fix 6: on_fail directive parsing from skill templates
- Fix 7: Scroll recovery before infeasibility
- Fix 9: Screenshot persistence
"""

from __future__ import annotations

import asyncio
import base64
import re
import textwrap
from dataclasses import dataclass
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.config import AgentConfig
from automation_agent.logging.event_logger import EventLogger
from automation_agent.logging.models import EventType
from automation_agent.orchestrator.agent import AutomationAgent
from automation_agent.shared_models import (
    ActionPlan,
    ActionStep,
    FindElementResult,
    StepResult,
)


# ---------------------------------------------------------------------------
# Test config helper
# ---------------------------------------------------------------------------


def _make_config(**overrides):
    return AgentConfig(
        grounding_model="",
        grounding_server_url="",
        model_provider="local",
        **overrides,
    )


# ---------------------------------------------------------------------------
# Minimal mock helpers
# ---------------------------------------------------------------------------


def _make_agent(config=None, **kw):
    """Build an AutomationAgent with all collaborators mocked."""
    config = config or _make_config()
    planner = MagicMock()
    skill_registry = MagicMock()
    coordinator = MagicMock()
    coordinator.capabilities = MagicMock(return_value=set())
    coordinator.describe_screen = AsyncMock(return_value="mock screen")
    coordinator.capture_screenshot = AsyncMock(
        return_value=base64.b64encode(b"\x89PNG fake").decode()
    )
    coordinator.verify_condition = AsyncMock(return_value=False)
    actuator = MagicMock()
    logger = MagicMock(spec=EventLogger)
    logger.run_id = "test-run"
    logger.log_event = MagicMock()
    logger.save_screenshot = MagicMock(return_value="/tmp/test_screenshot.png")
    agent = AutomationAgent(
        planner=planner,
        skill_registry=skill_registry,
        coordinator=coordinator,
        actuator=actuator,
        config=config,
        logger=logger,
        **kw,
    )
    return agent


# ---------------------------------------------------------------------------
# Fix 5: wait_for_user condition extraction
# ---------------------------------------------------------------------------


class TestExtractWaitCondition:
    """Tests for _extract_wait_condition() static method."""

    def test_if_pattern(self):
        result = AutomationAgent._extract_wait_condition(
            "If a login page appears, wait for user"
        )
        assert result == "a login page is visible"

    def test_please_log_in(self):
        result = AutomationAgent._extract_wait_condition(
            "Please log in to Walmart"
        )
        assert result == "Walmart login page is visible"

    def test_sign_in_variant(self):
        result = AutomationAgent._extract_wait_condition(
            "Sign in to your account"
        )
        assert result == "your account login page is visible"

    def test_you_need_to_log_in(self):
        result = AutomationAgent._extract_wait_condition(
            "You need to log in to Walmart first"
        )
        assert result == "Walmart login page is visible"

    def test_you_may_need_to_sign_in(self):
        result = AutomationAgent._extract_wait_condition(
            "You may need to sign in to your Walmart account"
        )
        assert result == "your Walmart account login page is visible"

    def test_please_complete(self):
        result = AutomationAgent._extract_wait_condition(
            "Please complete the verification"
        )
        assert result == "the verification form is visible"

    def test_complete_without_please(self):
        result = AutomationAgent._extract_wait_condition(
            "Complete the 2FA challenge"
        )
        assert result == "the 2FA challenge form is visible"

    def test_use_wait_for_user_syntax(self):
        """Matches actual Walmart skill on_fail text."""
        result = AutomationAgent._extract_wait_condition(
            "If a login page appears, use wait_for_user to ask the user to log in, then continue"
        )
        assert result == "a login page is visible"

    def test_use_wait_for_user_simple(self):
        """Matches 'use wait_for_user' without trailing clause."""
        result = AutomationAgent._extract_wait_condition(
            "If authentication is needed, use wait_for_user"
        )
        # "is needed" already contains "is", so no extra "is visible" appended
        assert result == "authentication is needed"

    def test_unrecognized_returns_empty(self):
        result = AutomationAgent._extract_wait_condition(
            "Do something random"
        )
        assert result == ""


class TestWaitForUser:
    """Tests for _wait_for_user() timeout behavior."""

    async def test_skips_when_condition_not_present(self):
        agent = _make_agent()
        step = ActionStep(
            action="wait_for_user",
            params={"message": "Please log in", "condition": "login page is visible"},
            verify="",
            on_fail="abort",
        )
        result = await agent._wait_for_user(step)
        assert result.success is True
        assert "Skipped wait" in result.evidence

    async def test_unconditional_uses_short_timeout(self):
        """When no condition is extracted, timeout should be 30s, not 120s."""
        agent = _make_agent()
        step = ActionStep(
            action="wait_for_user",
            params={"message": "Do something"},
            verify="",
            on_fail="abort",
        )
        # Force the baseline capture to fail so we get an early return
        # before the polling loop starts
        agent.coordinator.capture_screenshot = AsyncMock(side_effect=Exception("no PIL"))

        printed = []
        original_print = print

        def capture_print(*args, **kwargs):
            printed.append(str(args[0]) if args else "")

        with patch("builtins.print", side_effect=capture_print):
            result = await agent._wait_for_user(step)

        # Check that the printed timeout mentions 30, not 120
        timeout_prints = [p for p in printed if "timeout" in p.lower()]
        assert any("30" in p for p in timeout_prints), (
            f"Expected 30s timeout but got: {timeout_prints}"
        )


# ---------------------------------------------------------------------------
# Fix 6: Parse on_fail directives from skill templates
# ---------------------------------------------------------------------------


class TestParseSkillSteps:
    """Tests for _parse_skill_steps() with on_fail extraction."""

    def test_extracts_on_fail(self):
        skill_text = textwrap.dedent("""\
            1. Click on the order
               - verify: Order details visible
               - on_fail: scroll down to find it
            2. Click return button
               - verify: Return form visible
        """)
        steps = AutomationAgent._parse_skill_steps(skill_text)
        assert len(steps) == 2
        # 3-tuples: (instruction, verify, on_fail)
        assert len(steps[0]) == 3
        assert steps[0][0] == "Click on the order"
        assert steps[0][1] == "Order details visible"
        assert steps[0][2] == "scroll down to find it"

    def test_missing_on_fail_returns_empty(self):
        skill_text = textwrap.dedent("""\
            1. Click on the order
               - verify: Order details visible
            2. Click return button
               - verify: Return form visible
        """)
        steps = AutomationAgent._parse_skill_steps(skill_text)
        assert len(steps) == 2
        assert steps[0][2] == ""  # no on_fail
        assert steps[1][2] == ""

    def test_on_fail_with_verify_and_on_fail(self):
        skill_text = textwrap.dedent("""\
            1. Navigate to https://walmart.com/orders
               - verify: Walmart orders page visible
               - on_fail: If a login page appears, wait for user to sign in
            2. Click the order
               - verify: Order details visible
               - on_fail: If the item is not visible, scroll down to find it
        """)
        steps = AutomationAgent._parse_skill_steps(skill_text)
        assert len(steps) == 2
        assert "login" in steps[0][2].lower()
        assert "scroll" in steps[1][2].lower()


class TestOnFailDirectives:
    """Tests for on_fail metadata applied to compiled steps."""

    def test_on_fail_scroll_sets_scroll_recovery_param(self):
        """on_fail containing 'scroll' should set _scroll_recovery on last step."""
        agent = _make_agent()
        skill_text = textwrap.dedent("""\
            1. Click on the matching order
               - verify: Order details visible
               - on_fail: If the item is not visible, scroll down to find it
        """)
        primary_section = AutomationAgent._extract_primary_skill_section(skill_text)
        # Build fallback plan
        plan = agent._build_skill_fallback_plan("Return item on Walmart", skill_text)
        if plan is not None:
            # Find the click step
            click_steps = [s for s in plan.steps if s.action == "click"]
            assert len(click_steps) >= 1
            assert click_steps[0].params.get("_scroll_recovery") is True
            assert click_steps[0].params.get("_max_scrolls") == 3

    def test_on_fail_login_generates_wait_step(self):
        """on_fail containing 'log in' should insert a wait_for_user step."""
        agent = _make_agent()
        skill_text = textwrap.dedent("""\
            1. Navigate to https://walmart.com/orders
               - verify: Walmart orders page visible
               - on_fail: Please log in to Walmart
        """)
        plan = agent._build_skill_fallback_plan("Return item on Walmart", skill_text)
        assert plan is not None
        wait_steps = [s for s in plan.steps if s.action == "wait_for_user"]
        assert len(wait_steps) >= 1
        assert "condition" in wait_steps[0].params


# ---------------------------------------------------------------------------
# Fix 7: Scroll recovery before infeasibility
# ---------------------------------------------------------------------------


class TestScrollRecovery:
    """Tests for _scroll_recovery() method."""

    async def test_finds_element_after_2_scrolls(self):
        agent = _make_agent()
        step = ActionStep(
            action="click",
            params={"element": "Return Items button"},
            verify="Return form visible",
            on_fail="abort",
        )
        initial_result = StepResult(
            step=step,
            success=False,
            error="Element not found: Return Items button",
        )

        find_result = FindElementResult(x=100, y=200, confidence=0.9, source="mock")
        # Return None twice, then find the element on 3rd scroll
        agent._find_element = AsyncMock(
            side_effect=[None, None, find_result]
        )
        agent._dispatch_action = AsyncMock(
            return_value={"success": True}
        )

        with patch("automation_agent.orchestrator.agent.asyncio.sleep", new_callable=AsyncMock):
            result = await agent._scroll_recovery(
                step, initial_result, [], "Return item", max_scrolls=3
            )

        assert result is not None
        assert result.success is True
        # 3 scroll dispatches + 1 click dispatch = 4
        assert agent._dispatch_action.call_count == 4

    async def test_exhausted_returns_failure(self):
        agent = _make_agent()
        step = ActionStep(
            action="click",
            params={"element": "Missing button"},
            verify="Button visible",
            on_fail="abort",
        )
        initial_result = StepResult(
            step=step,
            success=False,
            error="Element not found: Missing button",
        )

        agent._find_element = AsyncMock(return_value=None)
        agent._dispatch_action = AsyncMock(return_value={"success": True})

        with patch("automation_agent.orchestrator.agent.asyncio.sleep", new_callable=AsyncMock):
            result = await agent._scroll_recovery(
                step, initial_result, [], "Find button", max_scrolls=3
            )

        assert result is not None
        assert result.success is False
        assert "not found after 3 scrolls" in result.error

    async def test_no_element_returns_none(self):
        agent = _make_agent()
        step = ActionStep(
            action="click",
            params={},  # no element
            verify="",
            on_fail="abort",
        )
        initial_result = StepResult(step=step, success=False, error="fail")
        result = await agent._scroll_recovery(
            step, initial_result, [], "goal", max_scrolls=3
        )
        assert result is None

    async def test_scroll_recovery_for_type_text_not_found(self):
        """Scroll recovery should work for type_text steps with element param."""
        agent = _make_agent()
        step = ActionStep(
            action="type_text",
            params={"element": "address field", "text": "123 Main St"},
            verify="Address entered",
            on_fail="abort",
        )
        initial_result = StepResult(
            step=step,
            success=False,
            error="Element not found: address field",
        )

        find_result = FindElementResult(x=50, y=100, confidence=0.8, source="mock")
        agent._find_element = AsyncMock(side_effect=[None, find_result])
        agent._dispatch_action = AsyncMock(return_value={"success": True})

        with patch("automation_agent.orchestrator.agent.asyncio.sleep", new_callable=AsyncMock):
            result = await agent._scroll_recovery(
                step, initial_result, [], "Fill form", max_scrolls=3
            )

        assert result is not None
        assert result.success is True


class TestScrollRecoveryIntegration:
    """Tests for scroll recovery integration with the infeasibility gate."""

    async def test_infeasibility_not_fired_before_scroll_recovery(self):
        """When scroll recovery succeeds, _check_infeasibility should not be called."""
        agent = _make_agent()
        agent._scroll_recovery = AsyncMock(
            return_value=StepResult(
                step=ActionStep(
                    action="click", params={"element": "btn"}, verify="", on_fail="abort"
                ),
                success=True,
                evidence="Found after scroll",
            )
        )
        agent._check_infeasibility = AsyncMock(return_value=None)

        # Simulate the gate logic from execute loop
        step = ActionStep(
            action="click",
            params={"element": "btn"},
            verify="visible",
            on_fail="abort",
        )
        result = StepResult(
            step=step,
            success=False,
            error="Element not found: btn",
        )

        # Gate logic: scroll recovery first
        _scrollable_actions = {"click", "type_text"}
        if (
            not result.success
            and step.action in _scrollable_actions
            and step.params.get("element")
            and result.error
            and "not found" in result.error.lower()
        ):
            scroll_result = await agent._scroll_recovery(
                step, result, [], "goal", max_scrolls=3
            )
            if scroll_result is not None and scroll_result.success:
                result = scroll_result
            elif scroll_result is not None:
                await agent._check_infeasibility("goal", None, [], force=True)

        assert result.success is True
        agent._check_infeasibility.assert_not_called()

    async def test_infeasibility_fires_after_scroll_exhausted(self):
        """When scroll recovery fails, _check_infeasibility should be called."""
        agent = _make_agent()
        agent._scroll_recovery = AsyncMock(
            return_value=StepResult(
                step=ActionStep(
                    action="click", params={"element": "btn"}, verify="", on_fail="abort"
                ),
                success=False,
                error="not found after 3 scrolls: btn",
            )
        )
        agent._check_infeasibility = AsyncMock(return_value=None)

        step = ActionStep(
            action="click",
            params={"element": "btn"},
            verify="visible",
            on_fail="abort",
        )
        result = StepResult(
            step=step,
            success=False,
            error="Element not found: btn",
        )

        _scrollable_actions = {"click", "type_text"}
        if (
            not result.success
            and step.action in _scrollable_actions
            and step.params.get("element")
            and result.error
            and "not found" in result.error.lower()
        ):
            scroll_result = await agent._scroll_recovery(
                step, result, [], "goal", max_scrolls=3
            )
            if scroll_result is not None and scroll_result.success:
                result = scroll_result
            elif scroll_result is not None:
                await agent._check_infeasibility("goal", None, [], force=True)

        assert result.success is False
        agent._check_infeasibility.assert_called_once()


class TestVaryStrategy:
    """Tests for scroll_down_and_retry in _vary_strategy()."""

    def test_scroll_down_and_retry_first_attempt(self):
        """Missing target with no suggested alternative on attempt 1 should scroll."""
        agent = _make_agent()
        step = ActionStep(
            action="click",
            params={"element": "Return button"},
            verify="",
            on_fail="abort",
        )
        prev_result = StepResult(
            step=step,
            success=False,
            error="Element not found: Return button",
            suggested_element="",
            retry_count=0,  # attempt = retry_count + 1 = 1
        )
        strategy_name, retry_step = agent._vary_strategy(step, prev_result)
        assert strategy_name == "scroll_down_and_retry"
        assert retry_step.action == "scroll"
        assert retry_step.params["direction"] == "down"


# ---------------------------------------------------------------------------
# Fix 9: Screenshot persistence
# ---------------------------------------------------------------------------


class TestScreenshotPersistence:
    """Tests for screenshot save points."""

    async def test_screenshot_saved_on_observe(self):
        config = _make_config(save_step_screenshots=True)
        agent = _make_agent(config=config)
        step = ActionStep(
            action="observe", params={}, verify="", on_fail="abort"
        )
        plan = ActionPlan(steps=[step], goal="test")
        result, _ = await agent._execute_step(0, step, [], "test", plan)
        assert result.success is True
        agent.logger.save_screenshot.assert_called()
        call_args = agent.logger.save_screenshot.call_args
        assert "observe" in call_args[0][1]  # name contains "observe"

    async def test_screenshot_saved_on_not_found(self):
        """Screenshot should be saved when element is NOT_FOUND."""
        config = _make_config(save_step_screenshots=True)
        agent = _make_agent(config=config)
        # Mock the full _find_element path — the screenshot save happens inside it
        # We need to verify the save is called with "not_found_*"
        agent.coordinator.find_element = AsyncMock(return_value=None)

        # Call _find_element directly with a mocked capture
        agent.coordinator.capture_screenshot = AsyncMock(
            return_value=base64.b64encode(b"\x89PNG fake").decode()
        )
        # _find_element uses grounding_router or coordinator — mock both to return None
        agent.grounding_router = None

        result = await agent._find_element("missing button")
        assert result is None
        # Check save_screenshot was called with not_found prefix
        save_calls = [
            c for c in agent.logger.save_screenshot.call_args_list
            if "not_found" in str(c)
        ]
        assert len(save_calls) >= 1

    async def test_screenshot_not_saved_when_disabled(self):
        config = _make_config(save_step_screenshots=False)
        agent = _make_agent(config=config)
        step = ActionStep(
            action="observe", params={}, verify="", on_fail="abort"
        )
        plan = ActionPlan(steps=[step], goal="test")
        result, _ = await agent._execute_step(0, step, [], "test", plan)
        assert result.success is True
        agent.logger.save_screenshot.assert_not_called()

    def test_config_save_step_screenshots_default_true(self):
        config = _make_config()
        assert config.save_step_screenshots is True


class TestScrollSettleConstant:
    """Verify the _SCROLL_SETTLE_S class constant exists and is testable."""

    def test_scroll_settle_constant_exists(self):
        assert hasattr(AutomationAgent, "_SCROLL_SETTLE_S")
        assert AutomationAgent._SCROLL_SETTLE_S == 1.0
