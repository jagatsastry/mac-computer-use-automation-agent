"""Unit tests for site-aware skill routing (P0-1), Target buy skill (P0-2),
and duplicate walmart deletion (P2-1)."""

import re
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.config import AgentConfig
from automation_agent.shared_models import (
    MatchType,
    SkillRouteCandidate,
    SkillRouteResult,
)
from automation_agent.skills.matcher import match_skill
from automation_agent.skills.models import Skill, SkillRequirements
from automation_agent.skills.registry import SkillRegistryImpl
from automation_agent.skills.router import (
    _SEED_SITES,
    _build_known_sites,
    _build_site_patterns,
    extract_site_entity,
)


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


def _make_skill(name, keywords=None, description="A skill", metadata=None):
    return Skill(
        name=name,
        description=description,
        trigger_keywords=keywords or [name],
        parameters={},
        requires=SkillRequirements(os="darwin"),
        success_condition="Done",
        steps_text=f"1. Do {name}\n   - verify: {name} done",
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# extract_site_entity tests
# ---------------------------------------------------------------------------

class TestExtractSiteEntity:
    def test_extract_on_target(self):
        result = extract_site_entity("buy bed sheets on target")
        assert result == ["target"]

    def test_extract_from_amazon(self):
        result = extract_site_entity("order from amazon")
        assert result == ["amazon"]

    def test_extract_at_walmart(self):
        result = extract_site_entity("shop at walmart")
        assert result == ["walmart"]

    def test_extract_target_com(self):
        result = extract_site_entity("search target.com")
        assert result == ["target"]

    def test_extract_possessive(self):
        result = extract_site_entity("on Target's website")
        assert result == ["target"]

    def test_no_site_generic(self):
        result = extract_site_entity("buy cheap bed sheets")
        assert result is None

    def test_no_false_positive_product(self):
        result = extract_site_entity("buy target gift card")
        assert result is None

    def test_no_false_positive_verb(self):
        result = extract_site_entity("target the cheapest option")
        assert result is None

    def test_case_insensitive(self):
        result = extract_site_entity("buy sheets ON TARGET")
        assert result == ["target"]

    def test_multi_site_conflict(self):
        result = extract_site_entity("buy from amazon and on target")
        assert result == ["amazon", "target"]

    def test_on_sale_at_target(self):
        """'on sale' should not match 'sale' as a site; 'at target' should match."""
        result = extract_site_entity("buy bedsheets on sale at target")
        assert result == ["target"]

    def test_dynamic_known_sites(self):
        custom_sites = frozenset({"shopify", "amazon", "target"})
        result = extract_site_entity("buy on shopify", known_sites=custom_sites)
        assert result == ["shopify"]

    def test_build_known_sites_from_skills(self):
        skills = {
            "zappos-search": _make_skill(
                "zappos-search", metadata={"site": "zappos"}
            ),
        }
        known = _build_known_sites(skills)
        assert "zappos" in known
        # Seed sites also present
        assert "amazon" in known
        assert "target" in known


# ---------------------------------------------------------------------------
# _build_site_patterns tests
# ---------------------------------------------------------------------------

class TestBuildSitePatterns:
    def test_patterns_match_preposition(self):
        patterns = _build_site_patterns(frozenset({"target"}))
        text = "buy on target"
        matches = []
        for p in patterns:
            matches.extend(p.findall(text))
        assert "target" in [m.lower() for m in matches]

    def test_patterns_match_dotcom(self):
        patterns = _build_site_patterns(frozenset({"target"}))
        text = "visit target.com"
        matches = []
        for p in patterns:
            matches.extend(p.findall(text))
        assert "target" in [m.lower() for m in matches]


# ---------------------------------------------------------------------------
# _filter_by_site tests
# ---------------------------------------------------------------------------

class TestFilterBySite:
    def _make_registry(self, tmp_path, skills_dict):
        """Build a registry with pre-loaded skills (no file IO)."""
        skill_dir = tmp_path / "empty_skills"
        skill_dir.mkdir(exist_ok=True)
        registry = SkillRegistryImpl(skill_dir=skill_dir, config=_make_config())
        registry._skills = skills_dict
        registry._rebuild_router()
        return registry

    def test_filter_removes_wrong_site(self, tmp_path):
        skills = {
            "amazon-search": _make_skill(
                "amazon-search", metadata={"site": "amazon"}
            ),
        }
        registry = self._make_registry(tmp_path, skills)
        candidates = [
            SkillRouteCandidate(
                skill_id="amazon-search",
                match_type=MatchType.DIRECT,
                confidence=0.9,
                reason="test",
            )
        ]
        filtered = registry._filter_by_site(candidates, "target")
        assert len(filtered) == 0

    def test_filter_keeps_matching_site(self, tmp_path):
        skills = {
            "buy-on-target": _make_skill(
                "buy-on-target", metadata={"site": "target"}
            ),
        }
        registry = self._make_registry(tmp_path, skills)
        candidates = [
            SkillRouteCandidate(
                skill_id="buy-on-target",
                match_type=MatchType.DIRECT,
                confidence=0.9,
                reason="test",
            )
        ]
        filtered = registry._filter_by_site(candidates, "target")
        assert len(filtered) == 1

    def test_filter_keeps_generic_skill(self, tmp_path):
        skills = {
            "open-safari": _make_skill("open-safari"),
        }
        registry = self._make_registry(tmp_path, skills)
        candidates = [
            SkillRouteCandidate(
                skill_id="open-safari",
                match_type=MatchType.GENERIC,
                confidence=0.6,
                reason="test",
            )
        ]
        filtered = registry._filter_by_site(candidates, "target")
        assert len(filtered) == 1

    def test_filter_no_entity(self, tmp_path):
        """When site_entity is None, no filtering happens (caller checks)."""
        skills = {
            "amazon-search": _make_skill(
                "amazon-search", metadata={"site": "amazon"}
            ),
        }
        registry = self._make_registry(tmp_path, skills)
        candidates = [
            SkillRouteCandidate(
                skill_id="amazon-search",
                match_type=MatchType.DIRECT,
                confidence=0.9,
                reason="test",
            )
        ]
        # Caller is responsible for not calling with None; test pass-through
        filtered = registry._filter_by_site(candidates, "amazon")
        assert len(filtered) == 1

    def test_filter_no_skill_for_site(self, tmp_path):
        skills = {
            "amazon-search": _make_skill(
                "amazon-search", metadata={"site": "amazon"}
            ),
        }
        registry = self._make_registry(tmp_path, skills)
        candidates = [
            SkillRouteCandidate(
                skill_id="amazon-search",
                match_type=MatchType.DIRECT,
                confidence=0.9,
                reason="test",
            )
        ]
        filtered = registry._filter_by_site(candidates, "bestbuy")
        assert len(filtered) == 0


# ---------------------------------------------------------------------------
# Site filter integration with match() — Stage 2b LLM path
# ---------------------------------------------------------------------------

class TestSiteFilterIntegration:
    @pytest.mark.asyncio
    async def test_match_filters_wrong_site_llm(self, tmp_path):
        """LLM router returns amazon-search for 'buy on target' -> filtered out."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        content = textwrap.dedent("""\
            ---
            name: amazon-search
            description: Search Amazon for a product
            trigger-keywords: [amazon, buy, shop, purchase]
            site: amazon
            parameters: {}
            requires:
              os: darwin
            success-condition: Done
            ---

            ## Steps
            1. Do amazon search
               - verify: done
        """)
        (skill_dir / "amazon.md").write_text(content)
        registry = SkillRegistryImpl(
            skill_dir=skill_dir, config=_make_config()
        )

        route_result = SkillRouteResult(
            candidates=[
                SkillRouteCandidate(
                    skill_id="amazon-search",
                    match_type=MatchType.DIRECT,
                    confidence=0.9,
                    reason="Shopping skill",
                )
            ]
        )
        mock_router = AsyncMock()
        mock_router.route.return_value = route_result
        registry._router = mock_router

        result = await registry.match("buy bed sheets on target")
        assert result is None

    @pytest.mark.asyncio
    async def test_match_keeps_correct_site_llm(self, tmp_path):
        """LLM router returns buy-on-target for 'buy on target' -> kept."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        content = textwrap.dedent("""\
            ---
            name: buy-on-target
            description: Buy on Target.com
            trigger-keywords: [target, buy, shop, purchase]
            site: target
            parameters: {}
            requires:
              os: darwin
            success-condition: Done
            ---

            ## Steps
            1. Click on search
               - verify: done
        """)
        (skill_dir / "target.md").write_text(content)
        registry = SkillRegistryImpl(
            skill_dir=skill_dir, config=_make_config()
        )

        route_result = SkillRouteResult(
            candidates=[
                SkillRouteCandidate(
                    skill_id="buy-on-target",
                    match_type=MatchType.DIRECT,
                    confidence=0.9,
                    reason="Target shopping skill",
                )
            ]
        )
        mock_router = AsyncMock()
        mock_router.route.return_value = route_result
        registry._router = mock_router

        result = await registry.match("buy bed sheets on target")
        assert result is not None
        assert result.skill_name == "buy-on-target"

    @pytest.mark.asyncio
    async def test_match_multi_site_returns_none(self, tmp_path):
        """Multiple conflicting sites -> no match."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        content = textwrap.dedent("""\
            ---
            name: amazon-search
            description: Amazon search
            trigger-keywords: [amazon, buy]
            site: amazon
            parameters: {}
            requires:
              os: darwin
            success-condition: Done
            ---

            ## Steps
            1. Do it
               - verify: done
        """)
        (skill_dir / "amazon.md").write_text(content)
        registry = SkillRegistryImpl(
            skill_dir=skill_dir, config=_make_config()
        )

        mock_router = AsyncMock()
        mock_router.route.return_value = SkillRouteResult(
            candidates=[
                SkillRouteCandidate(
                    skill_id="amazon-search",
                    match_type=MatchType.DIRECT,
                    confidence=0.9,
                    reason="test",
                )
            ]
        )
        registry._router = mock_router

        result = await registry.match("buy from amazon and on target")
        assert result is None


# ---------------------------------------------------------------------------
# Site filter on embedding path (Stage 1)
# ---------------------------------------------------------------------------

class TestSiteFilterEmbeddingPath:
    @pytest.mark.asyncio
    async def test_embedding_clear_winner_filtered_by_site(self, tmp_path):
        """Embedding clear winner with wrong site is filtered out."""
        from unittest.mock import MagicMock, patch

        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        content = textwrap.dedent("""\
            ---
            name: amazon-search
            description: Search Amazon
            trigger-keywords: [amazon, buy, shop]
            site: amazon
            parameters: {}
            requires:
              os: darwin
            success-condition: Done
            ---

            ## Steps
            1. Do amazon search
               - verify: done
        """)
        (skill_dir / "amazon.md").write_text(content)

        # Build registry without embedding enabled (avoids EmbeddingIndex init)
        config = _make_config(
            skill_embedding_enabled=True,
            skill_embedding_rerank_threshold=0.90,
            skill_embedding_min_gap=0.10,
        )

        # Bypass __init__ to avoid embedding build, then set attrs manually
        with patch.object(SkillRegistryImpl, "__init__", lambda self, **kw: None):
            registry = SkillRegistryImpl.__new__(SkillRegistryImpl)

        registry._skills = {}
        registry._config = config
        registry._cards = []
        registry._router = None
        registry._experience_store = None
        registry._distiller = None
        registry._librarian = None
        registry._skill_dir = skill_dir
        registry._event_logger = None
        registry._embedding_index = None
        registry._known_sites = _SEED_SITES

        # Load skills without triggering embedding build
        from automation_agent.skills.loader import load_skill_from_file

        skill = load_skill_from_file(skill_dir / "amazon.md")
        registry._skills[skill.name] = skill
        registry._known_sites = _build_known_sites(registry._skills)

        # Mock embedding index returning amazon-search as clear winner
        mock_index = MagicMock()
        mock_index.query.return_value = [
            SkillRouteCandidate(
                skill_id="amazon-search",
                match_type=MatchType.GENERIC,
                confidence=0.95,
                reason="Embedding similarity",
            ),
        ]
        registry._embedding_index = mock_index

        result = await registry.match("buy bed sheets on target")
        # amazon-search should be filtered because site=amazon != target
        assert result is None


# ---------------------------------------------------------------------------
# AC-7: Keyword fallback guard (required-keywords)
# ---------------------------------------------------------------------------

class TestKeywordFallbackGuard:
    def test_keyword_requires_target(self):
        """Keyword fallback matches buy-on-target when 'target' is in prompt."""
        skill = _make_skill(
            "buy-on-target",
            keywords=["target", "buy", "purchase", "shop", "target.com"],
            metadata={"required-keywords": ["target", "target.com"]},
        )
        result = match_skill("buy bed sheets on target", [skill])
        assert result is not None
        assert result[0].name == "buy-on-target"

    def test_keyword_no_target_no_match(self):
        """Without 'target' in prompt, required-keywords gate blocks match."""
        skill = _make_skill(
            "buy-on-target",
            keywords=["target", "buy", "purchase", "shop", "target.com"],
            metadata={"required-keywords": ["target", "target.com"]},
        )
        result = match_skill("buy cheap bed sheets", [skill])
        # "buy" matches but none of the required keywords match
        assert result is None

    def test_keyword_target_com(self):
        """'target.com' in prompt satisfies required-keywords."""
        skill = _make_skill(
            "buy-on-target",
            keywords=["target", "buy", "purchase", "shop", "target.com"],
            metadata={"required-keywords": ["target", "target.com"]},
        )
        result = match_skill("search target.com for sheets", [skill])
        assert result is not None


# ---------------------------------------------------------------------------
# Skill file validation: buy_on_target.md
# ---------------------------------------------------------------------------

SKILL_LIBRARY_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "automation_agent"
    / "skills"
    / "library"
)


class TestBuyOnTargetSkill:
    def test_buy_on_target_loads(self):
        """buy_on_target.md loads without errors."""
        from automation_agent.skills.loader import load_skill_from_file

        skill_path = SKILL_LIBRARY_DIR / "buy_on_target.md"
        assert skill_path.exists(), f"buy_on_target.md not found at {skill_path}"
        skill = load_skill_from_file(skill_path)
        assert skill.name == "buy-on-target"

    def test_buy_on_target_validates(self):
        """validate_all returns zero errors for buy-on-target."""
        registry = SkillRegistryImpl(skill_dir=SKILL_LIBRARY_DIR)
        errors = registry.validate_all()
        target_errors = [e for e in errors if "buy-on-target" in e]
        assert target_errors == [], f"Validation errors: {target_errors}"

    def test_buy_on_target_has_site_metadata(self):
        from automation_agent.skills.loader import load_skill_from_file

        skill = load_skill_from_file(SKILL_LIBRARY_DIR / "buy_on_target.md")
        assert skill.metadata.get("site") == "target"

    def test_buy_on_target_keywords_no_amazon(self):
        from automation_agent.skills.loader import load_skill_from_file

        skill = load_skill_from_file(SKILL_LIBRARY_DIR / "buy_on_target.md")
        assert "amazon" not in skill.trigger_keywords

    def test_buy_on_target_has_required_keywords(self):
        from automation_agent.skills.loader import load_skill_from_file

        skill = load_skill_from_file(SKILL_LIBRARY_DIR / "buy_on_target.md")
        rk = skill.metadata.get("required-keywords", [])
        assert "target" in rk or "target.com" in rk


class TestBuyOnTargetStepsCompile:
    """Verify each step in buy_on_target.md compiles through the skill compiler."""

    def _get_agent_class(self):
        from automation_agent.orchestrator.agent import AutomationAgent

        return AutomationAgent

    def _make_agent(self):
        AgentClass = self._get_agent_class()
        planner = AsyncMock()
        skill_registry = MagicMock()
        skill_registry.match = AsyncMock(return_value=None)
        skill_registry.learn_from_run = AsyncMock(return_value=[])
        skill_registry.promote_from_run = AsyncMock(return_value=None)
        coordinator = AsyncMock()
        coordinator.capabilities = MagicMock(return_value=frozenset())
        coordinator.capture_screenshot = AsyncMock(return_value="base64data")
        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.type_text = MagicMock(return_value={"success": True})
        actuator.scroll = MagicMock(return_value={"success": True})
        config = _make_config()
        return AgentClass(planner, skill_registry, coordinator, actuator, config)

    def test_buy_on_target_steps_compile(self):
        """Each step through _compile_skill_instruction() returns non-None."""
        from automation_agent.skills.loader import load_skill_from_file

        skill = load_skill_from_file(SKILL_LIBRARY_DIR / "buy_on_target.md")
        agent = self._make_agent()

        # Parse steps from the skill's steps_text
        step_pattern = re.compile(r"^\d+\.\s+(.*)$", re.MULTILINE)
        verify_pattern = re.compile(r"^\s+-\s+verify:\s+(.*)$", re.MULTILINE)

        steps = step_pattern.findall(skill.steps_text)
        verifies = verify_pattern.findall(skill.steps_text)

        assert len(steps) >= 5, f"Expected >= 5 steps, got {len(steps)}"

        for i, instruction in enumerate(steps):
            verify = verifies[i] if i < len(verifies) else "step done"
            compiled = agent._compile_skill_instruction(instruction, verify)
            assert compiled is not None, (
                f"Step {i + 1} failed to compile: '{instruction}'"
            )

    def test_buy_on_target_element_descriptions_concise(self):
        """Click steps produce element descriptions <= 8 words."""
        from automation_agent.skills.loader import load_skill_from_file

        skill = load_skill_from_file(SKILL_LIBRARY_DIR / "buy_on_target.md")
        agent = self._make_agent()

        step_pattern = re.compile(r"^\d+\.\s+(.*)$", re.MULTILINE)
        verify_pattern = re.compile(r"^\s+-\s+verify:\s+(.*)$", re.MULTILINE)

        steps = step_pattern.findall(skill.steps_text)
        verifies = verify_pattern.findall(skill.steps_text)

        for i, instruction in enumerate(steps):
            verify = verifies[i] if i < len(verifies) else "step done"
            compiled = agent._compile_skill_instruction(instruction, verify)
            if compiled is None:
                continue
            for action_step in compiled:
                if action_step.action == "click":
                    element = action_step.params.get("element", "")
                    word_count = len(element.split())
                    assert word_count <= 8, (
                        f"Step {i + 1} element '{element}' has "
                        f"{word_count} words (max 8)"
                    )


# ---------------------------------------------------------------------------
# P2-1: Duplicate walmart files deleted
# ---------------------------------------------------------------------------

class TestDuplicateWalmartDeleted:
    def test_duplicate_walmart_deleted(self):
        stub1 = SKILL_LIBRARY_DIR / "return-walmart-order.md"
        stub2 = SKILL_LIBRARY_DIR / "return-walmart-order-2.md"
        assert not stub1.exists(), f"Duplicate file still exists: {stub1}"
        assert not stub2.exists(), f"Duplicate file still exists: {stub2}"


# ---------------------------------------------------------------------------
# amazon_search.md has site: amazon metadata
# ---------------------------------------------------------------------------

class TestAmazonSearchSiteMetadata:
    def test_amazon_search_has_site(self):
        from automation_agent.skills.loader import load_skill_from_file

        skill_path = SKILL_LIBRARY_DIR / "amazon_search.md"
        skill = load_skill_from_file(skill_path)
        assert skill.metadata.get("site") == "amazon"
