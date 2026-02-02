"""Main automation agent with agentic loop."""

import asyncio
import json
from typing import List, Optional

from ..llm.client import OllamaClient
from ..actions.simple import ClickAction
from .models import (
    ActionResult,
    ExecutionResult,
    HistoryEntry,
    Intent,
    NextAction,
)
from .intent_parser import IntentParser
from .action_registry import ActionRegistry
from .observer import ScreenObserver


# System prompt for agent planning
AGENT_PLANNER_SYSTEM_PROMPT = """You are an automation agent planning the next action to achieve a goal.

Given:
1. The user's goal
2. History of observations and actions taken
3. Current screen state

Decide the next action to take.

## Response Format
Return ONLY valid JSON in one of these formats:

If goal is achieved:
{"complete": true, "reasoning": "Why the goal is achieved"}

If more actions needed:
{
    "action": "ACTION_TYPE",
    "params": {...},
    "reasoning": "Why this action"
}

## Available Actions
- activate_app: {"app_name": "App Name"}
- open_url: {"url": "https://...", "browser": "Safari"}
- quit_app: {"app_name": "App Name"}
- type_text: {"text": "text to type"}
- press_key: {"keys": ["command", "key"]}
- click: {"x": number, "y": number}
- click_element: {"description": "element to click"}

## Rules
1. Base decisions on current observations, not assumptions
2. If an action failed, try a different approach
3. Don't repeat the same failing action
4. Declare completion only when goal is visibly achieved
5. Keep actions simple and atomic

Output ONLY JSON, no explanation."""


