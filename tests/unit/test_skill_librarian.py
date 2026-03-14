"""Unit tests for SkillLibrarian — core evaluation, LLM calls, validation, file ops."""

from __future__ import annotations

import json
import os
import textwrap
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.config import AgentConfig
from automation_agent.skills.experience import SkillExperienceStore
from automation_agent.skills.models import (
    PromotionDecision,
    Skill,
    SkillObservation,
    SkillRequirements,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_SKILL_MD = textwrap.dedent("""\
---
name: return-amazon-order
skill-id: return-amazon-order
description: Return an item on Amazon
summary: Automates the Amazon return flow
tags: [ecommerce, return]
trigger-keywords: [return, amazon, refund]
parameters:
  item:
    type: string
    required: true
    description: What to return
requires:
  apps: [Safari]
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
""")

SAMPLE_SKILL_WITH_TIPS_MD = textwrap.dedent("""\
---
name: return-amazon-order
skill-id: return-amazon-order
description: Return an item on Amazon
summary: Automates the Amazon return flow
tags: [ecommerce, return]
trigger-keywords: [return, amazon, refund]
parameters:
  item:
    type: string
    required: true
    description: What to return
requires:
  apps: [Safari]
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

## Learned Tips
- When the return button is hidden, click "View order details" first

## Notes
- Treat labels as likely affordances, not guarantees
""")


def _make_config(tmp_path: Path, **overrides) -> AgentConfig:
    defaults = dict(
        _env_file=None,
        anthropic_api_key="test-key-not-real",
        model_provider="local",
        skill_learning_dir=tmp_path / "learning",
        skill_librarian_enabled=True,
        grounding_model="",
        grounding_server_url="",
    )
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_skill(**overrides) -> Skill:
    defaults = dict(
        name="return-amazon-order",
        description="Return an item on Amazon",
        trigger_keywords=["return", "amazon", "refund"],
        parameters={},
        requires=SkillRequirements(apps=["Safari"], os="darwin"),
        success_condition="Return confirmation visible",
        steps_text="1. Open orders\n   - verify: Orders visible",
        error_recovery_text="- If return absent: view details first",
        notes_text="- Treat labels as affordances",
        raw_content=SAMPLE_SKILL_MD,
        skill_id="return-amazon-order",
        tags=["ecommerce", "return"],
        summary="Automates Amazon return flow",
    )
    defaults.update(overrides)
    return Skill(**defaults)


def _make_observations(
    n: int = 5,
    category: str = "alternative_path",
    recommendation: str = "click View item first",
    confidence: float = 0.8,
    run_ids: list[str] | None = None,
) -> list[SkillObservation]:
    if run_ids is None:
        run_ids = [f"run-{i}" for i in range(n)]
    obs = []
    for i in range(n):
        obs.append(
            SkillObservation(
                category=category,
                condition="When return button is absent",
                recommendation=recommendation,
                rationale="Trace showed View item was visible",
                confidence=confidence,
                run_id=run_ids[i % len(run_ids)],
            )
        )
    return obs


@pytest.fixture
def tmp_config(tmp_path):
    return _make_config(tmp_path)


@pytest.fixture
def experience_store(tmp_path):
    store = SkillExperienceStore(tmp_path / "learning")
    return store


@pytest.fixture
def mock_registry(tmp_path):
    registry = MagicMock()
    skill = _make_skill()
    registry.get_skill.return_value = skill
    registry._skills = {"return-amazon-order": skill}
    registry.load_from_string.return_value = skill
    # Provide a real Path so librarian._skill_dir resolves correctly
    skill_dir = tmp_path / "skill_library"
    skill_dir.mkdir()
    registry._skill_dir = skill_dir
    return registry


@pytest.fixture
def librarian(tmp_config, experience_store, mock_registry):
    from automation_agent.skills.librarian import SkillLibrarian

    return SkillLibrarian(
        config=tmp_config,
        experience_store=experience_store,
        registry=mock_registry,
    )


# ===========================================================================
# 1. Data Model Tests
# ===========================================================================


class TestDataModels:
    def test_skill_observation_promoted_default(self):
        obs = SkillObservation(
            category="checkpoint",
            condition="Page loaded",
            recommendation="Check title",
        )
        assert obs.promoted is False

    def test_skill_observation_promoted_explicit(self):
        obs = SkillObservation(
            category="checkpoint",
            condition="Page loaded",
            recommendation="Check title",
            promoted=True,
        )
        assert obs.promoted is True

    def test_skill_parent_skill_id_default(self):
        skill = _make_skill()
        assert skill.parent_skill_id == ""

    def test_skill_learned_tips_text_default(self):
        skill = _make_skill()
        assert skill.learned_tips_text == ""

    def test_promotion_decision_fields(self):
        decision = PromotionDecision(
            skill_name="return-amazon-order",
            promotion_type="patch_parent",
            reason="Strong evidence",
            confidence_score=0.85,
            observation_keys=[["alternative_path", "click view item first"]],
            run_id="run-1",
            timestamp="2026-03-12T00:00:00",
            generated_tips="- Tip one",
            observation_count=5,
            distinct_run_count=3,
        )
        assert decision.promotion_type == "patch_parent"
        assert decision.new_skill_id == ""
        assert decision.parent_skill_id == ""

    def test_promotion_decision_serializes_to_json(self):
        decision = PromotionDecision(
            skill_name="test",
            promotion_type="observation_only",
            reason="Not enough evidence",
            confidence_score=0.5,
            observation_keys=[],
            run_id="run-1",
            timestamp="2026-03-12T00:00:00",
        )
        data = asdict(decision)
        text = json.dumps(data)
        restored = json.loads(text)
        assert restored["promotion_type"] == "observation_only"


# ===========================================================================
# 2. Config Tests
# ===========================================================================


class TestConfig:
    def test_librarian_defaults(self, monkeypatch):
        # Ensure .env values don't leak into defaults test
        monkeypatch.delenv("AGENT_SKILL_LIBRARIAN_ENABLED", raising=False)
        monkeypatch.delenv("AGENT_SKILL_LIBRARIAN_MIN_CONFIDENCE", raising=False)
        monkeypatch.delenv("AGENT_SKILL_LIBRARIAN_MIN_OBSERVATIONS", raising=False)
        monkeypatch.delenv("AGENT_SKILL_LIBRARIAN_MIN_RUNS", raising=False)
        config = AgentConfig(
            _env_file=None,
            anthropic_api_key="test-key-not-real",
            model_provider="local",
        )
        assert config.skill_librarian_enabled is True
        assert config.skill_librarian_min_confidence == 0.5
        assert config.skill_librarian_min_observations == 2
        assert config.skill_librarian_min_runs == 1
        assert config.skill_librarian_max_tips == 10

    def test_librarian_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_SKILL_LIBRARIAN_ENABLED", "true")
        monkeypatch.setenv("AGENT_SKILL_LIBRARIAN_MIN_CONFIDENCE", "0.8")
        config = AgentConfig(
            _env_file=None,
            anthropic_api_key="test-key-not-real",
            model_provider="local",
        )
        assert config.skill_librarian_enabled is True
        assert config.skill_librarian_min_confidence == 0.8


# ===========================================================================
# 3. Experience Store Tests
# ===========================================================================


class TestExperienceStore:
    def test_mark_promoted_round_trip(self, experience_store):
        obs = _make_observations(3, run_ids=["r1", "r2", "r3"])
        experience_store.append("test-skill", obs)

        keys = {("alternative_path", "click view item first")}
        marked = experience_store.mark_promoted("test-skill", keys)
        assert marked == 3

        reloaded = experience_store.load("test-skill")
        for item in reloaded:
            assert item.promoted is True

    def test_top_for_context_excludes_promoted(self, experience_store):
        obs = _make_observations(3, confidence=0.9, run_ids=["r1", "r2", "r3"])
        experience_store.append("test-skill", obs)

        # Before marking: should be returned
        top = experience_store.top_for_context("test-skill", limit=5)
        assert len(top) == 1  # deduplicated to 1 unique key

        # Mark promoted
        keys = {("alternative_path", "click view item first")}
        experience_store.mark_promoted("test-skill", keys)

        # After marking: should be excluded
        top = experience_store.top_for_context("test-skill", limit=5)
        assert len(top) == 0

    def test_mark_promoted_backward_compat(self, experience_store):
        """Old JSONL lines without 'promoted' field still work."""
        path = experience_store._path_for("test-skill")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write a line without 'promoted' field
        line = json.dumps({
            "category": "checkpoint",
            "condition": "Page loaded",
            "recommendation": "Check title",
            "rationale": "",
            "confidence": 0.9,
            "run_id": "r1",
            "created_at": datetime.utcnow().isoformat(),
        })
        path.write_text(line + "\n")

        loaded = experience_store.load("test-skill")
        assert len(loaded) == 1
        assert loaded[0].promoted is False

        top = experience_store.top_for_context("test-skill", limit=5)
        assert len(top) == 1

    def test_mark_promoted_no_matching_keys(self, experience_store):
        obs = _make_observations(2, run_ids=["r1", "r2"])
        experience_store.append("test-skill", obs)

        keys = {("nonexistent", "nothing")}
        marked = experience_store.mark_promoted("test-skill", keys)
        assert marked == 0

    def test_mark_promoted_missing_file(self, experience_store):
        keys = {("x", "y")}
        marked = experience_store.mark_promoted("nonexistent-skill", keys)
        assert marked == 0


# ===========================================================================
# 4. Score Computation Tests
# ===========================================================================


class TestScoreComputation:
    def test_all_corroborating(self, librarian):
        obs = _make_observations(5, confidence=0.9)
        score = librarian._compute_score(obs)
        # alpha=5, beta=0 -> (5+1)/(5+0+2) = 6/7 ≈ 0.857
        assert abs(score - 6 / 7) < 1e-6

    def test_all_contradicting(self, librarian):
        obs = _make_observations(5, confidence=0.2)
        score = librarian._compute_score(obs)
        # alpha=0, beta=5 -> (0+1)/(0+5+2) = 1/7 ≈ 0.143
        assert abs(score - 1 / 7) < 1e-6

    def test_mixed(self, librarian):
        obs = [
            *_make_observations(3, confidence=0.8, run_ids=["r1", "r2", "r3"]),
            *_make_observations(2, confidence=0.2, run_ids=["r4", "r5"]),
        ]
        score = librarian._compute_score(obs)
        # alpha=3, beta=2 -> (3+1)/(3+2+2) = 4/7 ≈ 0.571
        assert abs(score - 4 / 7) < 1e-6

    def test_all_neutral(self, librarian):
        obs = _make_observations(5, confidence=0.5)
        score = librarian._compute_score(obs)
        # alpha=0, beta=0 -> 1/2 = 0.5
        assert score == 0.5

    def test_empty_group(self, librarian):
        score = librarian._compute_score([])
        # alpha=0, beta=0 -> 1/2 = 0.5
        assert score == 0.5


# ===========================================================================
# 5. Grouping Tests
# ===========================================================================


class TestGrouping:
    def test_multi_group(self, librarian):
        obs = [
            SkillObservation(
                category="alternative_path",
                condition="c",
                recommendation="click View item",
                confidence=0.8,
                run_id="r1",
            ),
            SkillObservation(
                category="checkpoint",
                condition="c",
                recommendation="verify order title",
                confidence=0.7,
                run_id="r2",
            ),
            SkillObservation(
                category="alternative_path",
                condition="c",
                recommendation="click View item",
                confidence=0.9,
                run_id="r3",
            ),
        ]
        groups = librarian._group_observations(obs)
        assert len(groups) == 2

    def test_case_insensitive_dedup(self, librarian):
        obs = [
            SkillObservation(
                category="Alternative_Path",
                condition="c",
                recommendation="Click VIEW item",
                confidence=0.8,
                run_id="r1",
            ),
            SkillObservation(
                category="alternative_path",
                condition="c",
                recommendation="click view item",
                confidence=0.9,
                run_id="r2",
            ),
        ]
        groups = librarian._group_observations(obs)
        assert len(groups) == 1

    def test_punctuation_normalization(self, librarian):
        obs = [
            SkillObservation(
                category="checkpoint",
                condition="c",
                recommendation="Click the 'confirm' button.",
                confidence=0.8,
                run_id="r1",
            ),
            SkillObservation(
                category="checkpoint",
                condition="c",
                recommendation="Click the confirm button",
                confidence=0.9,
                run_id="r2",
            ),
        ]
        groups = librarian._group_observations(obs)
        assert len(groups) == 1

    def test_empty_observations(self, librarian):
        groups = librarian._group_observations([])
        assert groups == {}


# ===========================================================================
# 6. History Dedup Tests
# ===========================================================================


class TestHistoryDedup:
    def test_skip_already_promoted(self, librarian, tmp_config):
        history_dir = tmp_config.skill_learning_dir / "promotions"
        history_dir.mkdir(parents=True)
        entry = {
            "skill_name": "return-amazon-order",
            "observation_keys": [["alternative_path", "click view item first"]],
        }
        (history_dir / "history.jsonl").write_text(
            json.dumps(entry) + "\n", encoding="utf-8"
        )

        promoted = librarian._load_promotion_history("return-amazon-order")
        assert ("alternative_path", "click view item first") in promoted

    def test_missing_history_file(self, librarian):
        promoted = librarian._load_promotion_history("return-amazon-order")
        assert promoted == set()

    def test_corrupt_jsonl_line(self, librarian, tmp_config):
        history_dir = tmp_config.skill_learning_dir / "promotions"
        history_dir.mkdir(parents=True)
        content = "not valid json\n" + json.dumps({
            "skill_name": "return-amazon-order",
            "observation_keys": [["checkpoint", "verify title"]],
        }) + "\n"
        (history_dir / "history.jsonl").write_text(content, encoding="utf-8")

        promoted = librarian._load_promotion_history("return-amazon-order")
        assert ("checkpoint", "verify title") in promoted


# ===========================================================================
# 7. Threshold Filtering Tests
# ===========================================================================


class TestThresholdFiltering:
    def test_below_min_observations(self, librarian, tmp_config):
        """Group with fewer than min_observations should not qualify."""
        tmp_config.skill_librarian_min_observations = 5
        obs = _make_observations(3, confidence=0.9, run_ids=["r1", "r2", "r3"])
        groups = librarian._group_observations(obs)
        key = list(groups.keys())[0]
        group = groups[key]
        assert len(group) < tmp_config.skill_librarian_min_observations

    def test_below_min_runs(self, librarian, tmp_config):
        """Group with fewer than min_runs distinct run_ids should not qualify."""
        tmp_config.skill_librarian_min_runs = 3
        obs = _make_observations(5, confidence=0.9, run_ids=["r1", "r2"])
        groups = librarian._group_observations(obs)
        key = list(groups.keys())[0]
        group = groups[key]
        distinct = len({o.run_id for o in group if o.run_id})
        assert distinct < tmp_config.skill_librarian_min_runs

    def test_below_min_confidence(self, librarian, tmp_config):
        """Group with score below min_confidence should not qualify."""
        tmp_config.skill_librarian_min_confidence = 0.7
        obs = _make_observations(5, confidence=0.5, run_ids=["r1", "r2", "r3", "r4", "r5"])
        score = librarian._compute_score(obs)
        assert score < tmp_config.skill_librarian_min_confidence

    def test_exactly_at_threshold(self, librarian, tmp_config):
        """Group exactly at thresholds should qualify."""
        tmp_config.skill_librarian_min_observations = 5
        tmp_config.skill_librarian_min_runs = 3
        obs = _make_observations(
            5, confidence=0.9, run_ids=["r1", "r2", "r3", "r4", "r5"]
        )
        groups = librarian._group_observations(obs)
        key = list(groups.keys())[0]
        group = groups[key]
        assert len(group) >= tmp_config.skill_librarian_min_observations
        distinct = len({o.run_id for o in group if o.run_id})
        assert distinct >= tmp_config.skill_librarian_min_runs
        score = librarian._compute_score(group)
        assert score >= tmp_config.skill_librarian_min_confidence


# ===========================================================================
# 8. LLM Parse Tests
# ===========================================================================


class TestLLMParse:
    def test_valid_json(self, librarian):
        result = librarian._parse_response(
            '{"promotion_type": "patch_parent", "reason": "Strong evidence"}'
        )
        assert result is not None
        assert result["promotion_type"] == "patch_parent"

    def test_fenced_json(self, librarian):
        result = librarian._parse_response(
            '```json\n{"promotion_type": "observation_only", "reason": "Not enough"}\n```'
        )
        assert result is not None
        assert result["promotion_type"] == "observation_only"

    def test_missing_fields(self, librarian):
        result = librarian._parse_response('{"promotion_type": "patch_parent"}')
        assert result is None  # missing "reason"

    def test_invalid_promotion_type(self, librarian):
        result = librarian._parse_response(
            '{"promotion_type": "invalid_type", "reason": "test"}'
        )
        assert result is None

    def test_non_json_response(self, librarian):
        result = librarian._parse_response("This is not JSON at all")
        assert result is None


# ===========================================================================
# 9. Tips Validation Tests
# ===========================================================================


class TestTipsValidation:
    def test_valid_tips(self, librarian):
        assert librarian._validate_tips("- Tip one\n- Tip two") is True

    def test_empty_tips(self, librarian):
        assert librarian._validate_tips("") is False
        assert librarian._validate_tips("   ") is False

    def test_tips_with_frontmatter_delimiter(self, librarian):
        assert librarian._validate_tips("---\nname: bad\n---") is False

    def test_tips_with_steps_heading(self, librarian):
        assert librarian._validate_tips("## Steps\n1. Do thing") is False

    def test_tips_with_error_recovery_heading(self, librarian):
        assert librarian._validate_tips("## Error Recovery\n- If X: Y") is False


# ===========================================================================
# 10. Sibling Validation Tests
# ===========================================================================


class TestSiblingValidation:
    def test_valid_sibling_md(self, librarian):
        md = textwrap.dedent("""\
        ---
        name: return-walmart-order
        skill-id: return-walmart-order
        description: Return an item on Walmart
        summary: Automates the Walmart return flow
        tags: [ecommerce, return]
        trigger-keywords: [return, walmart]
        parameters: {}
        requires:
          apps: [Safari]
          os: darwin
        success-condition: Return confirmation visible
        max-retries: 3
        parent-skill-id: return-amazon-order
        ---

        ## Steps
        1. Open orders page
           - verify: Orders page visible

        ## Error Recovery
        - If return button absent: check order details

        ## Notes
        - Walmart-specific notes
        """)
        assert librarian._validate_sibling_md(md) is True

    def test_sibling_missing_frontmatter(self, librarian):
        md = "# No frontmatter\nJust text"
        assert librarian._validate_sibling_md(md) is False

    def test_sibling_missing_steps(self, librarian):
        md = textwrap.dedent("""\
        ---
        name: bad-skill
        description: Missing steps
        trigger-keywords: [test]
        requires:
          os: darwin
        success-condition: Done
        parent-skill-id: return-amazon-order
        ---

        ## Notes
        - No steps section
        """)
        assert librarian._validate_sibling_md(md) is False

    def test_sibling_missing_parent_skill_id(self, librarian):
        md = textwrap.dedent("""\
        ---
        name: no-parent
        skill-id: no-parent
        description: Missing parent
        summary: No parent
        tags: [test]
        trigger-keywords: [test]
        parameters: {}
        requires:
          apps: [Safari]
          os: darwin
        success-condition: Done
        max-retries: 3
        ---

        ## Steps
        1. Do thing
           - verify: Done

        ## Error Recovery
        - If X: Y

        ## Notes
        - Notes
        """)
        assert librarian._validate_sibling_md(md) is False


# ===========================================================================
# 11. File Manipulation Tests
# ===========================================================================


class TestFileManipulation:
    def test_patch_without_existing_tips(self, librarian, mock_registry):
        skill = _make_skill(raw_content=SAMPLE_SKILL_MD)
        mock_registry.get_skill.return_value = skill

        result = librarian._apply_patch_parent(
            "return-amazon-order", "- New tip one\n- New tip two"
        )
        assert "## Learned Tips" in result
        assert "- New tip one" in result
        assert "- New tip two" in result
        # Original sections should remain
        assert "## Steps" in result
        assert "## Notes" in result

    def test_patch_with_existing_tips(self, librarian, mock_registry):
        skill = _make_skill(raw_content=SAMPLE_SKILL_WITH_TIPS_MD)
        mock_registry.get_skill.return_value = skill

        result = librarian._apply_patch_parent(
            "return-amazon-order", "- New appended tip"
        )
        assert "## Learned Tips" in result
        # Both old and new tips present
        assert "click \"View order details\" first" in result
        assert "- New appended tip" in result

    def test_sibling_write(self, librarian, tmp_config, mock_registry):
        md = textwrap.dedent("""\
        ---
        name: return-walmart-order
        skill-id: return-walmart-order
        description: Return item on Walmart
        summary: Walmart return flow
        tags: [ecommerce]
        trigger-keywords: [return, walmart]
        parameters: {}
        requires:
          apps: [Safari]
          os: darwin
        success-condition: Return confirmed
        max-retries: 3
        parent-skill-id: return-amazon-order
        ---

        ## Steps
        1. Go to orders
           - verify: Orders visible

        ## Error Recovery
        - If absent: refresh

        ## Notes
        - Walmart notes
        """)
        parent = _make_skill()
        skill_id, file_path = librarian._apply_create_sibling(md, parent)
        assert skill_id == "return-walmart-order"
        assert Path(file_path).exists()

    def test_sibling_name_collision(self, librarian, mock_registry):
        """When skill name already exists, append numeric suffix."""
        mock_registry._skills = {
            "return-amazon-order": _make_skill(),
            "return-walmart-order": _make_skill(name="return-walmart-order"),
        }
        md = textwrap.dedent("""\
        ---
        name: return-walmart-order
        skill-id: return-walmart-order
        description: Duplicate name
        summary: Duplicate
        tags: [test]
        trigger-keywords: [return, walmart]
        parameters: {}
        requires:
          apps: [Safari]
          os: darwin
        success-condition: Done
        max-retries: 3
        parent-skill-id: return-amazon-order
        ---

        ## Steps
        1. Do thing
           - verify: Done

        ## Error Recovery
        - If X: Y

        ## Notes
        - Notes
        """)
        parent = _make_skill()
        skill_id, file_path = librarian._apply_create_sibling(md, parent)
        # Should have a suffix to avoid collision
        assert skill_id != "return-walmart-order"
        assert "return-walmart-order" in skill_id


# ===========================================================================
# 12. Commit Sequence Tests
# ===========================================================================


class TestCommitSequence:
    @pytest.mark.asyncio
    async def test_successful_patch_parent(self, librarian, mock_registry, experience_store):
        """Full patch_parent commit: write file, reload, mark promoted, write history."""
        from automation_agent.skills.librarian import SkillLibrarian

        obs = _make_observations(
            6, confidence=0.9, run_ids=["r1", "r2", "r3", "r4", "r5", "r6"]
        )
        experience_store.append("return-amazon-order", obs)

        # Create the skill file on disk so _find_skill_path succeeds
        skill_file = mock_registry._skill_dir / "return-amazon-order.md"
        skill_file.write_text(SAMPLE_SKILL_MD)

        # Mock LLM calls
        librarian._decide_promotion_type = AsyncMock(
            return_value={
                "promotion_type": "patch_parent",
                "reason": "Strong evidence for tips",
            }
        )
        librarian._generate_content = AsyncMock(
            return_value={"learned_tips": "- When return is hidden, try View item"}
        )

        decision = await librarian.evaluate_run(
            goal="Return Tylenol",
            skill_name="return-amazon-order",
            derived_session=None,
            observations=obs,
            trace=[],
            run_id="run-eval-1",
            had_replan=False,
            success=True,
        )

        assert decision is not None
        assert decision.promotion_type == "patch_parent"
        assert decision.generated_tips != ""
        # History file should exist
        history_path = (
            librarian.config.skill_learning_dir / "promotions" / "history.jsonl"
        )
        assert history_path.exists()

    @pytest.mark.asyncio
    async def test_rollback_on_router_failure(
        self, librarian, mock_registry, experience_store
    ):
        """If load_from_string fails, rollback and return observation_only."""
        obs = _make_observations(
            6, confidence=0.9, run_ids=["r1", "r2", "r3", "r4", "r5", "r6"]
        )
        experience_store.append("return-amazon-order", obs)

        # Create the skill file on disk so _find_skill_path succeeds
        skill_file = mock_registry._skill_dir / "return-amazon-order.md"
        skill_file.write_text(SAMPLE_SKILL_MD)

        librarian._decide_promotion_type = AsyncMock(
            return_value={
                "promotion_type": "patch_parent",
                "reason": "Strong evidence",
            }
        )
        librarian._generate_content = AsyncMock(
            return_value={"learned_tips": "- Tip"}
        )
        # Explicitly reset any prior return_value, then set side_effect
        mock_registry.load_from_string.reset_mock()
        mock_registry.load_from_string.side_effect = Exception("Router rebuild failed")

        decision = await librarian.evaluate_run(
            goal="Return Tylenol",
            skill_name="return-amazon-order",
            derived_session=None,
            observations=obs,
            trace=[],
            run_id="run-rollback",
            had_replan=False,
            success=True,
        )

        assert decision is not None
        assert decision.promotion_type == "observation_only"
        assert "rollback" in decision.reason.lower() or "fail" in decision.reason.lower()

    @pytest.mark.asyncio
    async def test_successful_create_sibling(
        self, librarian, mock_registry, experience_store
    ):
        sibling_md = textwrap.dedent("""\
        ---
        name: return-walmart-order
        skill-id: return-walmart-order
        description: Return item on Walmart
        summary: Walmart return
        tags: [ecommerce]
        trigger-keywords: [return, walmart]
        parameters: {}
        requires:
          apps: [Safari]
          os: darwin
        success-condition: Return confirmed
        max-retries: 3
        parent-skill-id: return-amazon-order
        ---

        ## Steps
        1. Go to orders
           - verify: Orders visible

        ## Error Recovery
        - If absent: refresh

        ## Notes
        - Walmart notes
        """)

        obs = _make_observations(
            6, confidence=0.9, run_ids=["r1", "r2", "r3", "r4", "r5", "r6"]
        )
        experience_store.append("return-amazon-order", obs)

        librarian._decide_promotion_type = AsyncMock(
            return_value={
                "promotion_type": "create_sibling",
                "reason": "Distinct workflow for Walmart",
            }
        )
        librarian._generate_content = AsyncMock(
            return_value={"sibling_skill_md": sibling_md}
        )

        # Create a mock DerivedSkillSession
        derived = MagicMock()
        derived.serialize_for_context.return_value = "Derived context"
        derived.parent_skill_ids = ["return-amazon-order"]

        decision = await librarian.evaluate_run(
            goal="Return item on Walmart",
            skill_name="return-amazon-order",
            derived_session=derived,
            observations=obs,
            trace=[],
            run_id="run-sibling",
            had_replan=False,
            success=True,
        )

        assert decision is not None
        assert decision.promotion_type == "create_sibling"
        assert decision.new_skill_id != ""


# ===========================================================================
# 13. evaluate_run Flow Tests
# ===========================================================================


class TestEvaluateRunFlow:
    @pytest.mark.asyncio
    async def test_no_qualifying_groups(self, librarian, experience_store):
        """With insufficient observations, should return None."""
        obs = _make_observations(1, confidence=0.9, run_ids=["r1"])
        experience_store.append("return-amazon-order", obs)

        decision = await librarian.evaluate_run(
            goal="Return Tylenol",
            skill_name="return-amazon-order",
            derived_session=None,
            observations=obs,
            trace=[],
            run_id="run-noqualify",
            had_replan=False,
            success=True,
        )
        assert decision is None

    @pytest.mark.asyncio
    async def test_observation_only_decision(self, librarian, experience_store):
        obs = _make_observations(
            6, confidence=0.9, run_ids=["r1", "r2", "r3", "r4", "r5", "r6"]
        )
        experience_store.append("return-amazon-order", obs)

        librarian._decide_promotion_type = AsyncMock(
            return_value={
                "promotion_type": "observation_only",
                "reason": "Environment specific",
            }
        )

        decision = await librarian.evaluate_run(
            goal="Return Tylenol",
            skill_name="return-amazon-order",
            derived_session=None,
            observations=obs,
            trace=[],
            run_id="run-obsonly",
            had_replan=False,
            success=True,
        )
        assert decision is not None
        assert decision.promotion_type == "observation_only"

    @pytest.mark.asyncio
    async def test_exception_during_llm_returns_none(
        self, librarian, experience_store
    ):
        obs = _make_observations(
            6, confidence=0.9, run_ids=["r1", "r2", "r3", "r4", "r5", "r6"]
        )
        experience_store.append("return-amazon-order", obs)

        librarian._decide_promotion_type = AsyncMock(side_effect=Exception("LLM down"))

        decision = await librarian.evaluate_run(
            goal="Return Tylenol",
            skill_name="return-amazon-order",
            derived_session=None,
            observations=obs,
            trace=[],
            run_id="run-error",
            had_replan=False,
            success=True,
        )
        assert decision is None

    @pytest.mark.asyncio
    async def test_tip_limit_reached(self, librarian, mock_registry, experience_store):
        """When existing tips exceed max, should return observation_only."""
        librarian.config.skill_librarian_max_tips = 2
        skill_with_tips = _make_skill(
            raw_content=SAMPLE_SKILL_WITH_TIPS_MD,
            learned_tips_text="- Tip 1\n- Tip 2",
        )
        mock_registry.get_skill.return_value = skill_with_tips

        obs = _make_observations(
            6, confidence=0.9, run_ids=["r1", "r2", "r3", "r4", "r5", "r6"]
        )
        experience_store.append("return-amazon-order", obs)

        librarian._decide_promotion_type = AsyncMock(
            return_value={
                "promotion_type": "patch_parent",
                "reason": "Strong evidence",
            }
        )
        librarian._generate_content = AsyncMock(
            return_value={"learned_tips": "- Another tip"}
        )

        decision = await librarian.evaluate_run(
            goal="Return Tylenol",
            skill_name="return-amazon-order",
            derived_session=None,
            observations=obs,
            trace=[],
            run_id="run-limit",
            had_replan=False,
            success=True,
        )
        assert decision is not None
        assert decision.promotion_type == "observation_only"
        assert "tip_limit" in decision.reason.lower() or "limit" in decision.reason.lower()

    @pytest.mark.asyncio
    async def test_disabled_returns_none(self, tmp_path, experience_store, mock_registry):
        """When librarian is disabled, evaluate_run should be a no-op."""
        config = _make_config(tmp_path, skill_librarian_enabled=False)
        from automation_agent.skills.librarian import SkillLibrarian

        lib = SkillLibrarian(config=config, experience_store=experience_store, registry=mock_registry)
        obs = _make_observations(6, confidence=0.9, run_ids=["r1", "r2", "r3", "r4", "r5", "r6"])

        decision = await lib.evaluate_run(
            goal="Return Tylenol",
            skill_name="return-amazon-order",
            derived_session=None,
            observations=obs,
            trace=[],
            run_id="run-disabled",
            had_replan=False,
            success=True,
        )
        assert decision is None

    @pytest.mark.asyncio
    async def test_one_promotion_per_run(self, librarian, experience_store):
        """Only the highest-scoring group should be promoted."""
        obs_group1 = [
            SkillObservation(
                category="alternative_path",
                condition="c",
                recommendation="click View item",
                confidence=0.9,
                run_id=f"r{i}",
            )
            for i in range(6)
        ]
        obs_group2 = [
            SkillObservation(
                category="checkpoint",
                condition="c",
                recommendation="verify title",
                confidence=0.7,
                run_id=f"r{i}",
            )
            for i in range(6)
        ]
        all_obs = obs_group1 + obs_group2
        experience_store.append("return-amazon-order", all_obs)

        call_count = 0

        async def mock_decide(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return {
                "promotion_type": "observation_only",
                "reason": "test",
            }

        librarian._decide_promotion_type = mock_decide

        await librarian.evaluate_run(
            goal="Return Tylenol",
            skill_name="return-amazon-order",
            derived_session=None,
            observations=all_obs,
            trace=[],
            run_id="run-multi",
            had_replan=False,
            success=True,
        )
        # Only one LLM call — the highest scoring group
        assert call_count == 1


# ===========================================================================
# 14. LLM Utils / call_skill_llm Tests
# ===========================================================================


class TestCallSkillLLM:
    @pytest.mark.asyncio
    async def test_local_dispatch(self, tmp_config):
        from automation_agent.skills.llm_utils import call_skill_llm

        tmp_config.model_provider = "local"
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "choices": [{"message": {"content": "test response"}}]
            }
            mock_response.raise_for_status = MagicMock()
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await call_skill_llm(tmp_config, "test prompt")
            assert result == "test response"

    @pytest.mark.asyncio
    async def test_anthropic_dispatch(self, tmp_path):
        config = _make_config(tmp_path, model_provider="anthropic")
        from automation_agent.skills.llm_utils import call_skill_llm

        with patch("anthropic.AsyncAnthropic") as mock_anthropic_cls:
            mock_client = MagicMock()
            mock_message = MagicMock()
            mock_message.content = [MagicMock(text="anthropic response")]
            mock_client.messages.create = AsyncMock(return_value=mock_message)
            mock_anthropic_cls.return_value = mock_client

            result = await call_skill_llm(config, "test prompt")
            assert result == "anthropic response"


# ===========================================================================
# 15. Atomic Write Tests
# ===========================================================================


class TestAtomicWrite:
    def test_atomic_write(self, tmp_path):
        from automation_agent.skills.librarian import _atomic_write

        target = tmp_path / "test.md"
        _atomic_write(target, "hello world")
        assert target.read_text() == "hello world"
        # Temp file should not remain
        assert not (tmp_path / "test.md.tmp").exists()

    def test_atomic_write_overwrites(self, tmp_path):
        from automation_agent.skills.librarian import _atomic_write

        target = tmp_path / "test.md"
        target.write_text("old content")
        _atomic_write(target, "new content")
        assert target.read_text() == "new content"


# ===========================================================================
# 16. History Write Tests
# ===========================================================================


class TestHistoryWrite:
    def test_write_history(self, librarian, tmp_config):
        decision = PromotionDecision(
            skill_name="return-amazon-order",
            promotion_type="patch_parent",
            reason="Strong evidence",
            confidence_score=0.85,
            observation_keys=[["alternative_path", "click view item first"]],
            run_id="run-1",
            timestamp="2026-03-12T00:00:00",
            generated_tips="- Tip one",
        )
        librarian._write_history(decision)

        history_path = tmp_config.skill_learning_dir / "promotions" / "history.jsonl"
        assert history_path.exists()
        data = json.loads(history_path.read_text().strip())
        assert data["skill_name"] == "return-amazon-order"
        assert data["promotion_type"] == "patch_parent"

    def test_write_history_appends(self, librarian, tmp_config):
        for i in range(3):
            decision = PromotionDecision(
                skill_name="return-amazon-order",
                promotion_type="patch_parent",
                reason=f"Reason {i}",
                confidence_score=0.85,
                observation_keys=[],
                run_id=f"run-{i}",
                timestamp="2026-03-12T00:00:00",
            )
            librarian._write_history(decision)

        history_path = tmp_config.skill_learning_dir / "promotions" / "history.jsonl"
        lines = [l for l in history_path.read_text().splitlines() if l.strip()]
        assert len(lines) == 3


# ===========================================================================
# 17. Pre-promotion Baseline Tests
# ===========================================================================


class TestPrePromotionBaseline:
    def test_baseline_computation(self, librarian, experience_store):
        """Baseline should capture friction run stats before promotion."""
        obs = [
            SkillObservation(
                category="alternative_path",
                condition="c",
                recommendation="click view item",
                confidence=0.9,
                run_id="r1",
            ),
            SkillObservation(
                category="alternative_path",
                condition="c",
                recommendation="click view item",
                confidence=0.9,
                run_id="r2",
            ),
        ]
        baseline = librarian._compute_baseline(obs)
        assert "friction_runs" in baseline
        assert baseline["friction_runs"] == 2


# ===========================================================================
# P2-2: New Default Threshold Tests
# ===========================================================================


class TestLibrarianNewDefaults:
    """Tests that new librarian defaults are correct."""

    def test_librarian_enabled_by_default(self, monkeypatch):
        """AgentConfig().skill_librarian_enabled is True by default."""
        monkeypatch.delenv("AGENT_SKILL_LIBRARIAN_ENABLED", raising=False)
        config = AgentConfig(
            _env_file=None,
            anthropic_api_key="test-key-not-real",
            model_provider="local",
        )
        assert config.skill_librarian_enabled is True

    def test_librarian_lower_thresholds(self, monkeypatch):
        """min_observations=2, min_runs=1 by default."""
        monkeypatch.delenv("AGENT_SKILL_LIBRARIAN_MIN_OBSERVATIONS", raising=False)
        monkeypatch.delenv("AGENT_SKILL_LIBRARIAN_MIN_RUNS", raising=False)
        config = AgentConfig(
            _env_file=None,
            anthropic_api_key="test-key-not-real",
            model_provider="local",
        )
        assert config.skill_librarian_min_observations == 2
        assert config.skill_librarian_min_runs == 1

    def test_librarian_min_confidence_lowered(self, monkeypatch):
        """min_confidence=0.5 by default (below replan 0.6 cap)."""
        monkeypatch.delenv("AGENT_SKILL_LIBRARIAN_MIN_CONFIDENCE", raising=False)
        config = AgentConfig(
            _env_file=None,
            anthropic_api_key="test-key-not-real",
            model_provider="local",
        )
        assert config.skill_librarian_min_confidence == 0.5

    def test_replan_obs_above_threshold(self, monkeypatch):
        """Replan-capped observation (conf=0.6) passes 0.5 gate."""
        monkeypatch.delenv("AGENT_SKILL_LIBRARIAN_MIN_CONFIDENCE", raising=False)
        config = AgentConfig(
            _env_file=None,
            anthropic_api_key="test-key-not-real",
            model_provider="local",
        )
        replan_capped_confidence = 0.6
        assert replan_capped_confidence >= config.skill_librarian_min_confidence

    def test_dead_zone_fixed(self, monkeypatch, tmp_path):
        """Replan-capped obs (conf=0.6) score passes default min_confidence=0.5.

        Previously min_confidence=0.7 blocked all replan observations
        because learn_from_run caps had_replan obs at 0.6. This created
        a dead zone where observations were generated but never promoted.
        With min_confidence=0.5, the dead zone is eliminated.
        """
        from automation_agent.skills.librarian import SkillLibrarian

        monkeypatch.delenv("AGENT_SKILL_LIBRARIAN_MIN_CONFIDENCE", raising=False)
        monkeypatch.delenv("AGENT_SKILL_LIBRARIAN_MIN_OBSERVATIONS", raising=False)
        monkeypatch.delenv("AGENT_SKILL_LIBRARIAN_MIN_RUNS", raising=False)

        config = _make_config(tmp_path)
        store = SkillExperienceStore(config.skill_learning_dir)
        registry = MagicMock()
        registry.get_skill = MagicMock(return_value=_make_skill())
        librarian = SkillLibrarian(
            config=config, experience_store=store, registry=registry
        )

        # Simulate the dead zone: a single replan-capped observation at 0.6.
        # With only 1 observation, Bayesian score = (1+1)/(1+0+2) = 0.667.
        # Old threshold 0.7 blocked this (0.667 < 0.7 = dead zone).
        # New threshold 0.5 allows it (0.667 >= 0.5 = promoted).
        obs = _make_observations(
            1, confidence=0.6, run_ids=["r1"]
        )
        score = librarian._compute_score(obs)
        # Score must exceed the NEW default threshold (0.5)
        assert score >= config.skill_librarian_min_confidence, (
            f"Dead zone NOT fixed: score {score:.3f} "
            f"< {config.skill_librarian_min_confidence} threshold"
        )
        # Score must NOT have exceeded the OLD threshold (0.7) — proves dead zone existed
        assert score < 0.7, (
            f"Score {score:.3f} exceeds old 0.7 threshold — "
            f"dead zone scenario requires fewer observations"
        )


# ===========================================================================
# P0-3: Promotion writes must use registry's configured skill_dir
# ===========================================================================


class TestSkillDirFromRegistry:
    """Librarian must resolve file paths from registry._skill_dir, not __file__."""

    def test_find_skill_path_uses_registry_skill_dir(self, tmp_path):
        """_find_skill_path should look in registry._skill_dir, not hardcoded path."""
        from automation_agent.skills.librarian import SkillLibrarian

        skill_dir = tmp_path / "custom_skills"
        skill_dir.mkdir()
        (skill_dir / "my_skill.md").write_text("content")

        config = _make_config(tmp_path)
        store = SkillExperienceStore(config.skill_learning_dir)
        registry = MagicMock()
        registry._skill_dir = skill_dir
        lib = SkillLibrarian(config=config, experience_store=store, registry=registry)

        result = lib._find_skill_path("my_skill")
        assert result is not None
        assert result == skill_dir / "my_skill.md"

    def test_find_skill_path_not_hardcoded(self, tmp_path):
        """_find_skill_path must NOT find files only in the source library dir."""
        from automation_agent.skills.librarian import SkillLibrarian

        # Use a custom dir that differs from the default
        skill_dir = tmp_path / "skills_here"
        skill_dir.mkdir()

        config = _make_config(tmp_path)
        store = SkillExperienceStore(config.skill_learning_dir)
        registry = MagicMock()
        registry._skill_dir = skill_dir
        lib = SkillLibrarian(config=config, experience_store=store, registry=registry)

        # Should NOT find files that only exist in the hardcoded source path
        result = lib._find_skill_path("nonexistent_in_custom_dir")
        assert result is None

    def test_create_sibling_writes_to_registry_skill_dir(self, tmp_path):
        """_apply_create_sibling must write files to registry._skill_dir."""
        from automation_agent.skills.librarian import SkillLibrarian

        skill_dir = tmp_path / "custom_library"
        skill_dir.mkdir()

        config = _make_config(tmp_path)
        store = SkillExperienceStore(config.skill_learning_dir)
        registry = MagicMock()
        registry._skill_dir = skill_dir
        registry._skills = {}
        lib = SkillLibrarian(config=config, experience_store=store, registry=registry)

        md = textwrap.dedent("""\
        ---
        name: new-sibling-skill
        skill-id: new-sibling-skill
        description: A new sibling
        summary: New sibling
        tags: [test]
        trigger-keywords: [test]
        parameters: {}
        requires:
          apps: [Safari]
          os: darwin
        success-condition: Done
        max-retries: 3
        parent-skill-id: return-amazon-order
        ---

        ## Steps
        1. Do thing
           - verify: Done

        ## Error Recovery
        - If X: Y

        ## Notes
        - Notes
        """)
        parent = _make_skill()
        skill_id, file_path = lib._apply_create_sibling(md, parent)

        assert Path(file_path).parent == skill_dir
        assert Path(file_path).exists()

    def test_patch_parent_fails_when_skill_path_not_found(self, tmp_path):
        """_commit_patch_parent should fail if _find_skill_path returns None."""
        from automation_agent.skills.librarian import SkillLibrarian

        # Point to empty dir — skill file doesn't exist on disk
        skill_dir = tmp_path / "empty_skills"
        skill_dir.mkdir()

        config = _make_config(tmp_path)
        store = SkillExperienceStore(config.skill_learning_dir)
        registry = MagicMock()
        registry._skill_dir = skill_dir
        skill = _make_skill()
        registry.get_skill.return_value = skill
        registry._skills = {"return-amazon-order": skill}
        registry.load_from_string.return_value = skill
        lib = SkillLibrarian(config=config, experience_store=store, registry=registry)

        import asyncio

        obs = _make_observations(5, confidence=0.9, run_ids=["r1", "r2", "r3", "r4", "r5"])
        decision = asyncio.get_event_loop().run_until_complete(
            lib._commit_patch_parent(
                skill_name="return-amazon-order",
                parent_skill=skill,
                tips_text="- New tip",
                score=0.9,
                obs_keys=[["alt", "tip"]],
                run_id="run-1",
                best_key=("alt", "tip"),
                best_group=obs,
                reason="test",
                baseline={"friction_runs": 3},
                distinct_run_count=3,
            )
        )
        # Must NOT succeed as patch_parent if skill file can't be found
        assert decision.promotion_type == "observation_only"
        assert "path" in decision.reason.lower() or "file" in decision.reason.lower()

    def test_find_skill_path_dash_underscore_equivalence(self, tmp_path):
        """_find_skill_path should find files with underscored names for dashed skills."""
        from automation_agent.skills.librarian import SkillLibrarian

        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "return_amazon_order.md").write_text("content")

        config = _make_config(tmp_path)
        store = SkillExperienceStore(config.skill_learning_dir)
        registry = MagicMock()
        registry._skill_dir = skill_dir
        lib = SkillLibrarian(config=config, experience_store=store, registry=registry)

        result = lib._find_skill_path("return-amazon-order")
        assert result is not None
        assert result.exists()


