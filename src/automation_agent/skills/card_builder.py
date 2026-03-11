"""Builds compact SkillCard objects from Skill dataclasses."""

from automation_agent.skills.models import Skill, SkillCard


class SkillCardBuilder:
    """Builds compact SkillCard objects from Skill dataclasses.

    Cards are computed on-the-fly, not persisted. Used by the router
    to build the LLM prompt.
    """

    @staticmethod
    def build(skill: Skill) -> SkillCard:
        """Build a SkillCard from a Skill object.

        Raises ValueError if the resulting card would have empty skill_id or summary.
        """
        skill_id = skill.skill_id or skill.name
        if not skill_id:
            raise ValueError("Cannot build SkillCard: empty skill_id and name")
        title = skill_id.replace("-", " ").replace("_", " ").title()
        summary = skill.summary or skill.description
        if not summary:
            raise ValueError(
                f"Cannot build SkillCard for '{skill_id}': "
                "empty summary and description"
            )
        tags = list(skill.tags) if skill.tags else list(skill.trigger_keywords)
        return SkillCard(
            skill_id=skill_id,
            title=title,
            summary=summary,
            tags=tags,
            required_apps=list(skill.requires.apps),
            required_os=skill.requires.os,
        )

    @classmethod
    def build_all(cls, skills: dict[str, Skill]) -> list[SkillCard]:
        """Build cards for all skills. Filters out skills that fail card building."""
        cards = []
        for skill in skills.values():
            try:
                cards.append(cls.build(skill))
            except ValueError:
                continue
        return cards

    @staticmethod
    def format_cards_for_prompt(cards: list[SkillCard]) -> str:
        """Format skill cards as a compact text block for the router prompt."""
        parts = []
        for card in cards:
            lines = [
                f"### {card.skill_id}",
                f"**{card.title}**",
                card.summary,
            ]
            if card.tags:
                lines.append(f"Tags: {', '.join(card.tags)}")
            if card.required_apps:
                lines.append(f"Requires apps: {', '.join(card.required_apps)}")
            parts.append("\n".join(lines))
        return "\n\n".join(parts)
