"""Skill registry: loads, matches, and expands skill templates."""

import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog

from automation_agent.config import AgentConfig
from automation_agent.skills.loader import load_skill_from_file, parse_skill_file
from automation_agent.skills.matcher import match_skill
from automation_agent.skills.models import Skill
from automation_agent.skills.router import SkillRouter

std_logger = logging.getLogger(__name__)
logger = structlog.get_logger(__name__)


class SkillRegistryImpl:
    """Concrete implementation of the SkillRegistry protocol.

    Loads skill templates from .md files (YAML frontmatter + Markdown body),
    matches user prompts to skills via an LLM-driven router (with keyword
    fallback), and expands templates with parameter values.
    """

    def __init__(
        self,
        skill_dir: Optional[Path] = None,
        config: Optional[AgentConfig] = None,
    ) -> None:
        self._skills: Dict[str, Skill] = {}
        self._skill_dir = skill_dir or Path(__file__).parent / "library"
        self._config = config
        if self._skill_dir.is_dir():
            self.load_from_directory(self._skill_dir)
        # Router created lazily after skills are loaded
        self._router: Optional[SkillRouter] = None
        if self._config is not None:
            self._router = SkillRouter(self._config, self._skills)

    def load_from_directory(self, path: Path) -> None:
        """Load all .md skill files from a directory.

        Skills whose OS requirement does not match the current platform are
        silently skipped.

        Args:
            path: Directory containing .md skill files.
        """
        if not path.is_dir():
            return
        for md_file in sorted(path.glob("*.md")):
            try:
                skill = load_skill_from_file(md_file)
                # Gate by OS requirement
                if not _os_matches(skill.requires.os):
                    continue
                if skill.name in self._skills:
                    std_logger.warning(
                        "Duplicate skill name '%s': '%s' overwrites previous definition",
                        skill.name,
                        md_file,
                    )
                self._skills[skill.name] = skill
            except Exception:
                # Skip malformed files during loading; validate_all catches them
                continue
        # Rebuild router when skills change
        if self._config is not None:
            self._router = SkillRouter(self._config, self._skills)

    def load_from_string(self, content: str) -> Skill:
        """Load a single skill from raw file content.

        Args:
            content: YAML frontmatter + Markdown body.

        Returns:
            The parsed Skill (also registered internally).
        """
        skill = parse_skill_file(content)
        self._skills[skill.name] = skill
        # Rebuild router when skills change
        if self._config is not None:
            self._router = SkillRouter(self._config, self._skills)
        return skill

    async def match(self, prompt: str) -> Optional[Dict[str, Any]]:
        """Find a matching skill for the given prompt.

        Uses the LLM-driven SkillRouter when available, falling back to
        keyword matching if the router is not configured or the LLM call fails.

        Returns:
            Dict with 'skill_name', 'expanded_steps', 'params', or None.
        """
        # Primary path: LLM router
        if self._router is not None:
            router_result = await self._router.route(prompt)
            if router_result is not None:
                skill_name = router_result["skill_name"]
                params = router_result.get("params", {})
                expanded = self.expand(skill_name, params)
                skill = self._skills[skill_name]
                logger.info(
                    "🤔 Skill matched via LLM router",
                    skill_name=skill_name,
                    params=params,
                )
                return {
                    "skill_name": skill_name,
                    "expanded_steps": expanded or skill.steps_text,
                    "params": params,
                }

        # Fallback: keyword matching (no param extraction)
        result = match_skill(prompt, list(self._skills.values()))
        if result is None:
            return None
        skill, params = result
        # Fallback returns empty params — skip expand to avoid missing-param errors
        try:
            expanded = self.expand(skill.name, params)
        except ValueError:
            expanded = None
        logger.info(
            "🤔 Skill matched via keyword fallback",
            skill_name=skill.name,
            params=params,
        )
        return {
            "skill_name": skill.name,
            "expanded_steps": expanded or skill.steps_text,
            "params": params,
        }

    def list_skills(self) -> List[Dict[str, str]]:
        """List all available skills with name and description."""
        return [
            {"name": s.name, "description": s.description}
            for s in self._skills.values()
        ]

    def expand(self, skill_name: str, params: Dict[str, str]) -> Optional[str]:
        """Expand a skill template with parameter values.

        Replaces ``{{param}}`` placeholders in the steps text.

        Args:
            skill_name: Name of the skill to expand.
            params: Parameter values to substitute.

        Returns:
            Expanded steps text, or None if skill not found.

        Raises:
            ValueError: If a required parameter is missing.
        """
        skill = self._skills.get(skill_name)
        if skill is None:
            return None

        # Check for missing required params
        missing = [
            name
            for name, p in skill.parameters.items()
            if p.required and name not in params
        ]
        if missing:
            raise ValueError(
                f"Missing required parameter(s) for skill '{skill_name}': "
                + ", ".join(missing)
            )

        text = skill.steps_text

        # Single-pass replacement to avoid template injection
        # (user-supplied values containing {{...}} won't be re-expanded)
        def _replace_placeholder(m: re.Match) -> str:
            pname = m.group(1)
            if pname in params:
                return params[pname]
            # Optional param not provided — log warning and strip
            std_logger.warning(
                "Unexpanded placeholder '{{%s}}' in skill '%s' (stripped)",
                pname,
                skill_name,
            )
            return ""

        text = re.sub(r"\{\{(\w+)\}\}", _replace_placeholder, text)
        return text

    def validate_all(self) -> List[str]:
        """Validate all loaded skills. Returns list of error messages."""
        errors: List[str] = []
        for name, skill in self._skills.items():
            if not skill.name:
                errors.append(f"Skill at '{name}' missing 'name'")
            if not skill.description:
                errors.append(f"Skill '{name}' missing 'description'")
            if not skill.trigger_keywords:
                errors.append(f"Skill '{name}' has no trigger keywords")
            if not skill.steps_text:
                errors.append(f"Skill '{name}' has no Steps section")
            if not skill.success_condition:
                errors.append(f"Skill '{name}' missing 'success-condition'")
        return errors

    def get_skill(self, skill_name: str) -> Optional[Skill]:
        """Get a skill by name."""
        return self._skills.get(skill_name)


def validate_skill_file(path: Path) -> List[str]:
    """Validate a single skill file and return error messages."""
    errors: List[str] = []
    try:
        content = path.read_text(encoding="utf-8")
        skill = parse_skill_file(content)
        if not skill.trigger_keywords:
            errors.append(f"{path.name}: no trigger keywords")
        if not skill.steps_text:
            errors.append(f"{path.name}: no Steps section")
        if not skill.success_condition:
            errors.append(f"{path.name}: no success-condition")
    except Exception as exc:
        errors.append(f"{path.name}: {exc}")
    return errors


def _os_matches(required_os: str) -> bool:
    """Check if the required OS matches the current platform."""
    if not required_os:
        return True
    current = sys.platform  # "darwin", "linux", "win32"
    return current.startswith(required_os)
