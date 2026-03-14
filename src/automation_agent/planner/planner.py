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
        if provider == "gemini":
            return await self._call_gemini_llm(prompt)
        return await self._call_anthropic_llm(prompt)

    # Ollama structured output schema — guarantees valid JSON via GBNF grammar.
    _OLLAMA_FORMAT_SCHEMA = {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string"},
                        "params": {"type": "object"},
                        "verify": {"type": "string"},
                        "expected_observation": {"type": "string"},
                        "on_fail": {"type": "string"},
                    },
                    "required": ["action", "params", "verify"],
                },
            },
        },
        "required": ["steps"],
    }

    @staticmethod
    def _is_ollama(url: str) -> bool:
        """Heuristic: detect Ollama by port 11434."""
        return "11434" in url

    async def _call_local_llm(self, prompt: str) -> dict:
        """Call a local LLM endpoint.

        Uses Ollama native /api/chat with structured output (format schema) when
        an Ollama server is detected, otherwise falls back to OpenAI-compatible
        /v1/chat/completions.
        """
        import httpx

        if self._is_ollama(self.config.text_server_url):
            return await self._call_ollama_native(prompt)
        return await self._call_openai_compat(prompt)

    async def _call_ollama_native(self, prompt: str) -> dict:
        """Call Ollama native /api/chat with grammar-constrained JSON output."""
        import httpx

        url = f"{self.config.text_server_url}/api/chat"
        payload = {
            "model": self.config.text_model,
            "messages": [{"role": "user", "content": prompt}],
            "format": self._OLLAMA_FORMAT_SCHEMA,
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 4096},
        }
        async with httpx.AsyncClient(timeout=self.config.vision_server_timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            return {
                "content": data["message"]["content"],
                "usage": {
                    "input_tokens": data.get("prompt_eval_count", 0),
                    "output_tokens": data.get("eval_count", 0),
                },
            }

    async def _call_openai_compat(self, prompt: str) -> dict:
        """Call an OpenAI-compatible /v1/chat/completions endpoint (e.g. llama.cpp)."""
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

    async def _call_gemini_llm(self, prompt: str) -> dict:
        """Call Google Gemini API."""
        import asyncio

        from google import genai

        client = genai.Client(api_key=self.config.gemini_api_key)

        max_retries = 4
        base_delay = 1.0

        for attempt in range(max_retries + 1):
            try:
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=self.config.gemini_model,
                    contents=prompt,
                    config=genai.types.GenerateContentConfig(
                        max_output_tokens=8192,
                        temperature=0.0,
                    ),
                )
                text = response.text or ""
                usage = response.usage_metadata
                return {
                    "content": text,
                    "usage": {
                        "input_tokens": getattr(usage, "prompt_token_count", 0) or 0,
                        "output_tokens": getattr(usage, "candidates_token_count", 0) or 0,
                    },
                }
            except Exception as e:
                if attempt < max_retries and ("429" in str(e) or "503" in str(e)):
                    delay = base_delay * (2 ** attempt)
                    await asyncio.sleep(delay)
                    continue
                raise

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
                # AC-1: Smart fallback — map unknown actions instead of dropping.
                # Fix 1's alias map should catch all common LLM action names.
                # This only fires for truly novel actions.
                action_name = s.get("action", "")
                params = s.get("params", {})

                # Guard: actions that sound like waits/scrolls should NOT become clicks
                _no_click_keywords = {"wait", "scroll", "delay", "sleep", "pause"}
                if any(kw in action_name.lower() for kw in _no_click_keywords):
                    skipped.append(f"{action_name}: {e} (refused click fallback)")
                    continue

                if "text" in params:
                    fallback_action = "type_text"
                else:
                    fallback_action = "click"
                logger.warning(
                    "Unknown action '%s' mapped to '%s' (best-effort fallback)",
                    action_name,
                    fallback_action,
                    original_error=str(e),
                    step_description=s.get("verify", ""),
                )
                # Preserve the step with the fallback action
                s_copy = dict(s)
                s_copy["action"] = fallback_action
                try:
                    steps.append(ActionStep.from_dict(s_copy))
                except ValueError:
                    # Even the fallback failed — truly skip
                    skipped.append(f"{action_name}: {e}")

        raw_count = len(data["steps"])
        parsed_count = len(steps)
        if parsed_count < raw_count:
            dropped = raw_count - parsed_count
            logger.warning(
                "Plan step count mismatch: LLM returned %d steps but only %d parsed "
                "(%d dropped). Skipped: %s",
                raw_count,
                parsed_count,
                dropped,
                "; ".join(skipped),
            )

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

    async def check_infeasibility(
        self,
        goal: str,
        absent_elements: list[str],
        failure_history: list[str],
        frustration_summary: dict,
    ) -> dict:
        """Ask LLM whether the task is achievable given current state.

        Returns:
            {"infeasible": bool, "reason": str}
        """
        prompt = self._build_infeasibility_prompt(
            goal, absent_elements, failure_history, frustration_summary
        )
        response = await self._call_llm(prompt)
        return self._parse_infeasibility_response(response)

    def _build_infeasibility_prompt(
        self,
        goal: str,
        absent_elements: list[str],
        failure_history: list[str],
        frustration_summary: dict,
    ) -> str:
        """Build the infeasibility check prompt from the template."""
        template = self._load_prompt("check_infeasibility.md")
        prompt = template.replace("{{goal}}", goal)
        prompt = prompt.replace(
            "{{absent_elements}}",
            "\n".join(f"- {el}" for el in absent_elements) if absent_elements else "None",
        )
        prompt = prompt.replace(
            "{{failure_history}}",
            "\n".join(f"- {h}" for h in failure_history) if failure_history else "None",
        )
        prompt = prompt.replace(
            "{{same_state_count}}", str(frustration_summary.get("same_state_count", 0))
        )
        prompt = prompt.replace(
            "{{identical_action_count}}",
            str(frustration_summary.get("identical_action_count", 0)),
        )
        prompt = prompt.replace(
            "{{replan_count}}", str(frustration_summary.get("replan_count", 0))
        )
        return prompt

    def _parse_infeasibility_response(self, response: dict) -> dict:
        """Parse infeasibility check LLM response.

        Returns:
            {"infeasible": bool, "reason": str}
        """
        content = response["content"]
        json_str = content
        if "```json" in content:
            json_str = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            json_str = content.split("```")[1].split("```")[0]

        try:
            data = json.loads(json_str.strip())
        except json.JSONDecodeError:
            logger.warning(
                "Failed to parse infeasibility response, treating as infeasible",
                response=content[:200],
            )
            return {"infeasible": True, "reason": "Failed to parse LLM response"}

        return {
            "infeasible": bool(data.get("infeasible", True)),
            "reason": str(data.get("reason", "No reason provided")),
        }
