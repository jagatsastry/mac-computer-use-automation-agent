"""Data models for the skill registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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
    os: str = ""


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
    skill_id: str = ""  # Optional: from frontmatter `skill-id`
    tags: List[str] = field(default_factory=list)  # Optional: from frontmatter `tags`
    summary: str = ""  # Optional: from frontmatter `summary`
    parent_skill_id: str = ""  # Optional: from frontmatter `parent-skill-id`
    learned_tips_text: str = ""  # Optional: from `## Learned Tips` section


@dataclass
class SkillCard:
    """Compact routing card for LLM-based skill selection."""

    skill_id: str
    title: str
    summary: str
    tags: list[str]
    required_apps: list[str]
    required_os: str
    param_names: list[str] = field(default_factory=list)  # Parameter names for routing

    def __post_init__(self) -> None:
        if not self.skill_id or not self.skill_id.strip():
            raise ValueError("SkillCard requires non-empty skill_id")
        if not self.summary or not self.summary.strip():
            raise ValueError(
                f"SkillCard '{self.skill_id}' requires non-empty summary"
            )


@dataclass
class ExpandedSkill:
    """A skill with parameters substituted into the template."""

    skill: Skill
    expanded_text: str  # Steps with params substituted
    params: Dict[str, str]  # Actual parameter values used


@dataclass
class SkillObservation:
    """A generalized observation learned from a previous run."""

    category: str
    condition: str
    recommendation: str
    rationale: str = ""
    confidence: float = 0.0
    run_id: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    promoted: bool = False


@dataclass
class PromotionDecision:
    """Result of a skill librarian evaluation."""

    skill_name: str
    promotion_type: str  # "patch_parent" | "create_sibling" | "observation_only"
    reason: str
    confidence_score: float
    observation_keys: List[List[str]]  # [[category, recommendation], ...]
    run_id: str
    timestamp: str  # ISO 8601
    generated_tips: str = ""
    new_skill_id: str = ""
    new_skill_path: str = ""
    parent_skill_id: str = ""
    observation_count: int = 0
    distinct_run_count: int = 0
    pre_promotion_baseline: Dict = field(default_factory=dict)
