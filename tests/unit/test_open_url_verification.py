"""Tests for P1-2: open_url screenshot-diff false negative fix.

open_url with no visible screenshot change should NOT be marked as failed.
Instead, it stores metadata and defers to verification.
click with no visible change should still be marked as failed (existing behavior).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.config import AgentConfig
from automation_agent.shared_models import (
    ActionPlan,
    ActionStep,
    StepResult,
)


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


def _make_agent(actuator=None, config=None):
    from automation_agent.orchestrator.agent import AutomationAgent

    planner = AsyncMock()
    skill_registry = MagicMock()
    skill_registry.match = AsyncMock(return_value=None)
    skill_registry.learn_from_run = AsyncMock(return_value=[])
    skill_registry.promote_from_run = AsyncMock(return_value=None)
    coordinator = AsyncMock()
    coordinator.capabilities = MagicMock(return_value=frozenset())
    coordinator.capture_screenshot = AsyncMock(return_value="base64data")
    if config is None:
        config = _make_config()
    if actuator is None:
        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.type_text = MagicMock(return_value={"success": True})
        actuator.scroll = MagicMock(return_value={"success": True})
        actuator.open_url = MagicMock(return_value={"success": True, "output": ""})
        actuator.get_state = MagicMock(
            return_value={
                "app_name": "Safari",
                "browser_url": "https://www.target.com",
                "window_title": "Target",
            }
        )
    return AutomationAgent(planner, skill_registry, coordinator, actuator, config)


def _make_plan(step):
    """Wrap a single step in an ActionPlan for _execute_step."""
    return ActionPlan(
        steps=[step, ActionStep(action="done", params={}, verify="")],
        goal="test",
    )


class TestOpenUrlNoDiffProceeds:
    """open_url with no visible change should not be marked as failed."""

    @pytest.mark.asyncio
    async def test_open_url_no_diff_proceeds(self):
        """open_url + no visible change -> success stays True."""
        agent = _make_agent()

        # Set up a screenshot_diff mock that says no change
        screenshot_diff = MagicMock()
        screenshot_diff.screen_changed = MagicMock(return_value=False)
        screenshot_diff.capture_before = MagicMock()
        agent.screenshot_diff = screenshot_diff

        step = ActionStep(
            action="open_url",
            params={"url": "https://www.target.com"},
            verify="Target.com is visible",
        )
        plan = _make_plan(step)

        # Mock _dispatch_action to return success
        with patch.object(
            agent, "_dispatch_action",
            new_callable=AsyncMock,
            return_value={"success": True, "output": ""},
        ):
            # Mock the verifier to return success
            agent.verifier = MagicMock()
            agent.verifier.verify = AsyncMock(
                return_value=StepResult(
                    step=step,
                    success=True,
                    verification_method="actuator_state",
                    evidence="Browser URL matches",
                )
            )
            result, _ = await agent._execute_step(0, step, [], "test", plan)

        # open_url should NOT be failed by screenshot diff
        assert result.success is True

    @pytest.mark.asyncio
    async def test_open_url_stores_metadata(self):
        """open_url with no visible change stores _no_visible_change metadata."""
        agent = _make_agent()

        screenshot_diff = MagicMock()
        screenshot_diff.screen_changed = MagicMock(return_value=False)
        screenshot_diff.capture_before = MagicMock()
        agent.screenshot_diff = screenshot_diff

        step = ActionStep(
            action="open_url",
            params={"url": "https://www.target.com"},
            verify="Target.com is visible",
        )
        plan = _make_plan(step)

        captured_result = {}

        async def fake_dispatch(s, **kw):
            return {"success": True, "output": ""}

        with patch.object(agent, "_dispatch_action", side_effect=fake_dispatch):
            agent.verifier = MagicMock()

            async def capture_verify(s, ar, **kw):
                captured_result.update(ar)
                return StepResult(
                    step=s,
                    success=True,
                    verification_method="actuator_state",
                    evidence="OK",
                )

            agent.verifier.verify = AsyncMock(side_effect=capture_verify)
            await agent._execute_step(0, step, [], "test", plan)

        # The actuator_result passed to verify should have the metadata
        assert captured_result.get("_no_visible_change") is True
        # success should NOT have been overwritten to False
        assert captured_result.get("success") is True

    @pytest.mark.asyncio
    async def test_click_no_diff_still_fails(self):
        """click + no visible change -> success = False (existing behavior)."""
        agent = _make_agent()

        screenshot_diff = MagicMock()
        screenshot_diff.screen_changed = MagicMock(return_value=False)
        screenshot_diff.region_changed = MagicMock(return_value=False)
        screenshot_diff.capture_before = MagicMock()
        agent.screenshot_diff = screenshot_diff

        step = ActionStep(
            action="click",
            params={"x": 100, "y": 200},
            verify="Button clicked",
        )
        plan = _make_plan(step)

        with patch.object(
            agent, "_dispatch_action",
            new_callable=AsyncMock,
            return_value={"success": True, "output": ""},
        ):
            result, _ = await agent._execute_step(0, step, [], "test", plan)

        # click with no visible effect should still fail
        assert result.success is False


class TestSkillFallbackSkipsUnrecognizedStep:
    """R2-BLOCK-2: _build_skill_fallback_plan skips uncompilable steps."""

    def test_fallback_plan_skips_unrecognized_step(self):
        """Unrecognized step verb is skipped, other steps compile."""
        import textwrap

        agent = _make_agent()
        skill_ctx = textwrap.dedent("""\
        ## Steps
        1. Use open_url to navigate to https://example.com
           - verify: Page loaded
        2. Sort by price low to high
           - verify: Sorted results visible
        3. Click on the "Add to cart" button
           - verify: Item in cart
        """)
        plan = agent._build_skill_fallback_plan("Test", skill_ctx)
        assert plan is not None
        actions = [s.action for s in plan.steps if s.action != "done"]
        assert "open_url" in actions
        assert "click" in actions
        # Step 2 ("Sort by price...") is unrecognized and skipped
        assert len(actions) == 2

    def test_fallback_plan_all_unrecognized_returns_empty(self):
        """If all steps are unrecognized, plan has only done step."""
        import textwrap

        agent = _make_agent()
        skill_ctx = textwrap.dedent("""\
        ## Steps
        1. Sort by price low to high
           - verify: Sorted results visible
        2. Select the first product
           - verify: Product selected
        """)
        plan = agent._build_skill_fallback_plan("Test", skill_ctx)
        # Plan may be None or have only a done step — either is acceptable
        if plan is not None:
            actions = [s.action for s in plan.steps if s.action != "done"]
            assert len(actions) == 0
