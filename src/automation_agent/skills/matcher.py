"""Skill matching: map user prompts to skill templates."""

import re
from typing import Dict, List, Optional, Tuple

from automation_agent.skills.models import Skill


def match_skill(
    prompt: str, skills: List[Skill]
) -> Optional[Tuple[Skill, Dict[str, str]]]:
    """Match a user prompt to a skill by keyword matching.

    Matching algorithm:
    1. Lowercase the prompt.
    2. For each skill, count how many trigger keywords appear in the prompt.
    3. Return the skill with the most keyword hits (minimum 1 hit required).
    4. Extract parameters from the prompt using simple heuristics.

    Args:
        prompt: User's natural language prompt.
        skills: List of loaded skills to match against.

    Returns:
        Tuple of (matched_skill, extracted_params), or None if no match.
    """
    if not skills:
        return None

    prompt_lower = prompt.lower()
    best_skill: Optional[Skill] = None
    best_score = 0

    for skill in skills:
        score = 0
        for keyword in skill.trigger_keywords:
            if keyword.lower() in prompt_lower:
                score += 1
        if score > best_score:
            best_score = score
            best_skill = skill

    if best_skill is None or best_score == 0:
        return None

    params = extract_params(prompt, best_skill)
    return (best_skill, params)


def extract_params(prompt: str, skill: Skill) -> Dict[str, str]:
    """Extract parameter values from a prompt using simple heuristics.

    Strategies:
    1. Look for quoted strings in the prompt.
    2. For single required params, use the remaining text after keyword removal.

    Args:
        prompt: User's natural language prompt.
        skill: The matched skill.

    Returns:
        Dict mapping parameter names to extracted values.
    """
    params: Dict[str, str] = {}

    # Strategy 1: Extract quoted strings
    quoted = re.findall(r'"([^"]+)"', prompt)
    if not quoted:
        quoted = re.findall(r"'([^']+)'", prompt)

    required_params = [
        name for name, p in skill.parameters.items() if p.required
    ]

    if quoted:
        # Assign quoted strings to required params in order
        for i, pname in enumerate(required_params):
            if i < len(quoted):
                params[pname] = quoted[i]
        return params

    # Strategy 2: For skills with a single required param, try to extract
    # the meaningful part of the prompt after removing trigger keywords
    if len(required_params) == 1:
        remaining = prompt
        for keyword in skill.trigger_keywords:
            remaining = re.sub(re.escape(keyword), "", remaining, flags=re.IGNORECASE)
        # Remove common filler words (case-insensitive)
        filler = [
            "please", "can you", "could you", "i want to", "i need to",
            "i'd like to", "help me", "my", "the", "a", "an", "on", "for",
            "from", "to", "in", "with",
        ]
        for word in filler:
            remaining = re.sub(rf"\b{re.escape(word)}\b", "", remaining, flags=re.IGNORECASE)
        remaining = re.sub(r"\s+", " ", remaining).strip()
        if remaining:
            params[required_params[0]] = remaining

    return params