# ===========================================================================
# P1-6: Sibling validation must check full schema
# ===========================================================================


class TestSiblingValidationStrict:
    """_validate_sibling_md must reject siblings missing required fields/sections."""

    def test_sibling_missing_summary(self, librarian):
        """Sibling without summary should be rejected."""
        md = textwrap.dedent("""\
        ---
        name: bad-sibling
        skill-id: bad-sibling
        description: Has no summary
        trigger-keywords: [test]
        tags: [test]
        parameters: {}
        requires:
          apps: [Safari]
          os: darwin
        success-condition: Done
        max-retries: 3
        parent-skill-id: return-amazon-order
        ---

        ## Steps
        1. Do thing
           - verify: Done

        ## Error Recovery
        - If X: Y

        ## Notes
        - Notes
        """)
        assert librarian._validate_sibling_md(md) is False

    def test_sibling_missing_tags(self, librarian):
        """Sibling without tags should be rejected."""
        md = textwrap.dedent("""\
        ---
        name: bad-sibling
        skill-id: bad-sibling
        description: Has no tags
        summary: Summary here
        trigger-keywords: [test]
        parameters: {}
        requires:
          apps: [Safari]
          os: darwin
        success-condition: Done
        max-retries: 3
        parent-skill-id: return-amazon-order
        ---

        ## Steps
        1. Do thing
           - verify: Done

        ## Error Recovery
        - If X: Y

        ## Notes
        - Notes
        """)
        assert librarian._validate_sibling_md(md) is False

    def test_sibling_missing_error_recovery(self, librarian):
        """Sibling without Error Recovery section should be rejected."""
        md = textwrap.dedent("""\
        ---
        name: bad-sibling
        skill-id: bad-sibling
        description: Has no error recovery
        summary: Summary here
        tags: [test]
        trigger-keywords: [test]
        parameters: {}
        requires:
          apps: [Safari]
          os: darwin
        success-condition: Done
        max-retries: 3
        parent-skill-id: return-amazon-order
        ---

        ## Steps
        1. Do thing
           - verify: Done

        ## Notes
        - Notes
        """)
        assert librarian._validate_sibling_md(md) is False

    def test_sibling_missing_notes(self, librarian):
        """Sibling without Notes section should be rejected."""
        md = textwrap.dedent("""\
        ---
        name: bad-sibling
        skill-id: bad-sibling
        description: Has no notes
        summary: Summary here
        tags: [test]
        trigger-keywords: [test]
        parameters: {}
        requires:
          apps: [Safari]
          os: darwin
        success-condition: Done
        max-retries: 3
        parent-skill-id: return-amazon-order
        ---

        ## Steps
        1. Do thing
           - verify: Done

        ## Error Recovery
        - If X: Y
        """)
        assert librarian._validate_sibling_md(md) is False

    def test_sibling_valid_with_all_fields(self, librarian):
        """A sibling with all required fields/sections should pass validation."""
        md = textwrap.dedent("""\
        ---
        name: good-sibling
        skill-id: good-sibling
        description: Complete sibling
        summary: A properly formed sibling skill
        tags: [test, ecommerce]
        trigger-keywords: [test, return]
        parameters: {}
        requires:
          apps: [Safari]
          os: darwin
        success-condition: Done
        max-retries: 3
        parent-skill-id: return-amazon-order
        ---

        ## Steps
        1. Do thing
           - verify: Done

        ## Error Recovery
        - If X: Y

        ## Notes
        - Notes here
        """)
        assert librarian._validate_sibling_md(md) is True

    def test_sibling_missing_description(self, librarian):
        """Sibling without description should be rejected (parse_skill_file raises)."""
        md = textwrap.dedent("""\
        ---
        name: bad-sibling
        skill-id: bad-sibling
        summary: Has no description
        tags: [test]
        trigger-keywords: [test]
        parameters: {}
        requires:
          apps: [Safari]
          os: darwin
        success-condition: Done
        max-retries: 3
        parent-skill-id: return-amazon-order
        ---

        ## Steps
        1. Do thing
           - verify: Done

        ## Error Recovery
        - If X: Y

        ## Notes
        - Notes
        """)
        assert librarian._validate_sibling_md(md) is False


