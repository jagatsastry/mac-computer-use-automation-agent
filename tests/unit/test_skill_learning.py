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


class TestDistillerConfidenceCap:
    """Bug 1: Distiller encodes wrong paths at high confidence during replan runs."""

    @pytest.mark.asyncio
    async def test_distiller_caps_confidence_on_replan_run(self, registry):
        """When had_replan=True, all observation confidences should be capped at 0.6."""
        registry._distiller = AsyncMock()
        registry._distiller.distill.return_value = [
            SkillObservation(
                category="alternative_path",
                condition="Return button missing",
                recommendation="Use product support instead",
                rationale="Trace showed product support link",
                confidence=0.9,
            ),
            SkillObservation(
                category="checkpoint",
                condition="Order page loaded",
                recommendation="Verify order title",
                rationale="Prevents wrong order",
                confidence=0.75,
            ),
        ]

        trace = [
            StepResult(
                step=ActionStep(
                    action="click",
                    params={"element": "Return or Replace Items"},
                    verify="Return flow visible",
                ),
                success=False,
                evidence="Element not found",
                retry_strategies_used=["visible_alternative_affordance"],
                suggested_element="Product support",
            )
        ]

        observations = await registry.learn_from_run(
            "return-amazon-order",
            "Return Tylenol order",
            trace,
            skill_context="## Steps\n1. Find order",
            run_id="run-cap-test",
            had_replan=True,
        )

        assert len(observations) == 2
        for obs in observations:
            assert obs.confidence <= 0.6, (
                f"Observation confidence {obs.confidence} exceeds 0.6 cap for replan run"
            )

    @pytest.mark.asyncio
    async def test_distiller_preserves_confidence_on_clean_run(self, registry):
        """When had_replan=False, observation confidences should be unchanged."""
        registry._distiller = AsyncMock()
        registry._distiller.distill.return_value = [
            SkillObservation(
                category="alternative_path",
                condition="Return button missing",
                recommendation="Use View item first",
                rationale="Trace showed View item was visible",
                confidence=0.9,
            ),
        ]

        trace = [
            StepResult(
                step=ActionStep(
                    action="click",
                    params={"element": "Return or Replace Items"},
                    verify="Return flow visible",
                ),
                success=False,
                evidence="Element not found",
                retry_strategies_used=["visible_alternative_affordance"],
                suggested_element="View item",
            )
        ]

        observations = await registry.learn_from_run(
            "return-amazon-order",
            "Return Tylenol order",
            trace,
            skill_context="## Steps\n1. Find order",
            run_id="run-clean-test",
            had_replan=False,
        )

        assert len(observations) == 1
        assert observations[0].confidence == 0.9

    def test_distiller_prompt_mentions_replan_skepticism(self):
        """The distiller prompt should warn about replan alternative_path observations."""
        from pathlib import Path

        prompt_path = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "automation_agent"
            / "skills"
            / "prompts"
            / "distill_skill_updates.md"
        )
        content = prompt_path.read_text()
        assert "skeptical" in content.lower(), (
            "Distiller prompt should mention being skeptical of alternative paths during replan"
        )
        assert "alternative path" in content.lower(), (
            "Distiller prompt should reference 'alternative path' observations"
        )


class TestReplanPromptStrength:
    """Bug 3: ReplanPatch comes back empty — prompt needs strengthening."""

    def test_replan_prompt_requires_derived_skill_patch(self):
        """The replan prompt should use MUST language for derived_skill_patch."""
        from pathlib import Path

        prompt_path = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "automation_agent"
            / "planner"
            / "prompts"
            / "replan_from_state.md"
        )
        content = prompt_path.read_text()
        assert "MUST include" in content, (
            "Replan prompt should require derived_skill_patch with MUST language"
        )
        assert "derived_skill_patch" in content

    def test_replan_prompt_no_optional_language(self):
        """The replan prompt should NOT say derived_skill_patch is optional."""
        from pathlib import Path

        prompt_path = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "automation_agent"
            / "planner"
            / "prompts"
            / "replan_from_state.md"
        )
        content = prompt_path.read_text()
        assert "Optionally include a derived_skill_patch" not in content, (
            "Replan prompt should not use optional language for derived_skill_patch"
        )
        # Also check the old "optional" footer
        assert 'The "derived_skill_patch" field is optional' not in content, (
            "Replan prompt should not mark derived_skill_patch as optional"
        )


