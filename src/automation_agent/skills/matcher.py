"""Skill matching: lightweight keyword-based fallback for skill routing.

The primary routing path is the LLM-driven ``SkillRouter`` in ``router.py``.
This module provides a fast, no-network fallback used when the LLM server
is unavailable.  ``match_skill`` returns the best keyword-scored skill with
an **empty** params dict (no regex extraction) and the keyword hit count.
"""

import re
from typing import Dict, List, Optional, Tuple

import structlog

from automation_agent.skills.models import Skill

slog = structlog.get_logger(__name__)


def match_skill(
    prompt: str, skills: List[Skill]
) -> Optional[Tuple[Skill, Dict[str, str], int]]:
    """Match a user prompt to a skill by keyword scoring (fallback).

    This is a lightweight fallback for when the LLM router is unavailable.
    It returns the skill with the most keyword hits but does **not** attempt
    parameter extraction -- the caller receives an empty params dict.

    Args:
        prompt: User's natural language prompt.
        skills: List of loaded skills to match against.

    Returns:
        Tuple of (matched_skill, empty_params, keyword_hit_count), or None
        if no match.
    """
    if not skills:
        return None

    prompt_lower = prompt.lower()
    best_skill: Optional[Skill] = None
    best_score = 0

    for skill in skills:
        # AC-7: required-keywords gate — skip if none of the required
        # keywords appear in the prompt.
        # NOTE: \b is Unicode-aware (re.UNICODE default). Safe for ASCII keywords;
        # may produce surprising boundaries with non-ASCII text.
        required_kws = skill.metadata.get("required-keywords")
        if required_kws:
            req_set = {k.lower() for k in required_kws}
            if not any(
                re.search(r"\b" + re.escape(rk) + r"\b", prompt_lower)
                for rk in req_set
            ):
                slog.debug(
                    "skill_rejected_by_required_keywords",
                    skill_name=skill.metadata.get("name", ""),
                    required_keywords=skill.metadata.get("required-keywords", []),
                    prompt=prompt_lower[:100],
                )
                continue

        score = 0
        for keyword in skill.trigger_keywords:
            if keyword.lower() in prompt_lower:
                score += 1
        if score > best_score:
            best_score = score
            best_skill = skill

    if best_skill is None or best_score == 0:
        return None

    return (best_skill, {}, best_score)
