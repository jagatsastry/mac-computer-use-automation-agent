"""Skill file parser: YAML frontmatter + Markdown body."""

import re
from pathlib import Path
from typing import Optional

import yaml

from automation_agent.skills.models import Skill, SkillParam, SkillRequirements


def parse_skill_file(content: str) -> Skill:
    """Parse a skill file with YAML frontmatter and Markdown body.

    File format::

        ---
        name: skill-name
        description: ...
        trigger-keywords: [...]
        parameters:
          param_name:
            type: string
            required: true
            description: ...
            examples: [...]
        requires:
          apps: [App1]
          os: darwin
        success-condition: ...
        max-retries: 3
        ---

        ## Steps
        1. Do thing
           - verify: thing done
        ...

        ## Error Recovery
        - If X: do Y
        ...

        ## Notes
        - Additional notes
        ...

    Args:
        content: Raw file content.

    Returns:
        Parsed Skill dataclass.

    Raises:
        ValueError: If frontmatter is missing or required fields are absent.
    """
    frontmatter, body = _split_frontmatter(content)
    meta = yaml.safe_load(frontmatter)
    if not isinstance(meta, dict):
        raise ValueError("YAML frontmatter must be a mapping")

    # Required fields
    name = meta.get("name")
    if not name:
        raise ValueError("Skill file missing required field: 'name'")
    description = meta.get("description", "")
    if not description:
        raise ValueError("Skill file missing required field: 'description'")

    trigger_keywords = meta.get("trigger-keywords", [])
    if not isinstance(trigger_keywords, list):
        raise ValueError("'trigger-keywords' must be a list")

    # Parameters
    raw_params = meta.get("parameters", {})
    parameters = _parse_parameters(raw_params)

    # Requirements
    raw_requires = meta.get("requires", {})
    requires = _parse_requirements(raw_requires)

    success_condition = meta.get("success-condition", "")
    max_retries = int(meta.get("max-retries", 3))

    # Parse body sections
    steps_text = _extract_section(body, "Steps")
    error_recovery_text = _extract_section(body, "Error Recovery")
    notes_text = _extract_section(body, "Notes")

    # Optional adaptive-skill fields
    skill_id = meta.get("skill-id", "")
    tags = meta.get("tags", [])
    if not isinstance(tags, list):
        tags = []
    summary = meta.get("summary", "")
    parent_skill_id = meta.get("parent-skill-id", "")

    # Extract Learned Tips section
    learned_tips_text = _extract_section(body, "Learned Tips")

    # Collect extra frontmatter keys into metadata dict
    _known_keys = {
        "name", "description", "trigger-keywords", "parameters", "requires",
        "success-condition", "max-retries", "skill-id", "tags", "summary",
        "parent-skill-id",
    }
    metadata = {k: v for k, v in meta.items() if k not in _known_keys}

    return Skill(
        name=name,
        description=description,
        trigger_keywords=trigger_keywords,
        parameters=parameters,
        requires=requires,
        success_condition=success_condition,
        max_retries=max_retries,
        steps_text=steps_text,
        error_recovery_text=error_recovery_text,
        notes_text=notes_text,
        raw_content=content,
        skill_id=skill_id,
        tags=tags,
        summary=summary,
        parent_skill_id=parent_skill_id,
        learned_tips_text=learned_tips_text,
        metadata=metadata,
    )


def load_skill_from_file(path: Path) -> Skill:
    """Load and parse a single skill file.

    Args:
        path: Path to a .md skill file.

    Returns:
        Parsed Skill.
    """
    content = path.read_text(encoding="utf-8")
    return parse_skill_file(content)


def _split_frontmatter(content: str) -> tuple:
    """Split YAML frontmatter from Markdown body.

    Returns:
        Tuple of (frontmatter_str, body_str).

    Raises:
        ValueError: If frontmatter delimiters are not found.
    """
    pattern = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)", re.DOTALL)
    match = pattern.match(content.strip())
    if not match:
        raise ValueError(
            "Could not find YAML frontmatter (expected '---' delimiters)"
        )
    return match.group(1), match.group(2)


def _parse_parameters(raw: Optional[dict]) -> dict:
    """Parse parameter definitions from frontmatter."""
    if not raw or not isinstance(raw, dict):
        return {}
    params = {}
    for pname, pdef in raw.items():
        if not isinstance(pdef, dict):
            continue
        params[pname] = SkillParam(
            type=pdef.get("type", "string"),
            required=bool(pdef.get("required", True)),
            description=pdef.get("description", ""),
            examples=pdef.get("examples", []),
        )
    return params


def _parse_requirements(raw: Optional[dict]) -> SkillRequirements:
    """Parse requirements from frontmatter."""
    if not raw or not isinstance(raw, dict):
        return SkillRequirements()
    return SkillRequirements(
        apps=raw.get("apps", []) or [],
        os=raw.get("os", ""),
    )


def _extract_section(body: str, heading: str) -> str:
    """Extract content under a ## heading until the next ## heading or end of text.

    Args:
        body: Markdown body text.
        heading: Section heading (without ##).

    Returns:
        Section content (stripped), or empty string if not found.
    """
    # Match ## <heading> followed by content until next ## or end
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(body)
    if not match:
        return ""
    return match.group(1).strip()
