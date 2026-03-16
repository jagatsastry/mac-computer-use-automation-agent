"""Integration/wiring tests for the Skill Librarian feature.

Tests:
- Loader: parent-skill-id parsing, Learned Tips extraction
- Registry: learned_tips_text in runtime context, librarian instantiation, promote_from_run
- Orchestrator: _maybe_learn_skill_run returns list, _maybe_promote_skill wiring
"""

import textwrap
from dataclasses import dataclass, field
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.config import AgentConfig
from automation_agent.shared_models import (
    ActionPlan,
    ActionStep,
    StepResult,
)
from automation_agent.skills.loader import parse_skill_file
from automation_agent.skills.models import Skill, SkillObservation
from automation_agent.skills.registry import SkillRegistryImpl


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


SAMPLE_SKILL_WITH_TIPS = textwrap.dedent("""\
    ---
    name: return-amazon-order
    skill-id: return-amazon-order
    description: Return an item on Amazon
    summary: Help return Amazon orders
    tags: [ecommerce, return]
    trigger-keywords: [return, amazon, order]
    parameters:
      item:
        type: string
        required: true
        description: What to return
    requires:
      os: darwin
    success-condition: Return confirmation visible
    max-retries: 3
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

    ## Learned Tips
    - When the return window has expired, check the product support page instead
    - If multiple orders match, sort by most recent first
""")

SAMPLE_SKILL_WITH_PARENT = textwrap.dedent("""\
    ---
    name: return-walmart-order
    skill-id: return-walmart-order
    parent-skill-id: return-amazon-order
    description: Return an item on Walmart
    summary: Help return Walmart orders
    tags: [ecommerce, return]
    trigger-keywords: [return, walmart, order]
    parameters:
      item:
        type: string
        required: true
        description: What to return
    requires:
      os: darwin
    success-condition: Return confirmation visible
    max-retries: 3
    ---

    ## Steps
    1. Open Walmart orders page
       - verify: Orders page visible

    ## Error Recovery
    - If return button is absent: check order details first
""")

SAMPLE_SKILL_MINIMAL = textwrap.dedent("""\
    ---
    name: google-search
    skill-id: google-search
    description: Search on Google
    summary: Perform a Google search
    tags: [search]
    trigger-keywords: [search, google]
    parameters: {}
    requires:
      os: darwin
    success-condition: Search results visible
    ---

    ## Steps
    1. Open Google
       - verify: Google page visible
""")


# ---------------------------------------------------------------------------
# Loader Tests
# ---------------------------------------------------------------------------

class TestLoaderParentSkillId:
    """Test that parse_skill_file reads parent-skill-id from YAML frontmatter."""

    def test_parse_skill_with_parent_skill_id(self):
        skill = parse_skill_file(SAMPLE_SKILL_WITH_PARENT)
        assert skill.parent_skill_id == "return-amazon-order"

    def test_parse_skill_without_parent_skill_id(self):
        skill = parse_skill_file(SAMPLE_SKILL_MINIMAL)
        assert skill.parent_skill_id == ""

    def test_parse_skill_parent_id_does_not_break_other_fields(self):
        skill = parse_skill_file(SAMPLE_SKILL_WITH_PARENT)
        assert skill.name == "return-walmart-order"
        assert skill.skill_id == "return-walmart-order"
        assert skill.description == "Return an item on Walmart"


class TestLoaderLearnedTips:
    """Test that parse_skill_file extracts ## Learned Tips section."""

    def test_parse_skill_with_learned_tips(self):
        skill = parse_skill_file(SAMPLE_SKILL_WITH_TIPS)
        assert skill.learned_tips_text != ""
        assert "return window has expired" in skill.learned_tips_text
        assert "sort by most recent first" in skill.learned_tips_text

    def test_parse_skill_without_learned_tips(self):
        skill = parse_skill_file(SAMPLE_SKILL_MINIMAL)
        assert skill.learned_tips_text == ""

    def test_learned_tips_does_not_include_other_sections(self):
        skill = parse_skill_file(SAMPLE_SKILL_WITH_TIPS)
        # Should NOT contain content from other sections
        assert "## Steps" not in skill.learned_tips_text
        assert "## Error Recovery" not in skill.learned_tips_text
        assert "## Notes" not in skill.learned_tips_text

    def test_round_trip_skill_with_learned_tips(self, tmp_path):
        """Write a skill file with Learned Tips, then parse it back."""
        skill_path = tmp_path / "test_skill.md"
        skill_path.write_text(SAMPLE_SKILL_WITH_TIPS)
        content = skill_path.read_text()
        skill = parse_skill_file(content)
        assert "return window has expired" in skill.learned_tips_text
        # Also verify other sections survived
        assert skill.steps_text != ""
        assert skill.error_recovery_text != ""
        assert skill.notes_text != ""


