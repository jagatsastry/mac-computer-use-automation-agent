"""Unit tests for the top-k skill router (Slice 2)."""

import json
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.shared_models import (
    MatchType,
    SkillMatchResult,
    SkillRouteCandidate,
    SkillRouteResult,
)
from automation_agent.skills.matcher import match_skill
from automation_agent.skills.models import Skill, SkillCard, SkillParam, SkillRequirements
from automation_agent.skills.registry import SkillRegistryImpl
from automation_agent.skills.router import MIN_USEFUL_CONFIDENCE, SkillRouter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_skill(name, keywords=None, params=None, description="A skill"):
    return Skill(
        name=name,
        description=description,
        trigger_keywords=keywords or [name],
        parameters=params or {},
        requires=SkillRequirements(os="darwin"),
        success_condition="Done",
        steps_text=f"1. Do {name}\n   - verify: {name} done",
    )


def _make_skills_dict(*names):
    return {n: _make_skill(n) for n in names}


def _make_router(skills_dict, cards=None):
    config = MagicMock()
    config.model_provider.value = "local"
    return SkillRouter(config, skills_dict, cards=cards)


def _make_registry_with_skills(tmp_path, *skill_contents):
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir(exist_ok=True)
    for i, content in enumerate(skill_contents):
        (skill_dir / f"skill_{i}.md").write_text(content)
    return SkillRegistryImpl(skill_dir=skill_dir)


SIMPLE_SKILL_CONTENT = textwrap.dedent("""\
    ---
    name: open-safari
    description: Open Safari browser
    trigger-keywords: [open, safari, browser, launch]
    parameters: {}
    requires:
      os: darwin
    success-condition: Safari is open
    ---

    ## Steps
    1. Open Safari
       - verify: Safari is frontmost app
""")


# ---------------------------------------------------------------------------
# Test 1: route() returns top-k candidates
# ---------------------------------------------------------------------------