# ===========================================================================
# Finding 1: getattr fallback raises ValueError when _skill_dir is absent
# ===========================================================================


class TestSkillDirRequiredOnRegistry:
    """Librarian must raise ValueError if registry lacks _skill_dir."""

    def test_missing_skill_dir_raises_valueerror(self, tmp_path):
        """SkillLibrarian.__init__ raises ValueError when registry has no _skill_dir."""
        from automation_agent.skills.librarian import SkillLibrarian

        config = _make_config(tmp_path)
        store = SkillExperienceStore(config.skill_learning_dir)
        registry = MagicMock(spec=["get_skill", "_skills", "load_from_string"])
        # spec= restricts attrs; _skill_dir is NOT in the spec
        assert not hasattr(registry, "_skill_dir")

        with pytest.raises(ValueError, match="requires registry._skill_dir"):
            SkillLibrarian(config=config, experience_store=store, registry=registry)

    def test_present_skill_dir_no_error(self, tmp_path):
        """SkillLibrarian.__init__ succeeds when registry has _skill_dir."""
        from automation_agent.skills.librarian import SkillLibrarian

        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()

        config = _make_config(tmp_path)
        store = SkillExperienceStore(config.skill_learning_dir)
        registry = MagicMock()
        registry._skill_dir = skill_dir
        lib = SkillLibrarian(config=config, experience_store=store, registry=registry)
        assert lib._skill_dir == skill_dir