# ---------------------------------------------------------------------------
# Registry Tests
# ---------------------------------------------------------------------------

class TestRegistryLearnedTipsContext:
    """Test that build_runtime_context includes learned_tips_text."""

    @pytest.fixture
    def config(self, tmp_path):
        return _make_config(skill_learning_dir=tmp_path / "skill-learning")

    @pytest.fixture
    def registry_with_tips(self, tmp_path, config):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "return_amazon_order.md").write_text(SAMPLE_SKILL_WITH_TIPS)
        return SkillRegistryImpl(skill_dir=skill_dir, config=config)

    @pytest.fixture
    def registry_without_tips(self, tmp_path, config):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "google_search.md").write_text(SAMPLE_SKILL_MINIMAL)
        return SkillRegistryImpl(skill_dir=skill_dir, config=config)

    def test_runtime_context_includes_learned_tips(self, registry_with_tips):
        ctx = registry_with_tips.build_runtime_context(
            "return-amazon-order", {"item": "Tylenol"}
        )
        assert ctx is not None
        assert "## Learned Tips" in ctx
        assert "return window has expired" in ctx

    def test_runtime_context_without_learned_tips(self, registry_without_tips):
        ctx = registry_without_tips.build_runtime_context("google-search")
        assert ctx is not None
        assert "## Learned Tips" not in ctx

    def test_learned_tips_appears_after_notes(self, registry_with_tips):
        ctx = registry_with_tips.build_runtime_context(
            "return-amazon-order", {"item": "Tylenol"}
        )
        assert ctx is not None
        notes_idx = ctx.index("## Notes")
        tips_idx = ctx.index("## Learned Tips")
        assert tips_idx > notes_idx, "Learned Tips should appear after Notes"

    def test_learned_tips_appears_before_observed_variants_if_present(
        self, registry_with_tips
    ):
        """If there are observed variants, tips should come before them."""
        registry_with_tips._experience_store.append(
            "return-amazon-order",
            [
                SkillObservation(
                    category="alternative_path",
                    condition="Return absent",
                    recommendation="Try product support",
                    confidence=0.9,
                )
            ],
        )
        ctx = registry_with_tips.build_runtime_context(
            "return-amazon-order", {"item": "Tylenol"}
        )
        assert ctx is not None
        tips_idx = ctx.index("## Learned Tips")
        variants_idx = ctx.index("## Observed Variants")
        assert tips_idx < variants_idx, "Learned Tips should appear before Observed Variants"


class TestRegistryLibrarianInstantiation:
    """Test that SkillRegistryImpl instantiates/skips the librarian based on config."""

    def test_librarian_not_instantiated_when_disabled(self, tmp_path):
        config = _make_config(
            skill_learning_dir=tmp_path / "skill-learning",
            skill_librarian_enabled=False,
        )
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "google_search.md").write_text(SAMPLE_SKILL_MINIMAL)
        registry = SkillRegistryImpl(skill_dir=skill_dir, config=config)
        assert getattr(registry, "_librarian", None) is None

    def test_librarian_instantiated_when_enabled(self, tmp_path):
        config = _make_config(
            skill_learning_dir=tmp_path / "skill-learning",
            skill_librarian_enabled=True,
        )
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "google_search.md").write_text(SAMPLE_SKILL_MINIMAL)
        # Mock the SkillLibrarian class so we don't need librarian.py to exist yet
        mock_librarian_instance = MagicMock()
        mock_librarian_cls = MagicMock(return_value=mock_librarian_instance)
        import sys
        import types
        fake_module = types.ModuleType("automation_agent.skills.librarian")
        fake_module.SkillLibrarian = mock_librarian_cls
        with patch.dict(sys.modules, {"automation_agent.skills.librarian": fake_module}):
            registry = SkillRegistryImpl(skill_dir=skill_dir, config=config)
            assert registry._librarian is not None
            mock_librarian_cls.assert_called_once()


