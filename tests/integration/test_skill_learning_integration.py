"""Integration tests for skill learning flowing into planning and replanning."""

import json
import textwrap
from unittest.mock import AsyncMock, MagicMock

import pytest

from automation_agent.config import AgentConfig
from automation_agent.logging.event_logger import EventLogger
from automation_agent.orchestrator.agent import AutomationAgent
from automation_agent.planner.planner import ActionPlannerImpl
from automation_agent.shared_models import ActionPlan, ActionStep, StepResult
from automation_agent.skills.models import SkillObservation
from automation_agent.skills.registry import SkillRegistryImpl


SKILL_CONTENT = textwrap.dedent(
    """\
    ---
    name: return-amazon-order
    description: Return an item or package on Amazon
    trigger-keywords: [return, amazon]
    parameters:
      item:
        type: string
        required: true
        description: What to return
    requires:
      os: darwin
    success-condition: Return confirmation visible
    ---

    ## Steps
    1. Open orders page
       - verify: Orders page visible
    2. Search for "{{item}}"
       - verify: Matching order visible

    ## Error Recovery
    - If Return is absent, use a semantically adjacent affordance on the same order card
    """
)


def _make_llm_response(steps_data: list) -> dict:
    return {
        "content": json.dumps({"steps": steps_data}),
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }


@pytest.fixture
def config(tmp_path):
    return AgentConfig(
        _env_file=None,
        anthropic_api_key="test-key-not-real",
        skill_learning_dir=tmp_path / "skill-learning",
    )


@pytest.fixture
def registry(tmp_path, config):
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    (skill_dir / "return_amazon_order.md").write_text(SKILL_CONTENT)
    return SkillRegistryImpl(skill_dir=skill_dir, config=config)


@pytest.mark.asyncio
async def test_learned_skill_observations_flow_into_replan_prompt(registry, config):
    registry._router = AsyncMock()
    registry._router.route.return_value = {
        "skill_name": "return-amazon-order",
        "params": {"item": "Tylenol"},
    }
    registry._experience_store.append(
        "return-amazon-order",
        [
            SkillObservation(
                category="alternative_path",
                condition="Return is absent on the order card",
                recommendation="click View item on the same order card first",
                confidence=0.92,
            )
        ],
    )

    match = await registry.match("Return the most recent Tylenol order on Amazon")
    planner = ActionPlannerImpl(config)
    planner._call_llm = AsyncMock(
        return_value=_make_llm_response(
            [
                {
                    "action": "done",
                    "params": {},
                    "verify": "",
                    "on_fail": "abort",
                }
            ]
        )
    )
    history = [
        StepResult(
            step=ActionStep(
                action="click",
                params={"element": "Return or Replace Items"},
                verify="Return flow visible",
            ),
            success=False,
            evidence="Return control not found",
            retry_strategies_used=["replan_missing_target"],
            suggested_element="View item",
        )
    ]

    await planner.replan(
        goal="Return the most recent Tylenol order on Amazon",
        screen_description="Amazon order card with View item visible",
        history=history,
        retry_strategies_used=["replan_missing_target"],
        skill_context=match["skill_context"],
    )

    prompt = planner._call_llm.call_args[0][0]
    assert "click View item on the same order card first" in prompt


@pytest.mark.asyncio
async def test_replanned_run_persists_generalized_skill_observation(
    registry, config, tmp_path
):
    registry._router = AsyncMock()
    registry._router.route.return_value = {
        "skill_name": "return-amazon-order",
        "params": {"item": "Tylenol"},
    }
    registry._distiller = AsyncMock()
    registry._distiller.distill.return_value = [
        SkillObservation(
            category="alternative_path",
            condition="Return is missing from the order card",
            recommendation="open View item on the same card before searching for return controls",
            confidence=0.9,
        )
    ]

    planner = MagicMock()
    planner.plan = AsyncMock(
        return_value=ActionPlan(
            steps=[
                ActionStep(
                    action="click",
                    params={"x": 120, "y": 220},
                    verify="Return flow visible",
                    on_fail="replan",
                )
            ],
            goal="Return the most recent Tylenol order on Amazon",
        )
    )
    planner.replan = AsyncMock(
        return_value=ActionPlan(
            steps=[ActionStep(action="done", params={}, verify="")],
            goal="Return the most recent Tylenol order on Amazon",
        )
    )

    coordinator = MagicMock()
    coordinator.verify_condition = AsyncMock(return_value=False)
    coordinator.describe_screen = AsyncMock(
        return_value="Amazon order details page with View item visible"
    )
    coordinator.capture_screenshot = AsyncMock(return_value="")

    actuator = MagicMock()
    actuator.click.return_value = {"success": True}
    actuator.get_state.return_value = {}

    logger = EventLogger(tmp_path / "runs")
    agent = AutomationAgent(
        planner=planner,
        skill_registry=registry,
        coordinator=coordinator,
        actuator=actuator,
        config=config,
        logger=logger,
    )

    result = await agent.execute("Return the most recent Tylenol order on Amazon")

    assert result.success is True
    stored = registry._experience_store.load("return-amazon-order")
    assert len(stored) == 1
    assert "View item on the same card" in stored[0].recommendation
