"""Skill registry for the automation agent.

Loads skill templates from YAML frontmatter + Markdown files, matches user
prompts to skills by keyword, and expands templates with parameters.
"""

from automation_agent.skills.models import (
    ExpandedSkill,
    Skill,
    SkillParam,
    SkillRequirements,
)
from automation_agent.skills.registry import SkillRegistryImpl

__all__ = [
    "ExpandedSkill",
    "Skill",
    "SkillParam",
    "SkillRegistryImpl",
    "SkillRequirements",
]
