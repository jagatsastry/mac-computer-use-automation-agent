"""
Deterministic test: seed parent match → inject repeated observations across
distinct run_ids → call promotion → assert the full create_sibling commit contract.

Tests the complete promotion pipeline for create_sibling without requiring
live agent runs or real LLM calls. This is the authoritative test for
property (b) of the adaptive skill system.
"""

import json
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from automation_agent.config import AgentConfig
from automation_agent.skills.experience import SkillExperienceStore
from automation_agent.skills.librarian import SkillLibrarian
from automation_agent.skills.models import PromotionDecision, SkillObservation
from automation_agent.skills.registry import SkillRegistryImpl


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PARENT_SKILL_MD = textwrap.dedent("""\
    ---
    name: test-parent
    skill-id: test-parent
    description: A parent skill for testing sibling promotion
    summary: Parent skill used to validate create_sibling pipeline
    tags: [test, parent]
    trigger-keywords: [test, parent, buy]
    parameters:
      product:
        type: string
        required: true
        description: Product to buy
    requires:
      os: darwin
    success-condition: Product added to cart
    ---

    ## Steps
    1. Open the product page
       - verify: Product page visible
    2. Click Add to Cart
       - verify: Cart updated

    ## Error Recovery
    - If Add to Cart is missing, scroll down

    ## Notes
    - This is a test parent skill
""")

# Valid sibling MD that passes _validate_sibling_md:
# - trigger_keywords, steps_text, success_condition (loader fields)
# - summary, tags (routing fields)
# - error_recovery_text, notes_text (runtime context)
# - parent-skill-id (sibling link)
SIBLING_SKILL_MD = textwrap.dedent("""\
    ---
    name: test-parent-variant
    skill-id: test-parent-variant
    description: A sibling variant for a different workflow
    summary: Variant of test-parent for alternative checkout flow
    tags: [test, sibling, checkout]
    trigger-keywords: [test, variant, checkout]
    parent-skill-id: test-parent
    parameters:
      product:
        type: string
        required: true
        description: Product to buy
    requires:
      os: darwin
    success-condition: Checkout completed via alternative flow
    ---

    ## Steps
    1. Open the product page
       - verify: Product page visible
    2. Click Buy Now instead of Add to Cart
       - verify: Checkout flow started

    ## Error Recovery
    - If Buy Now is missing, try the mobile checkout button

    ## Notes
    - This variant uses the Buy Now shortcut instead of Add to Cart
""")


def _make_observations(n: int = 3) -> list[SkillObservation]:
    """Create n observations with the SAME (category, recommendation) but different run_ids."""
    return [
        SkillObservation(
            category="alternative_path",
            condition="Add to Cart button is hidden behind a paywall",
            recommendation="Use Buy Now button to bypass the cart step entirely",
            rationale="Buy Now is always visible and skips the cart page",
            confidence=0.6,
            run_id=f"run-{i:03d}",
        )
        for i in range(n)
    ]


@pytest.fixture
def skill_dirs(tmp_path):
    """Create isolated skill directory and skill_learning directory."""
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    (skill_dir / "test_parent.md").write_text(PARENT_SKILL_MD)

    learning_dir = tmp_path / "skill_learning"
    learning_dir.mkdir()
    (learning_dir / "promotions").mkdir()

    return skill_dir, learning_dir


@pytest.fixture
def config(skill_dirs):
    _, learning_dir = skill_dirs
    return AgentConfig(
        _env_file=None,
        anthropic_api_key="test-key-not-real",
        skill_learning_dir=learning_dir,
        skill_learning_enabled=True,
        skill_librarian_enabled=True,
        skill_librarian_min_observations=2,
        skill_librarian_min_runs=1,
        skill_librarian_min_confidence=0.5,
        model_provider="local",
    )


@pytest.fixture
def registry(skill_dirs, config):
    skill_dir, _ = skill_dirs
    return SkillRegistryImpl(skill_dir=skill_dir, config=config)


