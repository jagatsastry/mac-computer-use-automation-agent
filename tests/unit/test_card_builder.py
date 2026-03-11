"""Unit tests for SkillCardBuilder."""

import pytest

from automation_agent.skills.card_builder import SkillCardBuilder
from automation_agent.skills.models import (
    Skill,
    SkillCard,
    SkillParam,
    SkillRequirements,
)


def _make_skill(**overrides) -> Skill:
    """Create a Skill with sensible defaults, overridden by kwargs."""
    defaults = dict(
        name="test-skill",
        description="A test skill",
        trigger_keywords=["test", "demo"],
        parameters={},
        requires=SkillRequirements(apps=["Safari"], os="darwin"),
        success_condition="Done",
        steps_text="1. Do something\n   - verify: done",
        skill_id="",
        tags=[],
        summary="",
    )
    defaults.update(overrides)
    return Skill(**defaults)


class TestBuildCardFromSkillWithAllFields:
    def test_build_card_from_skill_with_all_fields(self):
        """Skill with skill_id/tags/summary produces correct card."""
        skill = _make_skill(
            skill_id="return-amazon-order",
            tags=["ecommerce", "return", "refund"],
            summary="Navigate a retailer's order history and complete a return.",
        )
        card = SkillCardBuilder.build(skill)
        assert card.skill_id == "return-amazon-order"
        assert card.title == "Return Amazon Order"
        assert card.summary == "Navigate a retailer's order history and complete a return."
        assert card.tags == ["ecommerce", "return", "refund"]
        assert card.required_apps == ["Safari"]
        assert card.required_os == "darwin"


class TestBuildCardFromSkillWithoutOptionalFields:
    def test_build_card_from_skill_without_optional_fields(self):
        """Falls back to name for skill_id, description for summary,
        trigger_keywords for tags."""
        skill = _make_skill(
            name="google-search",
            description="Search Google for a query",
            trigger_keywords=["google", "search"],
            skill_id="",
            tags=[],
            summary="",
        )
        card = SkillCardBuilder.build(skill)
        assert card.skill_id == "google-search"
        assert card.title == "Google Search"
        assert card.summary == "Search Google for a query"
        assert card.tags == ["google", "search"]


class TestBuildCardEmptySkillIdRaises:
    def test_build_card_empty_skill_id_raises(self):
        """Skill with empty name AND empty skill_id raises ValueError."""
        skill = _make_skill(name="", skill_id="")
        with pytest.raises(ValueError, match="empty skill_id"):
            SkillCardBuilder.build(skill)


class TestBuildCardEmptySummaryRaises:
    def test_build_card_empty_summary_raises(self):
        """Skill with empty description AND empty summary raises ValueError."""
        skill = _make_skill(description="", summary="")
        with pytest.raises(ValueError, match="empty summary"):
            SkillCardBuilder.build(skill)


class TestBuildAllSkipsFailures:
    def test_build_all_skips_failures(self):
        """Malformed skill (empty id/summary) doesn't crash build_all."""
        skills = {
            "good": _make_skill(name="good-skill", description="Good desc"),
            "bad": _make_skill(name="", skill_id="", description="No id"),
        }
        cards = SkillCardBuilder.build_all(skills)
        assert len(cards) == 1
        assert cards[0].skill_id == "good-skill"


class TestFormatCardsForPrompt:
    def test_format_cards_for_prompt(self):
        """Output contains skill_id, summary, tags."""
        cards = [
            SkillCard(
                skill_id="return-amazon-order",
                title="Return Amazon Order",
                summary="Return an item on Amazon.",
                tags=["ecommerce", "return"],
                required_apps=["Safari"],
                required_os="darwin",
            ),
            SkillCard(
                skill_id="google-search",
                title="Google Search",
                summary="Search Google for a query.",
                tags=["search"],
                required_apps=[],
                required_os="darwin",
            ),
        ]
        output = SkillCardBuilder.format_cards_for_prompt(cards)
        assert "### return-amazon-order" in output
        assert "Return an item on Amazon." in output
        assert "ecommerce, return" in output
        assert "Requires apps: Safari" in output
        assert "### google-search" in output
        assert "Search Google for a query." in output


class TestCardBuilderEmptySkills:
    def test_card_builder_empty_skills(self):
        """Empty dict returns empty list."""
        cards = SkillCardBuilder.build_all({})
        assert cards == []


# ---------------------------------------------------------------------------
# Issue 1 & 2: SkillCard __post_init__ validation
# ---------------------------------------------------------------------------


class TestSkillCardPostInitValidation:
    def test_empty_skill_id_raises(self):
        """Constructing SkillCard with empty skill_id raises ValueError."""
        with pytest.raises(ValueError, match="non-empty skill_id"):
            SkillCard(
                skill_id="",
                title="X",
                summary="desc",
                tags=[],
                required_apps=[],
                required_os="",
            )

    def test_empty_summary_raises(self):
        """Constructing SkillCard with empty summary raises ValueError."""
        with pytest.raises(ValueError, match="non-empty summary"):
            SkillCard(
                skill_id="test",
                title="X",
                summary="",
                tags=[],
                required_apps=[],
                required_os="",
            )

    def test_whitespace_only_skill_id_raises(self):
        """Whitespace-only skill_id is treated as empty."""
        with pytest.raises(ValueError, match="non-empty skill_id"):
            SkillCard(
                skill_id="   ",
                title="X",
                summary="desc",
                tags=[],
                required_apps=[],
                required_os="",
            )

    def test_whitespace_only_summary_raises(self):
        """Whitespace-only summary is treated as empty."""
        with pytest.raises(ValueError, match="non-empty summary"):
            SkillCard(
                skill_id="test",
                title="X",
                summary="  \t  ",
                tags=[],
                required_apps=[],
                required_os="",
            )
