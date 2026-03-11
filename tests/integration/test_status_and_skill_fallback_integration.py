"""Integration tests for live status UI wiring and skill fallback execution."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.__main__ import run_agent
from automation_agent.config import AgentConfig, StatusUIMode
from automation_agent.logging.event_logger import EventLogger
from automation_agent.orchestrator.agent import AutomationAgent
from automation_agent.shared_models import (
    ActionPlan,
    ActionStep,
    ExecutionResult,
    MatchType,
    SkillRouteCandidate,
    SkillRouteResult,
)
from automation_agent.skills import SkillRegistryImpl
from automation_agent.status_overlay import StatusOverlayTailer


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_agent_starts_status_overlay_when_enabled():
    """run_agent should launch the overlay controller before executing the agent."""
    config = AgentConfig(_env_file=None, status_ui=StatusUIMode.OVERLAY)
    fake_agent = MagicMock()
    fake_agent.logger = MagicMock(events_file=Path("logs/runs/test/events.jsonl"))
    fake_agent.execute = AsyncMock(
        return_value=ExecutionResult(
            success=True,
            message="Task completed",
            goal="Open Safari",
        )
    )

    with patch("automation_agent.__main__.ActionPlannerImpl"), patch(
        "automation_agent.__main__.SkillRegistryImpl"
    ), patch("automation_agent.__main__.ScreenCoordinatorImpl"), patch(
        "automation_agent.__main__.create_actuator"
    ), patch(
        "automation_agent.__main__.AutomationAgent", return_value=fake_agent
    ), patch(
        "automation_agent.__main__.StatusOverlayController"
    ) as overlay_cls:
        overlay = MagicMock()
        overlay_cls.return_value = overlay

        exit_code = await run_agent("Open Safari", config)

    assert exit_code == 0
    overlay_cls.assert_called_once_with(config=config, events_file=fake_agent.logger.events_file)
    overlay.start.assert_called_once()
    fake_agent.execute.assert_awaited_once_with("Open Safari")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_amazon_skill_fallback_executes_browser_steps(tmp_path):
    """A real bundled skill should compile into executable fallback steps."""
    config = AgentConfig(_env_file=None, anthropic_api_key="test-key")
    planner = AsyncMock()
    planner.plan = AsyncMock(
        return_value=ActionPlan(steps=[ActionStep(action="done", params={}, verify="")])
    )
    planner.replan = AsyncMock(return_value=ActionPlan(steps=[]))

    skill_registry = SkillRegistryImpl(config=config)
    _route_result = SkillRouteResult(candidates=[
        SkillRouteCandidate(
            skill_id="return-amazon-order",
            match_type=MatchType.DIRECT,
            confidence=0.95,
            reason="Exact match",
        ),
    ])
    _route_result.params = {"item": "blue headphones"}
    skill_registry._router = MagicMock()
    skill_registry._router.route = AsyncMock(return_value=_route_result)

    coordinator = AsyncMock()
    coordinator.describe_screen = AsyncMock(return_value="Terminal is frontmost")
    coordinator.capture_screenshot = AsyncMock(return_value=None)
    coordinator.verify_condition = AsyncMock(side_effect=[False, False, False, True])
    coordinator.accessibility = None

    actuator = MagicMock()
    actuator.activate_app = MagicMock(return_value={"success": True, "output": ""})
    actuator.open_url = MagicMock(return_value={"success": True, "output": ""})
    actuator.get_state = MagicMock(
        return_value={"app_name": "Safari", "window_title": "Blank Start Page"}
    )

    logger = EventLogger(tmp_path / "integration_logs")
    agent = AutomationAgent(
        planner=planner,
        skill_registry=skill_registry,
        coordinator=coordinator,
        actuator=actuator,
        config=config,
        logger=logger,
    )

    result = await agent.execute(
        "Return my blue headphones on Amazon, but stop as soon as the Amazon orders page or sign-in page is visible."
    )

    assert result.success is True
    actuator.activate_app.assert_not_called()
    assert actuator.open_url.call_count >= 1
    assert actuator.open_url.call_args_list[0].args == (
        "https://www.amazon.com/gp/your-account/order-history",
    )


@pytest.mark.integration
def test_status_overlay_tailer_reads_goal_and_step_from_event_log(tmp_path):
    """The real tailer should parse JSONL events into goal + step snapshots."""
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(
        "\n".join(
            [
                '{"event_type":"task_start","message":"Goal: Return headphones on Amazon","timestamp":"2026-03-10T03:55:00","data":{"goal":"Return headphones on Amazon"}}',
                '{"event_type":"step_start","message":"Step 1: open_url","timestamp":"2026-03-10T03:55:02","step_index":1,"data":{"action":"open_url"}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    class _FakeUI:
        def __init__(self):
            self.snapshots = []
            self.destroyed = False

        def apply_snapshot(self, snapshot):
            self.snapshots.append(snapshot)

        def destroy(self):
            self.destroyed = True

    ui = _FakeUI()
    tailer = StatusOverlayTailer(
        events_file=events_file,
        parent_pid=0,
        poll_interval=0.01,
        linger_seconds=1.0,
        ui=ui,
    )

    tailer._poll_once()

    assert len(ui.snapshots) == 2
    assert ui.snapshots[0].goal == "Return headphones on Amazon"
    assert ui.snapshots[1].step_label == "Step 1: open_url"