@pytest.fixture
def experience_store(config):
    return SkillExperienceStore(root=config.skill_learning_dir)


@pytest.fixture
def librarian(config, experience_store, registry):
    return SkillLibrarian(
        config=config,
        experience_store=experience_store,
        registry=registry,
    )


# ---------------------------------------------------------------------------
# Test: Full create_sibling commit contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_sibling_full_commit_contract(
    librarian, registry, experience_store, config, skill_dirs
):
    """
    Seed parent match → inject observations → call promotion →
    assert full create_sibling commit contract.
    """
    skill_dir, learning_dir = skill_dirs

    # Step 1: Verify parent skill loaded
    parent = registry.get_skill("test-parent")
    assert parent is not None, "Parent skill must be loaded"

    # Step 2: Seed observations — 3 with same (cat, rec), different run_ids
    observations = _make_observations(3)
    experience_store.append("test-parent", observations)

    # Verify observations persisted
    stored = experience_store.load("test-parent")
    assert len(stored) == 3
    assert len({o.run_id for o in stored}) == 3  # distinct run_ids

    # Step 3: Mock LLM calls to return create_sibling decision + valid sibling MD
    librarian._decide_promotion_type = AsyncMock(
        return_value={
            "promotion_type": "create_sibling",
            "reason": "Workflow diverges significantly: Buy Now vs Add to Cart",
        }
    )
    librarian._generate_content = AsyncMock(
        return_value={"sibling_skill_md": SIBLING_SKILL_MD}
    )

    # Step 4: Call evaluate_run
    decision = await librarian.evaluate_run(
        goal="Buy a widget on test-parent site",
        skill_name="test-parent",
        derived_session=None,
        observations=observations,
        trace=[],
        run_id="run-promotion",
        had_replan=False,
        success=True,
    )

    # Step 5: Assert full commit contract
    assert decision is not None, "Promotion decision must be returned"

    # (a) promotion_type == "create_sibling"
    assert decision.promotion_type == "create_sibling"

    # (b) A new .md file exists in the configured skill_dir (not hardcoded path)
    new_files = [
        f for f in skill_dir.glob("*.md") if f.name != "test_parent.md"
    ]
    assert len(new_files) == 1, f"Expected 1 new sibling file, got {len(new_files)}: {new_files}"
    sibling_path = new_files[0]

    # (c) The new .md contains "parent-skill-id: test-parent"
    sibling_content = sibling_path.read_text()
    assert "parent-skill-id: test-parent" in sibling_content

    # (d) The new .md has "trusted: false"
    assert "trusted: false" in sibling_content

    # (e) SkillRegistryImpl can reload and find the new skill
    new_skill_id = decision.new_skill_id
    assert new_skill_id, "new_skill_id must be set on decision"
    found = registry.get_skill(new_skill_id)
    assert found is not None, f"Registry must find sibling skill '{new_skill_id}'"
    assert found.parent_skill_id == "test-parent"

    # (f) The seeded observations are marked as promoted in the experience store
    reloaded = experience_store.load("test-parent")
    promoted_count = sum(1 for o in reloaded if o.promoted)
    assert promoted_count > 0, "At least some observations must be marked as promoted"

    # (g) promotions/history.jsonl has entry with promotion_type == "create_sibling"
    history_path = learning_dir / "promotions" / "history.jsonl"
    assert history_path.exists(), "History file must exist"
    history_lines = [
        json.loads(l) for l in history_path.read_text().splitlines() if l.strip()
    ]
    sibling_entries = [
        h for h in history_lines if h.get("promotion_type") == "create_sibling"
    ]
    assert len(sibling_entries) == 1
    assert sibling_entries[0]["skill_name"] == "test-parent"
    assert sibling_entries[0]["parent_skill_id"] == "test-parent"

    # (h) Negative: default source library did NOT get a copy
    default_library = Path("src/automation_agent/skills/library/")
    if default_library.exists():
        default_files = [f.name for f in default_library.glob("*.md")]
        assert sibling_path.name not in default_files, (
            f"Sibling file {sibling_path.name} must NOT appear in default library"
        )