class TestRouteTopK:
    @pytest.mark.asyncio
    async def test_route_returns_top_k_candidates(self):
        skills = _make_skills_dict("skill-a", "skill-b", "skill-c")
        router = _make_router(skills)

        with patch.object(router, "_call_local", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = json.dumps({
                "matches": [
                    {
                        "skill_id": "skill-a",
                        "match_type": "direct",
                        "confidence": 0.95,
                        "reason": "Exact match",
                        "params": {"key": "val"},
                    },
                    {
                        "skill_id": "skill-b",
                        "match_type": "analogical",
                        "confidence": 0.7,
                        "reason": "Similar structure",
                    },
                    {
                        "skill_id": "skill-c",
                        "match_type": "generic",
                        "confidence": 0.4,
                        "reason": "General utility",
                    },
                ]
            })
            result = await router.route("do something")

        assert result is not None
        assert len(result.candidates) == 3
        assert result.candidates[0].skill_id == "skill-a"
        assert result.candidates[0].match_type == MatchType.DIRECT
        assert result.candidates[0].confidence == 0.95
        assert result.candidates[1].match_type == MatchType.ANALOGICAL
        assert result.candidates[2].match_type == MatchType.GENERIC

    @pytest.mark.asyncio
    async def test_route_single_direct_match(self):
        skills = _make_skills_dict("amazon-return")
        router = _make_router(skills)

        with patch.object(router, "_call_local", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = json.dumps({
                "matches": [{
                    "skill_id": "amazon-return",
                    "match_type": "direct",
                    "confidence": 0.98,
                    "reason": "Exact match for Amazon return",
                }]
            })
            result = await router.route("return my Amazon order")

        assert result is not None
        assert len(result.candidates) == 1
        assert result.primary.skill_id == "amazon-return"
        assert result.has_direct_match

    @pytest.mark.asyncio
    async def test_route_analogical_match(self):
        skills = _make_skills_dict("amazon-return")
        router = _make_router(skills)

        with patch.object(router, "_call_local", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = json.dumps({
                "matches": [{
                    "skill_id": "amazon-return",
                    "match_type": "analogical",
                    "confidence": 0.65,
                    "reason": "Similar return flow for Walmart",
                }]
            })
            result = await router.route("return my Walmart order")

        assert result is not None
        assert result.candidates[0].match_type == MatchType.ANALOGICAL
        assert not result.has_direct_match

    @pytest.mark.asyncio
    async def test_route_no_match_empty_list(self):
        skills = _make_skills_dict("skill-a")
        router = _make_router(skills)

        with patch.object(router, "_call_local", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = '{"matches": []}'
            result = await router.route("unrelated prompt")

        assert result is None


# ---------------------------------------------------------------------------
# Test 2: parse response edge cases
# ---------------------------------------------------------------------------

class TestRouteParseEdgeCases:
    def test_route_parse_malformed_response(self):
        skills = _make_skills_dict("skill-a")
        router = _make_router(skills)

        result = router._parse_response("this is not json at all")
        assert result is None

    def test_route_parse_missing_fields(self):
        skills = _make_skills_dict("skill-a")
        router = _make_router(skills)

        result = router._parse_response(json.dumps({
            "matches": [{"skill_id": "skill-a"}]
        }))
        assert result is not None
        assert result.candidates[0].confidence == 0.5  # default
        assert result.candidates[0].match_type == MatchType.GENERIC  # default

    @pytest.mark.asyncio
    async def test_route_llm_failure_returns_none(self):
        skills = _make_skills_dict("skill-a")
        router = _make_router(skills)

        with patch.object(router, "_call_local", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = Exception("Connection refused")
            result = await router.route("anything")

        assert result is None

    def test_route_strips_markdown_fences(self):
        skills = _make_skills_dict("skill-a")
        router = _make_router(skills)

        result = router._parse_response(
            '```json\n{"matches": [{"skill_id": "skill-a", '
            '"match_type": "direct", "confidence": 0.9, '
            '"reason": "test"}]}\n```'
        )
        assert result is not None
        assert result.candidates[0].skill_id == "skill-a"


# ---------------------------------------------------------------------------
# Test 3: registry match() returns new shape
# ---------------------------------------------------------------------------

class TestRegistryMatchNewShape:
    @pytest.mark.asyncio
    async def test_match_returns_new_dict_shape(self, tmp_path):
        registry = _make_registry_with_skills(tmp_path, SIMPLE_SKILL_CONTENT)
        route_result = SkillRouteResult(candidates=[
            SkillRouteCandidate(
                skill_id="open-safari",
                match_type=MatchType.DIRECT,
                confidence=0.95,
                reason="Exact match",
            ),
        ])
        mock_router = AsyncMock()
        mock_router.route.return_value = route_result
        registry._router = mock_router

        result = await registry.match("open safari browser")
        assert result is not None
        assert isinstance(result, SkillMatchResult)
        assert result.skill_name == "open-safari"
        assert result["skill_name"] == "open-safari"  # dict-compat
        assert result.candidates[0].match_type == MatchType.DIRECT

    @pytest.mark.asyncio
    async def test_match_includes_skill_context(self, tmp_path):
        registry = _make_registry_with_skills(tmp_path, SIMPLE_SKILL_CONTENT)
        route_result = SkillRouteResult(candidates=[
            SkillRouteCandidate(
                skill_id="open-safari",
                match_type=MatchType.DIRECT,
                confidence=0.95,
                reason="Exact match",
            ),
        ])
        mock_router = AsyncMock()
        mock_router.route.return_value = route_result
        registry._router = mock_router

        result = await registry.match("open safari browser")
        assert result is not None
        assert result.skill_context  # non-empty
        assert "## Skill Priors" in result.skill_context
        assert "[direct]" in result.skill_context

    @pytest.mark.asyncio
    async def test_match_filters_low_confidence(self, tmp_path):
        registry = _make_registry_with_skills(tmp_path, SIMPLE_SKILL_CONTENT)
        route_result = SkillRouteResult(candidates=[
            SkillRouteCandidate(
                skill_id="open-safari",
                match_type=MatchType.GENERIC,
                confidence=0.3,
                reason="Weak match",
            ),
        ])
        mock_router = AsyncMock()
        mock_router.route.return_value = route_result
        registry._router = mock_router

        result = await registry.match("something unrelated")
        assert result is None  # All below MIN_USEFUL_CONFIDENCE

    @pytest.mark.asyncio
    async def test_match_none_when_no_skills(self, tmp_path):
        skill_dir = tmp_path / "empty_skills"
        skill_dir.mkdir()
        registry = SkillRegistryImpl(skill_dir=skill_dir)

        result = await registry.match("anything")
        assert result is None


# ---------------------------------------------------------------------------
# Test 4: keyword fallback new shape
# ---------------------------------------------------------------------------

class TestKeywordFallbackNewShape:
    @pytest.mark.asyncio
    async def test_match_keyword_fallback_new_shape(self, tmp_path):
        """Keyword fallback returns SkillMatchResult shape."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        content = textwrap.dedent("""\
            ---
            name: simple-task
            description: A simple no-param task
            trigger-keywords: [simple, task, easy, quick]
            parameters: {}
            requires:
              os: darwin
            success-condition: Done
            ---

            ## Steps
            1. Do the simple task
               - verify: Task done
        """)
        (skill_dir / "simple.md").write_text(content)
        registry = SkillRegistryImpl(skill_dir=skill_dir)
        assert registry._router is None  # No config = no router

        # "simple task" matches 2 of 4 keywords -> confidence=0.5
        result = await registry.match("do the simple task please")
        assert result is not None
        assert isinstance(result, SkillMatchResult)
        assert result.skill_name == "simple-task"
        assert len(result.candidates) == 1
        assert result.candidates[0].match_type == MatchType.DIRECT

    @pytest.mark.asyncio
    async def test_match_keyword_fallback_proportional_confidence(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        content = textwrap.dedent("""\
            ---
            name: multi-kw
            description: Multi keyword skill
            trigger-keywords: [alpha, beta, gamma, delta]
            parameters: {}
            requires:
              os: darwin
            success-condition: Done
            ---

            ## Steps
            1. Do it
               - verify: done
        """)
        (skill_dir / "multi.md").write_text(content)
        registry = SkillRegistryImpl(skill_dir=skill_dir)

        # "alpha beta" matches 2/4 -> confidence=0.5
        result = await registry.match("alpha beta something")
        assert result is not None
        assert result.candidates[0].confidence == 0.5

    @pytest.mark.asyncio
    async def test_match_keyword_fallback_weak_match_rejected(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        content = textwrap.dedent("""\
            ---
            name: four-kw
            description: Four keyword skill
            trigger-keywords: [alpha, beta, gamma, delta]
            parameters: {}
            requires:
              os: darwin
            success-condition: Done
            ---

            ## Steps
            1. Do it
               - verify: done
        """)
        (skill_dir / "four.md").write_text(content)
        registry = SkillRegistryImpl(skill_dir=skill_dir)

        # "alpha something" matches 1/4 -> confidence=0.25, below 0.5
        result = await registry.match("alpha something else")
        assert result is None

    @pytest.mark.asyncio
    async def test_match_keyword_fallback_strong_match_accepted(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        content = textwrap.dedent("""\
            ---
            name: four-kw
            description: Four keyword skill
            trigger-keywords: [alpha, beta, gamma, delta]
            parameters: {}
            requires:
              os: darwin
            success-condition: Done
            ---

            ## Steps
            1. Do it
               - verify: done
        """)
        (skill_dir / "four.md").write_text(content)
        registry = SkillRegistryImpl(skill_dir=skill_dir)

        # "alpha beta gamma" matches 3/4 -> confidence=0.75
        result = await registry.match("alpha beta gamma please")
        assert result is not None
        assert result.candidates[0].confidence == 0.75


# ---------------------------------------------------------------------------
# Test 5: multi-skill context
# ---------------------------------------------------------------------------

class TestMultiSkillContext:
    @pytest.mark.asyncio
    async def test_multi_skill_context_includes_labels(self, tmp_path):
        registry = _make_registry_with_skills(tmp_path, SIMPLE_SKILL_CONTENT)
        route_result = SkillRouteResult(candidates=[
            SkillRouteCandidate(
                skill_id="open-safari",
                match_type=MatchType.DIRECT,
                confidence=0.95,
                reason="Exact match",
            ),
        ])
        mock_router = AsyncMock()
        mock_router.route.return_value = route_result
        registry._router = mock_router

        result = await registry.match("open safari")
        assert result is not None
        assert "[direct]" in result.skill_context
        assert "open-safari" in result.skill_context
        assert "confidence: 0.95" in result.skill_context

    @pytest.mark.asyncio
    async def test_multi_skill_context_includes_observations(self, tmp_path):
        """Learned observations appear in multi-skill context per skill."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "safari.md").write_text(SIMPLE_SKILL_CONTENT)

        from automation_agent.config import AgentConfig
        config = AgentConfig(
            _env_file=None,
            anthropic_api_key="test-key-not-real",
            skill_learning_dir=tmp_path / "learning",
        )
        registry = SkillRegistryImpl(skill_dir=skill_dir, config=config)

        from automation_agent.skills.models import SkillObservation
        registry._experience_store.append(
            "open-safari",
            [
                SkillObservation(
                    category="alternative_path",
                    condition="Safari not in Dock",
                    recommendation="Use Spotlight to launch Safari",
                    confidence=0.8,
                )
            ],
        )

        route_result = SkillRouteResult(candidates=[
            SkillRouteCandidate(
                skill_id="open-safari",
                match_type=MatchType.DIRECT,
                confidence=0.95,
                reason="Exact match",
            ),
        ])
        registry._router = AsyncMock()
        registry._router.route.return_value = route_result

        result = await registry.match("open safari")
        assert result is not None
        assert "## Observed Variants" in result.skill_context
        assert "Use Spotlight to launch Safari" in result.skill_context


# ---------------------------------------------------------------------------
# Test 6: matcher returns hit count
# ---------------------------------------------------------------------------

class TestMatcherHitCount:
    def test_match_skill_returns_hit_count(self):
        skill = _make_skill("test", keywords=["alpha", "beta", "gamma"])
        result = match_skill("alpha gamma test", [skill])
        assert result is not None
        _, _, hit_count = result
        assert hit_count == 2

    def test_match_skill_no_match_returns_none(self):
        skill = _make_skill("test", keywords=["alpha", "beta"])
        result = match_skill("completely unrelated", [skill])
        assert result is None

    def test_match_skill_empty_list(self):
        result = match_skill("anything", [])
        assert result is None


# ---------------------------------------------------------------------------
# Test 7: SkillMatchResult dict-compat shims
# ---------------------------------------------------------------------------

class TestSkillMatchResultCompat:
    def test_getitem_compat(self):
        result = SkillMatchResult(
            skill_name="test",
            expanded_steps="steps",
            skill_context="context",
            params={"a": "b"},
            candidates=[],
        )
        assert result["skill_name"] == "test"
        assert result["params"] == {"a": "b"}

    def test_get_compat(self):
        result = SkillMatchResult(
            skill_name="test",
            expanded_steps="steps",
            skill_context="context",
            params={},
            candidates=[],
        )
        assert result.get("skill_name") == "test"
        assert result.get("nonexistent", "default") == "default"


# ---------------------------------------------------------------------------
# Test 8: 25-skill warning log (spec requirement, item 1)
# ---------------------------------------------------------------------------

class TestSkillCountWarning:
    @staticmethod
    def _load_n_skills(tmp_path, n):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir(exist_ok=True)
        for i in range(n):
            content = textwrap.dedent(f"""\
                ---
                name: skill-{i:03d}
                description: Skill number {i}
                trigger-keywords: [kw{i}]
                parameters: {{}}
                requires:
                  os: darwin
                success-condition: Done
                ---

                ## Steps
                1. Do thing {i}
                   - verify: done
            """)
            (skill_dir / f"skill_{i:03d}.md").write_text(content)
        return skill_dir

    def test_25_skill_warning_emitted(self, tmp_path, capsys):
        """Loading 25+ skills emits a warning about router prompt budget."""
        skill_dir = self._load_n_skills(tmp_path, 26)
        registry = SkillRegistryImpl(skill_dir=skill_dir)

        assert len(registry._cards) >= 25
        captured = capsys.readouterr()
        assert "approaching router prompt budget" in captured.out

    def test_24_skills_no_warning(self, tmp_path, capsys):
        """Loading 24 skills does NOT emit the warning."""
        skill_dir = self._load_n_skills(tmp_path, 24)
        registry = SkillRegistryImpl(skill_dir=skill_dir)

        assert len(registry._cards) == 24
        captured = capsys.readouterr()
        assert "approaching router prompt budget" not in captured.out


# ---------------------------------------------------------------------------
# Test 10: Multi-candidate context with all label types (item 3)
# ---------------------------------------------------------------------------

SECOND_SKILL_CONTENT = textwrap.dedent("""\
    ---
    name: open-chrome
    description: Open Chrome browser
    trigger-keywords: [open, chrome, browser]
    parameters: {}
    requires:
      os: darwin
    success-condition: Chrome is open
    ---

    ## Steps
    1. Open Chrome
       - verify: Chrome is frontmost app
""")

THIRD_SKILL_CONTENT = textwrap.dedent("""\
    ---
    name: open-app-generic
    description: Open any application
    trigger-keywords: [open, app, launch]
    parameters: {}
    requires:
      os: darwin
    success-condition: App is open
    ---

    ## Steps
    1. Open the app
       - verify: App is frontmost
""")


class TestMultiCandidateLabels:
    @pytest.mark.asyncio
    async def test_multi_candidate_context_shows_all_labels(self, tmp_path):
        """Multiple candidates with different match types show all labels."""
        registry = _make_registry_with_skills(
            tmp_path, SIMPLE_SKILL_CONTENT, SECOND_SKILL_CONTENT, THIRD_SKILL_CONTENT
        )
        route_result = SkillRouteResult(candidates=[
            SkillRouteCandidate(
                skill_id="open-safari",
                match_type=MatchType.DIRECT,
                confidence=0.95,
                reason="Exact match for Safari",
            ),
            SkillRouteCandidate(
                skill_id="open-chrome",
                match_type=MatchType.ANALOGICAL,
                confidence=0.7,
                reason="Similar browser open flow",
            ),
            SkillRouteCandidate(
                skill_id="open-app-generic",
                match_type=MatchType.GENERIC,
                confidence=0.5,
                reason="General app opening utility",
            ),
        ])
        mock_router = AsyncMock()
        mock_router.route.return_value = route_result
        registry._router = mock_router

        result = await registry.match("open safari")
        assert result is not None
        ctx = result.skill_context
        assert "[direct]" in ctx
        assert "[analogical]" in ctx
        assert "[generic]" in ctx
        assert "open-safari" in ctx
        assert "open-chrome" in ctx
        assert "open-app-generic" in ctx
        # Verify separators appear between skill blocks (structural check)
        assert ctx.count("---") >= 3
        # Verify ordering: each skill block appears before its separator
        safari_pos = ctx.index("[direct]")
        first_sep = ctx.index("---", safari_pos)
        chrome_pos = ctx.index("[analogical]")
        assert safari_pos < first_sep < chrome_pos, (
            "Separator should appear between [direct] and [analogical] blocks"
        )
        second_sep = ctx.index("---", chrome_pos)
        generic_pos = ctx.index("[generic]")
        assert chrome_pos < second_sep < generic_pos, (
            "Separator should appear between [analogical] and [generic] blocks"
        )


# ---------------------------------------------------------------------------
# Test 11: Invalid match_type handled gracefully (item 5)
# ---------------------------------------------------------------------------

class TestInvalidMatchType:
    def test_invalid_match_type_skipped_gracefully(self):
        """LLM returning invalid match_type should skip that candidate, not crash."""
        skills = _make_skills_dict("skill-a", "skill-b")
        router = _make_router(skills)

        result = router._parse_response(json.dumps({
            "matches": [
                {
                    "skill_id": "skill-a",
                    "match_type": "partial",  # invalid MatchType
                    "confidence": 0.9,
                    "reason": "test",
                },
                {
                    "skill_id": "skill-b",
                    "match_type": "direct",  # valid
                    "confidence": 0.8,
                    "reason": "test",
                },
            ]
        }))
        # skill-a should be skipped, skill-b should be kept
        assert result is not None
        assert len(result.candidates) == 1
        assert result.candidates[0].skill_id == "skill-b"

    def test_all_invalid_match_types_returns_none(self):
        """If all candidates have invalid match_type, return None."""
        skills = _make_skills_dict("skill-a")
        router = _make_router(skills)

        result = router._parse_response(json.dumps({
            "matches": [
                {
                    "skill_id": "skill-a",
                    "match_type": "close",  # invalid
                    "confidence": 0.9,
                    "reason": "test",
                },
            ]
        }))
        assert result is None
