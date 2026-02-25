"""Data models for the skill registry."""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class SkillParam:
    """A single parameter for a skill template."""

    type: str  # "string", "int", etc.
    required: bool = True
    description: str = ""
    examples: List[str] = field(default_factory=list)


@dataclass
class SkillRequirements:
    """System requirements for a skill to be usable."""

    apps: List[str] = field(default_factory=list)
    os: str = "darwin"


@dataclass
class Skill:
    """A loaded skill template with parsed frontmatter and body sections."""

    name: str
    description: str
    trigger_keywords: List[str]
    parameters: Dict[str, SkillParam]
    requires: SkillRequirements
    success_condition: str
    max_retries: int = 3
    steps_text: str = ""  # Markdown body: Steps section
    error_recovery_text: str = ""  # Markdown body: Error Recovery section
    notes_text: str = ""  # Markdown body: Notes section
    raw_content: str = ""  # Full original file content


@dataclass
class ExpandedSkill:
    """A skill with parameters substituted into the template."""

    skill: Skill
    expanded_text: str  # Steps with params substituted
    params: Dict[str, str]  # Actual parameter values used