# ---------------------------------------------------------------------------
# Test: Name collision handling (sibling with same name as parent)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_sibling_name_collision_increments(
    librarian, registry, experience_store, config, skill_dirs
):
    """When sibling name matches parent, suffix -2 is appended."""
    skill_dir, _ = skill_dirs

    # Sibling MD with same name as parent
    colliding_md = SIBLING_SKILL_MD.replace(
        "name: test-parent-variant", "name: test-parent"
    ).replace(
        "skill-id: test-parent-variant", "skill-id: test-parent"
    )

    observations = _make_observations(3)
    experience_store.append("test-parent", observations)

    librarian._decide_promotion_type = AsyncMock(
        return_value={
            "promotion_type": "create_sibling",
            "reason": "Workflow diverges",
        }
    )
    librarian._generate_content = AsyncMock(
        return_value={"sibling_skill_md": colliding_md}
    )

    decision = await librarian.evaluate_run(
        goal="Test collision",
        skill_name="test-parent",
        derived_session=None,
        observations=observations,
        trace=[],
        run_id="run-collision",
        had_replan=False,
        success=True,
    )

    assert decision is not None
    assert decision.promotion_type == "create_sibling"
    # Should have been renamed to test-parent-2
    assert decision.new_skill_id == "test-parent-2"
    found = registry.get_skill("test-parent-2")
    assert found is not None


# ---------------------------------------------------------------------------
# Test: Rollback on load_from_string failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_sibling_rollback_on_reload_failure(
    librarian, registry, experience_store, config, skill_dirs
):
    """If load_from_string raises, file is deleted and registry is clean."""
    skill_dir, learning_dir = skill_dirs

    observations = _make_observations(3)
    experience_store.append("test-parent", observations)

    librarian._decide_promotion_type = AsyncMock(
        return_value={
            "promotion_type": "create_sibling",
            "reason": "Workflow diverges",
        }
    )
    librarian._generate_content = AsyncMock(
        return_value={"sibling_skill_md": SIBLING_SKILL_MD}
    )

    # Patch load_from_string to raise after file is written
    original_load = registry.load_from_string
    with patch.object(
        registry, "load_from_string", side_effect=ValueError("parse failed")
    ):
        decision = await librarian.evaluate_run(
            goal="Test rollback",
            skill_name="test-parent",
            derived_session=None,
            observations=observations,
            trace=[],
            run_id="run-rollback",
            had_replan=False,
            success=True,
        )

    assert decision is not None
    # Should fall back to observation_only due to rollback
    assert decision.promotion_type == "observation_only"
    assert "rollback" in decision.reason

    # File should have been cleaned up
    new_files = [f for f in skill_dir.glob("*.md") if f.name != "test_parent.md"]
    assert len(new_files) == 0, f"Sibling file should be deleted on rollback: {new_files}"

    # Registry should NOT have the sibling
    assert registry.get_skill("test-parent-variant") is None


# ---------------------------------------------------------------------------
# Test: Rollback on _write_history failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_sibling_rollback_on_history_write_failure(
    librarian, registry, experience_store, config, skill_dirs
):
    """If _write_history raises, file is deleted and registry cleaned."""
    skill_dir, _ = skill_dirs

    observations = _make_observations(3)
    experience_store.append("test-parent", observations)

    librarian._decide_promotion_type = AsyncMock(
        return_value={
            "promotion_type": "create_sibling",
            "reason": "Workflow diverges",
        }
    )
    librarian._generate_content = AsyncMock(
        return_value={"sibling_skill_md": SIBLING_SKILL_MD}
    )

    # Patch _write_history to raise
    with patch.object(
        librarian, "_write_history", side_effect=IOError("disk full")
    ):
        decision = await librarian.evaluate_run(
            goal="Test history rollback",
            skill_name="test-parent",
            derived_session=None,
            observations=observations,
            trace=[],
            run_id="run-history-fail",
            had_replan=False,
            success=True,
        )

    assert decision is not None
    assert decision.promotion_type == "observation_only"
    assert "rollback" in decision.reason

    # Sibling file should be cleaned up
    new_files = [f for f in skill_dir.glob("*.md") if f.name != "test_parent.md"]
    assert len(new_files) == 0, f"Sibling file should be deleted on rollback: {new_files}"

    # Registry should NOT have the sibling
    assert registry.get_skill("test-parent-variant") is None


