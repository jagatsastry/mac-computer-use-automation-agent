"""AutomationAgent -- main orchestrator that coordinates planner, skills, vision, actuator, and verifier."""

import asyncio
import base64
import inspect
import io
import re
import time
from typing import Callable, Optional, Tuple

import structlog

from automation_agent.config import AgentConfig
from automation_agent.logging.event_logger import EventLogger
from automation_agent.logging.models import EventType
from automation_agent.orchestrator.verifier import StepVerifier
from automation_agent.shared_models import (
    ActionPlan,
    ActionStep,
    ExecutionResult,
    FindElementResult,
    SkillMatchResult,
    StepResult,
)
from automation_agent.skills.derived_skill import DerivedSkillSession

slog = structlog.get_logger(__name__)


class AutomationAgent:
    """Main orchestrator that coordinates planner, skills, vision, actuator, and verifier."""

    # Confidence thresholds for Rec 2
    _DEFAULT_CONFIDENCE_THRESHOLD = 0.5
    _CRITICAL_CONFIDENCE_THRESHOLD = 0.9
    _CRITICAL_ACTION_KEYWORDS = frozenset(
        {"submit", "pay", "confirm", "reserve", "delete", "remove", "send"}
    )

    # Resolution threshold for Rec 4 cropping
    _CROP_WIDTH_THRESHOLD = 1440

    # wait_for_user polling config
    _WAIT_POLL_INTERVAL_S = 5.0
    _WAIT_TIMEOUT_S = 120.0
    _WAIT_DIFF_THRESHOLD = 0.02  # 2% pixel change = "screen changed"

    def __init__(
        self,
        planner,
        skill_registry,
        coordinator,
        actuator,
        config: AgentConfig,
        logger: Optional[EventLogger] = None,
        screenshot_diff=None,
        context_monitor=None,
        grounding_router=None,
    ):
        self.planner = planner
        self.skill_registry = skill_registry
        self.coordinator = coordinator
        self.actuator = actuator
        self.config = config
        self.logger = logger or EventLogger(config.event_log_dir)
        self.verifier = StepVerifier(
            actuator=actuator,
            coordinator=coordinator,
            logger=self.logger,
            accessibility=getattr(coordinator, "accessibility", None),
        )
        self.screenshot_diff = screenshot_diff
        self.context_monitor = context_monitor
        self.grounding_router = grounding_router
        # Rec 4: tracks (left, top, right, bottom) of last successful click
        # in screenshot_resolution pixel space. Reset at start of each execute().
        self.last_successful_region: Optional[Tuple[int, int, int, int]] = None

    async def execute(self, goal: str) -> ExecutionResult:
        """Execute a natural language goal end-to-end."""
        self.last_successful_region = None  # Rec 4: reset for new task
        start = time.monotonic()
        slog.info("🎯 Executing goal", goal=goal)
        self.logger.log_event(EventType.TASK_START, f"Goal: {goal}", data={"goal": goal})

        step_results: list[StepResult] = []
        iterations = 0
        skill_name: Optional[str] = None
        skill_context: Optional[str] = None

        derived_session: Optional[DerivedSkillSession] = None
        expanded_steps_for_distiller: Optional[str] = None

        try:
            # 1. Check for matching skill
            skill_match = await self.skill_registry.match(goal)
            if skill_match:
                skill_name = skill_match["skill_name"]
                params = skill_match.get("params", {})
                skill_context = skill_match.get("skill_context") or skill_match.get("expanded_steps")
                if skill_context is None:
                    skill_context = self.skill_registry.expand(skill_name, params)

                # Adaptive skill system: create DerivedSkillSession
                if isinstance(skill_match, SkillMatchResult) and skill_match.candidates:
                    candidates = skill_match.candidates
                    parent_ids = [c.skill_id for c in candidates]
                    match_types = [str(c.match_type.value) for c in candidates]
                    steps_text = skill_match.expanded_steps or ""
                    expanded_steps_for_distiller = steps_text
                    derived_session = DerivedSkillSession.seed(
                        parent_ids, match_types, steps_text
                    )
                    slog.info(
                        "Created DerivedSkillSession from"
                        f" {len(candidates)} parent skill(s)"
                    )
                    # Append derived procedure to skill_context
                    if skill_context and derived_session:
                        skill_context = (
                            skill_context + "\n\n---\n\n"
                            + derived_session.serialize_for_context()
                        )
                elif not isinstance(skill_match, SkillMatchResult):
                    expanded_steps_for_distiller = skill_context

                slog.info("🤔 Skill matched", skill_name=skill_name, params=params)
                self.logger.log_event(
                    EventType.SKILL_MATCH,
                    f"Matched skill: {skill_name}",
                    data={"skill_name": skill_name, "params": params},
                )
            else:
                slog.info("🤔 No matching skill found")
                self.logger.log_event(EventType.SKILL_NO_MATCH, "No matching skill found")

            # 2. Get screen description for context
            screen_desc = ""
            desktop_context = ""
            if self.context_monitor:
                self.context_monitor.update_cheap()
            if not self.context_monitor or self.context_monitor.needs_full_vision():
                try:
                    screen_desc = await self.coordinator.describe_screen()
                    if self.context_monitor:
                        self.context_monitor.context.last_vision_description = screen_desc
                except Exception:
                    pass
            if self.context_monitor:
                desktop_context = self.context_monitor.format_for_planner()

            # 3. Plan
            self.logger.log_event(EventType.PLAN_START, "Planning...")
            plan_kwargs = dict(
                screen_description=screen_desc,
                skill_context=skill_context,
            )
            if desktop_context:
                plan_kwargs["desktop_context"] = desktop_context
            plan = await self.planner.plan(goal, **plan_kwargs)
            fallback_plan = self._build_skill_fallback_plan(goal, skill_context)
            if self._is_trivial_done_plan(plan) and fallback_plan is not None:
                if await self._plan_already_satisfied(fallback_plan):
                    slog.info("✅ Trivial done plan accepted because fallback condition is already met")
                else:
                    slog.warning(
                        "Planner returned trivial done plan before fallback target was satisfied",
                        goal=goal,
                    )
                    self.logger.log_event(
                        EventType.SKILL_EXPAND,
                        f"Replacing trivial done plan with skill fallback ({len(fallback_plan.steps)} steps)",
                        data={"step_count": len(fallback_plan.steps)},
                    )
                    plan = fallback_plan
            slog.info("📋 Plan generated", step_count=len(plan.steps), goal=goal)
            self.logger.log_event(
                EventType.PLAN_COMPLETE,
                f"Plan: {len(plan.steps)} steps",
                data={"step_count": len(plan.steps)},
            )

            # BUG 5 FIX: Validate plan before execution — reject empty verify fields
            validation_errors = plan.validate()
            if validation_errors:
                duration = int((time.monotonic() - start) * 1000)
                error_msg = "; ".join(validation_errors)
                self.logger.log_event(
                    EventType.TASK_FAIL, f"Plan validation failed: {error_msg}"
                )
                return ExecutionResult(
                    success=False,
                    message=f"Plan validation failed: {error_msg}",
                    error=error_msg,
                    steps=step_results,
                    total_duration_ms=duration,
                    iterations=iterations,
                    goal=goal,
                    run_id=self.logger.run_id,
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

                # Cheap context update before each step
                if self.context_monitor:
                    self.context_monitor.update_cheap()

                result = await self._execute_step(i, step, step_results, goal, plan)
                step_results.append(result)
                iterations += 1

                # Record context after actions
                if self.context_monitor:
                    self._record_context(step)

                if step.action == "done":
                    break

                if step.action == "wait_for_user":
                    self.logger.log_event(
                        EventType.USER_WAIT,
                        f"Waiting for user: {step.params.get('message', '')}",
                    )
                    continue

                if not result.success:
                    # Handle failure based on on_fail strategy
                    recovery_result = await self._handle_failure(
                        i, step, result, step_results, goal, plan, iterations
                    )
                    if recovery_result is None:
                        # None means replan was requested
                        replan_result = await self._replan_and_continue(
                            goal,
                            step_results,
                            iterations,
                            start,
                            skill_name=skill_name,
                            skill_context=skill_context,
                            derived_session=derived_session,
                            expanded_steps_for_distiller=expanded_steps_for_distiller,
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
            slog.info(
                "🏁 Task completed",
                duration_s=round(duration / 1000, 1),
                iterations=iterations,
            )
            await self._maybe_learn_skill_run(
                goal=goal,
                skill_name=skill_name,
                skill_context=skill_context or "",
                step_results=step_results,
                had_replan=False,
                derived_session=derived_session,
                expanded_steps_for_distiller=expanded_steps_for_distiller,
            )
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
        slog.info("🎯 Executing step", step_index=index, action=step.action, params=step.params)
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
            return await self._wait_for_user(step)

        if step.action == "observe":
            desc = await self.coordinator.describe_screen()
            return StepResult(
                step=step,
                success=True,
                verification_method="vision",
                evidence=f"Screen: {desc}",
            )

        # Capture screenshot before visually meaningful actions for diff-based verification.
        if self.screenshot_diff and step.action in ("click", "open_url"):
            self.screenshot_diff.capture_before()

        # For element-based actions (click with element description), find the element first
        actuator_result = await self._dispatch_action(step)
        visible_effect = None

        # After click, quick diff check: if no visible effect, mark as failed for retry
        if (
            self.screenshot_diff
            and step.action in ("click", "open_url")
            and actuator_result.get("success", False)
        ):
            await asyncio.sleep(0.3)  # Brief wait for UI update
            if step.action == "click":
                click_x = actuator_result.get("image_x", actuator_result.get("x", step.params.get("x", 0)))
                click_y = actuator_result.get("image_y", actuator_result.get("y", step.params.get("y", 0)))
                visible_effect = (
                    self.screenshot_diff.region_changed(click_x, click_y)
                    or self.screenshot_diff.screen_changed()
                )
            else:
                visible_effect = self.screenshot_diff.screen_changed()

            if not visible_effect:
                actuator_result["success"] = False
                actuator_result["error"] = (
                    f"{step.action} had no visible effect (screenshot unchanged)"
                )

        # BUG 1 FIX: If the actuator action failed (e.g. element not found), skip
        # verification and return failure immediately. Vision verification must not
        # override a real action failure.
        if not actuator_result.get("success", False):
            error_text = actuator_result.get("error", "unknown error")
            result = StepResult(
                step=step,
                success=False,
                verification_method="",
                evidence=f"Action failed: {error_text}",
                error=error_text,
            )
            if (
                step.action == "click"
                and "element" in step.params
                and isinstance(error_text, str)
                and error_text.startswith("Element not found:")
            ):
                result = await self._suggest_alternative_for_missing_target(
                    step,
                    goal,
                    result,
                )
            self.logger.log_event(
                EventType.STEP_COMPLETE,
                f"Step {index}: FAIL -- actuator failed: {error_text}",
                step_index=index,
                data={"success": False, "method": "actuator"},
            )
            return result

        # Brief delay after actions that need time to take effect (app launch, URL open)
        if step.action in ("activate_app", "open_url", "quit_app"):
            await asyncio.sleep(self.config.action_delay)

        # Verify
        self.logger.log_event(
            EventType.VERIFY_START, f"Verifying: {step.verify}", step_index=index
        )
        verification = await self.verifier.verify(step, actuator_result)
        if (
            not verification.success
            and visible_effect
            and self._has_explicit_method(self.coordinator, "reflect_action_outcome")
        ):
            verification = await self._reflect_failed_action(step, actuator_result, verification)

        self.logger.log_event(
            EventType.STEP_COMPLETE,
            f"Step {index}: {'PASS' if verification.success else 'FAIL'} -- {verification.evidence}",
            step_index=index,
            data={
                "success": verification.success,
                "method": verification.verification_method,
            },
        )

        # Rec 4: record the successful click region for resolution-aware narrowing
        if step.action == "click" and verification.success:
            click_x = actuator_result.get("image_x", actuator_result.get("x", step.params.get("x", 0)))
            click_y = actuator_result.get("image_y", actuator_result.get("y", step.params.get("y", 0)))
            half = 256
            self.last_successful_region = (
                max(0, click_x - half),
                max(0, click_y - half),
                click_x + half,
                click_y + half,
            )

        return verification

    @staticmethod
    def _is_trivial_done_plan(plan: ActionPlan) -> bool:
        """Return True when the plan is only a single done step."""
        return len(plan.steps) == 1 and plan.steps[0].action == "done"

    async def _plan_already_satisfied(self, plan: ActionPlan) -> bool:
        """Check whether the final actionable step in a fallback plan is already satisfied."""
        actionable_steps = [
            step for step in plan.steps if step.action not in ("done", "wait_for_user", "observe")
        ]
        if not actionable_steps:
            return False

        try:
            verification = await self.verifier.verify(actionable_steps[-1], {"success": True})
        except Exception:
            return False
        return verification.success

    def _build_skill_fallback_plan(
        self,
        goal: str,
        skill_context: Optional[str],
    ) -> Optional[ActionPlan]:
        """Compile a deterministic fallback plan from expanded skill steps when possible.

        With multi-skill context, extracts steps only from the primary (first)
        skill section before the first ``---`` separator. This prevents
        cross-contamination from analogical/generic candidates or derived
        procedure sections.
        """
        if not skill_context:
            return None

        # Extract primary skill section (before first --- separator)
        primary_section = self._extract_primary_skill_section(skill_context)

        stop_condition = self._extract_stop_condition(goal)
        compiled_steps: list[ActionStep] = []
        stop_matched = False

        for instruction, verify in self._parse_skill_steps(primary_section):
            action_steps = self._compile_skill_instruction(instruction, verify)
            if action_steps is None:
                return None
            if action_steps:
                compiled_steps.extend(action_steps)
            if stop_condition and verify and self._conditions_overlap(stop_condition, verify):
                stop_matched = True
                break

        if not compiled_steps:
            return None
        if stop_condition and not stop_matched:
            return None
        if compiled_steps[-1].action != "done":
            compiled_steps.append(ActionStep(action="done", params={}, verify="", on_fail="abort"))
        return ActionPlan(steps=compiled_steps, goal=goal)

    @staticmethod
    def _extract_primary_skill_section(skill_context: str) -> str:
        """Extract the primary (first) skill section from multi-skill context.

        Splits on ``---`` separators and returns the first section. Also
        strips out any ``## Derived Procedure`` block that may appear
        within the primary section.
        """
        # Split on --- (horizontal rule separator between skill sections)
        # Use \n+ to handle both single and double newline separators
        sections = re.split(r"\n+---\n+", skill_context)
        primary = sections[0] if sections else skill_context

        # Remove any Derived Procedure block from the primary section
        primary = re.split(
            r"^## Derived Procedure\b", primary, flags=re.MULTILINE
        )[0]

        return primary

    @staticmethod
    def _extract_stop_condition(goal: str) -> Optional[str]:
        """Extract a user-specified stop condition such as 'stop as soon as X'."""
        match = re.search(
            r"\bstop\s+(?:as soon as|once|when)\s+(.+?)(?:[.;]|$)",
            goal,
            flags=re.IGNORECASE,
        )
        if match is None:
            return None
        return match.group(1).strip()

    @staticmethod
    def _parse_skill_steps(skill_context: str) -> list[tuple[str, str]]:
        """Parse numbered skill text into (instruction, verify) tuples."""
        steps: list[tuple[str, str]] = []
        instruction: Optional[str] = None
        verify = ""

        for raw_line in skill_context.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            step_match = re.match(r"^\d+\.\s+(.*)$", line)
            if step_match:
                if instruction is not None:
                    steps.append((instruction, verify))
                instruction = step_match.group(1).strip()
                verify = ""
                continue
            if instruction and line.lower().startswith("- verify:"):
                verify = line.split(":", 1)[1].strip()

        if instruction is not None:
            steps.append((instruction, verify))
        return steps

    def _compile_skill_instruction(
        self,
        instruction: str,
        verify: str,
    ) -> Optional[list[ActionStep]]:
        """Compile a skill instruction into one or more executable action steps."""
        text = instruction.strip().rstrip(".")
        lower = text.lower()

        if not text:
            return []
        if lower.startswith("use done") or lower == "done" or lower.startswith("complete task"):
            return [ActionStep(action="done", params={}, verify="", on_fail="abort")]
        if lower.startswith("navigate to"):
            destination = re.sub(r"(?i)^navigate to", "", text)
            destination = re.sub(r"(?i)\(if specified\)", "", destination).strip()
            if not destination:
                return []
            if re.match(r"^https?://\S+$", destination, flags=re.IGNORECASE):
                return [
                    ActionStep(
                        action="open_url",
                        params={"url": destination},
                        verify=verify or f"The browser shows {destination}",
                        expected_observation=verify or f"The destination page for {destination} becomes visible",
                        on_fail="retry_different",
                        max_retries=self.config.max_retries,
                    )
                ]
            return [self._make_click_step(destination, verify)]
        if "wait for user" in lower or "wait for the user" in lower:
            wait_params = {"message": text}
            wait_condition = self._extract_wait_condition(text)
            if wait_condition:
                wait_params["condition"] = wait_condition
            return [
                ActionStep(
                    action="wait_for_user",
                    params=wait_params,
                    verify="",
                    expected_observation="",
                    on_fail="abort",
                )
            ]
        if lower.startswith("if ") and "wait for user" in lower:
            wait_params = {"message": text}
            wait_condition = self._extract_wait_condition(text)
            if wait_condition:
                wait_params["condition"] = wait_condition
            return [
                ActionStep(
                    action="wait_for_user",
                    params=wait_params,
                    verify="",
                    expected_observation="",
                    on_fail="abort",
                )
            ]

        open_match = re.match(
            r"^(?:Use activate_app to open|Open)\s+(.+?)(?:\s+app)?(?:\s+and navigate to\s+(https?://\S+))?$",
            text,
            flags=re.IGNORECASE,
        )
        if open_match:
            app_name = open_match.group(1).strip()
            url = open_match.group(2)
            steps = [
                ActionStep(
                    action="activate_app",
                    params={"app_name": app_name},
                    verify=f"{app_name} is the frontmost application",
                    expected_observation=f"{app_name} becomes the frontmost application",
                    on_fail="retry_different",
                    max_retries=self.config.max_retries,
                )
            ]
            if url:
                steps.append(
                    ActionStep(
                        action="open_url",
                        params={"url": url},
                        verify=verify or f"The browser shows {url}",
                        expected_observation=verify or f"The destination page for {url} becomes visible",
                        on_fail="retry_different",
                        max_retries=self.config.max_retries,
                    )
                )
            elif verify:
                steps[0].verify = verify
                steps[0].expected_observation = verify
            return steps

        url_match = re.match(
            r"^Use open_url to navigate to\s+(https?://\S+)$",
            text,
            flags=re.IGNORECASE,
        )
        if url_match:
            return [
                ActionStep(
                    action="open_url",
                    params={"url": url_match.group(1)},
                    verify=verify or f"The browser shows {url_match.group(1)}",
                    expected_observation=verify or f"The destination page for {url_match.group(1)} becomes visible",
                    on_fail="retry_different",
                    max_retries=self.config.max_retries,
                )
            ]

        find_click_match = re.match(
            r'^Find\s+(.+?)\s+and click\s+"([^"]+)"$',
            text,
            flags=re.IGNORECASE,
        )
        if find_click_match:
            element = f'"{find_click_match.group(2)}" for {find_click_match.group(1)}'
            return [self._make_click_step(element, verify)]

        find_it_match = re.match(
            r"^Find\s+(.+?)\s+and click\s+(?:it|them)$",
            text,
            flags=re.IGNORECASE,
        )
        if find_it_match:
            return [self._make_click_step(find_it_match.group(1), verify)]

        click_match = re.match(
            r"^(?:Click on|Click the|Click)\s+(.+)$",
            text,
            flags=re.IGNORECASE,
        )
        if click_match:
            return [self._make_click_step(click_match.group(1), verify)]

        type_match = re.match(
            r'^Type\s+"?(.+?)"?\s+(?:in the .+?\s+)?(?:and|then)\s+press\s+Enter$',
            text,
            flags=re.IGNORECASE,
        )
        if type_match:
            typed_text = type_match.group(1).strip()
            return [
                ActionStep(
                    action="type_text",
                    params={"text": typed_text},
                    verify=f'The focused text field contains "{typed_text}"',
                    expected_observation=f'The focused text field contains "{typed_text}"',
                    on_fail="retry_different",
                    max_retries=self.config.max_retries,
                ),
                ActionStep(
                    action="press_key",
                    params={"keys": ["return"]},
                    verify=verify,
                    expected_observation=verify,
                    on_fail="retry_different",
                    max_retries=self.config.max_retries,
                ),
            ]

        press_match = re.match(r"^Press\s+(.+?)(?:\s+to\s+.+)?$", text, flags=re.IGNORECASE)
        if press_match:
            keys = self._parse_key_combo(press_match.group(1))
            if keys:
                return [
                    ActionStep(
                        action="press_key",
                        params={"keys": keys},
                        verify=verify,
                        expected_observation=verify,
                        on_fail="retry_different",
                        max_retries=self.config.max_retries,
                    )
                ]

        return None

    def _make_click_step(self, element: str, verify: str) -> ActionStep:
        """Create a click step for compiled skill plans."""
        return ActionStep(
            action="click",
            params={"element": element.strip()},
            verify=verify,
            expected_observation=verify,
            on_fail="retry_different",
            max_retries=self.config.max_retries,
        )

    @staticmethod
    def _parse_key_combo(combo: str) -> list[str]:
        """Parse a human-readable key combo like Cmd+N into actuator keys."""
        alias = {
            "cmd": "cmd",
            "command": "cmd",
            "ctrl": "ctrl",
            "control": "ctrl",
            "shift": "shift",
            "alt": "alt",
            "option": "alt",
            "return": "return",
            "enter": "return",
            "space": "space",
        }
        parts = re.split(r"\s*\+\s*", combo.strip())
        keys: list[str] = []
        for part in parts:
            normalized = part.strip().lower()
            if not normalized:
                continue
            keys.append(alias.get(normalized, normalized))
        return keys

    @classmethod
    def _coerce_key_sequence(cls, raw_keys) -> list[str]:
        """Accept either keys=[...], key='Return', or a human-readable combo."""
        if isinstance(raw_keys, dict):
            raw_keys = raw_keys.get("keys", raw_keys.get("key"))
        if raw_keys is None:
            return []
        if isinstance(raw_keys, str):
            return cls._parse_key_combo(raw_keys)
        if isinstance(raw_keys, tuple):
            raw_keys = list(raw_keys)

        keys: list[str] = []
        if isinstance(raw_keys, list):
            for entry in raw_keys:
                if isinstance(entry, str):
                    keys.extend(cls._parse_key_combo(entry))
        return keys

    @staticmethod
    def _has_explicit_method(target: object, method_name: str) -> bool:
        """Return True only when a method is implemented or explicitly mocked."""
        if target is None:
            return False
        try:
            attr = inspect.getattr_static(target, method_name)
        except AttributeError:
            return False
        return callable(attr)

    @staticmethod
    def _extract_wait_condition(text: str) -> str:
        """Extract a visibility predicate from a conditional wait instruction."""
        match = re.match(
            r"^If\s+(.+?),\s*wait for (?:the )?user(?:\s+to\s+.+)?$",
            text.strip(),
            flags=re.IGNORECASE,
        )
        if match is None:
            return ""

        condition = match.group(1).strip().rstrip(".")
        condition = re.sub(r"(?i)\bappears?\b", "is visible", condition)
        condition = re.sub(r"\s+", " ", condition).strip()
        if not re.search(r"(?i)\b(?:is|are|visible|shown|loaded|frontmost)\b", condition):
            condition = f"{condition} is visible"
        return condition

    @staticmethod
    def _conditions_overlap(left: str, right: str) -> bool:
        """Return True when two textual conditions describe the same stop target."""
        left_norm = AutomationAgent._normalize_condition_text(left)
        right_norm = AutomationAgent._normalize_condition_text(right)
        if not left_norm or not right_norm:
            return False
        if left_norm in right_norm or right_norm in left_norm:
            return True

        left_tokens = set(left_norm.split())
        right_tokens = set(right_norm.split())
        if not left_tokens or not right_tokens:
            return False
        overlap = len(left_tokens & right_tokens)
        return overlap / min(len(left_tokens), len(right_tokens)) >= 0.6

    @staticmethod
    def _normalize_condition_text(text: str) -> str:
        """Normalize a human-readable condition for loose matching."""
        normalized = text.lower()
        normalized = normalized.replace("sign-in", "login").replace("sign in", "login")
        normalized = normalized.replace("log in", "login")
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
        stop_words = {"the", "a", "an", "is", "are", "to", "be"}
        tokens = [token for token in normalized.split() if token not in stop_words]
        return " ".join(tokens)

    async def _dispatch_action(self, step: ActionStep) -> dict:
        """Dispatch an action to the actuator."""
        action = step.action
        params = dict(step.params)

        slog.debug("🎯 Dispatching action", action=action, params=params)
        self.logger.log_event(EventType.ACTION_START, f"{action}({params})")

        try:
            await self._apply_pre_delay(params)
            pre_keys = self._coerce_key_sequence(params.pop("_pre_keys", None))
            if pre_keys:
                pre_result = self.actuator.press_key(pre_keys)
                if not pre_result.get("success", False):
                    return pre_result
                await asyncio.sleep(max(self.config.action_delay, 0.2))

            if action == "click":
                # If element description given, find it first
                if "element" in params:
                    location = await self._find_element(params["element"])
                    if location is None:
                        self.logger.log_event(
                            EventType.ELEMENT_NOT_FOUND,
                            f"Element not found: {params['element']}",
                        )
                        return {
                            "success": False,
                            "error": f"Element not found: {params['element']}",
                        }

                    # Rec 2: confidence gate — check before any click
                    confidence = location.confidence
                    threshold = self._get_confidence_threshold(step)
                    if confidence > 0.0 and confidence < threshold:
                        slog.warning(
                            "Low confidence element match",
                            confidence=confidence,
                            threshold=threshold,
                            element=params["element"],
                        )
                        return {
                            "success": False,
                            "error": f"low_confidence:{confidence:.2f}",
                        }

                    # Rec 3: pre-click validation (skip for accessibility or very high confidence)
                    skip_validation = (location.source == "accessibility") or (confidence >= 0.9)
                    if not skip_validation:
                        try:
                            is_valid = await self._validate_candidate(
                                location.x, location.y, params["element"]
                            )
                        except Exception:
                            slog.warning(
                                "Pre-click validation error, proceeding with click",
                                exc_info=True,
                            )
                            is_valid = True  # Fail-open

                        if not is_valid:
                            slog.warning(
                                "Pre-click validation failed",
                                element=params["element"],
                                x=location.x,
                                y=location.y,
                            )
                            return {
                                "success": False,
                                "error": (
                                    f"Pre-click validation failed: element at "
                                    f"({location.x}, {location.y}) does not appear "
                                    f"to be '{params['element']}'"
                                ),
                            }

                    self.logger.log_event(
                        EventType.ELEMENT_FOUND,
                        f"Found at image ({location.x}, {location.y}) / screen ({location.screen_x}, {location.screen_y})",
                    )
                    screen_x = location.screen_x if location.screen_x is not None else location.x
                    screen_y = location.screen_y if location.screen_y is not None else location.y
                    result = self.actuator.click(screen_x, screen_y)
                    result["x"] = location.x
                    result["y"] = location.y
                    result["image_x"] = location.x
                    result["image_y"] = location.y
                    result["screen_x"] = screen_x
                    result["screen_y"] = screen_y
                else:
                    screen_x = params.get("x", 0)
                    screen_y = params.get("y", 0)
                    image_x, image_y = self._screen_to_image_coords(
                        screen_x,
                        screen_y,
                        self.config.screenshot_resolution[0],
                        self.config.screenshot_resolution[1],
                    )
                    result = self.actuator.click(screen_x, screen_y)
                    result["x"] = image_x
                    result["y"] = image_y
                    result["image_x"] = image_x
                    result["image_y"] = image_y
                    result["screen_x"] = screen_x
                    result["screen_y"] = screen_y
            elif action == "type_text":
                if params.pop("_clear_first", False):
                    clear_result = self.actuator.press_key(["cmd", "a"])
                    if not clear_result.get("success", False):
                        return clear_result
                    await asyncio.sleep(max(self.config.action_delay, 0.2))
                if params.pop("_slow_type", False):
                    result = await self._type_text_slowly(params.get("text", ""))
                else:
                    result = self.actuator.type_text(params.get("text", ""))
            elif action == "press_key":
                result = self.actuator.press_key(self._coerce_key_sequence(params))
            elif action == "activate_app":
                app_name = params.get("app_name", "")
                if params.pop("_quit_first", False):
                    quit_result = self.actuator.quit_app(app_name)
                    if not quit_result.get("success", False):
                        return quit_result
                    await asyncio.sleep(max(self.config.action_delay, 0.2))
                if params.pop("_spotlight", False):
                    result = await self._activate_app_via_spotlight(app_name)
                else:
                    result = self.actuator.activate_app(app_name)
            elif action == "open_url":
                if params.pop("_address_bar_fallback", False):
                    result = await self._open_url_via_address_bar(params.get("url", ""))
                else:
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

    async def _find_element(self, description: str) -> Optional[FindElementResult]:
        """Find a UI element by description, using grounding router if available.

        Also wires accessibility candidates (Rec 1) and resolution-aware
        screenshot cropping (Rec 4).

        Returns FindElementResult on success, or None if not found.
        """
        screenshot_b64 = await self._capture_screenshot()
        if not isinstance(screenshot_b64, str):
            screenshot_b64 = None
        image_size = self._image_size_from_b64(screenshot_b64) if screenshot_b64 else self._fallback_image_size()

        if self.grounding_router is not None:
            gr = await self.grounding_router.find_element(description)
            if gr is not None:
                result = FindElementResult(
                    x=gr.x,
                    y=gr.y,
                    confidence=gr.confidence,
                    source=gr.strategy_used.value,
                )
                normalized = self._normalize_find_result(
                    result,
                    image_size=image_size,
                )
                debug_point = self._debug_point_for_location(normalized)
                if screenshot_b64:
                    self._save_debug_image(
                        screenshot_b64,
                        normalized,
                        description,
                        image_point=debug_point,
                    )
                return normalized
            return None

        # Rec 1: get accessibility candidates if actuator supports it
        candidates = None
        if hasattr(self.actuator, "get_accessibility_elements"):
            try:
                result = self.actuator.get_accessibility_elements()
                if isinstance(result, list):
                    candidates = result if result else None
            except Exception:
                pass  # Fallback: no candidates

        # Rec 4: capture screenshot and optionally crop to last successful region
        original_b64 = screenshot_b64
        screenshot_b64 = original_b64
        crop_offset = None

        if screenshot_b64 and self.last_successful_region is not None:
            crop_result = self._maybe_crop_screenshot(screenshot_b64)
            if crop_result is not None:
                screenshot_b64, crop_offset = crop_result

        find_kwargs = {}
        if screenshot_b64:
            find_kwargs["screenshot_b64"] = screenshot_b64
        if candidates is not None:
            find_kwargs["candidates"] = candidates

        result = await self.coordinator.find_element(description, **find_kwargs)
        result = self._coerce_find_result(result)

        # Adjust coordinates back to full-image space if we cropped
        if result is not None and crop_offset is not None:
            result = FindElementResult(
                x=result.x + crop_offset[0],
                y=result.y + crop_offset[1],
                confidence=result.confidence,
                source=result.source,
                raw_response=result.raw_response,
            )

        # Save debug image with crosshair at predicted coordinates
        if result is not None:
            normalized = self._normalize_find_result(
                result,
                image_size=image_size,
            )
            if original_b64:
                self._save_debug_image(original_b64, normalized, description)
            return normalized

        return None

    def _save_debug_image(
        self,
        screenshot_b64: str,
        location: FindElementResult,
        description: str,
        image_point: Optional[Tuple[int, int]] = None,
    ) -> None:
        """Save a debug screenshot with a crosshair at the predicted click point."""
        try:
            from PIL import Image, ImageDraw

            img_bytes = base64.b64decode(screenshot_b64)
            img = Image.open(io.BytesIO(img_bytes)).convert("RGB")

            draw = ImageDraw.Draw(img)
            x, y = image_point or (location.x, location.y)
            r = 30  # crosshair radius
            color = (255, 0, 0)  # red
            outline = (0, 0, 0)  # black outline for contrast
            w = 5

            # Black outline first, then red on top
            for c, off in [(outline, 2), (color, 0)]:
                draw.line([(x - r, y), (x + r, y)], fill=c, width=w + off)
                draw.line([(x, y - r), (x, y + r)], fill=c, width=w + off)
                draw.ellipse(
                    [(x - r, y - r), (x + r, y + r)], outline=c, width=w + off
                )

            # Label with background box
            label = f"({x},{y}) {description[:50]}"
            lx, ly = x + r + 6, y - 12
            bbox = draw.textbbox((lx, ly), label)
            draw.rectangle(
                [bbox[0] - 2, bbox[1] - 2, bbox[2] + 2, bbox[3] + 2],
                fill=(0, 0, 0),
            )
            draw.text((lx, ly), label, fill=(255, 255, 0))

            debug_dir = self.logger.run_dir / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time() * 1000)
            slug = re.sub(r"[^a-zA-Z0-9]+", "_", description)[:60].strip("_")
            path = debug_dir / f"find_{ts}_{slug}.jpg"
            img.save(str(path), format="JPEG", quality=90)
            slog.debug("Debug image saved", path=str(path))
        except Exception as exc:
            slog.warning("Debug image save failed", error=str(exc))

    async def _wait_for_user(self, step: ActionStep) -> StepResult:
        """Wait for the user to complete an action by polling for screen changes.

        Captures a baseline screenshot, prints the message, then polls every
        _WAIT_POLL_INTERVAL_S seconds comparing against baseline. Returns when
        the screen changes significantly or after _WAIT_TIMEOUT_S seconds.
        """
        message = step.params.get("message", "Please complete the required action")
        wait_condition = str(step.params.get("condition", "")).strip()
        if wait_condition:
            try:
                condition_visible = await self.coordinator.verify_condition(wait_condition)
            except Exception:
                condition_visible = True
            if not condition_visible:
                slog.info("Skipping wait_for_user; condition not present", condition=wait_condition)
                return StepResult(
                    step=step,
                    success=True,
                    verification_method="",
                    evidence=f"Skipped wait because '{wait_condition}' is not present",
                )

        slog.info("⏳ Waiting for user action", message=message)
        print(f"\n[WAITING] {message}")
        print(f"  (will auto-resume when screen changes, timeout {self._WAIT_TIMEOUT_S}s)")

        try:
            from PIL import Image
        except ImportError:
            slog.warning("PIL not installed — cannot poll for screen changes, continuing")
            return StepResult(
                step=step, success=True, verification_method="",
                evidence=f"Waiting for user (no PIL): {message}",
            )

        # Capture baseline
        try:
            baseline_b64 = await self.coordinator.capture_screenshot()
            baseline_bytes = base64.b64decode(baseline_b64)
            baseline_img = Image.open(io.BytesIO(baseline_bytes)).convert("L")
        except Exception:
            slog.warning("Cannot capture baseline for wait polling — proceeding")
            return StepResult(
                step=step, success=True, verification_method="",
                evidence=f"Waiting for user: {message}",
            )

        elapsed = 0.0
        while elapsed < self._WAIT_TIMEOUT_S:
            await asyncio.sleep(self._WAIT_POLL_INTERVAL_S)
            elapsed += self._WAIT_POLL_INTERVAL_S

            try:
                current_b64 = await self.coordinator.capture_screenshot()
                current_bytes = base64.b64decode(current_b64)
                current_img = Image.open(io.BytesIO(current_bytes)).convert("L")

                # Pixel-level diff ratio
                diff = self._image_diff_ratio(baseline_img, current_img)
                if diff >= self._WAIT_DIFF_THRESHOLD:
                    slog.info(
                        "Screen changed — resuming",
                        diff_ratio=round(diff, 4),
                        waited_s=round(elapsed, 1),
                    )
                    print(f"  [RESUMED] Screen changed ({diff:.1%}) after {elapsed:.0f}s")
                    return StepResult(
                        step=step, success=True, verification_method="",
                        evidence=f"Screen changed ({diff:.1%}) after {elapsed:.0f}s wait",
                    )
            except Exception:
                pass  # Screenshot capture failed — keep polling

        slog.warning("wait_for_user timed out", timeout_s=self._WAIT_TIMEOUT_S)
        print(f"  [TIMEOUT] No screen change detected after {self._WAIT_TIMEOUT_S}s")
        return StepResult(
            step=step, success=True, verification_method="",
            evidence=f"Timed out after {self._WAIT_TIMEOUT_S}s — proceeding anyway",
        )

    @staticmethod
    def _image_diff_ratio(img_a, img_b) -> float:
        """Compute the fraction of pixels that differ between two grayscale PIL images."""
        if img_a.size != img_b.size:
            img_b = img_b.resize(img_a.size)
        pixels_a = img_a.tobytes()
        pixels_b = img_b.tobytes()
        if len(pixels_a) != len(pixels_b):
            return 1.0
        diff_count = sum(1 for a, b in zip(pixels_a, pixels_b) if abs(a - b) > 20)
        return diff_count / len(pixels_a)

    def _get_confidence_threshold(self, step: ActionStep) -> float:
        """Return the confidence threshold for a step (Rec 2).

        Steps whose verify text contains critical-action keywords use a higher
        threshold of 0.9. All other steps use 0.5.
        """
        verify_lower = step.verify.lower() if step.verify else ""
        if any(kw in verify_lower for kw in self._CRITICAL_ACTION_KEYWORDS):
            return self._CRITICAL_CONFIDENCE_THRESHOLD
        return self._DEFAULT_CONFIDENCE_THRESHOLD

    async def _apply_pre_delay(self, params: dict) -> None:
        """Sleep before action execution when retry strategies request it."""
        delay = float(params.pop("_pre_delay", 0.0) or 0.0)
        if delay > 0:
            await asyncio.sleep(delay)

    async def _capture_screenshot(self) -> str:
        """Capture a screenshot from the coordinator, tolerating sync test doubles."""
        result = self.coordinator.capture_screenshot()
        if inspect.isawaitable(result):
            return await result
        return result

    def _coerce_find_result(self, result) -> Optional[FindElementResult]:
        """Normalize legacy dict results into FindElementResult."""
        if result is None:
            return None
        if isinstance(result, FindElementResult):
            return result
        if isinstance(result, dict):
            return FindElementResult(
                x=int(result.get("x", 0)),
                y=int(result.get("y", 0)),
                confidence=float(result.get("confidence", 0.0)),
                source=str(result.get("source", "")),
                raw_response=str(result.get("raw_response", "")),
                screen_x=result.get("screen_x"),
                screen_y=result.get("screen_y"),
                image_width=int(result.get("image_width", 0) or 0),
                image_height=int(result.get("image_height", 0) or 0),
            )
        raise TypeError(f"Unsupported find_element result type: {type(result)!r}")

    def _image_size_from_b64(self, screenshot_b64: str) -> Tuple[int, int]:
        """Read image dimensions from a base64-encoded screenshot."""
        try:
            from PIL import Image

            img_bytes = base64.b64decode(screenshot_b64)
            with Image.open(io.BytesIO(img_bytes)) as img:
                return img.size
        except Exception:
            return self._fallback_image_size()

    def _fallback_image_size(self) -> Tuple[int, int]:
        """Return a safe fallback image size when metadata is unavailable."""
        config_size = getattr(self.config, "screenshot_resolution", None)
        if (
            isinstance(config_size, tuple)
            and len(config_size) == 2
            and all(isinstance(v, int) and v > 0 for v in config_size)
        ):
            return config_size
        return (1024, 768)

    def _get_logical_screen_size(self) -> Tuple[int, int]:
        """Return the OS logical screen size used by the actuator."""
        capture = getattr(self.coordinator, "capture", None)
        if capture is None or "Mock" in type(capture).__name__:
            return self._fallback_image_size()

        if capture is not None and hasattr(capture, "get_screen_size"):
            try:
                size = capture.get_screen_size()
                if not inspect.isawaitable(size) and len(size) == 2:
                    return int(size[0]), int(size[1])
            except Exception:
                pass

        try:
            import pyautogui

            size_fn = pyautogui.size
            if inspect.iscoroutinefunction(size_fn) or "AsyncMock" in type(size_fn).__name__:
                raise TypeError("pyautogui.size() is async in this environment")
            size = size_fn()
            if inspect.isawaitable(size):
                if hasattr(size, "close"):
                    size.close()
                raise TypeError("pyautogui.size() returned awaitable")
            return int(size.width), int(size.height)
        except Exception:
            return self._fallback_image_size()

    def _image_to_screen_coords(
        self,
        image_x: int,
        image_y: int,
        image_width: int,
        image_height: int,
    ) -> Tuple[int, int]:
        """Map screenshot/image coordinates back to logical screen coordinates."""
        if image_width <= 0 or image_height <= 0:
            return image_x, image_y

        screen_width, screen_height = self._get_logical_screen_size()
        if screen_width <= 0 or screen_height <= 0:
            return image_x, image_y

        screen_x = round(image_x * screen_width / image_width)
        screen_y = round(image_y * screen_height / image_height)
        return (
            max(0, min(screen_x, screen_width - 1)),
            max(0, min(screen_y, screen_height - 1)),
        )

    def _screen_to_image_coords(
        self,
        screen_x: int,
        screen_y: int,
        image_width: int,
        image_height: int,
    ) -> Tuple[int, int]:
        """Project logical screen coordinates into screenshot/image space."""
        screen_width, screen_height = self._get_logical_screen_size()
        if image_width <= 0 or image_height <= 0 or screen_width <= 0 or screen_height <= 0:
            return screen_x, screen_y

        image_x = round(screen_x * image_width / screen_width)
        image_y = round(screen_y * image_height / screen_height)
        return (
            max(0, min(image_x, image_width - 1)),
            max(0, min(image_y, image_height - 1)),
        )

    def _normalize_find_result(
        self,
        result: FindElementResult,
        image_size: Tuple[int, int],
    ) -> FindElementResult:
        """Attach both image-space and screen-space coordinates to a result."""
        image_width, image_height = image_size
        if result.source == "accessibility":
            image_x, image_y = self._screen_to_image_coords(
                result.x,
                result.y,
                image_width,
                image_height,
            )
            screen_x, screen_y = result.x, result.y
        else:
            image_x, image_y = result.x, result.y
            screen_x, screen_y = self._image_to_screen_coords(
                result.x,
                result.y,
                image_width,
                image_height,
            )

        return FindElementResult(
            x=image_x,
            y=image_y,
            confidence=result.confidence,
            source=result.source,
            raw_response=result.raw_response,
            screen_x=screen_x,
            screen_y=screen_y,
            image_width=image_width,
            image_height=image_height,
        )

    def _debug_point_for_location(self, location: FindElementResult) -> Tuple[int, int]:
        """Return the point that should be overlaid on the debug screenshot."""
        if location.source == "accessibility" and location.screen_x is not None and location.screen_y is not None:
            return self._screen_to_image_coords(
                location.screen_x,
                location.screen_y,
                location.image_width or self.config.screenshot_resolution[0],
                location.image_height or self.config.screenshot_resolution[1],
            )
        return location.x, location.y

    async def _run_action_sequence(
        self,
        steps: list[tuple[str, Callable[..., dict], tuple]],
    ) -> dict:
        """Execute multiple actuator calls as one retry strategy."""
        outputs = []
        for idx, (label, func, args) in enumerate(steps):
            result = func(*args)
            if not result.get("success", False):
                return {
                    "success": False,
                    "error": result.get("error", f"{label} failed"),
                    "output": result.get("output", ""),
                }
            if result.get("output"):
                outputs.append(result["output"])
            if idx < len(steps) - 1:
                await asyncio.sleep(max(self.config.action_delay, 0.2))

        return {"success": True, "output": "; ".join(outputs) or "Sequence complete"}

    async def _type_text_slowly(self, text: str) -> dict:
        """Retry typing character-by-character when bulk keystrokes fail."""
        if not text:
            return {"success": True, "output": ""}
        steps = [("type_char", self.actuator.type_text, (char,)) for char in text]
        return await self._run_action_sequence(steps)

    async def _activate_app_via_spotlight(self, app_name: str) -> dict:
        """Fallback activation path that uses Spotlight search."""
        return await self._run_action_sequence(
            [
                ("open_spotlight", self.actuator.press_key, (["cmd", "space"],)),
                ("type_app_name", self.actuator.type_text, (app_name,)),
                ("confirm_launch", self.actuator.press_key, (["return"],)),
            ]
        )

    async def _open_url_via_address_bar(self, url: str) -> dict:
        """Fallback navigation path that uses the browser address bar."""
        return await self._run_action_sequence(
            [
                ("focus_address_bar", self.actuator.press_key, (["cmd", "l"],)),
                ("type_url", self.actuator.type_text, (url,)),
                ("confirm_navigation", self.actuator.press_key, (["return"],)),
            ]
        )

    async def _validate_candidate(
        self,
        candidate_x: int,
        candidate_y: int,
        target_description: str,
        screenshot_b64: Optional[str] = None,
    ) -> bool:
        """Pre-click validation with detail and context crops around a candidate.

        If the coordinator explicitly supports multiscale validation, use a tight
        detail crop plus a wider context crop. Otherwise, fall back to the legacy
        single-crop verify_condition() path.

        Args:
            candidate_x: Pixel x of the candidate element center.
            candidate_y: Pixel y of the candidate element center.
            target_description: What the element should be (e.g., "Save button").
            screenshot_b64: Optional pre-captured full screenshot. If None, captures one.

        Returns:
            True if the vision model confirms the match, False otherwise.
        """
        try:
            from PIL import Image
        except ImportError:
            slog.warning(
                "PIL (Pillow) not installed — skipping pre-click validation. "
                "Install with: pip install pillow"
            )
            return True  # Skip validation, not fail-open silently

        if screenshot_b64 is None:
            screenshot_b64 = await self.coordinator.capture_screenshot()

        img_bytes = base64.b64decode(screenshot_b64)
        img = Image.open(io.BytesIO(img_bytes))

        detail_b64 = self._crop_square_b64(img, candidate_x, candidate_y, size=128)
        context_b64 = self._crop_square_b64(img, candidate_x, candidate_y, size=512)

        if detail_b64 is None or context_b64 is None:
            return True

        if self._has_explicit_method(self.coordinator, "verify_multiscale_target"):
            return await self.coordinator.verify_multiscale_target(
                target_description,
                detail_b64,
                context_b64,
            )

        condition = f"The element at the center of this image is: {target_description}"
        return await self.coordinator.verify_condition(condition, screenshot_b64=detail_b64)

    @staticmethod
    def _crop_square_b64(img, center_x: int, center_y: int, size: int) -> Optional[str]:
        """Crop a square image around a point and return it as base64 JPEG."""
        half = size // 2
        left = max(0, center_x - half)
        top = max(0, center_y - half)
        right = min(img.size[0], center_x + half)
        bottom = min(img.size[1], center_y + half)
        if left >= right or top >= bottom:
            return None
        cropped = img.crop((left, top, right, bottom))
        buf = io.BytesIO()
        cropped.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode()

    async def _reflect_failed_action(
        self,
        step: ActionStep,
        actuator_result: dict,
        verification: StepResult,
    ) -> StepResult:
        """Use semantic reflection when the screen changed but verification failed."""
        try:
            screenshot_b64 = await self._capture_screenshot()
            reflection = await self.coordinator.reflect_action_outcome(
                action=step.action,
                params=step.params,
                expected_observation=step.expected_observation or step.verify,
                screenshot_b64=screenshot_b64,
            )
        except Exception:
            slog.debug("Reflection step failed", exc_info=True)
            return verification

        verification.reflection_hint = reflection.get("hint", "")
        verification.reflection_observed = reflection.get("observed", "")
        if verification.reflection_observed:
            verification.evidence = (
                f"{verification.evidence}. Reflection observed: {verification.reflection_observed}"
            )

        if reflection.get("worked") == "yes":
            verification.success = True
            verification.verification_method = verification.verification_method or "vision"
        return verification

    async def _suggest_alternative_for_missing_target(
        self,
        step: ActionStep,
        goal: str,
        result: StepResult,
    ) -> StepResult:
        """Suggest a visible alternative control when a click target is absent."""
        if not self._has_explicit_method(self.coordinator, "suggest_alternative_affordance"):
            return result

        try:
            screenshot_b64 = await self._capture_screenshot()
            suggestion = await self.coordinator.suggest_alternative_affordance(
                missing_target=str(step.params.get("element", "")),
                task_goal=goal,
                expected_observation=step.expected_observation or step.verify,
                screenshot_b64=screenshot_b64,
            )
        except Exception:
            slog.debug("Alternative affordance suggestion failed", exc_info=True)
            return result

        if not suggestion:
            return result

        affordance = str(suggestion.get("affordance", "")).strip()
        if not affordance:
            return result
        if affordance.lower() == str(step.params.get("element", "")).strip().lower():
            return result

        result.reflection_hint = "use_alternative_affordance"
        result.suggested_element = affordance
        result.reflection_observed = str(suggestion.get("reason", "")).strip()
        result.evidence = (
            f"{result.evidence}. Suggested visible alternative: {affordance}"
        )
        if result.reflection_observed:
            result.evidence = (
                f"{result.evidence}. Suggestion reason: {result.reflection_observed}"
            )
        return result

    def _maybe_crop_screenshot(
        self, screenshot_b64: str
    ) -> Optional[Tuple[str, Tuple[int, int]]]:
        """Crop screenshot to 512x512 around last_successful_region if image is hi-res (Rec 4).

        Only activates when image width > _CROP_WIDTH_THRESHOLD (1440px) AND
        last_successful_region is set. This is a no-op for the default 1024px resolution.

        Args:
            screenshot_b64: Full screenshot as base64 JPEG.

        Returns:
            Tuple of (cropped_b64, (offset_x, offset_y)) if cropping was applied.
            None if image is at or below the width threshold or no previous region exists.
        """
        try:
            from PIL import Image
        except ImportError:
            slog.warning("PIL (Pillow) not installed — skipping resolution-aware crop")
            return None

        try:
            img_bytes = base64.b64decode(screenshot_b64)
            img = Image.open(io.BytesIO(img_bytes))
            w, h = img.size

            if w <= self._CROP_WIDTH_THRESHOLD:
                return None

            if self.last_successful_region is None:
                return None

            region = self.last_successful_region
            center_x = (region[0] + region[2]) // 2
            center_y = (region[1] + region[3]) // 2

            crop_half = 256
            left = max(0, min(center_x - crop_half, w - 512))
            top = max(0, min(center_y - crop_half, h - 512))
            right = min(w, left + 512)
            bottom = min(h, top + 512)

            cropped = img.crop((left, top, right, bottom))
            buf = io.BytesIO()
            cropped.save(buf, format="JPEG", quality=85)
            cropped_b64 = base64.b64encode(buf.getvalue()).decode()

            return cropped_b64, (left, top)
        except Exception:
            slog.warning("Resolution-aware crop failed, using full screenshot", exc_info=True)
            return None

    def _record_context(self, step: ActionStep) -> None:
        """Record action context in the context monitor after step execution."""
        if step.action == "click":
            self.context_monitor.record_click(step.params.get("element", ""))
        elif step.action == "type_text":
            self.context_monitor.record_type(
                step.params.get("text", ""),
                step.params.get("field_name"),
            )
        elif step.action == "open_url":
            self.context_monitor.record_navigation(step.params.get("url", ""))

    async def _handle_failure(self, index, step, result, history, goal, plan, iterations):
        """Handle a step failure based on on_fail policy.

        Strategy-changing retries: each retry attempts a genuinely different
        approach rather than blind repetition.  BUG 4 FIX: This now loops
        through ALL available retries rather than returning after just one.
        """
        if step.on_fail == "retry_different":
            current_result = result
            while current_result.retry_count < step.max_retries:
                strategy, retry_step = self._vary_strategy(step, current_result)
                if retry_step is None:
                    self.logger.log_event(
                        EventType.STEP_REPLAN,
                        f"Retry strategy {strategy} escalated to replan for step {index}",
                        step_index=index,
                        data={"strategy": strategy, "attempt": current_result.retry_count + 1},
                    )
                    return None
                slog.info(
                    "🔄 Retrying step",
                    step_index=index,
                    strategy=strategy,
                    attempt=current_result.retry_count + 1,
                )
                self.logger.log_event(
                    EventType.STEP_RETRY,
                    f"Retrying step {index} with strategy: {strategy}",
                    step_index=index,
                    data={"strategy": strategy, "attempt": current_result.retry_count + 1},
                )
                retry_result = await self._execute_step(index, retry_step, history, goal, plan)
                retry_result.retry_count = current_result.retry_count + 1
                retry_result.retry_strategies_used = current_result.retry_strategies_used + [strategy]

                if retry_result.success:
                    return retry_result
                current_result = retry_result

            # Retries exhausted — escalate to replan
            self.logger.log_event(
                EventType.STEP_REPLAN,
                f"Retries exhausted for step {index}, escalating to replan",
                step_index=index,
            )
            return None  # Signal replan

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

    def _vary_strategy(self, step: ActionStep, prev_result: StepResult) -> tuple:
        """Produce a different strategy for retrying a failed step.

        Returns (strategy_name, retry_step).
        """
        params = dict(step.params)
        attempt = prev_result.retry_count + 1

        def _retry_step(action: str, new_params: dict) -> ActionStep:
            return ActionStep(
                action=action,
                params=new_params,
                verify=step.verify,
                expected_observation=step.expected_observation,
                on_fail=step.on_fail,
                max_retries=step.max_retries,
            )

        reflection_hint = (prev_result.reflection_hint or "").strip().lower()
        missing_target = (prev_result.error or "").startswith("Element not found:")
        suggested_element = (prev_result.suggested_element or "").strip()

        if step.action == "click" and "element" in params:
            search_click = any(
                token in str(params.get("element", "")).lower()
                for token in ("search", "filter")
            )
            if missing_target and suggested_element:
                params["element"] = suggested_element
                if attempt == 1:
                    return ("visible_alternative_affordance", _retry_step("click", params))
                if attempt == 2:
                    params["element"] = (
                        f"{suggested_element} (exact visible control on the same relevant card/section)"
                    )
                    return ("refine_visible_alternative_affordance", _retry_step("click", params))
                return ("replan_after_alternative_affordance", None)
            if missing_target and not suggested_element:
                if attempt == 1:
                    params["element"] = (
                        f"{params['element']} (visible on the same relevant card/section only)"
                    )
                    return ("refine_missing_target_query", _retry_step("click", params))
                return ("replan_missing_target", None)
            if reflection_hint == "dismiss_modal":
                return ("dismiss_modal_then_retry", _retry_step("press_key", {"keys": ["escape"]}))
            if reflection_hint == "scroll_to_top":
                params["_pre_keys"] = ["cmd", "up"]
                return ("reflection_scroll_to_top", _retry_step("click", params))
            if reflection_hint == "refine_target":
                params["element"] = f"{params['element']} (look carefully for the exact matching target)"
                return ("reflection_refine_target", _retry_step("click", params))
            if attempt == 1:
                # Strategy: re-query vision with more context
                params["element"] = f"{params['element']} (look carefully, may be partially hidden)"
                return ("refine_element_query", _retry_step("click", params))
            elif attempt == 2 and search_click:
                params["_pre_keys"] = ["cmd", "up"]
                return ("jump_to_page_top_and_retry_click", _retry_step("click", params))
            elif attempt == 2:
                # Strategy: try the keyboard default action instead of another click
                return ("keyboard_fallback_enter", _retry_step("press_key", {"keys": ["return"]}))
            else:
                return ("keyboard_fallback_space", _retry_step("press_key", {"keys": ["space"]}))

        elif step.action == "type_text":
            if reflection_hint == "refocus_text_field":
                params["_clear_first"] = True
                return ("reflection_refocus_then_type", _retry_step("type_text", params))
            if attempt == 1:
                # Strategy: select existing text first, then type again
                params["_clear_first"] = True
                return ("select_all_then_type", _retry_step("type_text", params))
            else:
                # Strategy: type character-by-character to avoid dropped keystrokes
                params["_slow_type"] = True
                return ("slow_type_retry", _retry_step("type_text", params))

        elif step.action == "press_key":
            if reflection_hint == "keyboard_submit":
                return ("reflection_keyboard_submit", _retry_step("press_key", {"keys": ["return"]}))
            if attempt == 1:
                params["_pre_delay"] = 0.5
                return ("delayed_key_press", _retry_step("press_key", params))
            else:
                params["_pre_delay"] = 1.0 * attempt
                return (f"extended_delay_key_press_{attempt}", _retry_step("press_key", params))

        elif step.action == "open_url":
            if attempt == 1:
                params["_address_bar_fallback"] = True
                return ("browser_address_bar_fallback", _retry_step("open_url", params))
            else:
                params["_pre_delay"] = 2.0 * attempt
                return (f"extended_delay_open_url_{attempt}", _retry_step("open_url", params))

        elif step.action == "activate_app":
            if attempt == 1:
                # Strategy: quit and relaunch
                params["_quit_first"] = True
                return ("quit_and_relaunch", _retry_step("activate_app", params))
            else:
                params["_spotlight"] = True
                params["_pre_delay"] = 1.0 * attempt
                return ("spotlight_launch", _retry_step("activate_app", params))

        elif step.action == "quit_app":
            if attempt == 1:
                params["_pre_delay"] = 0.5
                return ("delayed_quit", _retry_step("quit_app", params))
            else:
                params["_pre_delay"] = 1.0 * attempt
                return (f"extended_delay_quit_{attempt}", _retry_step("quit_app", params))

        else:
            # Generic fallback: add increasing delay
            params["_pre_delay"] = 0.5 * attempt
            return (f"generic_retry_with_delay_{attempt}", _retry_step(step.action, params))

    async def _replan_and_continue(
        self,
        goal,
        step_results,
        iterations,
        start_time,
        *,
        skill_name: Optional[str] = None,
        skill_context: Optional[str] = None,
        derived_session: Optional[DerivedSkillSession] = None,
        expanded_steps_for_distiller: Optional[str] = None,
    ):
        """Replan and attempt execution with new plan."""
        screen_desc = await self.coordinator.describe_screen()
        retry_strategies = []
        for sr in step_results:
            retry_strategies.extend(sr.retry_strategies_used)

        desktop_context = ""
        if self.context_monitor:
            self.context_monitor.update_cheap()
            desktop_context = self.context_monitor.format_for_planner()

        # Re-assemble skill_context with updated derived procedure
        replan_ctx = skill_context
        if derived_session and replan_ctx:
            # Strip old derived procedure and append fresh one
            replan_ctx = re.split(
                r"\n+---\n+## Derived Procedure\b", replan_ctx
            )[0]
            replan_ctx = (
                replan_ctx + "\n\n---\n\n"
                + derived_session.serialize_for_context()
            )

        self.logger.log_event(EventType.REPLAN_START, "Replanning...")
        new_plan = await self.planner.replan(
            goal,
            screen_desc,
            step_results,
            retry_strategies,
            desktop_context=desktop_context,
            skill_context=replan_ctx,
        )
        self.logger.log_event(
            EventType.REPLAN_COMPLETE, f"New plan: {len(new_plan.steps)} steps"
        )

        # Apply replan patch to derived session if present
        if derived_session and new_plan.replan_patch:
            derived_session.apply_patch(new_plan.replan_patch)
            n_labels = len(new_plan.replan_patch.replace_labels)
            n_landmarks = len(new_plan.replan_patch.add_landmarks)
            slog.info(
                f"Applied ReplanPatch: {n_labels} label replacements,"
                f" {n_landmarks} landmarks"
            )
        elif derived_session:
            slog.info(
                "No replan patch in LLM response, derived procedure unchanged"
            )

        # Execute new plan with proper failure handling
        for i, step in enumerate(new_plan.steps):
            if iterations >= self.config.max_iterations:
                break
            result = await self._execute_step(i, step, step_results, goal, new_plan)
            step_results.append(result)
            iterations += 1
            if step.action == "done":
                break
            # BUG 3 FIX: Handle wait_for_user in replan loop (matching main execute loop)
            if step.action == "wait_for_user":
                self.logger.log_event(
                    EventType.USER_WAIT,
                    f"Waiting for user: {step.params.get('message', '')}",
                )
                continue
            if not result.success:
                if step.on_fail == "abort":
                    break
                # Don't recurse into another replan — just record the failure
                self.logger.log_event(
                    EventType.STEP_RETRY,
                    f"Replan step {i} failed: {result.evidence}",
                    step_index=i,
                )

        duration = int((time.monotonic() - start_time) * 1000)
        # Check final state: success only if last step passed or was 'done'
        last_result = step_results[-1] if step_results else None
        success = last_result.success if last_result else False
        # skill_context arg is ignored when derived_session is set;
        # _maybe_learn_skill_run rebuilds distiller context from
        # expanded_steps_for_distiller + derived_session.
        await self._maybe_learn_skill_run(
            goal=goal,
            skill_name=skill_name,
            skill_context=skill_context or "",
            step_results=step_results,
            had_replan=True,
            derived_session=derived_session,
            expanded_steps_for_distiller=expanded_steps_for_distiller,
        )
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

    async def _maybe_learn_skill_run(
        self,
        *,
        goal: str,
        skill_name: Optional[str],
        skill_context: str,
        step_results: list[StepResult],
        had_replan: bool,
        derived_session: Optional[DerivedSkillSession] = None,
        expanded_steps_for_distiller: Optional[str] = None,
    ) -> None:
        """Best-effort skill learning from execution traces.

        Builds a distiller-specific context containing only the primary
        skill's original steps plus the derived procedure. Does NOT pass
        the full multi-skill context to the distiller.
        """
        if not skill_name or not step_results:
            return
        learn = getattr(self.skill_registry, "learn_from_run", None)
        if learn is None:
            return

        # Build distiller-specific context: primary skill + derived procedure
        distiller_ctx = skill_context
        if derived_session and expanded_steps_for_distiller is not None:
            distiller_ctx = expanded_steps_for_distiller
            derived_text = derived_session.serialize_for_context()
            if derived_text:
                distiller_ctx = distiller_ctx + "\n\n---\n\n" + derived_text

        try:
            observations = await learn(
                skill_name,
                goal,
                step_results,
                skill_context=distiller_ctx,
                run_id=self.logger.run_id,
                had_replan=had_replan,
            )
        except Exception:
            slog.warning("skill_learning_failed", skill_name=skill_name, exc_info=True)
            return
        if observations:
            self.logger.log_event(
                EventType.SKILL_EXPAND,
                f"Learned {len(observations)} generalized skill observation(s)",
                data={"skill_name": skill_name, "count": len(observations)},
            )
