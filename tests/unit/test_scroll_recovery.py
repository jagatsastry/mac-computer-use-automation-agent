"""Unit tests for Slice 1: P0-1 Scroll Recovery + P2-5 _vary_strategy fixes.

Covers:
- Postcondition verification in _scroll_recovery after successful dispatch
- False-positive prevention (dispatch succeeds but verify fails)
- Removal of bare scroll_down_and_retry from _vary_strategy
- Scroll exhaustion returns failure
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from automation_agent.config import AgentConfig
from automation_agent.logging.event_logger import EventLogger
from automation_agent.orchestrator.agent import AutomationAgent
from automation_agent.shared_models import (
    ActionPlan,
    ActionStep,
    FindElementResult,
    StepResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides):
    return AgentConfig(
        grounding_model="",
        grounding_server_url="",
        model_provider="local",
        **overrides,
    )


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
# Scroll Recovery: postcondition verification
# ---------------------------------------------------------------------------


class TestScrollRecoveryPostcondition:
    """_scroll_recovery must run postcondition verification after dispatch."""

    async def test_scroll_recovery_runs_postcondition_verification(self):
        """After scroll finds element and dispatches action, verify step runs."""
        agent = _make_agent()
        step = ActionStep(
            action="click",
            params={"element": "Add to Cart"},
            verify="Cart icon shows 1 item",
            on_fail="retry_different",
        )
        initial_result = StepResult(
            step=step,
            success=False,
            error="Element not found: Add to Cart",
            evidence="Element not found",
        )

        # _find_element: fail first scroll, succeed second
        find_results = [None, FindElementResult(x=100, y=200, confidence=0.9)]
        agent._find_element = AsyncMock(side_effect=find_results)

        # _dispatch_action: succeed for both scroll and click
        agent._dispatch_action = AsyncMock(return_value={"success": True})

        # verifier.verify should be called and return a passing result
        verify_result = StepResult(
            step=step,
            success=True,
            verification_method="vision",
            evidence="Cart icon shows 1 item",
        )
        agent.verifier = MagicMock()
        agent.verifier.verify = AsyncMock(return_value=verify_result)

        result = await agent._scroll_recovery(step, initial_result, [], "Buy item")

        assert result is not None
        assert result.success is True
        assert result.verification_method == "scroll_recovery_verified"
        # Verify that the verifier was actually called
        agent.verifier.verify.assert_called_once()

    async def test_scroll_recovery_false_action_not_treated_as_success(self):
        """If dispatch succeeds but verify fails, result.success should be False."""
        agent = _make_agent()
        step = ActionStep(
            action="click",
            params={"element": "Add to Cart"},
            verify="Cart icon shows 1 item",
            on_fail="retry_different",
        )
        initial_result = StepResult(
            step=step,
            success=False,
            error="Element not found: Add to Cart",
            evidence="Element not found",
        )

        # _find_element: succeed on first scroll
        agent._find_element = AsyncMock(
            return_value=FindElementResult(x=100, y=200, confidence=0.9)
        )

        # _dispatch_action: succeed
        agent._dispatch_action = AsyncMock(return_value={"success": True})

        # verifier.verify returns FAILURE
        verify_result = StepResult(
            step=step,
            success=False,
            verification_method="vision",
            evidence="Cart icon still shows 0 items",
        )
        agent.verifier = MagicMock()
        agent.verifier.verify = AsyncMock(return_value=verify_result)

        result = await agent._scroll_recovery(step, initial_result, [], "Buy item")

        assert result is not None
        assert result.success is False
        assert result.verification_method == "scroll_recovery_verified"
        assert "0 items" in result.evidence

    async def test_scroll_recovery_no_verify_field_skips_verification(self):
        """When step has no verify field, skip postcondition check."""
        agent = _make_agent()
        step = ActionStep(
            action="click",
            params={"element": "Add to Cart"},
            verify="",
            on_fail="retry_different",
        )
        initial_result = StepResult(
            step=step,
            success=False,
            error="Element not found: Add to Cart",
            evidence="Element not found",
        )

        agent._find_element = AsyncMock(
            return_value=FindElementResult(x=100, y=200, confidence=0.9)
        )
        agent._dispatch_action = AsyncMock(return_value={"success": True})
        agent.verifier = MagicMock()
        agent.verifier.verify = AsyncMock()

        result = await agent._scroll_recovery(step, initial_result, [], "Buy item")

        assert result is not None
        assert result.success is True
        assert result.verification_method == "scroll_recovery"
        # Verifier should NOT be called when verify field is empty
        agent.verifier.verify.assert_not_called()


# ---------------------------------------------------------------------------
# Scroll Recovery: dispatch failure continues scrolling
# ---------------------------------------------------------------------------


class TestScrollRecoveryDispatchFailure:
    """When element is found but dispatch fails, scroll recovery should continue."""

    async def test_dispatch_failure_continues_to_next_scroll(self):
        """Element found but click fails → should keep scrolling, not return immediately."""
        agent = _make_agent()
        step = ActionStep(
            action="click",
            params={"element": "Add to Cart"},
            verify="Cart icon shows 1 item",
            on_fail="retry_different",
        )
        initial_result = StepResult(
            step=step,
            success=False,
            error="Element not found: Add to Cart",
            evidence="Element not found",
        )

        # _find_element: always finds the element.
        # NOTE: side_effect ordering of _dispatch_action below depends on this —
        # each iteration calls dispatch twice (scroll + click) only when _find_element
        # returns a result. If _find_element returned None, the click dispatch would
        # be skipped, breaking the interleaved side_effect sequence.
        agent._find_element = AsyncMock(
            return_value=FindElementResult(x=100, y=200, confidence=0.9)
        )

        # _dispatch_action: interleaved scroll/click calls.
        # Iteration 1: scroll(ok) → click(fail) → continue to next iteration
        # Iteration 2: scroll(ok) → click(ok) → verify → return
        agent._dispatch_action = AsyncMock(
            side_effect=[
                {"success": True},   # scroll 1
                {"success": False, "error": "Click missed target"},  # click 1 fails
                {"success": True},   # scroll 2
                {"success": True},   # click 2 succeeds
            ]
        )

        # verifier for the successful click
        verify_result = StepResult(
            step=step,
            success=True,
            verification_method="vision",
            evidence="Cart icon shows 1 item",
        )
        agent.verifier = MagicMock()
        agent.verifier.verify = AsyncMock(return_value=verify_result)

        result = await agent._scroll_recovery(step, initial_result, [], "Buy item")

        assert result is not None
        assert result.success is True
        assert result.verification_method == "scroll_recovery_verified"
        # Should have scrolled twice (first click failed, continued to second)
        assert agent._find_element.call_count == 2

    async def test_dispatch_failure_all_scrolls_exhausted(self):
        """Element found every time but dispatch always fails → exhaustion."""
        agent = _make_agent()
        step = ActionStep(
            action="click",
            params={"element": "Add to Cart"},
            verify="Cart shows 1",
            on_fail="retry_different",
        )
        initial_result = StepResult(
            step=step,
            success=False,
            error="Element not found: Add to Cart",
            evidence="Element not found",
        )

        agent._find_element = AsyncMock(
            return_value=FindElementResult(x=100, y=200, confidence=0.9)
        )
        # Every click fails
        agent._dispatch_action = AsyncMock(
            return_value={"success": False, "error": "Click missed"}
        )

        result = await agent._scroll_recovery(
            step, initial_result, [], "Buy item", max_scrolls=3
        )

        assert result is not None
        assert result.success is False
        assert "3 scroll attempts" in result.evidence


# ---------------------------------------------------------------------------
# Scroll Recovery: exhaustion
# ---------------------------------------------------------------------------


class TestScrollRecoveryExhaustion:
    """_scroll_recovery must return failure when max_scrolls exhausted."""

    async def test_scroll_recovery_exhaustion_returns_failure(self):
        """3 scrolls with no element found → StepResult(success=False)."""
        agent = _make_agent()
        step = ActionStep(
            action="click",
            params={"element": "Hidden Button"},
            verify="Button visible",
            on_fail="retry_different",
        )
        initial_result = StepResult(
            step=step,
            success=False,
            error="Element not found: Hidden Button",
            evidence="Element not found",
        )

        # _find_element always returns None (element never found)
        agent._find_element = AsyncMock(return_value=None)
        agent._dispatch_action = AsyncMock(return_value={"success": True})

        result = await agent._scroll_recovery(
            step, initial_result, [], "Find button", max_scrolls=3
        )

        assert result is not None
        assert result.success is False
        assert "3 scroll attempts" in result.evidence
        assert "Hidden Button" in result.error


# ---------------------------------------------------------------------------
# _vary_strategy: no bare scroll
# ---------------------------------------------------------------------------


class TestVaryStrategyNoBareScroll:
    """_vary_strategy must not return a bare scroll step for click actions."""

    def test_vary_strategy_no_bare_scroll(self):
        """Missing target + no suggested element + attempt 1 should NOT scroll."""
        agent = _make_agent()
        step = ActionStep(
            action="click",
            params={"element": "Return button"},
            verify="Return page visible",
            on_fail="retry_different",
        )
        prev_result = StepResult(
            step=step,
            success=False,
            error="Element not found: Return button",
            suggested_element="",
            retry_count=0,  # attempt = retry_count + 1 = 1
        )

        strategy_name, retry_step = agent._vary_strategy(step, prev_result)

        # Should NOT be scroll_down_and_retry
        assert strategy_name != "scroll_down_and_retry"
        # Should be refine_missing_element_query (distinct from generic refine_element_query)
        assert strategy_name == "refine_missing_element_query"
        assert retry_step.action == "click"
        assert "look carefully" in retry_step.params["element"]

    def test_vary_strategy_attempt_2_refines_missing_target(self):
        """Missing target + no suggested element + attempt 2 → refine_missing_target_query."""
        agent = _make_agent()
        step = ActionStep(
            action="click",
            params={"element": "Submit"},
            verify="Submitted",
            on_fail="retry_different",
        )
        prev_result = StepResult(
            step=step,
            success=False,
            error="Element not found: Submit",
            suggested_element="",
            retry_count=1,  # attempt = 2
        )

        strategy_name, retry_step = agent._vary_strategy(step, prev_result)

        assert strategy_name == "refine_missing_target_query"
        assert retry_step.action == "click"
        assert "relevant card/section" in retry_step.params["element"]

    def test_vary_strategy_attempt_3_replans(self):
        """Missing target + no suggested element + attempt >= 3 → replan."""
        agent = _make_agent()
        step = ActionStep(
            action="click",
            params={"element": "Submit"},
            verify="Submitted",
            on_fail="retry_different",
        )
        prev_result = StepResult(
            step=step,
            success=False,
            error="Element not found: Submit",
            suggested_element="",
            retry_count=2,  # attempt = 3
        )

        strategy_name, retry_step = agent._vary_strategy(step, prev_result)

        assert strategy_name == "replan_missing_target"
        assert retry_step is None

    def test_vary_strategy_never_returns_scroll_action(self):
        """Sweep all attempt counts 1-5: no scroll action should ever be returned."""
        agent = _make_agent()
        step = ActionStep(
            action="click",
            params={"element": "Target"},
            verify="Target visible",
            on_fail="retry_different",
        )

        for retry_count in range(5):
            prev_result = StepResult(
                step=step,
                success=False,
                error="Element not found: Target",
                suggested_element="",
                retry_count=retry_count,
            )
            strategy_name, retry_step = agent._vary_strategy(step, prev_result)
            assert strategy_name != "scroll_down_and_retry", (
                f"Got scroll_down_and_retry at retry_count={retry_count}"
            )
            if retry_step is not None:
                assert retry_step.action != "scroll", (
                    f"Got scroll action at retry_count={retry_count}"
                )
