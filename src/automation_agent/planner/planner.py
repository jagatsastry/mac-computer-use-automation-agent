"""ActionPlannerImpl — Plans multi-step action sequences using Claude API."""

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

import structlog

from automation_agent.config import AgentConfig
from automation_agent.shared_models import ActionPlan, ActionStep, ReplanPatch, StepResult

logger = structlog.get_logger(__name__)


class ActionPlannerImpl:
    """Plans multi-step action sequences using Claude API.

    Implements the ActionPlanner protocol defined in protocols.py.
    """

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self._prompts_dir = Path(__file__).parent / "prompts"

    async def plan(
        self,
        goal: str,
        screen_description: str = "",
        skill_context: Optional[str] = None,
        desktop_context: str = "",
    ) -> ActionPlan:
        """Generate action plan. ALL steps must have non-empty 'verify' fields.

        Args:
            goal: Natural language description of what to accomplish.
            screen_description: Current screen state description.
            skill_context: Optional expanded skill template for context.
            desktop_context: Structured desktop state from ContextMonitor.

        Returns:
            ActionPlan with validated steps.

        Raises:
            ValueError: If the LLM response cannot be parsed or validation fails.
        """
        prompt = self._build_plan_prompt(
            goal, screen_description, skill_context, desktop_context
        )
        logger.info("📋 Planning started", goal=goal)
        start = time.monotonic()
        response = await self._call_llm(prompt)
        duration_ms = int((time.monotonic() - start) * 1000)

        plan = self._parse_plan_response(response, goal)
        plan.planning_duration_ms = duration_ms

        logger.info(
            "📋 Planning complete",
            duration_ms=duration_ms,
            input_tokens=response.get("usage", {}).get("input_tokens"),
            output_tokens=response.get("usage", {}).get("output_tokens"),
        )

        errors = plan.validate()
        if errors:
            raise ValueError(f"Plan validation failed: {'; '.join(errors)}")

        return plan

    async def replan(
        self,
        goal: str,
        screen_description: str,
        history: List[StepResult],
        retry_strategies_used: List[str],
        desktop_context: str = "",
        skill_context: Optional[str] = None,
        absent_elements: Optional[List[str]] = None,
    ) -> ActionPlan:
        """Replan with history. Must produce DIFFERENT approach than what was tried.

        Args:
            goal: Original goal.
            screen_description: Current screen state.
            history: Results of previously executed steps.
            retry_strategies_used: Strategies already attempted.
            desktop_context: Structured desktop state from ContextMonitor.
            skill_context: Optional expanded skill template for context.

        Returns:
            ActionPlan with a different approach.

        Raises:
            ValueError: If the LLM response cannot be parsed or validation fails.
        """
        prompt = self._build_replan_prompt(
            goal, screen_description, history, retry_strategies_used,
            desktop_context, skill_context, absent_elements,
        )
        logger.info("🔄 Replanning started", goal=goal)
        start = time.monotonic()
        response = await self._call_llm(prompt)
        duration_ms = int((time.monotonic() - start) * 1000)

        plan = self._parse_plan_response(response, goal)
        plan.planning_duration_ms = duration_ms

        logger.info(
            "🔄 Replanning complete",
            duration_ms=duration_ms,
            input_tokens=response.get("usage", {}).get("input_tokens"),
            output_tokens=response.get("usage", {}).get("output_tokens"),
        )

        errors = plan.validate()
        if errors:
            raise ValueError(f"Replan validation failed: {'; '.join(errors)}")

        return plan

    async def _call_llm(self, prompt: str) -> dict:
        """Call LLM for planning — routes to Anthropic or local based on config.

        Args:
            prompt: The prompt to send to the LLM.

        Returns:
            Dict with 'content' (str) and 'usage' (dict with token counts).
        """
        provider = getattr(self.config.model_provider, "value", self.config.model_provider)
        if provider == "local":
            return await self._call_local_llm(prompt)
        return await self._call_anthropic_llm(prompt)

    async def _call_local_llm(self, prompt: str) -> dict:
        """Call a local OpenAI-compatible endpoint (e.g. Ollama)."""
        import httpx

        url = f"{self.config.text_server_url}/v1/chat/completions"
        payload = {
            "model": self.config.text_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 4096,
            "temperature": 0.0,
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=self.config.vision_server_timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            choice = data["choices"][0]
            usage = data.get("usage", {})
            return {
                "content": choice["message"]["content"],
                "usage": {
                    "input_tokens": usage.get("prompt_tokens", 0),
                    "output_tokens": usage.get("completion_tokens", 0),
                },
            }

    async def _call_anthropic_llm(self, prompt: str) -> dict:
        """Call Anthropic Claude API with retry and exponential backoff."""
        import asyncio

        import anthropic

        client = anthropic.AsyncAnthropic(api_key=self.config.anthropic_api_key)

        max_retries = 4
        base_delay = 1.0

        for attempt in range(max_retries + 1):
            try:
                message = await client.messages.create(
                    model=self.config.anthropic_model,
                    max_tokens=4096,
                    messages=[{"role": "user", "content": prompt}],
                )
                return {
                    "content": message.content[0].text,
                    "usage": {
                        "input_tokens": message.usage.input_tokens,
                        "output_tokens": message.usage.output_tokens,
                    },
                }
            except anthropic.APIStatusError as e:
                if e.status_code in (429, 529) and attempt < max_retries:
                    delay = base_delay * (2 ** attempt)
                    await asyncio.sleep(delay)
                    continue
                raise

    def _build_plan_prompt(
        self,
        goal: str,
        screen_description: Optional[str],
        skill_context: Optional[str],
        desktop_context: str = "",
    ) -> str:
        """Build the planning prompt from the template.

        Args:
            goal: The user's natural language goal.
            screen_description: Current screen state, or empty string.
            skill_context: Optional skill context to inject.
            desktop_context: Structured desktop state from ContextMonitor.

        Returns:
            Formatted prompt string.
        """
        template = self._load_prompt("plan_from_prompt.md")
        prompt = template.replace("{{goal}}", goal)
        prompt = prompt.replace("{{desktop_context}}", desktop_context or "")
        prompt = prompt.replace(
            "{{screen_description}}", screen_description or "Not available"
        )
        prompt = prompt.replace(
            "{{skill_context}}", skill_context or "No skill context available"
        )
        return prompt

    def _build_replan_prompt(
        self,
        goal: str,
        screen_description: str,
        history: List[StepResult],
        retry_strategies: List[str],
        desktop_context: str = "",
        skill_context: Optional[str] = None,
        absent_elements: Optional[List[str]] = None,
    ) -> str:
        """Build the replanning prompt from the template.

        Args:
            goal: The original goal.
            screen_description: Current screen state.
            history: Execution history of previous steps.
            retry_strategies: List of strategies already tried.
            desktop_context: Structured desktop state from ContextMonitor.
            skill_context: Optional expanded skill template for context.

        Returns:
            Formatted prompt string.
        """
        template = self._load_prompt("replan_from_state.md")
        history_text = "\n".join(
            [
                f"- Step {i}: {sr.step.action}({sr.step.params}) -> "
                f"{'SUCCESS' if sr.success else 'FAILED'}: {sr.evidence}"
                for i, sr in enumerate(history)
            ]
        )
        strategies_text = (
            ", ".join(retry_strategies) if retry_strategies else "None yet"
        )

        # AC-5: Absent elements context
        if absent_elements:
            absent_text = (
                "The following UI elements were confirmed absent from the current page:\n"
                + "\n".join(f"- {el}" for el in absent_elements)
                + "\n\nDo not generate steps that depend on these elements. "
                "Consider that the task may be impossible in the current page state. "
                "If the task cannot be completed, emit a done step with abort_reason explaining why."
            )
        else:
            absent_text = ""

        prompt = template.replace("{{goal}}", goal)
        prompt = prompt.replace("{{desktop_context}}", desktop_context or "")
        prompt = prompt.replace(
            "{{skill_context}}", skill_context or "No skill context available"
        )
        prompt = prompt.replace("{{screen_description}}", screen_description)
        prompt = prompt.replace("{{history}}", history_text)
        prompt = prompt.replace("{{retry_strategies}}", strategies_text)
        prompt = prompt.replace("{{absent_elements}}", absent_text)
        return prompt

    def _load_prompt(self, name: str) -> str:
        """Load a prompt template from disk.

        Args:
            name: Filename of the prompt template.

        Returns:
            The prompt template text.

        Raises:
            FileNotFoundError: If the prompt file does not exist.
        """
        path = self._prompts_dir / name
        if not path.exists():
            raise FileNotFoundError(f"Prompt file not found: {path}")
        return path.read_text()

    def _parse_plan_response(self, response: dict, goal: str) -> ActionPlan:
        """Parse LLM response into an ActionPlan.

        Handles JSON responses that may be wrapped in markdown code blocks.

        Args:
            response: Dict with 'content' and optional 'usage'.
            goal: The original goal string.

        Returns:
            ActionPlan instance.

        Raises:
            ValueError: If the response cannot be parsed as valid JSON with steps.
        """
        content = response["content"]

        # Extract JSON from response (may be wrapped in ```json ... ```)
        json_str = content
        if "```json" in content:
            json_str = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            json_str = content.split("```")[1].split("```")[0]

        try:
            data = json.loads(json_str.strip())
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Failed to parse LLM response as JSON: {e}\n"
                f"Response: {content[:500]}"
            )

        # Extract derived_skill_patch BEFORE the steps check so it is
        # captured even if the response is otherwise invalid (for logging).
        replan_patch = None
        if isinstance(data, dict):
            patch_dict = data.get("derived_skill_patch")
            if patch_dict is not None:
                try:
                    replan_patch = ReplanPatch.from_dict(patch_dict)
                except Exception:
                    logger.debug(
                        "Failed to parse derived_skill_patch",
                        exc_info=True,
                    )
                    replan_patch = ReplanPatch()
                logger.debug(
                    "Extracted replan patch before steps check",
                    has_patch=replan_patch is not None,
                )

        if not isinstance(data, dict) or "steps" not in data:
            raise ValueError(
                f"LLM response missing 'steps' key: {content[:500]}"
            )

        steps = []
        skipped = []
        for s in data["steps"]:
            try:
                steps.append(ActionStep.from_dict(s))
            except ValueError as e:
                # LLM returned an invalid action — skip it rather than crash
                skipped.append(f"{s.get('action', '?')}: {e}")

        if not steps:
            if skipped:
                raise ValueError(
                    f"LLM returned only invalid steps: {'; '.join(skipped)}"
                )
            raise ValueError("LLM returned empty steps list")

        return ActionPlan(
            steps=steps,
            goal=goal,
            raw_llm_response=content,
            token_usage=response.get("usage"),
            replan_patch=replan_patch,
        )
