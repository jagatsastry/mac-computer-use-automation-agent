"""AutomationAgent -- main orchestrator that coordinates planner, skills, vision, actuator, and verifier."""

import time
from typing import Optional

from automation_agent.shared_models import ActionPlan, ActionStep, ExecutionResult, StepResult
from automation_agent.config import AgentConfig
from automation_agent.logging.event_logger import EventLogger
from automation_agent.logging.models import EventType
from automation_agent.orchestrator.verifier import StepVerifier


class AutomationAgent:
    """Main orchestrator that coordinates planner, skills, vision, actuator, and verifier."""

    def __init__(
        self,
        planner,
        skill_registry,
        coordinator,
        actuator,
        config: AgentConfig,
        logger: Optional[EventLogger] = None,
    ):
        self.planner = planner
        self.skill_registry = skill_registry
        self.coordinator = coordinator
        self.actuator = actuator
        self.config = config
        self.logger = logger or EventLogger(config.event_log_dir)
        self.verifier = StepVerifier(
            actuator=actuator, coordinator=coordinator, logger=self.logger
        )

    async def execute(self, goal: str) -> ExecutionResult:
        """Execute a natural language goal end-to-end."""
        start = time.monotonic()
        self.logger.log_event(EventType.TASK_START, f"Goal: {goal}", data={"goal": goal})

        step_results: list[StepResult] = []
        iterations = 0

        try:
            # 1. Check for matching skill
            skill_context = None
            skill_match = self.skill_registry.match(goal)
            if skill_match:
                skill_name = skill_match["skill_name"]
                params = skill_match.get("params", {})
                skill_context = self.skill_registry.expand(skill_name, params)
                self.logger.log_event(
                    EventType.SKILL_MATCH,
                    f"Matched skill: {skill_name}",
                    data={"skill_name": skill_name, "params": params},
                )
            else:
                self.logger.log_event(EventType.SKILL_NO_MATCH, "No matching skill found")

            # 2. Get screen description for context
            screen_desc = ""
            try:
                screen_desc = await self.coordinator.describe_screen()
            except Exception:
                pass

            # 3. Plan
            self.logger.log_event(EventType.PLAN_START, "Planning...")
            plan = await self.planner.plan(
                goal, screen_description=screen_desc, skill_context=skill_context
            )
            self.logger.log_event(
                EventType.PLAN_COMPLETE,
                f"Plan: {len(plan.steps)} steps",
                data={"step_count": len(plan.steps)},
            )

            # 4. Execute steps
            for i, step in enumerate(plan.steps):
                if iterations >= self.config.max_iterations:
                    duration = int((time.monotonic() - start) * 1000)
                    self.logger.log_event(EventType.TASK_FAIL, "Max iterations reached")
                    return ExecutionResult(
                        success=False,
                        message="Max iterations reached",
                        steps=step_results,
                        total_duration_ms=duration,
                        iterations=iterations,
                        goal=goal,
                        run_id=self.logger.run_id,
                    )

                result = await self._execute_step(i, step, step_results, goal, plan)
                step_results.append(result)
                iterations += 1

                if step.action == "done":
                    break

                if step.action == "wait_for_user":
                    self.logger.log_event(
                        EventType.USER_WAIT,
                        f"Waiting for user: {step.params.get('message', '')}",
                    )
                    # In real usage, this would pause. For now, continue.
                    continue

                if not result.success:
                    # Handle failure based on on_fail strategy
                    recovery_result = await self._handle_failure(
                        i, step, result, step_results, goal, plan, iterations
                    )
                    if recovery_result is None:
                        # None means replan was requested
                        replan_result = await self._replan_and_continue(
                            goal, step_results, iterations, start
                        )
                        return replan_result
                    else:
                        step_results.append(recovery_result)
                        iterations += 1
                        if not recovery_result.success:
                            # Recovery also failed
                            if step.on_fail == "abort":
                                duration = int((time.monotonic() - start) * 1000)
                                self.logger.log_event(
                                    EventType.TASK_FAIL, "Step failed with abort policy"
                                )
                                return ExecutionResult(
                                    success=False,
                                    message=f"Step {i} failed: {result.evidence}",
                                    steps=step_results,
                                    total_duration_ms=duration,
                                    iterations=iterations,
                                    goal=goal,
                                    run_id=self.logger.run_id,
                                )

            # Success
            duration = int((time.monotonic() - start) * 1000)
            self.logger.log_event(EventType.TASK_COMPLETE, "Task completed successfully")
            self.logger.finalize(True, f"Goal achieved: {goal}")
            return ExecutionResult(
                success=True,
                message="Task completed",
                steps=step_results,
                total_duration_ms=duration,
                iterations=iterations,
                goal=goal,
                run_id=self.logger.run_id,
            )

        except Exception as e:
            duration = int((time.monotonic() - start) * 1000)
            self.logger.log_event(EventType.TASK_FAIL, f"Exception: {e}")
            self.logger.finalize(False, str(e))
            return ExecutionResult(
                success=False,
                message=str(e),
                error=str(e),
                steps=step_results,
                total_duration_ms=duration,
                iterations=iterations,
                goal=goal,
                run_id=self.logger.run_id,
            )

    async def _execute_step(
        self,
        index: int,
        step: ActionStep,
        history: list,
        goal: str,
        plan: ActionPlan,
    ) -> StepResult:
        """Execute a single step: find element if needed, act, verify."""
        self.logger.log_event(
            EventType.STEP_START,
            f"Step {index}: {step.action}",
            step_index=index,
            data={"action": step.action, "params": step.params},
        )

        if step.action == "done":
            return StepResult(
                step=step,
                success=True,
                verification_method="",
                evidence="Task marked as done",
            )

        if step.action == "wait_for_user":
            return StepResult(
                step=step,
                success=True,
                verification_method="",
                evidence=f"Waiting for user: {step.params.get('message', '')}",
            )

        if step.action == "observe":
            desc = await self.coordinator.describe_screen()
            return StepResult(
                step=step,
                success=True,
                verification_method="vision",
                evidence=f"Screen: {desc}",
            )

        # For element-based actions (click with element description), find the element first
        actuator_result = await self._dispatch_action(step)

        # Verify
        self.logger.log_event(
            EventType.VERIFY_START, f"Verifying: {step.verify}", step_index=index
        )
        verification = await self.verifier.verify(step, actuator_result)

        self.logger.log_event(
            EventType.STEP_COMPLETE,
            f"Step {index}: {'PASS' if verification.success else 'FAIL'} -- {verification.evidence}",
            step_index=index,
            data={
                "success": verification.success,
                "method": verification.verification_method,
            },
        )

        return verification

    async def _dispatch_action(self, step: ActionStep) -> dict:
        """Dispatch an action to the actuator."""
        action = step.action
        params = step.params

        self.logger.log_event(EventType.ACTION_START, f"{action}({params})")

        try:
            if action == "click":
                # If element description given, find it first
                if "element" in params:
                    location = await self.coordinator.find_element(params["element"])
                    if location is None:
                        self.logger.log_event(
                            EventType.ELEMENT_NOT_FOUND,
                            f"Element not found: {params['element']}",
                        )
                        return {
                            "success": False,
                            "error": f"Element not found: {params['element']}",
                        }
                    self.logger.log_event(
                        EventType.ELEMENT_FOUND,
                        f"Found at ({location['x']}, {location['y']})",
                    )
                    result = self.actuator.click(location["x"], location["y"])
                else:
                    result = self.actuator.click(params.get("x", 0), params.get("y", 0))
            elif action == "type_text":
                result = self.actuator.type_text(params.get("text", ""))
            elif action == "press_key":
                result = self.actuator.press_key(params.get("keys", []))
            elif action == "activate_app":
                result = self.actuator.activate_app(params.get("app_name", ""))
            elif action == "open_url":
                result = self.actuator.open_url(params.get("url", ""))
            elif action == "quit_app":
                result = self.actuator.quit_app(params.get("app_name", ""))
            else:
                result = {"success": False, "error": f"Unknown action: {action}"}

            self.logger.log_event(EventType.ACTION_COMPLETE, f"{action} -> {result}")
            return result
        except Exception as e:
            self.logger.log_event(EventType.ACTION_ERROR, f"{action} error: {e}")
            return {"success": False, "error": str(e)}

    async def _handle_failure(self, index, step, result, history, goal, plan, iterations):
        """Handle a step failure based on on_fail policy."""
        if step.on_fail == "retry_different" and result.retry_count < step.max_retries:
            self.logger.log_event(
                EventType.STEP_RETRY,
                f"Retrying step {index} with different strategy",
                step_index=index,
            )
            # Create modified step for retry
            retry_step = ActionStep(
                action=step.action,
                params=step.params,
                verify=step.verify,
                on_fail=step.on_fail,
                max_retries=step.max_retries,
            )
            retry_result = await self._execute_step(index, retry_step, history, goal, plan)
            retry_result.retry_count = result.retry_count + 1
            retry_result.retry_strategies_used = result.retry_strategies_used + [
                f"retry_{result.retry_count + 1}"
            ]
            return retry_result

        if step.on_fail == "replan":
            self.logger.log_event(
                EventType.STEP_REPLAN, f"Replanning after step {index} failure"
            )
            return None  # Signal to caller to replan

        if step.on_fail == "abort":
            return result  # Return failure as-is

        if step.on_fail == "wait_for_user":
            self.logger.log_event(EventType.USER_WAIT, "Waiting for user after failure")
            return StepResult(
                step=step,
                success=False,
                verification_method=result.verification_method,
                evidence=f"Failed, waiting for user. {result.evidence}",
                retry_count=result.retry_count,
            )

        return result

    async def _replan_and_continue(self, goal, step_results, iterations, start_time):
        """Replan and attempt execution with new plan."""
        screen_desc = await self.coordinator.describe_screen()
        retry_strategies = []
        for sr in step_results:
            retry_strategies.extend(sr.retry_strategies_used)

        self.logger.log_event(EventType.REPLAN_START, "Replanning...")
        new_plan = await self.planner.replan(goal, screen_desc, step_results, retry_strategies)
        self.logger.log_event(
            EventType.REPLAN_COMPLETE, f"New plan: {len(new_plan.steps)} steps"
        )

        # Execute new plan
        for i, step in enumerate(new_plan.steps):
            if iterations >= self.config.max_iterations:
                break
            result = await self._execute_step(i, step, step_results, goal, new_plan)
            step_results.append(result)
            iterations += 1
            if step.action == "done":
                break

        duration = int((time.monotonic() - start_time) * 1000)
        success = step_results[-1].success if step_results else False
        self.logger.finalize(success, f"Replanned: {goal}")
        return ExecutionResult(
            success=success,
            message="Completed after replan" if success else "Failed after replan",
            steps=step_results,
            total_duration_ms=duration,
            iterations=iterations,
            goal=goal,
            run_id=self.logger.run_id,
        )
