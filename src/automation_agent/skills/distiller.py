"""LLM-backed skill distiller for generalized skill observations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

import structlog

from automation_agent.config import AgentConfig
from automation_agent.shared_models import StepResult
from automation_agent.skills.models import Skill, SkillObservation

logger = structlog.get_logger(__name__)

_PROMPT_TEMPLATE_PATH = Path(__file__).parent / "prompts" / "distill_skill_updates.md"


class SkillDistiller:
    """Uses a separate LLM role to derive reusable skill observations."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self._prompt_template = _PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")

    async def distill(
        self,
        skill: Skill,
        goal: str,
        skill_context: str,
        trace: List[StepResult],
        run_id: str = "",
    ) -> List[SkillObservation]:
        """Extract generalized observations from one run."""
        prompt = self._build_prompt(skill, goal, skill_context, trace)
        raw = await self._call_llm(prompt)
        observations = self._parse_response(raw, run_id=run_id)
        logger.info(
            "skill_distillation_complete",
            skill_name=skill.name,
            observations=len(observations),
        )
        return observations

    def _build_prompt(
        self,
        skill: Skill,
        goal: str,
        skill_context: str,
        trace: List[StepResult],
    ) -> str:
        trace_lines: List[str] = []
        for index, result in enumerate(trace, start=1):
            trace_lines.append(
                f"{index}. {result.step.action}({result.step.params}) -> "
                f"{'SUCCESS' if result.success else 'FAIL'}"
            )
            if result.evidence:
                trace_lines.append(f"   evidence: {result.evidence}")
            if result.error:
                trace_lines.append(f"   error: {result.error}")
            if result.suggested_element:
                trace_lines.append(f"   suggested_element: {result.suggested_element}")
            if result.retry_strategies_used:
                trace_lines.append(
                    "   retry_strategies: " + ", ".join(result.retry_strategies_used)
                )
            if result.reflection_hint:
                trace_lines.append(f"   reflection_hint: {result.reflection_hint}")
            if result.reflection_observed:
                trace_lines.append(f"   reflection_observed: {result.reflection_observed}")
        prompt = self._prompt_template
        prompt = prompt.replace("{{skill_name}}", skill.name)
        prompt = prompt.replace("{{goal}}", goal)
        prompt = prompt.replace("{{skill_context}}", skill_context or "No skill context available")
        prompt = prompt.replace("{{trace}}", "\n".join(trace_lines) or "No trace available")
        return prompt

    async def _call_llm(self, prompt: str) -> str:
        from automation_agent.skills.llm_utils import call_skill_llm

        return await call_skill_llm(
            self.config, prompt, max_tokens=1024, temperature=0.0,
        )

    def _parse_response(self, response: str, run_id: str = "") -> List[SkillObservation]:
        text = response.strip()
        if text.startswith("```"):
            lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("skill_distillation_parse_failed", response=text[:200])
            return []
        items = payload.get("observations", [])
        if not isinstance(items, list):
            return []
        observations: List[SkillObservation] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            recommendation = str(item.get("recommendation", "")).strip()
            condition = str(item.get("condition", "")).strip()
            category = str(item.get("category", "")).strip()
            if not (recommendation and condition and category):
                continue
            confidence = item.get("confidence", 0.0)
            try:
                confidence_value = max(0.0, min(1.0, float(confidence)))
            except (TypeError, ValueError):
                confidence_value = 0.0
            observations.append(
                SkillObservation(
                    category=category,
                    condition=condition,
                    recommendation=recommendation,
                    rationale=str(item.get("rationale", "")).strip(),
                    confidence=confidence_value,
                    run_id=run_id,
                )
            )
        return observations
