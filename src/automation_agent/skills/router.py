"""LLM-driven skill router: picks the best skill and extracts params via an LLM call."""

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

import structlog

from automation_agent.config import AgentConfig
from automation_agent.skills.models import Skill

logger = structlog.get_logger(__name__)

_PROMPT_TEMPLATE_PATH = Path(__file__).parent / "prompts" / "route_skill.md"


class SkillRouter:
    """Routes user prompts to skills using an LLM call.

    Supports two backends:
    - **Local (Qwen):** POST to ``{vision_server_url}/v1/chat/completions`` (text-only)
    - **Anthropic (Claude):** ``anthropic.AsyncAnthropic.messages.create()``

    The router builds a summary of all available skills, sends it along with
    the user prompt to the LLM, and parses a JSON response containing the
    matched skill name and extracted parameters.
    """

    def __init__(self, config: AgentConfig, skills: Dict[str, Skill]) -> None:
        self.config = config
        self.skills = skills
        self._prompt_template = _PROMPT_TEMPLATE_PATH.read_text()

    async def route(self, prompt: str) -> Optional[Dict[str, Any]]:
        """Route a user prompt to a skill using an LLM.

        Args:
            prompt: The user's natural language prompt.

        Returns:
            Dict with ``skill_name`` (str) and ``params`` (dict), or None if
            no skill matched or the LLM call failed.
        """
        summary = self._build_skills_summary()
        llm_prompt = self._build_prompt(prompt, summary)
        start = time.monotonic()
        try:
            response = await self._call_llm(llm_prompt)
        except Exception:
            logger.warning("🤔 Skill router LLM call failed, falling back", exc_info=True)
            return None
        elapsed = time.monotonic() - start
        logger.info(
            "⚡ Skill routing completed",
            duration_s=round(elapsed, 2),
            backend=self.config.model_provider.value,
        )
        return self._parse_response(response)

    def _build_skills_summary(self) -> str:
        """Build a markdown summary of all available skills for the LLM prompt."""
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
                        examples = f' Examples: {", ".join(repr(e) for e in param.examples)}'
                    param_lines.append(
                        f"  {pname} {req} - {desc}{examples}"
                    )
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
        """Text-only call to llama.cpp OpenAI-compatible API (no image).

        Uses the same ``vision_server_url`` as the vision coordinator but sends
        only a text message (no ``image_url`` content part).
        """
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
            logger.debug(
                "🐛 Local LLM response",
                tokens=len(content.split()),
            )
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
            "🐛 Anthropic LLM response",
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )
        return content

    def _parse_response(self, response: str) -> Optional[Dict[str, Any]]:
        """Parse the LLM JSON response into a skill routing result.

        Returns:
            Dict with ``skill_name`` and ``params``, or None if no match or
            the response is unparseable.
        """
        # Strip markdown fences if present
        text = response.strip()
        if text.startswith("```"):
            # Remove ```json ... ``` wrapper
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("🤔 Could not parse skill router response as JSON", response=text[:200])
            return None

        if not isinstance(data, dict):
            return None

        skill_name = data.get("skill_name")
        params = data.get("params", {})

        if skill_name is None:
            logger.info("🤔 No skill matched by LLM router")
            return None

        # Validate skill_name exists
        if skill_name not in self.skills:
            logger.warning(
                "🤔 LLM returned unknown skill name",
                skill_name=skill_name,
                available=list(self.skills.keys()),
            )
            return None

        logger.info(
            "🤔 Skill matched by LLM router",
            skill_name=skill_name,
            params=params,
        )
        return {"skill_name": skill_name, "params": params}
