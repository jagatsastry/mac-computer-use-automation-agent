"""Unit tests for skill runtime context and learning."""

import textwrap
from unittest.mock import AsyncMock

import pytest

from automation_agent.config import AgentConfig
from automation_agent.shared_models import (
    ActionStep,
    MatchType,
    SkillRouteCandidate,
    SkillRouteResult,
    StepResult,
)
from automation_agent.skills.distiller import SkillDistiller
from automation_agent.skills.models import SkillObservation
from automation_agent.skills.registry import SkillRegistryImpl


SAMPLE_SKILL = textwrap.dedent(
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
    - If return button is absent: look for order details first

    ## Notes
    - Treat labels as likely affordances, not guarantees
    """
)


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
    (skill_dir / "return_amazon_order.md").write_text(SAMPLE_SKILL)
    return SkillRegistryImpl(skill_dir=skill_dir, config=config)


class TestRuntimeContext:
    @pytest.mark.asyncio
    async def test_match_returns_rich_skill_context(self, registry):
        route_result = SkillRouteResult(candidates=[
            SkillRouteCandidate(
                skill_id="return-amazon-order",
                match_type=MatchType.DIRECT,
                confidence=0.95,
                reason="Exact match",
            ),
        ])
        route_result.params = {"item": "Tylenol"}
        registry._router = AsyncMock()
        registry._router.route.return_value = route_result

        result = await registry.match("Return the most recent Tylenol order on Amazon")

        assert result is not None
        assert "## Recovery Heuristics" in result["skill_context"]
        assert "Treat labels as likely affordances" in result["skill_context"]
        assert "Tylenol" in result["skill_context"]

    def test_build_runtime_context_includes_observed_variants(self, registry):
        registry._experience_store.append(
            "return-amazon-order",
            [
                SkillObservation(
                    category="alternative_path",
                    condition="Return is absent on the order card",
                    recommendation="click View item on the same order card first",
                    confidence=0.9,
                )
            ],
        )

        context = registry.build_runtime_context(
            "return-amazon-order", {"item": "Tylenol"}
        )

        assert context is not None
        assert "## Observed Variants" in context
        assert "click View item on the same order card first" in context


class TestSkillLearning:
    @pytest.mark.asyncio
    async def test_learn_from_run_persists_distilled_observations(self, registry):
        registry._distiller = AsyncMock()
        registry._distiller.distill.return_value = [
            SkillObservation(
                category="alternative_path",
                condition="Return is missing from the order card",
                recommendation="Use View item on the same card before replanning",
                rationale="The trace showed View item was the visible route into returns",
                confidence=0.88,
            )
        ]

        trace = [
            StepResult(
                step=ActionStep(
                    action="click",
                    params={"element": "Return or Replace Items"},
                    verify="Return flow visible",
                ),
                success=False,
                evidence="Return control not found",
                retry_strategies_used=["visible_alternative_affordance"],
                suggested_element="View item",
            )
        ]

        observations = await registry.learn_from_run(
            "return-amazon-order",
            "Return the most recent Tylenol order on Amazon",
            trace,
            skill_context="## Steps\n1. Search for Tylenol",
            run_id="run-123",
        )

        assert len(observations) == 1
        stored = registry._experience_store.load("return-amazon-order")
        assert len(stored) == 1
        assert stored[0].run_id == "run-123"
        assert stored[0].recommendation.startswith("Use View item")


class TestSkillDistiller:
    def test_parse_response_accepts_json_payload(self, config):
        distiller = SkillDistiller(config)

        observations = distiller._parse_response(
            """{
              "observations": [
                {
                  "category": "checkpoint",
                  "condition": "Order details page opens",
                  "recommendation": "Verify the order title before looking for return controls",
                  "rationale": "Prevents acting on the wrong order",
                  "confidence": 0.75
                }
              ]
            }""",
            run_id="run-456",
        )

        assert len(observations) == 1
        assert observations[0].category == "checkpoint"
        assert observations[0].run_id == "run-456"

    def test_parse_response_ignores_invalid_items(self, config):
        distiller = SkillDistiller(config)

        observations = distiller._parse_response(
            '{"observations":[{"category":"checkpoint","confidence":0.5}]}'
        )

        assert observations == []