class AutomationAgent:
    """
    Intelligent automation agent that can handle both simple and complex tasks.

    For simple tasks (no observation needed):
    - Parses intent and executes steps sequentially

    For complex tasks (observation needed):
    - Uses agentic loop: observe → think → act → check
    """

    def __init__(
        self,
        parser: IntentParser,
        observer: ScreenObserver,
        registry: ActionRegistry,
        llm_client: OllamaClient,
        text_model: str = "gemma2:9b",
        max_iterations: int = 20,
        action_delay: float = 1.0,
    ):
        """
        Initialize automation agent.

        Args:
            parser: Intent parser for natural language commands
            observer: Screen observer for vision-based understanding
            registry: Action registry for executing actions
            llm_client: LLM client for planning
            text_model: Model for planning decisions
            max_iterations: Maximum iterations for agentic loop
            action_delay: Delay between actions (seconds)
        """
        self.parser = parser
        self.observer = observer
        self.registry = registry
        self.llm = llm_client
        self.text_model = text_model
        self.max_iterations = max_iterations
        self.action_delay = action_delay

    async def execute(self, user_prompt: str) -> ExecutionResult:
        """
        Execute a user's natural language command.

        Automatically determines whether to use simple sequential
        execution or the full agentic loop based on task complexity.

        Args:
            user_prompt: Natural language command from user

        Returns:
            ExecutionResult with success status and details
        """
        # Parse the user's intent
        intent = await self.parser.parse(user_prompt)

        # Choose execution mode based on complexity
        if intent.requires_observation:
            return await self._execute_agentic(user_prompt, intent)
        else:
            return await self._execute_sequential(intent)

    async def _execute_sequential(self, intent: Intent) -> ExecutionResult:
        """
        Execute a simple task with sequential steps.

        No observation/vision used - just execute parsed steps in order.

        Args:
            intent: Parsed intent with action steps

        Returns:
            ExecutionResult with step-by-step results
        """
        results: List[ActionResult] = []

        for step in intent.steps:
            # Get action from registry
            action = self.registry.get_action(step.action, step.params)

            if action is None:
                # Unknown action type
                result = ActionResult(
                    success=False,
                    action=step.action,
                    params=step.params,
                    error=f"Unknown action type: {step.action}",
                )
                results.append(result)
                break

            # Execute the action
            try:
                action_result = await action.execute()

                result = ActionResult(
                    success=action_result.success,
                    action=step.action,
                    params=step.params,
                    output=getattr(action_result, "output", ""),
                    error=getattr(action_result, "error", None) if not action_result.success else None,
                )
                results.append(result)

                if not action_result.success:
                    # Stop on first failure
                    break

                # Brief delay between actions
                if self.action_delay > 0:
                    await asyncio.sleep(self.action_delay)

            except Exception as e:
                result = ActionResult(
                    success=False,
                    action=step.action,
                    params=step.params,
                    error=str(e),
                )
                results.append(result)
                break

        # Determine overall success
        all_success = all(r.success for r in results)

        return ExecutionResult(
            success=all_success,
            message="All steps completed successfully" if all_success else "Execution stopped due to error",
            steps=results,
            error=results[-1].error if results and not results[-1].success else None,
        )

    async def _execute_agentic(
        self, goal: str, intent: Intent
    ) -> ExecutionResult:
        """
        Execute a complex task using the agentic loop.

        Loop: observe → think → act → check until goal achieved or max iterations.

        Args:
            goal: Original user goal
            intent: Initial parsed intent (may be refined during execution)

        Returns:
            ExecutionResult with full execution history
        """
        history: List[HistoryEntry] = []
        results: List[ActionResult] = []

        # First, execute any initial steps that don't require observation
        # (e.g., opening the browser before searching)
        for step in intent.steps:
            if step.action in ["activate_app", "open_url", "type_text", "press_key"]:
                action = self.registry.get_action(step.action, step.params)
                if action:
                    action_result = await action.execute()
                    result = ActionResult(
                        success=action_result.success,
                        action=step.action,
                        params=step.params,
                        output=getattr(action_result, "output", ""),
                    )
                    results.append(result)
                    history.append(HistoryEntry.from_action(result))
                    await asyncio.sleep(self.action_delay)

        # Now enter the agentic loop for vision-based actions
        for iteration in range(self.max_iterations):
            # OBSERVE: What's on screen?
            observation = await self.observer.observe(
                "Describe the current screen state. What app is open? What can you see?"
            )
            history.append(HistoryEntry.from_observation(observation))

            # THINK: What should I do next?
            next_action = await self._plan_next_action(goal, history)

            # CHECK: Is goal achieved?
            if next_action.is_complete:
                return ExecutionResult(
                    success=True,
                    message=f"Goal achieved: {next_action.reasoning}",
                    steps=results,
                    iterations=iteration + 1,
                )

            # ACT: Execute the planned action
            result = await self._execute_action(next_action)
            results.append(result)
            history.append(HistoryEntry.from_action(result))

            if not result.success:
                # Don't stop on failure - let agent try alternative
                pass

            # Wait for UI to update
            await asyncio.sleep(self.action_delay)

        # Max iterations reached
        return ExecutionResult(
            success=False,
            message="Max iterations reached without completing goal",
            steps=results,
            iterations=self.max_iterations,
        )

    async def _plan_next_action(
        self, goal: str, history: List[HistoryEntry]
    ) -> NextAction:
        """
        Ask LLM to plan the next action based on goal and history.

        Args:
            goal: Original user goal
            history: List of past observations and actions

        Returns:
            NextAction with planned action or completion status
        """
        # Format history for prompt
        history_text = self._format_history(history)

        prompt = f"""Goal: {goal}

History:
{history_text}

Based on the current screen state and goal, what is the next action?
If the goal is achieved, respond with {{"complete": true, "reasoning": "..."}}
Otherwise respond with the next action."""

        response = await self.llm.generate(
            model=self.text_model,
            prompt=prompt,
            system=AGENT_PLANNER_SYSTEM_PROMPT,
            format="json",
        )

        # Parse response
        try:
            data = json.loads(response)
            return NextAction.from_dict(data)
        except json.JSONDecodeError:
            # Try to extract JSON
            import re
            match = re.search(r"\{[\s\S]*\}", response)
            if match:
                try:
                    data = json.loads(match.group(0))
                    return NextAction.from_dict(data)
                except json.JSONDecodeError:
                    pass

            # Fallback - couldn't parse, assume not complete
            return NextAction(
                action="",
                reasoning="Could not parse LLM response",
                is_complete=False,
            )

    async def _execute_action(self, next_action: NextAction) -> ActionResult:
        """
        Execute a planned action.

        Args:
            next_action: Action to execute

        Returns:
            ActionResult with execution outcome
        """
        action_type = next_action.action
        params = next_action.params

        # Special handling for click_element - need to find coordinates first
        if action_type == "click_element":
            description = params.get("description", "")
            coords = await self.observer.find_element(description)

            if coords is None:
                return ActionResult(
                    success=False,
                    action=action_type,
                    params=params,
                    error=f"Could not find element: {description}",
                )

            # Create click action with found coordinates
            click_action = ClickAction(x=coords.center_x, y=coords.center_y)
            try:
                click_result = await click_action.execute()
                return ActionResult(
                    success=click_result.success,
                    action="click",
                    params={"x": coords.center_x, "y": coords.center_y, "target": description},
                    output=f"Clicked at ({coords.center_x}, {coords.center_y})",
                    error=click_result.error if hasattr(click_result, 'error') else None,
                )
            except Exception as e:
                return ActionResult(
                    success=False,
                    action="click",
                    params={"x": coords.center_x, "y": coords.center_y},
                    error=str(e),
                )

        # Standard action execution
        action = self.registry.get_action(action_type, params)

        if action is None:
            return ActionResult(
                success=False,
                action=action_type,
                params=params,
                error=f"Unknown action type: {action_type}",
            )

        try:
            action_result = await action.execute()
            return ActionResult(
                success=action_result.success,
                action=action_type,
                params=params,
                output=getattr(action_result, "output", ""),
                error=getattr(action_result, "error", None) if not action_result.success else None,
            )
        except Exception as e:
            return ActionResult(
                success=False,
                action=action_type,
                params=params,
                error=str(e),
            )

    def _format_history(self, history: List[HistoryEntry]) -> str:
        """Format history entries for LLM prompt."""
        if not history:
            return "(No history yet)"

        lines = []
        for i, entry in enumerate(history[-10:], 1):  # Last 10 entries
            lines.append(f"{i}. {entry.content}")

        return "\n".join(lines)