class TestRegistryPromoteFromRun:
    """Test the promote_from_run public method on the registry."""

    @pytest.fixture
    def config(self, tmp_path):
        return _make_config(
            skill_learning_dir=tmp_path / "skill-learning",
            skill_librarian_enabled=False,
        )

    @pytest.fixture
    def registry(self, tmp_path, config):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "google_search.md").write_text(SAMPLE_SKILL_MINIMAL)
        return SkillRegistryImpl(skill_dir=skill_dir, config=config)

    @pytest.mark.asyncio
    async def test_promote_from_run_returns_none_when_disabled(self, registry):
        result = await registry.promote_from_run(
            goal="test",
            skill_name="google-search",
            derived_session=None,
            observations=[],
            trace=[],
            run_id="run-1",
            had_replan=False,
            success=True,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_promote_from_run_delegates_to_librarian(self, registry):
        mock_librarian = AsyncMock()
        mock_librarian.evaluate_run = AsyncMock(return_value=None)
        registry._librarian = mock_librarian

        observations = [
            SkillObservation(
                category="checkpoint",
                condition="Results load",
                recommendation="Wait for results",
                confidence=0.8,
                run_id="run-1",
            )
        ]
        trace = [
            StepResult(
                step=ActionStep(action="click", params={"element": "Search"}, verify="Results visible"),
                success=True,
            )
        ]

        await registry.promote_from_run(
            goal="Search Google",
            skill_name="google-search",
            derived_session=None,
            observations=observations,
            trace=trace,
            run_id="run-1",
            had_replan=False,
            success=True,
        )

        mock_librarian.evaluate_run.assert_awaited_once()
        call_kwargs = mock_librarian.evaluate_run.call_args.kwargs
        assert call_kwargs["goal"] == "Search Google"
        assert call_kwargs["skill_name"] == "google-search"
        assert call_kwargs["run_id"] == "run-1"
        assert call_kwargs["success"] is True


# ---------------------------------------------------------------------------
# Orchestrator Tests
# ---------------------------------------------------------------------------

class TestOrchestratorLearnReturnType:
    """Test that _maybe_learn_skill_run returns List[SkillObservation]."""

    @pytest.fixture
    def agent(self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir):
        from automation_agent.logging.event_logger import EventLogger
        from automation_agent.orchestrator.agent import AutomationAgent

        config = _make_config()
        logger = EventLogger(tmp_log_dir)
        return AutomationAgent(
            planner=mock_planner,
            skill_registry=mock_skill_registry,
            coordinator=mock_coordinator,
            actuator=mock_actuator,
            config=config,
            logger=logger,
        )

    @pytest.mark.asyncio
    async def test_learn_returns_empty_list_when_no_skill(self, agent):
        result = await agent._maybe_learn_skill_run(
            goal="test",
            skill_name=None,
            skill_context="",
            step_results=[],
            had_replan=False,
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_learn_returns_empty_list_when_no_learn_method(self, agent):
        result = await agent._maybe_learn_skill_run(
            goal="test",
            skill_name="some-skill",
            skill_context="",
            step_results=[
                StepResult(
                    step=ActionStep(action="click", params={"element": "X"}, verify="Y"),
                    success=True,
                )
            ],
            had_replan=False,
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_learn_returns_observations_on_success(self, agent):
        expected_obs = [
            SkillObservation(
                category="checkpoint",
                condition="Page loaded",
                recommendation="Wait for page",
                confidence=0.8,
                run_id="run-1",
            )
        ]
        agent.skill_registry.learn_from_run = AsyncMock(return_value=expected_obs)

        trace = [
            StepResult(
                step=ActionStep(action="click", params={"element": "X"}, verify="Y"),
                success=True,
                retry_strategies_used=["fallback"],
            )
        ]
        result = await agent._maybe_learn_skill_run(
            goal="test",
            skill_name="some-skill",
            skill_context="",
            step_results=trace,
            had_replan=False,
        )
        assert result == expected_obs

    @pytest.mark.asyncio
    async def test_learn_returns_empty_list_on_exception(self, agent):
        agent.skill_registry.learn_from_run = AsyncMock(side_effect=RuntimeError("boom"))

        trace = [
            StepResult(
                step=ActionStep(action="click", params={"element": "X"}, verify="Y"),
                success=True,
                retry_strategies_used=["fallback"],
            )
        ]
        result = await agent._maybe_learn_skill_run(
            goal="test",
            skill_name="some-skill",
            skill_context="",
            step_results=trace,
            had_replan=False,
        )
        assert result == []


class TestOrchestratorMaybePromoteSkill:
    """Test the _maybe_promote_skill orchestrator method."""

    @pytest.fixture
    def agent(self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir):
        from automation_agent.logging.event_logger import EventLogger
        from automation_agent.orchestrator.agent import AutomationAgent

        config = _make_config()
        logger = EventLogger(tmp_log_dir)
        return AutomationAgent(
            planner=mock_planner,
            skill_registry=mock_skill_registry,
            coordinator=mock_coordinator,
            actuator=mock_actuator,
            config=config,
            logger=logger,
        )

    @pytest.mark.asyncio
    async def test_promote_noop_when_no_skill_name(self, agent):
        """Should return immediately when skill_name is empty."""
        await agent._maybe_promote_skill(
            goal="test",
            skill_name=None,
            observations=[SkillObservation(category="c", condition="c", recommendation="r")],
            derived_session=None,
            step_results=[],
            run_id="run-1",
            had_replan=False,
            success=True,
        )
        # No exception = pass

    @pytest.mark.asyncio
    async def test_promote_fires_with_empty_current_observations(self, agent):
        """Empty observations should NOT block promotion — librarian loads history from disk."""
        mock_promote = AsyncMock(return_value=None)
        agent.skill_registry.promote_from_run = mock_promote

        await agent._maybe_promote_skill(
            goal="test",
            skill_name="some-skill",
            observations=[],
            derived_session=None,
            step_results=[],
            run_id="run-1",
            had_replan=False,
            success=True,
        )

        # promote_from_run SHOULD be called even with empty observations
        mock_promote.assert_awaited_once()
        # Verify the empty list was passed through (not filtered out)
        call_kwargs = mock_promote.call_args.kwargs
        assert call_kwargs["observations"] == []
        assert call_kwargs["skill_name"] == "some-skill"
        assert call_kwargs["goal"] == "test"

    @pytest.mark.asyncio
    async def test_promote_noop_when_no_promote_method(self, agent):
        """Should return when skill_registry lacks promote_from_run."""
        observations = [
            SkillObservation(category="c", condition="c", recommendation="r", confidence=0.8)
        ]
        # mock_skill_registry doesn't have promote_from_run by default
        await agent._maybe_promote_skill(
            goal="test",
            skill_name="some-skill",
            observations=observations,
            derived_session=None,
            step_results=[],
            run_id="run-1",
            had_replan=False,
            success=True,
        )

    @pytest.mark.asyncio
    async def test_promote_calls_registry_promote(self, agent):
        """Should delegate to registry.promote_from_run when available."""
        mock_promote = AsyncMock(return_value=None)
        agent.skill_registry.promote_from_run = mock_promote

        observations = [
            SkillObservation(
                category="checkpoint",
                condition="Page loaded",
                recommendation="Wait for page",
                confidence=0.8,
                run_id="run-1",
            )
        ]
        trace = [
            StepResult(
                step=ActionStep(action="click", params={"element": "X"}, verify="Y"),
                success=True,
            )
        ]

        await agent._maybe_promote_skill(
            goal="Search Google",
            skill_name="google-search",
            observations=observations,
            derived_session=None,
            step_results=trace,
            run_id="run-1",
            had_replan=False,
            success=True,
        )

        mock_promote.assert_awaited_once()
        call_kwargs = mock_promote.call_args.kwargs
        assert call_kwargs["goal"] == "Search Google"
        assert call_kwargs["skill_name"] == "google-search"
        assert call_kwargs["success"] is True

    @pytest.mark.asyncio
    async def test_promote_swallows_exceptions(self, agent):
        """Exceptions from promote_from_run must not bubble up."""
        agent.skill_registry.promote_from_run = AsyncMock(
            side_effect=RuntimeError("librarian exploded")
        )

        observations = [
            SkillObservation(category="c", condition="c", recommendation="r", confidence=0.8)
        ]

        # Should NOT raise
        await agent._maybe_promote_skill(
            goal="test",
            skill_name="some-skill",
            observations=observations,
            derived_session=None,
            step_results=[],
            run_id="run-1",
            had_replan=False,
            success=True,
        )