# ===========================================================================
# Finding 2: evaluate_run-level test for skill file not found
# ===========================================================================


class TestEvaluateRunSkillFileNotFound:
    """evaluate_run should return observation_only when skill file is missing on disk."""

    @pytest.mark.asyncio
    async def test_evaluate_run_skill_file_not_found_returns_observation_only(
        self, tmp_path
    ):
        """Full evaluate_run path: custom skill_dir without the skill file on disk
        should return observation_only with 'skill_file_not_found' reason."""
        from automation_agent.skills.librarian import SkillLibrarian

        # Point to empty dir -- skill file doesn't exist on disk
        skill_dir = tmp_path / "empty_skills"
        skill_dir.mkdir()

        config = _make_config(tmp_path)
        store = SkillExperienceStore(config.skill_learning_dir)
        registry = MagicMock()
        registry._skill_dir = skill_dir
        skill = _make_skill()
        registry.get_skill.return_value = skill
        registry._skills = {"return-amazon-order": skill}
        registry.load_from_string.return_value = skill
        lib = SkillLibrarian(config=config, experience_store=store, registry=registry)

        obs = _make_observations(
            6, confidence=0.9, run_ids=["r1", "r2", "r3", "r4", "r5", "r6"]
        )
        store.append("return-amazon-order", obs)

        # Mock LLM calls to request patch_parent
        lib._decide_promotion_type = AsyncMock(
            return_value={
                "promotion_type": "patch_parent",
                "reason": "Strong evidence for tips",
            }
        )
        lib._generate_content = AsyncMock(
            return_value={"learned_tips": "- When return is hidden, try View item"}
        )

        decision = await lib.evaluate_run(
            goal="Return Tylenol",
            skill_name="return-amazon-order",
            derived_session=None,
            observations=obs,
            trace=[],
            run_id="run-file-not-found",
            had_replan=False,
            success=True,
        )

        assert decision is not None
        assert decision.promotion_type == "observation_only"
        assert "file" in decision.reason.lower() or "not_found" in decision.reason


