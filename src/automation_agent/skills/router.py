"""LLM-driven skill router: picks top-k skills and extracts params via an LLM call."""

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog

from automation_agent.config import AgentConfig
from automation_agent.shared_models import (
    MatchType,
    SkillRouteCandidate,
    SkillRouteResult,
)
from automation_agent.skills.models import Skill, SkillCard

logger = structlog.get_logger(__name__)

_PROMPT_TEMPLATE_PATH = Path(__file__).parent / "prompts" / "route_skill.md"

MIN_USEFUL_CONFIDENCE = 0.5


class SkillRouter:
    """Routes user prompts to skills using an LLM call.

    Supports two backends:
    - **Local (Qwen):** POST to ``{vision_server_url}/v1/chat/completions`` (text-only)
    - **Anthropic (Claude):** ``anthropic.AsyncAnthropic.messages.create()``

    The router builds a summary of all available skill cards, sends it along
    with the user prompt to the LLM, and parses a JSON response containing
    top-k matched skills with match_type, confidence, and reason.
    """

    def __init__(
        self,
        config: AgentConfig,
        skills: Dict[str, Skill],
        cards: Optional[List[SkillCard]] = None,
    ) -> None:
        self.config = config
        self.skills = skills
        self._cards = cards or []
        self._prompt_template = _PROMPT_TEMPLATE_PATH.read_text()

    async def route(self, prompt: str) -> Optional[SkillRouteResult]:
        """Route a user prompt to skills using an LLM.

        Args:
            prompt: The user's natural language prompt.

        Returns:
            SkillRouteResult with top-k candidates, or None if the LLM call
            failed or no skill matched.
        """
        summary = self._build_skills_summary()
        llm_prompt = self._build_prompt(prompt, summary)
        start = time.monotonic()
        try:
            response = await self._call_llm(llm_prompt)
        except Exception:
            logger.warning("Skill router LLM call failed, falling back", exc_info=True)
            return None
        elapsed = time.monotonic() - start
        logger.info(
            "Skill routing completed",
            duration_s=round(elapsed, 2),
            backend=self.config.model_provider.value,
        )
        return self._parse_response(response)

    def _build_skills_summary(self) -> str:
        """Build a markdown summary of all available skills for the LLM prompt."""
        if self._cards:
            return _format_cards_for_prompt(self._cards)
        # Fallback: build from raw Skill objects (backward compat)
        parts: list[str] = []
        for skill in self.skills.values():
            lines = [f"### {skill.name}", skill.description]
            if skill.parameters:
                param_lines: list[str] = []
                for pname, param in skill.parameters.items():
                    req = "(required)" if param.required else "(optional)"
                    desc = param.description or ""
                    examples = ""
                    if param.examples:
                        examples = (
                            f' Examples: {", ".join(repr(e) for e in param.examples)}'
                        )
                    param_lines.append(f"  {pname} {req} - {desc}{examples}")
                lines.append("Parameters:")
                lines.extend(param_lines)
            parts.append("\n".join(lines))
        return "\n\n".join(parts)

    def _build_prompt(self, user_prompt: str, skills_summary: str) -> str:
        """Substitute placeholders in the prompt template."""
        prompt = self._prompt_template.replace("{{skills_summary}}", skills_summary)
        prompt = prompt.replace("{{prompt}}", user_prompt)
        return prompt

    async def _call_llm(self, prompt: str) -> str:
        """Call local (Qwen) or Anthropic (Claude) based on config.model_provider."""
        if self.config.model_provider.value == "anthropic":
            return await self._call_anthropic(prompt)
        else:
            return await self._call_local(prompt)

    async def _call_local(self, prompt: str) -> str:
        """Text-only call to llama.cpp OpenAI-compatible API (no image)."""
        import httpx

        url = f"{self.config.vision_server_url}/v1/chat/completions"
        payload = {
            "model": self.config.vision_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 512,
            "temperature": 0.0,
            "stream": False,
        }

        async with httpx.AsyncClient(timeout=self.config.vision_server_timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            logger.debug("Local LLM response", tokens=len(content.split()))
            return content

    async def _call_anthropic(self, prompt: str) -> str:
        """Call Anthropic Claude API for skill routing."""
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=self.config.anthropic_api_key)
        message = await client.messages.create(
            model=self.config.anthropic_model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        content = message.content[0].text
        logger.debug(
            "Anthropic LLM response",
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )
        return content

    def _parse_response(self, response: str) -> Optional[SkillRouteResult]:
        """Parse the LLM JSON response into a SkillRouteResult.

        Returns:
            SkillRouteResult with candidates, or None if no match or unparseable.
        """
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning(
                "Could not parse skill router response as JSON",
                response=text[:200],
            )
            return None

        if not isinstance(data, dict):
            return None

        matches = data.get("matches")
        if not isinstance(matches, list):
            # Try legacy single-match format for backward compat
            return self._parse_legacy_response(data)

        if not matches:
            logger.info("No skill matched by LLM router")
            return None

        candidates: list[SkillRouteCandidate] = []
        for match in matches[:3]:
            if not isinstance(match, dict):
                continue
            skill_id = match.get("skill_id", "")
            if not skill_id or skill_id not in self.skills:
                logger.debug(
                    "Skipping unknown skill_id from router",
                    skill_id=skill_id,
                )
                continue
            try:
                match_type = match.get("match_type", "generic")
                confidence = float(match.get("confidence", 0.5))
                reason = str(match.get("reason", ""))
                candidates.append(
                    SkillRouteCandidate(
                        skill_id=skill_id,
                        match_type=MatchType(match_type),
                        confidence=confidence,
                        reason=reason,
                    )
                )
            except (ValueError, KeyError):
                continue

        if not candidates:
            logger.info("No valid skill candidates from LLM router")
            return None

        # Extract params from the first (highest-confidence) match
        first_match = matches[0] if matches else {}
        params = first_match.get("params", {}) if isinstance(first_match, dict) else {}

        logger.info(
            "Skills matched by LLM router",
            candidates=[(c.skill_id, c.match_type.value, c.confidence) for c in candidates],
        )
        return SkillRouteResult(candidates=candidates, params=params)

    def _parse_legacy_response(self, data: Dict[str, Any]) -> Optional[SkillRouteResult]:
        """Parse the old single-match format for backward compatibility."""
        skill_name = data.get("skill_name")
        params = data.get("params", {})

        if skill_name is None or skill_name not in self.skills:
            return None

        candidate = SkillRouteCandidate(
            skill_id=skill_name,
            match_type=MatchType.DIRECT,
            confidence=0.9,
            reason="Legacy single-match router response",
        )
        return SkillRouteResult(candidates=[candidate], params=params)


def _format_cards_for_prompt(cards: list[SkillCard]) -> str:
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
        if card.param_names:
            lines.append(f"Parameters: {', '.join(card.param_names)}")
        if card.required_apps:
            lines.append(f"Requires apps: {', '.join(card.required_apps)}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)
