"""ActionPlannerImpl — Plans multi-step action sequences using Claude API."""

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from automation_agent.config import AgentConfig
from automation_agent.shared_models import ActionPlan, ActionStep, StepResult


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
    ) -> ActionPlan:
        """Generate action plan. ALL steps must have non-empty 'verify' fields.

        Args:
            goal: Natural language description of what to accomplish.
            screen_description: Current screen state description.
            skill_context: Optional expanded skill template for context.

        Returns:
            ActionPlan with validated steps.

        Raises:
            ValueError: If the LLM response cannot be parsed or validation fails.
        """
        prompt = self._build_plan_prompt(goal, screen_description, skill_context)
        start = time.monotonic()
        response = await self._call_llm(prompt)
        duration_ms = int((time.monotonic() - start) * 1000)

        plan = self._parse_plan_response(response, goal)
        plan.planning_duration_ms = duration_ms

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
    ) -> ActionPlan:
        """Replan with history. Must produce DIFFERENT approach than what was tried.

        Args:
            goal: Original goal.
            screen_description: Current screen state.
            history: Results of previously executed steps.
            retry_strategies_used: Strategies already attempted.

        Returns:
            ActionPlan with a different approach.

        Raises:
            ValueError: If the LLM response cannot be parsed or validation fails.
        """
        prompt = self._build_replan_prompt(
            goal, screen_description, history, retry_strategies_used
        )
        response = await self._call_llm(prompt)
        plan = self._parse_plan_response(response, goal)

        errors = plan.validate()
        if errors:
            raise ValueError(f"Replan validation failed: {'; '.join(errors)}")

        return plan

    async def _call_llm(self, prompt: str) -> dict:
        """Call Anthropic Claude API.

        Args:
            prompt: The prompt to send to the LLM.

        Returns:
            Dict with 'content' (str) and 'usage' (dict with token counts).
        """
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=self.config.anthropic_api_key)
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

    def _build_plan_prompt(
        self,
        goal: str,
        screen_description: Optional[str],
        skill_context: Optional[str],
    ) -> str:
        """Build the planning prompt from the template.

        Args:
            goal: The user's natural language goal.
            screen_description: Current screen state, or empty string.
            skill_context: Optional skill context to inject.

        Returns:
            Formatted prompt string.
        """
        template = self._load_prompt("plan_from_prompt.md")
        prompt = template.replace("{{goal}}", goal)
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
    ) -> str:
        """Build the replanning prompt from the template.

        Args:
            goal: The original goal.
            screen_description: Current screen state.
            history: Execution history of previous steps.
            retry_strategies: List of strategies already tried.

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

        prompt = template.replace("{{goal}}", goal)
        prompt = prompt.replace("{{screen_description}}", screen_description)
        prompt = prompt.replace("{{history}}", history_text)
        prompt = prompt.replace("{{retry_strategies}}", strategies_text)
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

        if not isinstance(data, dict) or "steps" not in data:
            raise ValueError(
                f"LLM response missing 'steps' key: {content[:500]}"
            )

        steps = [ActionStep.from_dict(s) for s in data["steps"]]
        if not steps:
            raise ValueError("LLM returned empty steps list")

        return ActionPlan(
            steps=steps,
            goal=goal,
            raw_llm_response=content,
            token_usage=response.get("usage"),
        )