# ===========================================================================
# Finding 3: _find_skill_path underscore-to-dash fallback
# ===========================================================================


class TestFindSkillPathUnderscoreToDash:
    """_find_skill_path should find files with dashed names for underscored skills."""

    def test_underscore_to_dash_fallback(self, tmp_path):
        """Skill named 'return_amazon_order' should find 'return-amazon-order.md'."""
        from automation_agent.skills.librarian import SkillLibrarian

        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "return-amazon-order.md").write_text("content")

        config = _make_config(tmp_path)
        store = SkillExperienceStore(config.skill_learning_dir)
        registry = MagicMock()
        registry._skill_dir = skill_dir
        lib = SkillLibrarian(config=config, experience_store=store, registry=registry)

        result = lib._find_skill_path("return_amazon_order")
        assert result is not None
        assert result.exists()
        assert result.name == "return-amazon-order.md"

    def test_dash_to_underscore_still_works(self, tmp_path):
        """Existing fallback: 'return-amazon-order' finds 'return_amazon_order.md'."""
        from automation_agent.skills.librarian import SkillLibrarian

        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "return_amazon_order.md").write_text("content")

        config = _make_config(tmp_path)
        store = SkillExperienceStore(config.skill_learning_dir)
        registry = MagicMock()
        registry._skill_dir = skill_dir
        lib = SkillLibrarian(config=config, experience_store=store, registry=registry)

        result = lib._find_skill_path("return-amazon-order")
        assert result is not None
        assert result.exists()
        assert result.name == "return_amazon_order.md"

    def test_exact_match_preferred(self, tmp_path):
        """If exact name matches, should prefer it over fallbacks."""
        from automation_agent.skills.librarian import SkillLibrarian

        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "my-skill.md").write_text("exact")
        (skill_dir / "my_skill.md").write_text("fallback")

        config = _make_config(tmp_path)
        store = SkillExperienceStore(config.skill_learning_dir)
        registry = MagicMock()
        registry._skill_dir = skill_dir
        lib = SkillLibrarian(config=config, experience_store=store, registry=registry)

        result = lib._find_skill_path("my-skill")
        assert result is not None
        assert result.name == "my-skill.md"