# ---------------------------------------------------------------------------
# Test: Observations below threshold do not trigger promotion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_no_promotion_below_threshold(
    librarian, registry, experience_store, config
):
    """With only 1 observation (below min_observations=2), no promotion fires."""
    experience_store.append("test-parent", _make_observations(1))

    decision = await librarian.evaluate_run(
        goal="Test threshold",
        skill_name="test-parent",
        derived_session=None,
        observations=_make_observations(1),
        trace=[],
        run_id="run-below-threshold",
        had_replan=False,
        success=True,
    )

    # Should return None (no qualifying groups)
    assert decision is None


# ---------------------------------------------------------------------------
# Test: Already-promoted observations are not re-promoted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_already_promoted_not_re_promoted(
    librarian, registry, experience_store, config, skill_dirs
):
    """Once a (cat, rec) group is promoted, it's not promoted again."""
    _, learning_dir = skill_dirs

    observations = _make_observations(3)
    experience_store.append("test-parent", observations)

    librarian._decide_promotion_type = AsyncMock(
        return_value={
            "promotion_type": "create_sibling",
            "reason": "Workflow diverges",
        }
    )
    librarian._generate_content = AsyncMock(
        return_value={"sibling_skill_md": SIBLING_SKILL_MD}
    )

    # First promotion should succeed
    decision1 = await librarian.evaluate_run(
        goal="First promotion",
        skill_name="test-parent",
        derived_session=None,
        observations=observations,
        trace=[],
        run_id="run-first",
        had_replan=False,
        success=True,
    )
    assert decision1 is not None
    assert decision1.promotion_type == "create_sibling"

    # Add more observations with SAME (cat, rec)
    experience_store.append("test-parent", _make_observations(3))

    # Second promotion should skip (already promoted)
    decision2 = await librarian.evaluate_run(
        goal="Second promotion attempt",
        skill_name="test-parent",
        derived_session=None,
        observations=observations,
        trace=[],
        run_id="run-second",
        had_replan=False,
        success=True,
    )

    # Should be None (no qualifying groups after history filter)
    assert decision2 is None


# ---------------------------------------------------------------------------
# Test: Sibling validation rejects incomplete MD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_sibling_rejects_invalid_md(
    librarian, registry, experience_store, config
):
    """Sibling MD missing required sections is rejected gracefully."""
    observations = _make_observations(3)
    experience_store.append("test-parent", observations)

    # MD missing ## Error Recovery and ## Notes
    invalid_md = textwrap.dedent("""\
        ---
        name: bad-sibling
        description: Missing sections
        trigger-keywords: [test]
        success-condition: Done
        ---

        ## Steps
        1. Do thing
           - verify: Thing done
    """)

    librarian._decide_promotion_type = AsyncMock(
        return_value={
            "promotion_type": "create_sibling",
            "reason": "Workflow diverges",
        }
    )
    librarian._generate_content = AsyncMock(
        return_value={"sibling_skill_md": invalid_md}
    )

    decision = await librarian.evaluate_run(
        goal="Test validation",
        skill_name="test-parent",
        derived_session=None,
        observations=observations,
        trace=[],
        run_id="run-invalid",
        had_replan=False,
        success=True,
    )

    assert decision is not None
    assert decision.promotion_type == "observation_only"
    assert "validation_failed" in decision.reason
