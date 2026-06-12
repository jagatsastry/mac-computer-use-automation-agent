"""State-changing clicks must not be re-clicked when their effect is confirmed.

Dominant add-to-cart failure mode: the planner writes an unobservable click
postcondition ("the Add to Cart button is no longer visible" — clicking it
doesn't hide it), the vision verify denies it, and the retry re-clicks the
same button → the cart fills with 3-5 duplicate items.

When a click (a) lands (actuator success), (b) has a confirmed visible effect
(screenshot diff), and (c) targets a non-idempotent control (add/cart/submit/
send/buy/...), a failed postcondition is accepted as "action performed" rather
than re-clicked. Idempotent clicks (menu/tab/link) keep normal retry.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.config import AgentConfig
from automation_agent.shared_models import ActionPlan, ActionStep, StepResult


def _make_config(**overrides):
    base = {"model_provider": "local", "action_delay": 0.0, "max_iterations": 5}
    base.update(overrides)
    return AgentConfig(**base)


def _make_agent(config=None):
    from automation_agent.orchestrator.agent import AutomationAgent

    planner = AsyncMock()
    skill_registry = MagicMock()
    skill_registry.match = AsyncMock(return_value=None)
    coordinator = AsyncMock()
    coordinator.capabilities = MagicMock(return_value=frozenset())
    coordinator.capture_screenshot = AsyncMock(return_value="base64data")
    actuator = MagicMock()
    actuator.click = MagicMock(return_value={"success": True})
    actuator.get_state = MagicMock(return_value={"app_name": "Google Chrome"})
    return AutomationAgent(
        planner, skill_registry, coordinator, actuator, config or _make_config()
    )


def _plan(step):
    return ActionPlan(steps=[step, ActionStep(action="done", params={}, verify="")], goal="t")


def _failing_verifier(step):
    v = MagicMock()
    v.verify = AsyncMock(
        return_value=StepResult(
            step=step,
            success=False,
            verification_method="vision",
            evidence="Vision denies: the button is no longer visible",
        )
    )
    return v


def _diff(changed: bool):
    d = MagicMock()
    d.capture_before = MagicMock()
    d.screen_changed = MagicMock(return_value=changed)
    d.region_changed = MagicMock(return_value=changed)
    return d


class TestStateChangingClickAccepted:
    @pytest.mark.asyncio
    async def test_add_to_cart_with_visible_effect_accepted_not_reclicked(self):
        agent = _make_agent()
        agent.screenshot_diff = _diff(changed=True)
        step = ActionStep(
            action="click",
            params={"element": "Add to Cart button for Blue Notebook"},
            verify="The Add to Cart button is no longer visible",
        )
        step.expected_observation = ""
        agent.verifier = _failing_verifier(step)
        # No reflection capability → tests the guard, not reflection
        with patch.object(
            agent, "_dispatch_action", new_callable=AsyncMock,
            return_value={"success": True, "output": "", "image_x": 620, "image_y": 360},
        ):
            result, _ = await agent._execute_step(0, step, [], "test", _plan(step))
        assert result.success is True
        assert "performed" in result.evidence.lower() or "visible effect" in result.evidence.lower()


class TestIdempotentClickStillFails:
    @pytest.mark.asyncio
    async def test_menu_click_with_visible_effect_not_auto_accepted(self):
        agent = _make_agent()
        agent.screenshot_diff = _diff(changed=True)
        step = ActionStep(
            action="click",
            params={"element": "hamburger menu icon"},
            verify="The settings panel is open",
        )
        step.expected_observation = ""
        agent.verifier = _failing_verifier(step)
        with patch.object(
            agent, "_dispatch_action", new_callable=AsyncMock,
            return_value={"success": True, "output": "", "image_x": 30, "image_y": 30},
        ):
            result, _ = await agent._execute_step(0, step, [], "test", _plan(step))
        # Idempotent click keeps normal (failed) verification → retry path intact
        assert result.success is False


class TestNoVisibleEffectStillFails:
    @pytest.mark.asyncio
    async def test_add_to_cart_without_visible_effect_still_fails(self):
        agent = _make_agent()
        agent.screenshot_diff = _diff(changed=False)  # click truly did nothing
        step = ActionStep(
            action="click",
            params={"element": "Add to Cart button"},
            verify="The item is added to the cart",
        )
        step.expected_observation = ""
        agent.verifier = _failing_verifier(step)
        with patch.object(
            agent, "_dispatch_action", new_callable=AsyncMock,
            return_value={"success": True, "output": "", "image_x": 620, "image_y": 360},
        ):
            result, _ = await agent._execute_step(0, step, [], "test", _plan(step))
        assert result.success is False


class TestHelper:
    def test_state_changing_detection(self):
        from automation_agent.orchestrator.agent import AutomationAgent

        for desc in [
            "Add to Cart button",
            "Submit order",
            "Send Message button",
            "Buy Now",
            "Place Order",
            "Save changes",
            "Post comment",
            "the Like button",
        ]:
            assert AutomationAgent._is_state_changing_click(desc, "") is True, desc
        for desc in ["hamburger menu icon", "Home tab", "next page link", "search field"]:
            assert AutomationAgent._is_state_changing_click(desc, "") is False, desc