# ===========================================================================
# Finding 4: CI guard for duplicate skill names in library frontmatter
# ===========================================================================


class TestNoDuplicateSkillNames:
    """CI guard: no two skill files in library/ may share the same 'name:' or 'skill-id:'."""

    @staticmethod
    def _scan_library_frontmatter():
        """Return list of (meta_dict, filename) for all .md files in library/."""
        import re as _re

        import yaml

        library_dir = (
            Path(__file__).resolve().parent.parent.parent
            / "src"
            / "automation_agent"
            / "skills"
            / "library"
        )
        if not library_dir.exists():
            pytest.skip("skills/library/ not found")

        results = []
        for md_file in sorted(library_dir.glob("*.md")):
            content = md_file.read_text(encoding="utf-8")
            fm_match = _re.match(
                r"^---\s*\n(.*?)\n---", content, _re.DOTALL
            )
            if not fm_match:
                continue
            try:
                meta = yaml.safe_load(fm_match.group(1))
            except Exception:
                continue
            if not isinstance(meta, dict):
                continue
            results.append((meta, md_file.name))
        return results

    def test_no_duplicate_skill_names_in_library(self):
        """Scan all .md files in skills/library/ for duplicate name: values.

        This prevents the P0-4 regression where multiple files claim the same
        skill name, causing nondeterministic routing.
        """
        entries = self._scan_library_frontmatter()

        seen_names: dict[str, str] = {}  # name -> file
        duplicates: list[str] = []

        for meta, filename in entries:
            name = meta.get("name", "")
            if not name:
                continue
            if name in seen_names:
                duplicates.append(
                    f"Duplicate name '{name}': {seen_names[name]} and {filename}"
                )
            else:
                seen_names[name] = filename

        assert not duplicates, (
            "Duplicate skill name(s) found in library/:\n"
            + "\n".join(duplicates)
        )

    def test_no_duplicate_skill_ids_in_library(self):
        """Scan all .md files in skills/library/ for duplicate skill-id: values.

        skill-id is the routing key used by the registry; duplicates cause
        nondeterministic skill selection just like duplicate names.
        """
        entries = self._scan_library_frontmatter()

        seen_ids: dict[str, str] = {}  # skill-id -> file
        duplicates: list[str] = []

        for meta, filename in entries:
            skill_id = meta.get("skill-id", "")
            if not skill_id:
                continue
            if skill_id in seen_ids:
                duplicates.append(
                    f"Duplicate skill-id '{skill_id}': {seen_ids[skill_id]} and {filename}"
                )
            else:
                seen_ids[skill_id] = filename

        assert not duplicates, (
            "Duplicate skill-id(s) found in library/:\n"
            + "\n".join(duplicates)
        )

    def test_at_least_one_skill_scanned(self):
        """Guard: the duplicate check must scan at least one file."""
        library_dir = (
            Path(__file__).resolve().parent.parent.parent
            / "src"
            / "automation_agent"
            / "skills"
            / "library"
        )
        md_files = list(library_dir.glob("*.md"))
        assert len(md_files) > 0, "No .md files found in library/ -- test is vacuous"