class TestSkillErrorRecoveryHints:
    """Bug 2: suggest_alternative_affordance ignores skill error recovery hints."""

    @pytest.mark.asyncio
    async def test_skill_error_recovery_used_before_vision(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """When skill has error recovery hints for a missing target, use them first."""
        from automation_agent.logging.event_logger import EventLogger
        from automation_agent.orchestrator.agent import AutomationAgent
        from automation_agent.shared_models import FindElementResult

        config = AgentConfig(
            _env_file=None,
            anthropic_api_key="test-key-not-real",
            model_provider="local",
        )
        logger = EventLogger(tmp_log_dir)
        agent = AutomationAgent(
            planner=mock_planner,
            skill_registry=mock_skill_registry,
            coordinator=mock_coordinator,
            actuator=mock_actuator,
            config=config,
            logger=logger,
        )

        # Set up skill context with error recovery hints
        agent._current_skill_context = textwrap.dedent("""\
            Skill: return-amazon-order
            ## Steps
            1. Click "Return or Replace Items"

            ## Error Recovery
            - If "Return or Replace Items" is absent: click "View item" on the order card first
            - If order not found: scroll down to find older orders
        """)

        # Mock suggest_alternative_affordance to track if it gets called
        mock_coordinator.suggest_alternative_affordance = AsyncMock(
            return_value={"affordance": "Contact support", "reason": "Vision suggestion"}
        )

        step = ActionStep(
            action="click",
            params={"element": "Return or Replace Items"},
            verify="Return flow visible",
        )
        result = StepResult(
            step=step,
            success=False,
            evidence="Action failed: Element not found: Return or Replace Items",
            error="Element not found: Return or Replace Items",
        )

        updated = await agent._suggest_alternative_for_missing_target(step, "Return order", result)

        # Should use skill hint "View item", NOT vision model's "Contact support"
        assert updated.suggested_element == "View item", (
            f"Expected skill hint 'View item', got '{updated.suggested_element}'"
        )
        # Vision model should NOT have been called
        mock_coordinator.suggest_alternative_affordance.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_vision_fallback_when_no_skill_hints(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """When no skill context is set, fall through to vision model."""
        from automation_agent.logging.event_logger import EventLogger
        from automation_agent.orchestrator.agent import AutomationAgent

        config = AgentConfig(
            _env_file=None,
            anthropic_api_key="test-key-not-real",
            model_provider="local",
        )
        logger = EventLogger(tmp_log_dir)
        agent = AutomationAgent(
            planner=mock_planner,
            skill_registry=mock_skill_registry,
            coordinator=mock_coordinator,
            actuator=mock_actuator,
            config=config,
            logger=logger,
        )

        # No skill context
        agent._current_skill_context = None

        mock_coordinator.suggest_alternative_affordance = AsyncMock(
            return_value={"affordance": "View order details", "reason": "Vision found it"}
        )
        mock_coordinator.capture_screenshot = AsyncMock(return_value="base64data")

        step = ActionStep(
            action="click",
            params={"element": "Return or Replace Items"},
            verify="Return flow visible",
        )
        result = StepResult(
            step=step,
            success=False,
            evidence="Action failed: Element not found: Return or Replace Items",
            error="Element not found: Return or Replace Items",
        )

        updated = await agent._suggest_alternative_for_missing_target(
            step, "Return order", result
        )

        # Vision model should have been called as fallback
        mock_coordinator.suggest_alternative_affordance.assert_awaited_once()
        assert updated.suggested_element == "View order details"

    def test_skill_hint_parsing(self):
        """Test that _check_skill_error_recovery extracts alternatives from markdown."""
        from automation_agent.orchestrator.agent import AutomationAgent

        skill_context = textwrap.dedent("""\
            Skill: return-amazon-order
            ## Steps
            1. Click "Return or Replace Items"

            ## Error Recovery
            - If "Return or Replace Items" is absent: click "View item" on the order card first
            - If order not found: scroll down to find older orders
        """)

        result = AutomationAgent._check_skill_error_recovery(
            "Return or Replace Items", skill_context
        )
        assert result is not None
        assert "View item" in result
