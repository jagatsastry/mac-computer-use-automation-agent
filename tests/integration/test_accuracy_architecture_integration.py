"""Integration tests for the recent accuracy architecture improvements."""

import base64
import io
from unittest.mock import AsyncMock, MagicMock

import pytest

from automation_agent.config import AgentConfig
from automation_agent.logging.event_logger import EventLogger
from automation_agent.orchestrator.agent import AutomationAgent
from automation_agent.orchestrator.grounding_router import GroundingRouter, GroundingStrategy
from automation_agent.orchestrator.verifier import StepVerifier
from automation_agent.planner.planner import ActionPlannerImpl
from automation_agent.shared_models import ActionPlan, ActionStep, FindElementResult, StepResult
from automation_agent.vision.coordinator import ScreenCoordinatorImpl


def _make_jpeg_b64(width: int = 800, height: int = 600) -> str:
    from PIL import Image

    img = Image.new("RGB", (width, height), color=(245, 245, 245))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_planner_expected_observation_flows_into_verifier(tmp_path):
    """Planner output should preserve expected_observation through verification."""
    config = AgentConfig(_env_file=None, anthropic_api_key="test-key")
    planner = ActionPlannerImpl(config)
    planner._call_llm = AsyncMock(
        return_value={
            "content": (
                '{"steps":[{"action":"click","params":{"x":100,"y":200},'
                '"verify":"Search UI visible",'
                '"expected_observation":"The search box expands and the text cursor is visible",'
                '"on_fail":"retry_different","max_retries":3},'
                '{"action":"done","params":{},"verify":"","on_fail":"abort"}]}'
            ),
            "usage": {"input_tokens": 10, "output_tokens": 10},
        }
    )
    plan = await planner.plan("Open search")

    coord = AsyncMock()
    coord.capture_screenshot = AsyncMock(return_value=_make_jpeg_b64())
    coord.verify_condition = AsyncMock(side_effect=[True])

    verifier = StepVerifier(coordinator=coord, logger=EventLogger(tmp_path / "logs"))
    result = await verifier.verify(plan.steps[0], {"success": True, "output": ""})

    assert result.success is True
    assert coord.verify_condition.await_args.args[0] == (
        "The search box expands and the text cursor is visible"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_grounding_router_llm_tiebreak_can_promote_vision():
    """Ambiguous AX results can be reordered to vision by the LLM tie-breaker."""
    config = AgentConfig(
        _env_file=None,
        anthropic_api_key="test-key",
        grounding_llm_routing_enabled=True,
    )
    ax = MagicMock()
    ax._extract_match_target = MagicMock(return_value=("AXButton", "submit"))
    ax.find_elements = MagicMock(
        return_value=[
            MagicMock(
                role="AXButton",
                title="Submit",
                value=None,
                description=None,
                position=(10, 10),
                size=(20, 20),
                focused=False,
                center=(20, 20),
            ),
            MagicMock(
                role="AXButton",
                title="Submit order",
                value=None,
                description=None,
                position=(40, 40),
                size=(20, 20),
                focused=False,
                center=(50, 50),
            ),
        ]
    )
    vision = AsyncMock()
    vision.find_element = AsyncMock(return_value={"x": 400, "y": 500, "confidence": 0.9})
    router = GroundingRouter(accessibility=ax, vision_coordinator=vision, config=config)
    router._classify_with_llm = AsyncMock(return_value=GroundingStrategy.VISION)

    result = await router.find_element("Submit button")

    assert result is not None
    assert result.strategy_used == GroundingStrategy.VISION


@pytest.mark.integration
@pytest.mark.asyncio
async def test_coordinator_accepts_native_point_output():
    """Production coordinator should parse native point tags."""
    capture = MagicMock()
    capture.capture_b64 = MagicMock(return_value=_make_jpeg_b64())
    capture.target_resolution = (1024, 768)
    config = AgentConfig(_env_file=None, vision_model="qwen3-vl", model_provider="local")
    coord = ScreenCoordinatorImpl(config, capture=capture)
    coord._call_vision_model = AsyncMock(return_value='<point x="500" y="250" />')

    result = await coord.find_element("Search box")

    assert result is not None
    assert result.x == 512
    assert result.y == 192


@pytest.mark.integration
@pytest.mark.asyncio
async def test_agent_multiscale_validation_and_reflection_work_together(tmp_path):
    """Agent should use multi-scale validation and semantic reflection together."""
    config = AgentConfig(_env_file=None, anthropic_api_key="test-key")
    planner = AsyncMock()
    planner.plan = AsyncMock(
        return_value=ActionPlan(
            steps=[
                ActionStep(
                    action="click",
                    params={"element": "search box"},
                    verify="Search box is focused",
                    expected_observation="The search box is focused and cursor is visible",
                )
            ],
            goal="Focus search box",
        )
    )
    planner.replan = AsyncMock(return_value=ActionPlan(steps=[]))
    skill_registry = MagicMock()
    skill_registry.match = AsyncMock(return_value=None)
    coordinator = AsyncMock()
    coordinator.describe_screen = AsyncMock(return_value="Browser showing a page footer")
    coordinator.capture_screenshot = AsyncMock(return_value=_make_jpeg_b64())
    coordinator.find_element = AsyncMock(
        return_value=FindElementResult(x=128, y=128, confidence=0.8, source="vision")
    )
    coordinator.verify_multiscale_target = AsyncMock(return_value=True)
    coordinator.verify_condition = AsyncMock(return_value=False)
    coordinator.reflect_action_outcome = AsyncMock(
        return_value={
            "worked": "no",
            "observed": "The page footer is visible instead of the search box.",
            "hint": "scroll_to_top",
        }
    )
    coordinator.accessibility = None
    actuator = MagicMock()
    actuator.click = MagicMock(return_value={"success": True, "output": "clicked"})
    actuator.get_state = MagicMock(return_value={"app_name": "Chrome", "window_title": "Amazon"})
    logger = EventLogger(tmp_path / "logs")
    screenshot_diff = MagicMock()
    screenshot_diff.capture_before = MagicMock()
    screenshot_diff.region_changed = MagicMock(return_value=False)
    screenshot_diff.screen_changed = MagicMock(return_value=True)

    agent = AutomationAgent(
        planner=planner,
        skill_registry=skill_registry,
        coordinator=coordinator,
        actuator=actuator,
        config=config,
        logger=logger,
        screenshot_diff=screenshot_diff,
    )

    result = await agent.execute("Focus the search box")

    assert result.success is False
    assert coordinator.verify_multiscale_target.await_count >= 1
    click_result = result.steps[0]
    assert click_result.reflection_hint == "scroll_to_top"
    assert "footer" in click_result.evidence.lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_agent_uses_visible_alternative_when_click_target_is_missing(tmp_path):
    """When a click target is absent, the agent should retry with a suggested visible control."""
    config = AgentConfig(_env_file=None, anthropic_api_key="test-key")
    plan = ActionPlan(
        steps=[
            ActionStep(
                action="click",
                params={"element": "Return or Replace Items button"},
                verify="Return options page is visible",
                expected_observation="Return options page is visible",
                on_fail="retry_different",
                max_retries=3,
            )
        ],
        goal="Return the most recent Tylenol order on Amazon",
    )
    planner = AsyncMock()
    planner.plan = AsyncMock(return_value=plan)
    planner.replan = AsyncMock(return_value=ActionPlan(steps=[], goal=plan.goal))
    skill_registry = MagicMock()
    skill_registry.match = AsyncMock(return_value=None)

    coordinator = AsyncMock()
    coordinator.describe_screen = AsyncMock(return_value="Amazon orders filtered to Tylenol")
    coordinator.capture_screenshot = AsyncMock(return_value=_make_jpeg_b64())
    coordinator.find_element = AsyncMock(side_effect=[
        None,
        FindElementResult(x=320, y=240, confidence=0.82, source="vision"),
    ])
    coordinator.suggest_alternative_affordance = AsyncMock(
        return_value={
            "affordance": "View item",
            "reason": "The same order card shows View item but not Return or Replace Items.",
            "safe_to_try": "yes",
        }
    )
    coordinator.verify_multiscale_target = AsyncMock(return_value=True)
    coordinator.verify_condition = AsyncMock(return_value=True)
    coordinator.accessibility = None

    actuator = MagicMock()
    actuator.click = MagicMock(return_value={"success": True, "output": "clicked"})
    actuator.get_state = MagicMock(return_value={"app_name": "Chrome", "window_title": "Amazon"})
    logger = EventLogger(tmp_path / "logs")

    agent = AutomationAgent(
        planner=planner,
        skill_registry=skill_registry,
        coordinator=coordinator,
        actuator=actuator,
        config=config,
        logger=logger,
    )

    result = await agent.execute(plan.goal)

    assert result.success is True
    assert coordinator.suggest_alternative_affordance.await_count == 1
    assert coordinator.find_element.await_args_list[0].args[0] == "Return or Replace Items button"
    assert coordinator.find_element.await_args_list[1].args[0] == "View item"
    click_retry_events = [
        event for event in logger.events
        if event.event_type.value == "step_retry"
    ]
    assert any(
        event.data.get("strategy") == "visible_alternative_affordance"
        for event in click_retry_events
    )