# ===========================================================================
# Finding 5: _validate_sibling_md parent_skill_id fallback path
# ===========================================================================


class TestValidateSiblingParentSkillId:
    """Test that _validate_sibling_md rejects siblings without parent-skill-id.

    The fallback re-parse of raw YAML was removed because parse_skill_file()
    faithfully extracts parent-skill-id from frontmatter, making the fallback
    dead code under normal operation. These tests verify the direct check.
    """

    def test_parent_skill_id_present_passes(self, librarian):
        """Sibling with parent-skill-id in frontmatter passes validation."""
        md = textwrap.dedent("""\
        ---
        name: valid-sibling
        skill-id: valid-sibling
        description: Has parent
        summary: Valid sibling
        tags: [test]
        trigger-keywords: [test]
        parameters: {}
        requires:
          apps: [Safari]
          os: darwin
        success-condition: Done
        max-retries: 3
        parent-skill-id: return-amazon-order
        ---

        ## Steps
        1. Do thing
           - verify: Done

        ## Error Recovery
        - If X: Y

        ## Notes
        - Notes
        """)
        result = librarian._validate_sibling_md(md)
        assert result is True

    def test_parent_skill_id_missing_fails(self, librarian):
        """Sibling without parent-skill-id is rejected."""
        md = textwrap.dedent("""\
        ---
        name: no-parent
        skill-id: no-parent
        description: No parent
        summary: No parent
        tags: [test]
        trigger-keywords: [test]
        parameters: {}
        requires:
          apps: [Safari]
          os: darwin
        success-condition: Done
        max-retries: 3
        ---

        ## Steps
        1. Do thing
           - verify: Done

        ## Error Recovery
        - If X: Y

        ## Notes
        - Notes
        """)
        result = librarian._validate_sibling_md(md)
        assert result is False
