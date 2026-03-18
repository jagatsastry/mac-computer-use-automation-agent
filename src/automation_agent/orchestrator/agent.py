"""AutomationAgent -- main orchestrator that coordinates planner, skills, vision, actuator, and verifier."""

import asyncio
import base64
import inspect
import io
import re
import time
import unicodedata
import urllib.parse
from dataclasses import dataclass, field as dataclass_field
from enum import Enum
from typing import Any, Callable, ClassVar, Optional, Tuple, Union

import structlog

from automation_agent.config import AgentConfig, ConfirmMode
from automation_agent.logging.event_logger import EventLogger
from automation_agent.logging.models import EventType
from automation_agent.protocols import CoordinatorCapability
from automation_agent.orchestrator.confirmation import (
    ConsoleConfirmationHandler,
    _sanitize_for_display,
)
from automation_agent.orchestrator.verifier import StepVerifier
from automation_agent.shared_models import (
    ActionPlan,
    ActionStep,
    ExecutionResult,
    FindElementResult,
    SkillMatchResult,
    StepResult,
    TEXT_INPUT_AX_ROLES,
    TEXT_INPUT_KEYWORDS,
)
from automation_agent.skills.derived_skill import DerivedSkillSession
from automation_agent.vision.geometry import image_to_screen_coords, screen_to_image_coords

slog = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Gap 5: Infeasibility Detection — FrustrationScore
# ---------------------------------------------------------------------------


@dataclass
class FrustrationScore:
    """Tracks diminishing-returns signals for infeasibility detection (AC-1).

    Lifecycle: Created FRESH at the top of each execute() call. Passed by
    reference to _check_infeasibility() and updated in-place. Discarded
    when execute() returns — NEVER stored on self.
    """

    same_state_count: int = 0
    identical_action_count: int = 0
    replan_count: int = 0
    advisory_checks_used: int = 0
    _last_action_key: str = ""
    _last_screenshot_hash: str = ""

    def reset_on_progress(self) -> None:
        """Reset same-state and identical-action on visible progress."""
        self.same_state_count = 0
        self.identical_action_count = 0

    def is_triggered(self, same_state_limit: int, replan_limit: int) -> bool:
        """AC-2: OR-based trigger."""
        return (
            self.same_state_count >= same_state_limit
            or self.replan_count >= replan_limit
        )

    def is_hard_abort(self, max_advisory: int) -> bool:
        """Hard abort after N advisory checks returned 'still achievable'."""
        return self.advisory_checks_used >= max_advisory


# ---------------------------------------------------------------------------
# Gap 6: Destructive Classification result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DestructiveClassification:
    """Result of _is_destructive_step() — replaces unnamed tuple."""

    NOT_DESTRUCTIVE: ClassVar["DestructiveClassification"]

    is_destructive: bool
    matched_keyword: Optional[str] = None
    classification_path: Optional[str] = None

    def __bool__(self) -> bool:
        return self.is_destructive


DestructiveClassification.NOT_DESTRUCTIVE = DestructiveClassification(
    is_destructive=False, matched_keyword=None, classification_path=None
)


# ---------------------------------------------------------------------------
# Gap 6: Phase 1 decision enum
# ---------------------------------------------------------------------------


class Phase1Decision(str, Enum):
    """Phase 1 confirmation decision."""

    CONFIRM = "confirm"
    SKIP = "skip"
    DEFER = "defer"


# ---------------------------------------------------------------------------
# Gap 6: PII redaction for audit logs
# ---------------------------------------------------------------------------

_SENSITIVE_PARAM_KEYS = frozenset({
    "text", "password", "card_number", "cvv", "ssn", "secret",
})

_SAFE_PARAM_KEYS = frozenset({
    "element", "key", "direction", "url", "app_name",
    "_clear_first", "_slow_type", "_pre_delay",
})


def _redact_params_for_log(params: dict) -> dict:
    """Redact PII-sensitive values from action params before logging."""
    redacted = {}
    for k, v in params.items():
        if k in _SENSITIVE_PARAM_KEYS:
            redacted[k] = "[REDACTED]"
        elif k in _SAFE_PARAM_KEYS:
            if k == "url" and isinstance(v, str) and "?" in v:
                parsed = urllib.parse.urlparse(v)
                redacted[k] = (
                    f"{parsed.scheme}://{parsed.netloc}{parsed.path}?[REDACTED]"
                )
            else:
                redacted[k] = v
        else:
            sv = str(v)
            redacted[k] = sv[:50] + "[truncated]" if len(sv) > 50 else sv
    return redacted


class AutomationAgent:
    """Main orchestrator that coordinates planner, skills, vision, actuator, and verifier."""

    # Confidence thresholds for Rec 2
    _DEFAULT_CONFIDENCE_THRESHOLD = 0.5
    _CRITICAL_CONFIDENCE_THRESHOLD = 0.9
    _CRITICAL_ACTION_KEYWORDS = frozenset(
        {"submit", "pay", "confirm", "reserve", "delete", "remove", "send",
         "purchase", "transfer", "authorize"}
    )

    # Pre-compiled regex patterns for word-boundary keyword matching
    _KEYWORD_PATTERNS: ClassVar[dict[str, re.Pattern]] = {
        kw: re.compile(rf"\b{kw}\b", re.IGNORECASE)
        for kw in _CRITICAL_ACTION_KEYWORDS
    }

    # Safe-navigation phrases exempt from the critical confidence threshold.
    # These contain critical keywords (e.g. "purchase") but are navigation
    # actions, not destructive commits.
    _SAFE_NAVIGATION_PHRASES: ClassVar[tuple[re.Pattern, ...]] = tuple(
        re.compile(p, re.IGNORECASE)
        for p in (
            r"\bpurchase\s+history\b",
            r"\border\s+history\b",
            r"\bview\s+order\b",
            r"\border\s+details\b",
            r"\bremove\s+filter\b",
            r"\bconfirm\s+address\b",
            r"\bsend\s+back\b",
            r"\breturn\s+purchase\b",
        )
    )

    # Hard-destructive keywords — never get confidence-based skip
    _HARD_DESTRUCTIVE_KEYWORDS = frozenset({
        "pay", "submit", "delete", "remove", "send",
        "purchase", "transfer", "authorize",
    })

    # Resolution threshold for Rec 4 cropping
    _CROP_WIDTH_THRESHOLD = 1440

    # wait_for_user polling config
    _WAIT_POLL_INTERVAL_S = 5.0
    _WAIT_TIMEOUT_S = 120.0
    _WAIT_DIFF_THRESHOLD = 0.02  # 2% pixel change = "screen changed"

    # Scroll recovery config
    _SCROLL_SETTLE_S: float = 1.0  # seconds to wait after scroll for lazy-loaded content

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
        confirmation_handler=None,
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
        self._confirmation_handler = (
            confirmation_handler or ConsoleConfirmationHandler()
        )
        # Rec 4: tracks (left, top, right, bottom) of last successful click
        # in screenshot_resolution pixel space. Reset at start of each execute().
        self.last_successful_region: Optional[Tuple[int, int, int, int]] = None
        # Current skill context for error recovery hint lookups
        self._current_skill_context: Optional[str] = None
        # Gap 5: Set by _execute_step, read by execute() for frustration tracking
        self._last_step_pixel_changed: Optional[bool] = None
        # P2-3: Expected domain for domain verification
        self._expected_domain: Optional[str] = None

    async def execute(self, goal: str) -> ExecutionResult:
        """Execute a natural language goal end-to-end."""
        self.last_successful_region = None  # Rec 4: reset for new task
        self._current_skill_context = None  # Reset for new task
        start = time.monotonic()
        slog.info("🎯 Executing goal", goal=goal)
        self.logger.log_event(EventType.TASK_START, f"Goal: {goal}", data={"goal": goal})

        step_results: list[StepResult] = []
        iterations = 0
        skill_name: Optional[str] = None
        skill_context: Optional[str] = None

        derived_session: Optional[DerivedSkillSession] = None
        expanded_steps_for_distiller: Optional[str] = None

        # Gap 5: Infeasibility detection — fresh per execute() call
        frustration = FrustrationScore()

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

            # Store skill context for error recovery hint lookups
            self._current_skill_context = skill_context

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

            # P2-3: Extract expected domain for single-site goals (before hardening)
            self._expected_domain = None
            try:
                from automation_agent.skills.router import extract_site_entity
                site_entities = extract_site_entity(goal)
                if site_entities and len(site_entities) == 1:
                    self._expected_domain = f"{site_entities[0]}.com"
            except ImportError:
                pass  # extract_site_entity not yet available
            except Exception as exc:
                slog.warning(
                    "domain_extraction_failed",
                    error=str(exc),
                    goal=goal,
                )

            # Apply all plan hardening checks
            plan = await self._harden_plan(plan, goal, skill_context, fallback_plan)

            slog.info("📋 Plan generated", step_count=len(plan.steps), goal=goal)
            plan_data = {"step_count": len(plan.steps)}
            if plan.raw_llm_response:
                plan_data["llm_response"] = plan.raw_llm_response
            plan_data["steps_summary"] = [
                f"{s.action}({s.params})" for s in plan.steps
            ]
            self.logger.log_event(
                EventType.PLAN_COMPLETE,
                f"Plan: {len(plan.steps)} steps",
                data=plan_data,
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
            _skip_next = False
            for i, step in enumerate(plan.steps):
                if _skip_next:
                    _skip_next = False
                    continue

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

                result, text_field_focused = await self._execute_step(i, step, step_results, goal, plan)

                # AC-1: Type-and-check bypass attempt
                bypass = await self._try_type_and_check_bypass(
                    i, step, result, text_field_focused, plan, step_results, goal,
                )
                if bypass is not None:
                    click_result, next_result, _ = bypass
                    step_results.append(click_result)
                    step_results.append(next_result)
                    iterations += 2
                    _skip_next = True

                    if click_result.success:
                        # Bypass succeeded -- both steps passed, skip next in loop
                        if self.context_monitor:
                            self._record_context(step, click_result)
                            self._record_context(next_result.step, next_result)
                        if next_result.step.action == "done":
                            break
                        continue

                    # Bypass attempted but both failed -- handle click failure
                    result = click_result
                else:
                    step_results.append(result)
                    iterations += 1

                # AC-4: On element NOT_FOUND, attempt scroll recovery
                # BEFORE context recording and frustration tracking, so that
                # a recovered success is what downstream bookkeeping sees.
                _scrollable_actions = {"click", "type_text"}
                if (
                    not result.success
                    and step.action in _scrollable_actions
                    and step.params.get("element")
                    and result.error
                    and "not found" in result.error.lower()
                ):
                    max_scrolls = step.params.get("_max_scrolls", 3)
                    scroll_result = await self._scroll_recovery(
                        step, result, step_results, goal, max_scrolls
                    )
                    if scroll_result is not None:
                        if scroll_result.success:
                            result = scroll_result
                            # Replace the original failure in step_results
                            # so the trace reflects the recovered outcome
                            if step_results and step_results[-1].step is step:
                                step_results[-1] = scroll_result
                        else:
                            infeas_result = await self._check_infeasibility(
                                goal, frustration, step_results, force=True
                            )
                            if infeas_result is not None:
                                infeas_result.total_duration_ms = int(
                                    (time.monotonic() - start) * 1000
                                )
                                infeas_result.iterations = iterations
                                infeas_result.goal = goal
                                infeas_result.run_id = self.logger.run_id
                                return infeas_result

                # Record context after actions (and after scroll recovery, so
                # recovered result is what bookkeeping sees)
                if self.context_monitor:
                    self._record_context(step, result)

                if step.action == "done":
                    # Generalized success gate: if a skill has a success_condition,
                    # verify it before accepting "done". If not met, replan.
                    try:
                        if skill_name and iterations < self.config.infeasibility_replan_limit:
                            skill_obj = self.skill_registry.get_skill(skill_name)
                            sc = (
                                skill_obj.success_condition
                                if skill_obj and isinstance(
                                    getattr(skill_obj, "success_condition", None), str
                                )
                                else ""
                            )
                            if sc:
                                slog.info(
                                    "🎯 Checking success condition before accepting done",
                                    condition=sc,
                                )
                                sc_step = ActionStep(
                                    action="done",
                                    params={},
                                    verify=sc,
                                )
                                sc_result = await self.verifier.verify(
                                    sc_step, {}, self.actuator, self.coordinator,
                                )
                                if not sc_result.success:
                                    slog.warning(
                                        "Success condition not met, replanning",
                                        condition=sc,
                                        evidence=sc_result.evidence,
                                    )
                                    self.logger.log_event(
                                        EventType.STEP_REPLAN,
                                        f"Success condition not met: {sc}. "
                                        f"Evidence: {sc_result.evidence}. Replanning.",
                                    )
                                    # Remove the premature "done" and replan
                                    step_results.pop()
                                    break  # Fall through to replan loop
                    except Exception as exc:
                        slog.debug("success_condition_check_error", error=str(exc))
                    break

                if step.action == "wait_for_user":
                    self.logger.log_event(
                        EventType.USER_WAIT,
                        f"Waiting for user: {step.params.get('message', '')}",
                    )
                    continue

                # ----------------------------------------------------------
                # Gap 5: Frustration tracking after each step
                # ----------------------------------------------------------
                # AC-1a: same-state detection via pixel diff OR semantic progress
                pixel_changed = self._last_step_pixel_changed or False
                semantic_progress = result.success and bool(step.verify)
                if pixel_changed or semantic_progress:
                    frustration.reset_on_progress()
                else:
                    frustration.same_state_count += 1

                # AC-1b: identical-action detection
                action_key = (
                    f"{step.action}:{sorted(step.params.items())}"
                )
                if action_key == frustration._last_action_key:
                    frustration.identical_action_count += 1
                else:
                    frustration.identical_action_count = 0
                frustration._last_action_key = action_key

                # AC-2: threshold-based trigger
                if frustration.is_triggered(
                    self.config.infeasibility_same_state_limit,
                    self.config.infeasibility_replan_limit,
                ):
                    if frustration.is_hard_abort(
                        self.config.infeasibility_max_advisory_checks
                    ):
                        duration = int(
                            (time.monotonic() - start) * 1000
                        )
                        return ExecutionResult(
                            success=False,
                            message="Exhausted advisory checks without"
                            " progress",
                            infeasibility_reason=(
                                f"Exhausted"
                                f" {self.config.infeasibility_max_advisory_checks}"
                                " advisory checks without progress"
                            ),
                            steps=step_results,
                            total_duration_ms=duration,
                            iterations=iterations,
                            goal=goal,
                            run_id=self.logger.run_id,
                        )
                    infeas_result = await self._check_infeasibility(
                        goal, frustration, step_results
                    )
                    if infeas_result is not None:
                        infeas_result.total_duration_ms = int(
                            (time.monotonic() - start) * 1000
                        )
                        infeas_result.iterations = iterations
                        infeas_result.goal = goal
                        infeas_result.run_id = self.logger.run_id
                        return infeas_result

                if not result.success:
                    # Handle failure based on on_fail strategy
                    recovery_result = await self._handle_failure(
                        i, step, result, step_results, goal, plan, iterations
                    )
                    if recovery_result is None:
                        # None means replan was requested
                        frustration.replan_count += 1
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
                            # Recovery also failed — stop executing
                            duration = int((time.monotonic() - start) * 1000)
                            if step.on_fail == "abort":
                                self.logger.log_event(
                                    EventType.TASK_FAIL, "Step failed with abort policy"
                                )
                            else:
                                self.logger.log_event(
                                    EventType.TASK_FAIL,
                                    f"Step {i} failed after recovery; stopping",
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

            # AC-6: Check if last step was a done-with-abort
            duration = int((time.monotonic() - start) * 1000)
            last_result = step_results[-1] if step_results else None
            if (
                last_result
                and last_result.step.action == "done"
                and last_result.step.params.get("abort_reason")
            ):
                self.logger.log_event(EventType.TASK_FAIL, f"Task aborted: {last_result.error}")
                self.logger.finalize(False, last_result.error)
                return ExecutionResult(
                    success=False,
                    message=f"Task aborted: {last_result.error}",
                    error=last_result.error,
                    steps=step_results,
                    total_duration_ms=duration,
                    iterations=iterations,
                    goal=goal,
                    run_id=self.logger.run_id,
                )

            # Success
            slog.info(
                "🏁 Task completed",
                duration_s=round(duration / 1000, 1),
                iterations=iterations,
            )
            observations = await self._maybe_learn_skill_run(
                goal=goal,
                skill_name=skill_name,
                skill_context=skill_context or "",
                step_results=step_results,
                had_replan=False,
                derived_session=derived_session,
                expanded_steps_for_distiller=expanded_steps_for_distiller,
            )
            await self._maybe_promote_skill(
                goal=goal,
                skill_name=skill_name,
                observations=observations,
                derived_session=derived_session,
                step_results=step_results,
                run_id=self.logger.run_id,
                had_replan=False,
                success=True,
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

    def _snapshot_state(self) -> tuple:
        """Return (app_name, browser_url) from the actuator."""
        try:
            s = self.actuator.get_state()
            return s.get("app_name", ""), s.get("browser_url", "")
        except Exception:
            return "", ""

    async def _execute_step(
        self,
        index: int,
        step: ActionStep,
        history: list,
        goal: str,
        plan: ActionPlan,
    ) -> Tuple[StepResult, bool]:
        """Execute a single step: find element if needed, act, verify.

        Returns:
            (StepResult, text_field_focused) where text_field_focused indicates
            whether a text input field was focused after a click action (AC-1).
        """
        _pre_app, _pre_url = self._snapshot_state()

        result, tf = await self._execute_step_inner(
            index, step, history, goal, plan,
        )

        _post_app, _post_url = self._snapshot_state()
        result.pre_state_app = _pre_app
        result.pre_state_url = _pre_url
        result.post_state_app = _post_app
        result.post_state_url = _post_url
        return result, tf

    async def _execute_step_inner(
        self,
        index: int,
        step: ActionStep,
        history: list,
        goal: str,
        plan: ActionPlan,
    ) -> Tuple[StepResult, bool]:
        """Inner step execution (wrapped by _execute_step for state capture)."""
        slog.info("🎯 Executing step", step_index=index, action=step.action, params=step.params)
        self.logger.log_event(
            EventType.STEP_START,
            f"Step {index}: {step.action}",
            step_index=index,
            data={"action": step.action, "params": step.params},
        )

        # Precondition check: assert what must be true before this step
        precondition = getattr(step, "precondition", "")
        if precondition and step.action not in ("done", "observe"):
            slog.info("🔍 Checking precondition", condition=precondition)
            pre_step = ActionStep(action="observe", params={}, verify=precondition)
            pre_result = await self.verifier.verify(
                pre_step, {}, self.actuator, self.coordinator,
            )
            if not pre_result.success:
                slog.warning(
                    "Precondition not met",
                    condition=precondition,
                    evidence=pre_result.evidence,
                )
                return StepResult(
                    step=step,
                    success=False,
                    verification_method=pre_result.verification_method,
                    evidence=f"Precondition failed: {precondition}. "
                    f"{pre_result.evidence}",
                    error=f"precondition_failed:{precondition[:60]}",
                ), False

        if step.action == "done":
            # AC-6: Graceful abort via done + abort_reason
            abort_reason = step.params.get("abort_reason")
            if abort_reason:
                return StepResult(
                    step=step,
                    success=False,
                    verification_method="",
                    evidence=f"Task aborted: {abort_reason}",
                    error=abort_reason,
                ), False
            return StepResult(
                step=step,
                success=True,
                verification_method="",
                evidence="Task marked as done",
            ), False

        if step.action == "wait_for_user":
            return await self._wait_for_user(step), False

        if step.action == "observe":
            desc = await self.coordinator.describe_screen()
            # AC-6: Save observe screenshot
            if self.config.save_step_screenshots:
                try:
                    obs_b64 = await self._capture_screenshot()
                    if obs_b64:
                        self.logger.save_screenshot(
                            base64.b64decode(obs_b64),
                            f"step_{index:02d}_observe",
                        )
                except Exception as exc:
                    slog.debug("Screenshot save failed", error=str(exc))
            return StepResult(
                step=step,
                success=True,
                verification_method="vision",
                evidence=f"Screen: {desc}",
            ), False

        # ----------------------------------------------------------
        # Gap 4: Lookahead prediction (only for destructive steps)
        # ----------------------------------------------------------
        classification = self._is_destructive_step(step)

        if (
            self.config.lookahead_enabled
            and classification.is_destructive
            and CoordinatorCapability.LOOKAHEAD in self.coordinator.capabilities()
        ):
            skip_lookahead = (
                self.config.lookahead_skip_when_confirmed
                and self.config.confirm_destructive != ConfirmMode.NEVER
                and self._should_confirm_phase1(step) != Phase1Decision.SKIP
            )

            _is_hard = (
                classification.matched_keyword is not None
                and classification.matched_keyword in self._HARD_DESTRUCTIVE_KEYWORDS
            ) or self.config.confirm_destructive == ConfirmMode.NEVER

            if not skip_lookahead:
                _la_start = time.monotonic()
                try:
                    screenshot = await self._capture_screenshot()
                    prediction = await asyncio.wait_for(
                        self.coordinator.predict_action_outcome(
                            action=step.action,
                            params=step.params,
                            expected_observation=(
                                step.expected_observation or step.verify
                            ),
                            screenshot_b64=screenshot,
                            is_hard_destructive=_is_hard,
                        ),
                        timeout=self.config.lookahead_timeout_s,
                    )
                except asyncio.TimeoutError:
                    _la_duration = int(
                        (time.monotonic() - _la_start) * 1000
                    )
                    slog.warning(
                        "lookahead_timeout",
                        action=step.action,
                        timeout_s=self.config.lookahead_timeout_s,
                        fallback="pessimistic" if _is_hard else "optimistic",
                    )
                    self.logger.log_event(
                        EventType.LOOKAHEAD_ERROR,
                        f"Lookahead timeout ({self.config.lookahead_timeout_s}s)",
                        step_index=index,
                        data={"action": step.action, "is_hard": _is_hard},
                        duration_ms=_la_duration,
                    )
                    prediction = (
                        {
                            "likely_success": False,
                            "reasoning": "Lookahead timed out",
                        }
                        if _is_hard
                        else {
                            "likely_success": True,
                            "reasoning": "Lookahead timed out (non-critical)",
                        }
                    )
                except Exception as e:
                    _la_duration = int(
                        (time.monotonic() - _la_start) * 1000
                    )
                    slog.error(
                        "lookahead_error",
                        action=step.action,
                        error=str(e),
                    )
                    self.logger.log_event(
                        EventType.LOOKAHEAD_ERROR,
                        f"Lookahead error: {e}",
                        step_index=index,
                        data={"action": step.action, "is_hard": _is_hard},
                        duration_ms=_la_duration,
                    )
                    prediction = (
                        {
                            "likely_success": False,
                            "reasoning": f"Lookahead error: {e}",
                        }
                        if _is_hard
                        else {
                            "likely_success": True,
                            "reasoning": f"Lookahead error (non-critical): {e}",
                        }
                    )
                else:
                    _la_duration = int(
                        (time.monotonic() - _la_start) * 1000
                    )

                self.logger.log_event(
                    EventType.LOOKAHEAD_PREDICT,
                    f"Lookahead: likely_success={prediction.get('likely_success')}",
                    step_index=index,
                    data={
                        "action": step.action,
                        "likely_success": prediction.get("likely_success"),
                        "reasoning": prediction.get("reasoning", ""),
                        "is_hard": _is_hard,
                    },
                    duration_ms=_la_duration,
                )
                slog.debug(
                    "lookahead_result",
                    action=step.action,
                    prediction=prediction,
                    duration_ms=_la_duration,
                    is_hard_destructive=_is_hard,
                )

                # AC-32: skip dispatch if prediction says failure
                if not prediction.get("likely_success", True):
                    self.logger.log_event(
                        EventType.LOOKAHEAD_BLOCK,
                        f"Lookahead blocked: {prediction.get('risk', '')}",
                        step_index=index,
                        data={
                            "action": step.action,
                            "risk": prediction.get("risk", ""),
                            "mismatch_reason": prediction.get(
                                "mismatch_reason", ""
                            ),
                        },
                    )
                    slog.warning(
                        "lookahead_blocked",
                        action=step.action,
                        risk=prediction.get("risk", ""),
                        mismatch_reason=prediction.get(
                            "mismatch_reason", ""
                        ),
                    )
                    return StepResult(
                        step=step,
                        success=False,
                        verification_method="lookahead",
                        evidence=(
                            "Lookahead predicted failure: "
                            + prediction.get("mismatch_reason", "")
                        ),
                        error=(
                            "Lookahead: "
                            + prediction.get("risk", "predicted failure")
                        ),
                        reflection_hint=prediction.get(
                            "mismatch_reason", ""
                        ),
                    ), False

        # ----------------------------------------------------------
        # Gap 6: Two-phase destructive action confirmation gate
        # ----------------------------------------------------------
        # TOCTOU: capture reference screenshot before confirmation dialog
        self._toctou_ref_b64 = None
        if (
            classification.is_destructive
            and classification.matched_keyword in self._HARD_DESTRUCTIVE_KEYWORDS
            and not getattr(self.config, "dry_run", False)
        ):
            try:
                self._toctou_ref_b64 = await self._capture_screenshot()
            except Exception:
                pass  # best-effort; TOCTOU check will skip if no reference

        _phase1_decision = None
        if classification.is_destructive:
            _phase1_decision = self._should_confirm_phase1(step)
            if _phase1_decision == Phase1Decision.SKIP:
                self._log_confirmation(
                    step,
                    "never_mode_auto_approved",
                    classification_path=classification.classification_path,
                    matched_keyword=classification.matched_keyword,
                    phase=1,
                )
            elif _phase1_decision == Phase1Decision.CONFIRM:
                if not getattr(self.config, "dry_run", False):
                    _confirm_start = time.monotonic()
                    confirmed = await self._prompt_user_confirmation(step)
                    _confirm_dur = int(
                        (time.monotonic() - _confirm_start) * 1000
                    )
                    self._log_confirmation(
                        step,
                        confirmed,
                        classification_path=classification.classification_path,
                        matched_keyword=classification.matched_keyword,
                        phase=1,
                        duration_ms=_confirm_dur,
                    )
                    if not confirmed:
                        return StepResult(
                            step=step,
                            success=False,
                            error="User denied destructive action",
                            evidence="User denied destructive action",
                        ), False
                else:
                    self._log_confirmation(
                        step,
                        "skipped_dry_run",
                        classification_path=classification.classification_path,
                        matched_keyword=classification.matched_keyword,
                        phase=1,
                    )
            # Phase1Decision.DEFER — handled after grounding in phase 2

        # ----------------------------------------------------------
        # Gap 6: Phase 2 — pre-dispatch grounding + confirmation for DEFER'd clicks
        # ----------------------------------------------------------
        # For DEFER'd destructive clicks with "element", ground FIRST to get
        # confidence, run phase 2, and only dispatch if approved.
        _pre_resolved_location: Optional[FindElementResult] = None
        if (
            classification.is_destructive
            and _phase1_decision == Phase1Decision.DEFER
            and step.action == "click"
            and "element" in step.params
        ):
            _pre_resolved_location = await self._find_element(
                step.params["element"]
            )
            if _pre_resolved_location is not None:
                grounding_confidence = _pre_resolved_location.confidence
                if self._should_confirm_phase2(
                    step, grounding_confidence, classification.matched_keyword
                ):
                    if not getattr(self.config, "dry_run", False):
                        _confirm_start = time.monotonic()
                        confirmed = await self._prompt_user_confirmation(step)
                        _confirm_dur = int(
                            (time.monotonic() - _confirm_start) * 1000
                        )
                        self._log_confirmation(
                            step,
                            confirmed,
                            classification_path=classification.classification_path,
                            matched_keyword=classification.matched_keyword,
                            phase=2,
                            confidence=grounding_confidence,
                            duration_ms=_confirm_dur,
                        )
                        if not confirmed:
                            return StepResult(
                                step=step,
                                success=False,
                                error="User denied destructive action (phase 2)",
                                evidence="User denied destructive action (phase 2)",
                            ), False
                    else:
                        self._log_confirmation(
                            step,
                            "skipped_dry_run",
                            classification_path=classification.classification_path,
                            matched_keyword=classification.matched_keyword,
                            phase=2,
                            confidence=grounding_confidence,
                        )
                else:
                    # Confidence high enough — auto-approve
                    self._log_confirmation(
                        step,
                        "phase2_auto_approved",
                        classification_path=classification.classification_path,
                        matched_keyword=classification.matched_keyword,
                        phase=2,
                        confidence=grounding_confidence,
                    )
            # If _pre_resolved_location is None, element not found —
            # _dispatch_action will handle the error normally.

        # Gap 6 TOCTOU mitigation: for hard-destructive steps that went through
        # user confirmation, take a post-confirmation screenshot and diff against
        # the pre-confirmation state to detect UI changes between grounding and
        # dispatch. Only applies when confirmation actually paused execution.
        if (
            classification.is_destructive
            and _phase1_decision in (Phase1Decision.CONFIRM, Phase1Decision.DEFER)
            and classification.matched_keyword in self._HARD_DESTRUCTIVE_KEYWORDS
            and not getattr(self.config, "dry_run", False)
        ):
            try:
                post_confirm_b64 = await self._capture_screenshot()
                # Compare against pre-grounding screenshot if we captured one
                # during lookahead, otherwise skip (no reference point).
                if hasattr(self, "_toctou_ref_b64") and self._toctou_ref_b64:
                    import base64 as _b64
                    import io as _io

                    from PIL import Image as _Img

                    ref_img = _Img.open(
                        _io.BytesIO(_b64.b64decode(self._toctou_ref_b64))
                    ).convert("L")
                    post_img = _Img.open(
                        _io.BytesIO(_b64.b64decode(post_confirm_b64))
                    ).convert("L")
                    diff_ratio = self._image_diff_ratio(ref_img, post_img)

                    if diff_ratio > self.config.infeasibility_same_state_threshold:
                        slog.warning(
                            "toctou_ui_changed",
                            diff_ratio=round(diff_ratio, 3),
                            threshold=self.config.infeasibility_same_state_threshold,
                            action=step.action,
                        )
                        return StepResult(
                            step=step,
                            success=False,
                            error=(
                                "UI changed during confirmation "
                                f"(diff={diff_ratio:.1%}). Re-plan needed."
                            ),
                            evidence="TOCTOU: UI state changed between grounding and dispatch",
                            reflection_hint="The page changed while waiting for user confirmation. "
                            "Re-ground the element before retrying.",
                        ), False
                    self._toctou_ref_b64 = None  # consumed
            except Exception as e:
                slog.debug("toctou_check_skipped", error=str(e))

        # Capture screenshot before visually meaningful actions for diff-based verification.
        if self.screenshot_diff and step.action in ("click", "open_url"):
            self.screenshot_diff.capture_before()

        # Dispatch action (pass pre-resolved location to avoid double-grounding)
        actuator_result = await self._dispatch_action(
            step, _pre_resolved_location=_pre_resolved_location
        )

        # AC-1: Capture focused element AX role immediately after click dispatch,
        # BEFORE the screenshot_diff gate can force failure.
        _text_field_focused = False
        if step.action == "click" and actuator_result.get("success", False):
            _text_field_focused = self._check_text_field_focused()

        visible_effect = None
        self._last_step_pixel_changed = None

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

            self._last_step_pixel_changed = bool(visible_effect)

            if not visible_effect:
                if step.action == "open_url":
                    actuator_result["_no_visible_change"] = True
                    slog.info(
                        "open_url had no visible effect, deferring to verification",
                        url=step.params.get("url", ""),
                    )
                else:
                    actuator_result["success"] = False
                    actuator_result["error"] = (
                        f"{step.action} had no visible effect"
                        " (screenshot unchanged)"
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
            return result, _text_field_focused

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

        step_complete_data = {
            "success": verification.success,
            "method": verification.verification_method,
            "evidence": verification.evidence,
        }
        if verification.reflection_hint:
            step_complete_data["reflection_hint"] = verification.reflection_hint
        if verification.suggested_element:
            step_complete_data["suggested_element"] = verification.suggested_element
        if verification.reflection_observed:
            step_complete_data["reflection_observed"] = verification.reflection_observed
        self.logger.log_event(
            EventType.STEP_COMPLETE,
            f"Step {index}: {'PASS' if verification.success else 'FAIL'} -- {verification.evidence}",
            step_index=index,
            data=step_complete_data,
        )

        # Save annotated verification screenshot with crosshair at action point
        if (
            step.action == "click"
            and verification.screenshot_path
            and actuator_result.get("success")
        ):
            ax = actuator_result.get(
                "image_x", actuator_result.get("x", 0)
            )
            ay = actuator_result.get(
                "image_y", actuator_result.get("y", 0)
            )
            if ax and ay:
                try:
                    with open(verification.screenshot_path, "rb") as f:
                        verify_b64 = base64.b64encode(f.read()).decode()
                    method = verification.verification_method or "unknown"
                    label = (
                        f"verify={step.verify[:30]} "
                        f"via {method}"
                    )
                    self._save_annotated_screenshot(
                        verify_b64,
                        ax,
                        ay,
                        label,
                        f"verify_step_{index:02d}_{step.action}",
                        verification.success,
                    )
                except Exception:
                    pass

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

        # AC-6: Save post-action screenshot
        if self.config.save_step_screenshots:
            try:
                post_b64 = await self._capture_screenshot()
                if post_b64:
                    path = self.logger.save_screenshot(
                        base64.b64decode(post_b64),
                        f"step_{index:02d}_post_{step.action}",
                    )
                    verification.screenshot_path = path

                    # Save annotated version in debug/ with crosshair at action point
                    if step.action == "click" and actuator_result.get("success"):
                        ax = actuator_result.get(
                            "image_x", actuator_result.get("x", 0)
                        )
                        ay = actuator_result.get(
                            "image_y", actuator_result.get("y", 0)
                        )
                        if ax and ay:
                            self._save_annotated_screenshot(
                                post_b64,
                                ax,
                                ay,
                                f"click {step.params.get('element', '')[:40]}",
                                f"post_step_{index:02d}_{step.action}",
                                verification.success,
                            )
            except Exception as exc:
                slog.debug("Screenshot save failed", error=str(exc))

        return verification, _text_field_focused

    def _check_text_field_focused(self) -> bool:
        """Check if the currently focused element is a text input field.

        Uses accessibility backend if available, returns False otherwise.
        Skips async accessibility backends (same guard as verifier._get_accessibility_backend).
        """
        accessibility = getattr(self.coordinator, "accessibility", None)
        if accessibility is None:
            return False
        method = getattr(accessibility, "get_focused_element", None)
        if method is None:
            return False
        if inspect.iscoroutinefunction(method) or "AsyncMock" in type(method).__name__:
            return False
        try:
            focused = method()
        except Exception:
            return False
        if focused is None:
            return False
        role = getattr(focused, "role", None) or ""
        return role in TEXT_INPUT_AX_ROLES

    @staticmethod
    def _is_text_field_by_keywords(step: ActionStep) -> bool:
        """Keyword fallback for text field detection when AX is unavailable."""
        element_desc = str(step.params.get("element", "")).lower()
        return any(kw in element_desc for kw in TEXT_INPUT_KEYWORDS)

    # ------------------------------------------------------------------
    # Gap 5: Infeasibility Detection
    # ------------------------------------------------------------------

    async def _check_infeasibility(
        self,
        goal: str,
        frustration: "FrustrationScore",
        step_results: list[StepResult],
        force: bool = False,
    ) -> Optional[ExecutionResult]:
        """AC-2: Ask planner if task is achievable."""
        _MAX_ABSENT_LEN = 200
        _MAX_HISTORY_LEN = 500
        _MAX_ITEMS = 10

        absent = [
            sr.error[len("Element absent:"):].strip()[:_MAX_ABSENT_LEN]
            for sr in step_results
            if sr.error and sr.error.startswith("Element absent:")
        ][:_MAX_ITEMS]

        failure_history = [
            sr.evidence[:_MAX_HISTORY_LEN]
            for sr in step_results if not sr.success
        ][:_MAX_ITEMS]

        _infeas_start = time.monotonic()
        try:
            response = await asyncio.wait_for(
                self.planner.check_infeasibility(
                    goal=goal,
                    absent_elements=absent,
                    failure_history=failure_history,
                    frustration_summary={
                        "same_state_count": frustration.same_state_count,
                        "replan_count": frustration.replan_count,
                        "identical_action_count": frustration.identical_action_count,
                    },
                ),
                timeout=self.config.infeasibility_timeout_s,
            )
        except asyncio.TimeoutError:
            _dur = int((time.monotonic() - _infeas_start) * 1000)
            self.logger.log_event(
                EventType.INFEASIBILITY_CHECK,
                "Infeasibility check timed out",
                data={"force": force, "timeout": True},
                duration_ms=_dur,
            )
            return ExecutionResult(
                success=False,
                message="Infeasibility check timed out",
                error="LLM infeasibility check did not respond within timeout",
                infeasibility_reason=(
                    "Infeasibility check timed out — treating as infeasible"
                ),
                steps=step_results,
            )

        _dur = int((time.monotonic() - _infeas_start) * 1000)
        self.logger.log_event(
            EventType.INFEASIBILITY_CHECK,
            f"Infeasibility check: force={force}",
            data={"force": force},
            duration_ms=_dur,
        )

        if response["infeasible"]:
            self.logger.log_event(
                EventType.INFEASIBILITY_ABORT,
                f"Task declared infeasible: {response['reason']}",
                data={
                    "reason": response["reason"],
                    "absent_elements": absent,
                },
            )
            return ExecutionResult(
                success=False,
                message=f"Task infeasible: {response['reason']}",
                error=response["reason"],
                infeasibility_reason=response["reason"],
                steps=step_results,
            )

        # Planner says achievable — increment advisory count
        frustration.advisory_checks_used += 1
        if (
            frustration.same_state_count
            >= self.config.infeasibility_same_state_limit
        ):
            frustration.same_state_count = 0
        return None

    # ------------------------------------------------------------------
    # Gap 6: Destructive Action Classification & Confirmation
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_for_matching(text: str) -> str:
        """NFKC-normalize text before keyword matching."""
        return unicodedata.normalize("NFKC", text)

    def _is_destructive_step(
        self, step: ActionStep
    ) -> "DestructiveClassification":
        """AC-6: Classify a step as destructive."""
        if step.destructive:
            return DestructiveClassification(True, None, "planner_flag")

        texts_to_check = [
            self._normalize_for_matching(step.verify.lower())
            if step.verify
            else "",
            self._normalize_for_matching(
                str(step.params.get("element", "")).lower()
            ),
        ]

        # Safe-navigation exemption: phrases like "Purchase History" or
        # "Order History" contain critical keywords but are navigation
        # actions, not destructive commits.
        combined = " ".join(texts_to_check)
        if any(pat.search(combined) for pat in self._SAFE_NAVIGATION_PHRASES):
            return DestructiveClassification.NOT_DESTRUCTIVE

        for text in texts_to_check:
            for kw, pattern in self._KEYWORD_PATTERNS.items():
                if pattern.search(text):
                    return DestructiveClassification(
                        True, kw, "keyword_match"
                    )

        return DestructiveClassification.NOT_DESTRUCTIVE

    def _should_confirm_phase1(
        self, step: ActionStep
    ) -> "Phase1Decision":
        """AC-8 phase 1: Pre-grounding confirmation decision."""
        mode = self.config.confirm_destructive

        if mode == ConfirmMode.NEVER:
            return Phase1Decision.SKIP
        elif mode == ConfirmMode.ALWAYS:
            return Phase1Decision.CONFIRM
        elif step.destructive:
            return Phase1Decision.CONFIRM
        elif step.action != "click":
            return Phase1Decision.CONFIRM
        else:
            return Phase1Decision.DEFER

    def _should_confirm_phase2(
        self,
        step: ActionStep,
        confidence: float,
        matched_keyword: Optional[str] = None,
    ) -> bool:
        """AC-8 phase 2: Post-grounding confirmation."""
        if step.destructive:
            return True
        if (
            matched_keyword
            and matched_keyword in self._HARD_DESTRUCTIVE_KEYWORDS
        ):
            return True
        return confidence < self._CRITICAL_CONFIDENCE_THRESHOLD

    async def _prompt_user_confirmation(
        self, step: ActionStep
    ) -> bool:
        """AC-7: Delegate to the injected ConfirmationHandler."""
        try:
            return await self._confirmation_handler.confirm(step)
        except Exception as e:
            self.logger.log_event(
                EventType.DESTRUCTIVE_CONFIRM_ERROR,
                f"Confirmation handler error: {e}",
                data={"action": step.action, "error": str(e)},
            )
            return False

    def _log_confirmation(
        self,
        step: ActionStep,
        decision: Union[bool, str],
        classification_path: Optional[str] = None,
        matched_keyword: Optional[str] = None,
        phase: Optional[int] = None,
        confidence: Optional[float] = None,
        duration_ms: Optional[int] = None,
    ) -> None:
        """AC-10: Log every confirmation decision to JSONL event log."""
        if isinstance(decision, bool):
            decision_str = "approved" if decision else "denied"
        else:
            decision_str = decision

        self.logger.log_event(
            EventType.DESTRUCTIVE_CONFIRM,
            f"Destructive action {decision_str}: {step.action}",
            data={
                "action": step.action,
                "params": _redact_params_for_log(step.params),
                "verify": step.verify,
                "destructive_flag": step.destructive,
                "decision": decision_str,
                "classification_path": classification_path,
                "matched_keyword": matched_keyword,
                "phase": phase,
                "confidence": confidence,
            },
            duration_ms=duration_ms,
        )

    async def _try_type_and_check_bypass(
        self,
        i: int,
        step: ActionStep,
        result: StepResult,
        text_field_focused: bool,
        plan: ActionPlan,
        step_results: list,
        goal: str,
    ) -> Optional[Tuple[StepResult, StepResult, bool]]:
        """Attempt type-and-check bypass for a failed click on a text field.

        Returns:
            None if bypass is not applicable (caller should use normal failure path).
            (click_result, next_result, next_text_field_focused) if bypass was attempted:
              - click_result: replacement StepResult for the click (success=True if
                next step passed, original failed result if next step also failed)
              - next_result: StepResult for the next step
              - next_text_field_focused: whether the next step also focused a text field
        """
        if not (
            not result.success
            and step.action == "click"
            and (text_field_focused or self._is_text_field_by_keywords(step))
            and i + 1 < len(plan.steps)
            and plan.steps[i + 1].action in ("type_text", "press_key")
        ):
            return None  # Not eligible

        next_step = plan.steps[i + 1]
        next_result, next_tf = await self._execute_step(
            i + 1, next_step, step_results, goal, plan
        )

        if next_result.success:
            # Retroactively confirm the click
            replacement = StepResult(
                step=step,
                success=True,
                verification_method="type_and_check",
                evidence="Verified by subsequent keystroke step success (type-and-check)",
                duration_ms=result.duration_ms,
            )
            return replacement, next_result, next_tf
        else:
            # Both failed -- return original click failure + next failure
            return result, next_result, next_tf

    async def _harden_plan(
        self,
        plan: ActionPlan,
        goal: str,
        skill_context: Optional[str],
        fallback_plan: Optional[ActionPlan],
    ) -> ActionPlan:
        """Apply all plan hardening checks. Used by both initial plan and replan.

        Steps:
        1. Trivial done / truncated plan -> fallback replacement
        2. Navigation enforcement
        3. Domain verification injection
        4. Click on_fail normalization (abort -> retry_different)

        Validation is caller responsibility: _execute hard-fails, replan soft-warns.
        """
        # 1. Trivial done / truncated plan -> fallback replacement
        replace_with_fallback = False
        if self._is_trivial_done_plan(plan) and fallback_plan is not None:
            if await self._plan_already_satisfied(fallback_plan):
                slog.info(
                    "trivial_done_plan_accepted",
                    reason="fallback condition already met",
                )
            else:
                replace_with_fallback = True
                slog.warning(
                    "trivial_done_plan_rejected",
                    reason="fallback target not yet satisfied",
                    goal=goal,
                )
        elif self._is_truncated_plan(plan, fallback_plan):
            replace_with_fallback = True
            slog.warning(
                "truncated_plan_detected",
                plan_steps=len(plan.steps),
                fallback_steps=len(fallback_plan.steps) if fallback_plan else 0,
                goal=goal,
            )
        if replace_with_fallback and fallback_plan is not None:
            self.logger.log_event(
                EventType.SKILL_EXPAND,
                f"Replacing incomplete plan with skill fallback"
                f" ({len(fallback_plan.steps)} steps)",
                data={"step_count": len(fallback_plan.steps)},
            )
            plan = fallback_plan

        # 2. Navigation enforcement
        if skill_context and fallback_plan and not self._is_trivial_done_plan(plan):
            plan = AutomationAgent._ensure_skill_navigation(plan, fallback_plan)

        # 3. Domain verification
        if self._expected_domain:
            self._inject_domain_verification(plan, self._expected_domain)

        # 4. Click on_fail normalization
        if skill_context:
            for step in plan.steps:
                if step.action == "click" and step.on_fail == "abort":
                    step.on_fail = "retry_different"

        return plan

    @staticmethod
    def _is_trivial_done_plan(plan: ActionPlan) -> bool:
        """Return True when the plan is only a single done step."""
        return len(plan.steps) == 1 and plan.steps[0].action == "done"

    @staticmethod
    def _is_truncated_plan(plan: ActionPlan, fallback: Optional[ActionPlan]) -> bool:
        """Return True when the LLM plan is suspiciously shorter than the skill template.

        Uses interaction step *counts* (not just presence) to catch plans that
        have a single click but miss the full add-to-cart workflow.

        NOTE: Fixed threshold of 3 is calibrated for current skills (max 4
        interactions). For skills with 6+ interaction steps, consider
        ratio-based: plan < fallback // 2.
        """
        if fallback is None:
            return False
        interaction_actions = {"click", "type_text", "scroll"}
        plan_interactions = sum(
            1 for s in plan.steps if s.action in interaction_actions
        )
        fallback_interactions = sum(
            1 for s in fallback.steps if s.action in interaction_actions
        )
        # No interactions at all but fallback has some -> truncated
        if fallback_interactions > 0 and plan_interactions == 0:
            return True
        # Fallback has 3+ interactions but plan has fewer than 3 -> truncated
        if fallback_interactions >= 3 and plan_interactions < 3:
            return True
        return False

    def _inject_domain_verification(
        self, plan: ActionPlan, expected_domain: str
    ) -> None:
        """P2-3: Append domain constraint to open_url verify fields.

        Only injects when the step's URL matches the expected domain,
        preventing poisoning of non-target open_url steps in multi-domain plans.
        """
        marker = "browser domain is"
        for step in plan.steps:
            if step.action == "open_url" and step.verify:
                # PB7: Only inject when step URL matches expected domain
                step_url = step.params.get("url", "")
                if not step_url:
                    # Empty/missing URL — skip injection (no domain to verify)
                    slog.debug(
                        "domain_injection_skipped",
                        step_url=step_url,
                        expected_domain=expected_domain,
                        reason="empty_url",
                    )
                    continue
                step_host = urllib.parse.urlparse(step_url).hostname
                if step_host is None:
                    # Scheme-less URL (e.g., "target.com/page") — urlparse returns
                    # hostname=None. Skip injection with debug log. (R1-2)
                    slog.debug(
                        "domain_injection_skipped",
                        step_url=step_url,
                        expected_domain=expected_domain,
                        reason="no_hostname_parsed",
                    )
                    continue
                if step_host != expected_domain and not step_host.endswith(
                    f".{expected_domain}"
                ):
                    slog.debug(
                        "domain_injection_skipped",
                        step_url=step_url,
                        step_host=step_host,
                        expected_domain=expected_domain,
                        reason="domain_mismatch",
                    )
                    continue
                if marker not in step.verify:
                    step.verify = (
                        f"{step.verify} AND browser domain is"
                        f" {expected_domain}"
                    )
                    # Source entity = domain minus .com suffix
                    source_entity = expected_domain.replace(".com", "")
                    slog.debug(
                        "domain_verification_injected",
                        expected_domain=expected_domain,
                        source_entity=source_entity,
                        step_action=step.action,
                        step_url=step_url,
                    )

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

        for instruction, verify, on_fail in self._parse_skill_steps(primary_section):
            action_steps = self._compile_skill_instruction(instruction, verify)
            if action_steps is None:
                slog.warning(
                    "skill_fallback_unrecognized_step",
                    step_text=instruction[:80],
                )
                continue
            # Apply on_fail metadata to the LAST compiled step
            if action_steps and on_fail:
                last_step = action_steps[-1]
                on_fail_lower = on_fail.lower().strip()
                # Map recognised on_fail values to the compiled step
                _VALID_ON_FAIL = {"replan", "abort", "retry_different"}
                if on_fail_lower in _VALID_ON_FAIL:
                    last_step.on_fail = on_fail_lower
                if "scroll" in on_fail_lower:
                    last_step.params["_scroll_recovery"] = True
                    last_step.params["_max_scrolls"] = 3
                if "wait_for_user" in on_fail_lower or "log in" in on_fail_lower:
                    last_step.on_fail = "wait_for_user"
                    wait_condition = self._extract_wait_condition(on_fail)
                    if wait_condition:
                        wait_step = ActionStep(
                            action="wait_for_user",
                            params={"message": on_fail, "condition": wait_condition},
                            verify="",
                            on_fail="abort",
                        )
                        action_steps.append(wait_step)
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
    def _ensure_skill_navigation(
        plan: ActionPlan,
        fallback_plan: ActionPlan,
    ) -> ActionPlan:
        """Prepend skill-mandated navigation if the LLM plan omits it.

        Checks whether the first navigation step from the fallback (skill) plan
        is present in the first 3 steps of the LLM plan. If missing, prepends it.
        """
        nav_actions = {"open_url", "activate_app"}

        # Find the first nav step in the fallback plan
        fallback_nav = None
        for step in fallback_plan.steps:
            if step.action in nav_actions:
                fallback_nav = step
                break

        if fallback_nav is None:
            return plan  # Skill has no navigation — nothing to enforce

        # Check if the LLM plan already has a nav step in its first 3 steps
        head = plan.steps[:3]
        has_nav = any(s.action in nav_actions for s in head)
        if has_nav:
            return plan

        # Prepend the skill's navigation step
        slog.warning(
            "LLM plan missing skill-mandated navigation — prepending",
            nav_action=fallback_nav.action,
            nav_params=fallback_nav.params,
        )
        new_steps = [fallback_nav] + list(plan.steps)
        return ActionPlan(
            steps=new_steps,
            goal=plan.goal,
            skill_name=plan.skill_name,
            raw_llm_response=plan.raw_llm_response,
            planning_duration_ms=plan.planning_duration_ms,
            token_usage=plan.token_usage,
            replan_patch=plan.replan_patch,
        )

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
    def _parse_skill_steps(skill_context: str) -> list[tuple[str, str, str]]:
        """Parse numbered skill text into (instruction, verify, on_fail) tuples."""
        steps: list[tuple[str, str, str]] = []
        instruction: Optional[str] = None
        verify = ""
        on_fail = ""

        for raw_line in skill_context.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            step_match = re.match(r"^\d+\.\s+(.*)$", line)
            if step_match:
                if instruction is not None:
                    steps.append((instruction, verify, on_fail))
                instruction = step_match.group(1).strip()
                verify = ""
                on_fail = ""
                continue
            if instruction and line.lower().startswith("- verify:"):
                verify = line.split(":", 1)[1].strip()
            elif instruction and line.lower().startswith("- on_fail:"):
                on_fail = line.split(":", 1)[1].strip()

        if instruction is not None:
            steps.append((instruction, verify, on_fail))
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
            # URL-encode spaces in URLs (from param substitution)
            if re.match(r"^https?://", destination, flags=re.IGNORECASE):
                raw_destination = destination  # capture for debug logging
                parsed = urllib.parse.urlparse(destination)
                # Always encode path spaces if present (component-based)
                if " " in (parsed.path or ""):
                    encoded_path = urllib.parse.quote(
                        urllib.parse.unquote(parsed.path), safe='/'
                    )
                    parsed = parsed._replace(path=encoded_path)
                # Re-encode query params to handle spaces
                if parsed.query:
                    params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
                    encoded_query = urllib.parse.urlencode(params, doseq=True)
                    parsed = parsed._replace(query=encoded_query)
                destination = urllib.parse.urlunparse(parsed)
                if destination != raw_destination:
                    slog.debug("url_encoded", before=raw_destination, after=destination)
                # Log which compiler branch matched and extracted params (OB PB-4)
                enc_parts = []
                if " " in (urllib.parse.urlparse(raw_destination).path or ""):
                    enc_parts.append("component_path")
                if urllib.parse.urlparse(raw_destination).query:
                    enc_parts.append("query_param")
                enc_method = "+".join(enc_parts) if enc_parts else "none"
                slog.debug(
                    "compiler_branch_matched",
                    branch="navigate_url",
                    destination=destination,
                    encoding_method=enc_method,
                )
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

        scroll_match = re.match(
            r"^Scroll\s+(down|up)(?:\s+.+)?$",
            text,
            flags=re.IGNORECASE,
        )
        if scroll_match:
            direction = scroll_match.group(1).lower()
            return [
                ActionStep(
                    action="scroll",
                    params={"direction": direction, "amount": 3},
                    verify=verify or "Page has scrolled",
                    expected_observation=verify or f"The page scrolls {direction}",
                    on_fail="retry_different",
                    max_retries=1,
                )
            ]

        type_match = re.match(
            r'^Type\s+"?(.+?)"?\s+(?:in the (.+?)\s+)?(?:and|then)\s+press\s+Enter$',
            text,
            flags=re.IGNORECASE,
        )
        if type_match:
            typed_text = type_match.group(1).strip()
            element_desc = type_match.group(2)
            type_params: dict[str, Any] = {"text": typed_text}
            if element_desc:
                type_params["element"] = element_desc.strip()
            else:
                # Default: ask find_element to locate a text input field
                type_params["element"] = "search or text input field"
            return [
                ActionStep(
                    action="type_text",
                    params=type_params,
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
        stripped = text.strip()

        # Pattern 1: "If X, wait for user..." / "If X, use wait_for_user..."
        match = re.match(
            r"^If\s+(.+?),\s*(?:wait for (?:the )?user|use wait_for_user)"
            r"(?:\s+to\s+.+)?(?:,\s*then\s+.+)?$",
            stripped,
            flags=re.IGNORECASE,
        )
        if match:
            condition = match.group(1).strip().rstrip(".")
            condition = re.sub(r"(?i)\bappears?\b", "is visible", condition)
            condition = re.sub(r"\s+", " ", condition).strip()
            if not re.search(
                r"(?i)\b(?:is|are|visible|shown|loaded|frontmost)\b",
                condition,
            ):
                condition = f"{condition} is visible"
            return condition

        # Pattern 2: Login/sign-in variants
        login_match = re.match(
            r"^(?:Please\s+)?(?:You\s+(?:need|may need)\s+to\s+)?"
            r"(?:log|sign)\s+in\s+to\s+(.+?)(?:\s+first)?$",
            stripped,
            flags=re.IGNORECASE,
        )
        if login_match:
            site = login_match.group(1).strip().rstrip(".")
            return f"{site} login page is visible"

        # Pattern 3: "Please complete X" / "Complete X"
        complete_match = re.match(
            r"^(?:Please\s+)?complete\s+(.+)$",
            stripped,
            flags=re.IGNORECASE,
        )
        if complete_match:
            task = complete_match.group(1).strip().rstrip(".")
            return f"{task} form is visible"

        return ""

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

    async def _dispatch_action(
        self,
        step: ActionStep,
        _pre_resolved_location: Optional["FindElementResult"] = None,
    ) -> dict:
        """Dispatch an action to the actuator.

        Args:
            _pre_resolved_location: If provided (for destructive click steps
                that were grounded during the phase 2 confirmation gate), skip
                the _find_element call and use this location directly.
        """
        action = step.action
        params = dict(step.params)

        # Narrate intent: what we're about to do and what we see
        _pre_app, _pre_url = self._snapshot_state()
        intent_parts = [f"I will {action}"]
        if action == "click" and params.get("element"):
            intent_parts[0] = f"I will click '{params['element']}'"
        elif action == "type_text" and params.get("text"):
            intent_parts[0] = f"I will type '{params['text'][:40]}'"
        elif action == "open_url" and params.get("url"):
            intent_parts[0] = f"I will open {params['url'][:60]}"
        elif action == "press_key" and params.get("keys"):
            intent_parts[0] = f"I will press {params['keys']}"
        elif action == "activate_app" and params.get("app_name"):
            intent_parts[0] = f"I will activate {params['app_name']}"
        if _pre_app:
            intent_parts.append(f"Currently focused: {_pre_app}")
        if _pre_url:
            intent_parts.append(f"URL: {_pre_url[:60]}")
        self.logger.log_event(
            EventType.NARRATE_INTENT,
            " | ".join(intent_parts),
            data={"app": _pre_app, "url": _pre_url},
        )

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
                    location = (
                        _pre_resolved_location
                        or await self._find_element(params["element"])
                    )
                    if location is None:
                        self.logger.log_event(
                            EventType.ELEMENT_NOT_FOUND,
                            f"Element not found: {params['element']}",
                        )
                        return {
                            "success": False,
                            "error": f"Element not found: {params['element']}",
                        }

                    # Keyboard shortcut fast path: press keys instead of clicking
                    if location.source == "keyboard_shortcut":
                        import ast
                        try:
                            keys = ast.literal_eval(location.raw_response)
                        except Exception:
                            keys = [location.raw_response]
                        slog.info(
                            "keyboard_shortcut_dispatch",
                            element=params["element"],
                            keys=keys,
                        )
                        for key_combo in keys:
                            key_parts = key_combo.replace("+", " ").split()
                            result = self.actuator.press_key(key_parts)
                            if not result.get("success", False):
                                return result
                        await asyncio.sleep(max(self.config.action_delay, 0.3))
                        self.logger.log_event(
                            EventType.ACTION_COMPLETE,
                            f"click via keyboard shortcut {keys} "
                            f"for '{params['element']}'",
                        )
                        return {
                            "success": True,
                            "output": f"Keyboard shortcut {keys} "
                            f"for '{params['element']}'",
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

                    # Rec 3: pre-click validation (skip for high confidence or
                    # dedicated grounding/vision model results — those are purpose-built
                    # for element finding). AX results use calibrated confidence so
                    # partial matches (conf < 0.9) go through crop validation.
                    skip_validation = (
                        location.source in ("grounding", "vision")
                        or confidence >= 0.9
                    )
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

                    element_data = {
                        "element": params["element"],
                        "x": location.x,
                        "y": location.y,
                        "confidence": location.confidence,
                        "source": location.source,
                    }
                    if location.raw_response:
                        element_data["vision_response"] = location.raw_response
                    self.logger.log_event(
                        EventType.ELEMENT_FOUND,
                        f"Found '{params['element']}' at ({location.x}, {location.y}) conf={location.confidence:.2f} via {location.source}",
                        data=element_data,
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
                    result["confidence"] = location.confidence
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
                # Click-to-focus: if an element is specified, find and click it first
                element_desc = params.pop("element", None)
                if element_desc and not params.pop("_skip_focus", False):
                    try:
                        location = await self._find_element(element_desc)
                        if location is not None:
                            # Keyboard shortcut path (e.g., Cmd+L for address bar)
                            if location.source == "keyboard_shortcut":
                                import ast
                                try:
                                    keys = ast.literal_eval(location.raw_response)
                                except Exception:
                                    keys = [location.raw_response]
                                for kc in keys:
                                    self.actuator.press_key(kc.replace("+", " ").split())
                            else:
                                sx = (
                                    location.screen_x
                                    if location.screen_x is not None
                                    else location.x
                                )
                                sy = (
                                    location.screen_y
                                    if location.screen_y is not None
                                    else location.y
                                )
                                self.actuator.click(sx, sy)
                            await asyncio.sleep(
                                max(self.config.action_delay, 0.3)
                            )
                        else:
                            slog.warning(
                                "type_text element not found,"
                                " typing to current focus",
                                element=element_desc,
                            )
                    except Exception as exc:
                        slog.warning(
                            "type_text click-to-focus failed,"
                            " typing to current focus",
                            element=element_desc,
                            error=str(exc),
                        )
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
            elif action == "scroll":
                direction = params.get("direction", "down")
                try:
                    amount = abs(int(params.get("amount", 3)))
                except (ValueError, TypeError):
                    amount = 3

                # AC-6: Capture scroll position before scroll for verification
                axis = "x" if direction in ("left", "right") else "y"
                scroll_before_val = None
                get_scroll = getattr(self.actuator, "get_scroll_position", None)
                if get_scroll is not None:
                    scroll_before_val = get_scroll(axis=axis)
                    if scroll_before_val is None:
                        slog.debug(
                            "scroll_before_position_unavailable",
                            axis=axis, direction=direction,
                            reason="get_scroll_position_returned_none",
                        )

                if self.screenshot_diff:
                    self.screenshot_diff.capture_before()

                if direction in ("left", "right"):
                    # pyautogui.hscroll: positive = right on macOS (inverted on Linux)
                    clicks = amount if direction == "right" else -amount
                    result = self.actuator.scroll(
                        clicks,
                        x=params.get("x"),
                        y=params.get("y"),
                        horizontal=True,
                    )
                else:
                    # pyautogui.scroll: positive = up on all platforms
                    clicks = amount if direction == "up" else -amount
                    result = self.actuator.scroll(
                        clicks,
                        x=params.get("x"),
                        y=params.get("y"),
                    )

                # AC-6: Store structured scroll verification metadata
                if scroll_before_val is not None:
                    result["_scroll_before"] = {
                        "axis": axis, "value": scroll_before_val
                    }
                if self.screenshot_diff:
                    await asyncio.sleep(0.5)
                    result["_scroll_pixel_changed"] = (
                        self.screenshot_diff.screen_changed()
                    )
            else:
                result = {"success": False, "error": f"Unknown action: {action}"}

            self.logger.log_event(EventType.ACTION_COMPLETE, f"{action} -> {result}")

            # Narrate observation: what we see after the action
            _post_app, _post_url = self._snapshot_state()
            obs_parts = []
            if result.get("success"):
                obs_parts.append(f"Done: {action} succeeded")
            else:
                obs_parts.append(f"Done: {action} failed — {result.get('error', '?')}")
            if _post_app:
                obs_parts.append(f"Now focused: {_post_app}")
            if _post_url:
                obs_parts.append(f"URL: {_post_url[:60]}")
            if _pre_app and _post_app and _pre_app != _post_app:
                obs_parts.append(f"Focus changed: {_pre_app} → {_post_app}")
            self.logger.log_event(
                EventType.NARRATE_OBSERVE,
                " | ".join(obs_parts),
                data={"app": _post_app, "url": _post_url},
            )

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
                # Keyboard shortcut fast path: press keys instead of clicking
                if gr.keyboard_shortcut:
                    return FindElementResult(
                        x=0, y=0,
                        confidence=gr.confidence,
                        source="keyboard_shortcut",
                        raw_response=str(gr.keyboard_shortcut),
                    )
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
            # AC-6: Save full screenshot on NOT_FOUND
            if self.config.save_step_screenshots and screenshot_b64:
                try:
                    slug = re.sub(r"[^a-zA-Z0-9]+", "_", description)[:40]
                    self.logger.save_screenshot(
                        base64.b64decode(screenshot_b64),
                        f"not_found_{slug}",
                    )
                except Exception as exc:
                    slog.debug("Screenshot save failed", error=str(exc))
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

        # AC-23 + AC-25: dual-res grounding gate
        use_dual = (
            self.config.dual_resolution_grounding
            and hasattr(self.coordinator, "capabilities")
            and CoordinatorCapability.DUAL_RESOLUTION
            in self.coordinator.capabilities()
        )

        if use_dual and screenshot_b64:
            should_crop = (
                image_size[0] > self.config.dual_res_threshold
                or self.last_successful_region is not None
            )
            if should_crop:
                crop_result = self._maybe_crop_screenshot(screenshot_b64)
                if crop_result is not None:
                    cropped_b64, dual_crop_offset = crop_result
                    _dual_start = time.monotonic()
                    # Timeout is handled inside find_element_dual() itself
                    # (asyncio.wait_for around _call_vision_model_with_images).
                    # On timeout it returns None — no outer wait_for needed.
                    dual_result = await self.coordinator.find_element_dual(
                        description,
                        screenshot_b64=cropped_b64,
                        context_b64=original_b64,
                        candidates=candidates,
                    )
                    _dual_duration = int(
                        (time.monotonic() - _dual_start) * 1000
                    )
                    if dual_result is not None:
                        self.logger.log_event(
                            EventType.DUAL_RES_GROUNDING,
                            f"Dual-res grounding: {description}",
                            data={
                                "element": description,
                                "crop_offset": dual_crop_offset,
                            },
                            duration_ms=_dual_duration,
                        )

                    # AC-24: map crop coords back to full-image space
                    if dual_result is not None and dual_crop_offset is not None:
                        dual_result = FindElementResult(
                            x=dual_result.x + dual_crop_offset[0],
                            y=dual_result.y + dual_crop_offset[1],
                            confidence=dual_result.confidence,
                            source=dual_result.source,
                            raw_response=dual_result.raw_response,
                        )
                    if dual_result is not None:
                        normalized = self._normalize_find_result(
                            dual_result, image_size
                        )
                        if original_b64:
                            self._save_debug_image(
                                original_b64, normalized, description
                            )
                        return normalized

        # Standard single-image path (Rec 4 crop for non-dual-res)
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

        # AC-6: Save full screenshot on NOT_FOUND
        if self.config.save_step_screenshots and screenshot_b64:
            try:
                slug = re.sub(r"[^a-zA-Z0-9]+", "_", description)[:40]
                self.logger.save_screenshot(
                    base64.b64decode(screenshot_b64),
                    f"not_found_{slug}",
                )
            except Exception as exc:
                slog.debug("Screenshot save failed", error=str(exc))
        return None

    @staticmethod
    def _draw_crosshair(
        draw,
        x: int,
        y: int,
        label: str,
        color: tuple = (255, 0, 0),
        r: int = 30,
    ) -> None:
        """Draw a crosshair with label on an ImageDraw canvas.

        Args:
            draw: PIL ImageDraw object.
            x, y: Center point for crosshair.
            label: Text label to display next to crosshair.
            color: RGB tuple for crosshair color.
            r: Crosshair radius in pixels.
        """
        outline = (0, 0, 0)
        w = 5

        # Black outline first, then color on top
        for c, off in [(outline, 2), (color, 0)]:
            draw.line([(x - r, y), (x + r, y)], fill=c, width=w + off)
            draw.line([(x, y - r), (x, y + r)], fill=c, width=w + off)
            draw.ellipse(
                [(x - r, y - r), (x + r, y + r)], outline=c, width=w + off
            )

        # Label with background box
        lx, ly = x + r + 6, y - 12
        bbox = draw.textbbox((lx, ly), label)
        draw.rectangle(
            [bbox[0] - 2, bbox[1] - 2, bbox[2] + 2, bbox[3] + 2],
            fill=(0, 0, 0),
        )
        draw.text((lx, ly), label, fill=(255, 255, 0))

    def _save_debug_image(
        self,
        screenshot_b64: str,
        location: FindElementResult,
        description: str,
        image_point: Optional[Tuple[int, int]] = None,
    ) -> None:
        """Save a debug screenshot with a crosshair at the predicted click point.

        Annotates with: coordinates, element description, source model,
        and confidence score.
        """
        try:
            from PIL import Image, ImageDraw

            img_bytes = base64.b64decode(screenshot_b64)
            img = Image.open(io.BytesIO(img_bytes)).convert("RGB")

            draw = ImageDraw.Draw(img)
            x, y = image_point or (location.x, location.y)

            # Build label with source and confidence
            source_tag = f" via {location.source}" if location.source else ""
            conf_tag = (
                f" conf={location.confidence:.2f}"
                if location.confidence and location.confidence > 0
                else ""
            )
            label = f"({x},{y}) {description[:40]}{source_tag}{conf_tag}"
            self._draw_crosshair(draw, x, y, label)

            debug_dir = self.logger.run_dir / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time() * 1000)
            slug = re.sub(r"[^a-zA-Z0-9]+", "_", description)[:60].strip("_")
            path = debug_dir / f"find_{ts}_{slug}.jpg"
            img.save(str(path), format="JPEG", quality=90)
            slog.debug("Debug image saved", path=str(path))
        except Exception as exc:
            slog.warning("Debug image save failed", error=str(exc))

    def _save_annotated_screenshot(
        self,
        screenshot_b64: str,
        x: int,
        y: int,
        label: str,
        name_slug: str,
        success: bool = True,
    ) -> None:
        """Save a screenshot annotated with a crosshair at (x, y).

        Uses green crosshair for success, red for failure.
        Saved to the debug/ directory alongside find_ images.
        """
        try:
            from PIL import Image, ImageDraw

            img_bytes = base64.b64decode(screenshot_b64)
            img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            draw = ImageDraw.Draw(img)

            color = (0, 200, 0) if success else (255, 0, 0)
            status = "PASS" if success else "FAIL"
            full_label = f"({x},{y}) [{status}] {label}"
            self._draw_crosshair(draw, x, y, full_label, color=color)

            debug_dir = self.logger.run_dir / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time() * 1000)
            slug = re.sub(r"[^a-zA-Z0-9]+", "_", name_slug)[:60].strip("_")
            path = debug_dir / f"annotated_{ts}_{slug}.jpg"
            img.save(str(path), format="JPEG", quality=90)
            slog.debug("Annotated screenshot saved", path=str(path))
        except Exception as exc:
            slog.debug("Annotated screenshot save failed", error=str(exc))

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
            if condition_visible is False:
                slog.info("Skipping wait_for_user; condition not present", condition=wait_condition)
                return StepResult(
                    step=step,
                    success=True,
                    verification_method="",
                    evidence=f"Skipped wait because '{wait_condition}' is not present",
                )

        # AC-3: If no condition was extracted, use a shorter timeout
        effective_timeout = self._WAIT_TIMEOUT_S
        if not wait_condition:
            effective_timeout = min(self._WAIT_TIMEOUT_S, 30.0)

        slog.info("⏳ Waiting for user action", message=message)
        print(f"\n[WAITING] {message}")
        print(f"  (will auto-resume when screen changes, timeout {effective_timeout}s)")

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
        while elapsed < effective_timeout:
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

        slog.warning("wait_for_user timed out", timeout_s=effective_timeout)
        print(f"  [TIMEOUT] No screen change detected after {effective_timeout}s")
        return StepResult(
            step=step, success=True, verification_method="",
            evidence=f"Timed out after {effective_timeout}s — proceeding anyway",
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

    async def _scroll_recovery(
        self,
        step: ActionStep,
        initial_result: StepResult,
        history: list,
        goal: str,
        max_scrolls: int = 3,
    ) -> Optional[StepResult]:
        """Attempt to find an element by scrolling down before declaring failure.

        Returns:
            StepResult with success=True if element found after scrolling.
            StepResult with success=False if max_scrolls exhausted.
            None if scroll recovery is not applicable.
        """
        element_desc = step.params.get("element", "")
        if not element_desc:
            return None

        for i in range(max_scrolls):
            slog.info(
                "Scroll recovery attempt",
                attempt=i + 1,
                max_scrolls=max_scrolls,
                element=element_desc,
            )
            self.logger.log_event(
                EventType.STEP_RETRY,
                f"Scroll recovery {i + 1}/{max_scrolls} for '{element_desc}'",
                data={"strategy": "scroll_down_and_retry", "scroll_attempt": i + 1},
            )

            # Scroll down half a viewport
            scroll_step = ActionStep(
                action="scroll",
                params={"direction": "down", "amount": 3},
                verify="",
                on_fail="abort",
            )
            await self._dispatch_action(scroll_step)
            await asyncio.sleep(self._SCROLL_SETTLE_S)

            # Retry finding the element
            find_result = await self._find_element(element_desc)
            if find_result is not None:
                slog.info(
                    "Scroll recovery succeeded",
                    attempt=i + 1,
                    element=element_desc,
                )
                action_result = await self._dispatch_action(
                    step, _pre_resolved_location=find_result
                )
                success = action_result.get("success", False)
                if not success:
                    # Element found but action failed — scroll and re-find rather than
                    # retrying at the same location. Rationale: a failed dispatch often
                    # means the element was partially obscured or the coordinate was stale
                    # (e.g., a lazy-loaded page shifted layout). Scrolling gives the page
                    # a fresh layout and _find_element produces a fresh coordinate.
                    slog.warning(
                        "Scroll recovery action failed",
                        attempt=i + 1,
                        error=action_result.get("error"),
                    )
                    continue
                if step.verify:
                    # Run postcondition verification (same pipeline as _execute_step)
                    verify_result = await self.verifier.verify(step, action_result)
                    if verify_result is not None:
                        return StepResult(
                            step=step,
                            success=verify_result.success,
                            verification_method="scroll_recovery_verified",
                            evidence=verify_result.evidence,
                            retry_strategies_used=[f"scroll_recovery_{i + 1}"],
                        )
                # NOTE: If verification was inconclusive (verify_result is None) or
                # the step had no verify field, we fall through to unverified success.
                # In _execute_step, an inconclusive Tier 1 escalates to Tier 2 (vision).
                # Here we accept unverified success as a pragmatic tradeoff — scroll
                # recovery is already a best-effort path. Future work: escalate to
                # Tier 2 vision verification on inconclusive results.
                return StepResult(
                    step=step,
                    success=True,
                    verification_method="scroll_recovery",
                    evidence=f"Scroll recovery click after {i + 1} scrolls",
                    retry_strategies_used=[f"scroll_recovery_{i + 1}"],
                )

        slog.warning(
            "Scroll recovery exhausted",
            max_scrolls=max_scrolls,
            element=element_desc,
        )
        return StepResult(
            step=step,
            success=False,
            verification_method="",
            evidence=(
                f"Element '{element_desc}' not found after"
                f" {max_scrolls} scroll attempts"
            ),
            error=f"Element not found after {max_scrolls} scrolls: {element_desc}",
        )

    def _get_confidence_threshold(self, step: ActionStep) -> float:
        """Return the confidence threshold for a step (Rec 2).

        Steps whose verify text contains critical-action keywords use a higher
        threshold of 0.9 — unless the text matches a safe-navigation phrase
        (e.g. "Purchase History", "Order History") that contains the keyword
        in a non-destructive context.  All other steps use 0.5.
        """
        texts = [
            step.params.get("element", "") if step.params else "",
            step.verify or "",
        ]
        combined = " ".join(texts)

        # Safe-navigation exemption: if any safe phrase matches, skip the
        # elevated threshold even though a critical keyword is present.
        if any(pat.search(combined) for pat in self._SAFE_NAVIGATION_PHRASES):
            return self._DEFAULT_CONFIDENCE_THRESHOLD

        for text in texts:
            if any(pat.search(text) for pat in self._KEYWORD_PATTERNS.values()):
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

        return image_to_screen_coords(
            image_x,
            image_y,
            (screen_width, screen_height),
            (image_width, image_height),
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

        return screen_to_image_coords(
            screen_x,
            screen_y,
            (screen_width, screen_height),
            (image_width, image_height),
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
    ) -> Optional[bool]:
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
        """Suggest a visible alternative control when a click target is absent.

        Checks skill error recovery hints first; falls back to vision model.
        """
        missing_target = str(step.params.get("element", ""))

        # Check skill error recovery hints first
        if self._current_skill_context:
            skill_hint = self._check_skill_error_recovery(
                missing_target, self._current_skill_context
            )
            if skill_hint:
                self.logger.log_event(
                    EventType.ELEMENT_SEARCH,
                    f"Skill hint alternative: '{skill_hint}' for missing '{missing_target}'",
                    data={
                        "missing_target": missing_target,
                        "suggested_affordance": skill_hint,
                        "source": "skill_error_recovery",
                    },
                )
                result.reflection_hint = "use_alternative_affordance"
                result.suggested_element = skill_hint
                result.reflection_observed = "Skill error recovery hint"
                result.evidence = (
                    f"{result.evidence}. Suggested from skill hints: {skill_hint}"
                )
                return result

        # Fall back to vision model
        if not self._has_explicit_method(self.coordinator, "suggest_alternative_affordance"):
            return result

        try:
            screenshot_b64 = await self._capture_screenshot()
            suggestion = await self.coordinator.suggest_alternative_affordance(
                missing_target=missing_target,
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
        if affordance.lower() == missing_target.strip().lower():
            return result

        self.logger.log_event(
            EventType.ELEMENT_SEARCH,
            f"Alternative affordance suggested: '{affordance}' for missing '{missing_target}'",
            data={
                "missing_target": missing_target,
                "suggested_affordance": affordance,
                "reason": str(suggestion.get("reason", "")),
                "vision_response": str(suggestion.get("raw_response", "")),
            },
        )
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

    @staticmethod
    def _check_skill_error_recovery(
        missing_target: str, skill_context: str
    ) -> Optional[str]:
        """Extract a suggested alternative from skill error recovery hints.

        Scans the Error Recovery / Recovery Heuristics section for lines that
        mention the missing target and extracts the suggested action.

        Returns the suggested alternative element, or None if not found.
        """
        if not skill_context or not missing_target:
            return None

        # Find the error recovery section (supports both header variants)
        recovery_match = re.search(
            r"##\s*(?:Error Recovery|Recovery Heuristics)\s*\n(.*?)(?=\n##|\Z)",
            skill_context,
            re.DOTALL | re.IGNORECASE,
        )
        if not recovery_match:
            return None

        recovery_text = recovery_match.group(1)
        missing_lower = missing_target.lower()

        for line in recovery_text.splitlines():
            line_stripped = line.strip()
            if not line_stripped:
                continue
            # Match lines like: - If "X" is absent: click "Y" ...
            if missing_lower in line_stripped.lower():
                # Extract quoted alternative after the colon
                colon_idx = line_stripped.find(":")
                if colon_idx < 0:
                    continue
                after_colon = line_stripped[colon_idx + 1:]
                # Look for quoted text as the suggested element
                quoted = re.findall(r'"([^"]+)"', after_colon)
                if quoted:
                    return quoted[0]
                # Look for "click X" / "look for X" patterns
                action_match = re.search(
                    r"(?:click|look for|try|use)\s+(\S+(?:\s+\S+){0,3})",
                    after_colon,
                    re.IGNORECASE,
                )
                if action_match:
                    return action_match.group(1).strip().rstrip(".")
        return None

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

    def _record_context(
        self, step: ActionStep, result: Optional[StepResult] = None
    ) -> None:
        """Record action context in the context monitor after step execution.

        AC-29: When result is provided, persist step outcomes (milestones/obstacles).
        """
        if step.action == "click":
            self.context_monitor.record_click(step.params.get("element", ""))
        elif step.action == "type_text":
            self.context_monitor.record_type(
                step.params.get("text", ""),
                step.params.get("field_name"),
            )
        elif step.action == "open_url":
            self.context_monitor.record_navigation(step.params.get("url", ""))

        # AC-29: persist step outcomes
        if result is not None:
            self.context_monitor.record_step_outcome(step, result)

            # Observability: STATE_DIFF call site (spec §Observability, pushback 4).
            # Log after record_step_outcome so diff reflects this step's changes.
            diff = self.context_monitor.format_state_diff()
            if diff:
                self.logger.log_event(
                    EventType.STATE_DIFF,
                    f"State changed: {len(diff.changes)} changes",
                    data={
                        "changes": diff.changes,
                        "new_elements": diff.new_elements[:5],
                        "removed_elements": diff.removed_elements[:5],
                        "step_action": step.action,
                    },
                )

    @staticmethod
    def _is_element_not_found(result: StepResult) -> bool:
        """Check if a step result indicates element-not-found."""
        return bool(result.error and result.error.startswith("Element not found:"))

    def _is_element_absent(self, element_description: str) -> bool:
        """Check whether an element is confirmed absent from the current page.

        Uses the accessibility tree as structural confirmation when available.
        When the AX backend is unavailable or errors, returns True (falls back
        to count-only absence detection per AC-4). This fallback-to-True behavior
        is intentional: the counter threshold (N>=2) has already been met before
        this method is called, so AX is a bonus confirmation, not a gate.

        Returns True if element is absent (or AX unavailable), False if AX finds a match.
        """
        accessibility = getattr(self.coordinator, "accessibility", None)
        if accessibility is None:
            return True  # No AX = fall back to count-only
        get_elements = getattr(accessibility, "get_accessibility_elements", None)
        if not callable(get_elements):
            return True  # No usable AX method
        # Skip async backends (same guard as _check_text_field_focused at line 568)
        if inspect.iscoroutinefunction(get_elements) or "AsyncMock" in type(get_elements).__name__:
            return True
        try:
            elements = get_elements()
            # Guard: if the call somehow returns a non-list (e.g. coroutine), treat as unavailable
            if not isinstance(elements, (list, tuple)):
                return True
        except Exception:
            return True  # AX failure = fall back to count-only
        if not elements:
            return True  # Empty AX tree = confirmed absent
        # Check if any AX element matches the description
        desc_lower = element_description.lower()
        for el in elements:
            title = (getattr(el, "title", "") or "").lower()
            description = (getattr(el, "description", "") or "").lower()
            if desc_lower in title or desc_lower in description:
                return False  # AX found a match -- NOT absent
        return True  # AX tree searched, no match found

    async def _handle_failure(self, index, step, result, history, goal, plan, iterations):
        """Handle a step failure based on on_fail policy.

        Strategy-changing retries: each retry attempts a genuinely different
        approach rather than blind repetition.  BUG 4 FIX: This now loops
        through ALL available retries rather than returning after just one.
        """
        if step.on_fail == "retry_different":
            current_result = result
            # AC-4: Track element-not-found count for absence detection.
            # Keyed on the ORIGINAL element description, not _vary_strategy mutations.
            original_element = step.params.get("element", "")
            not_found_count = 1 if self._is_element_not_found(current_result) else 0

            while current_result.retry_count < step.max_retries:
                strategy, retry_step = self._vary_strategy(step, current_result)
                if retry_step is None:
                    # About to escalate to replan -- check absence threshold
                    if not_found_count >= 2 and original_element and self._is_element_absent(original_element):
                        current_result.error = f"Element absent: {original_element}"
                        # Ensure the modified result is in step_results for AC-5
                        if current_result is not result:
                            history.append(current_result)
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
                retry_result, _ = await self._execute_step(index, retry_step, history, goal, plan)
                retry_result.retry_count = current_result.retry_count + 1
                retry_result.retry_strategies_used = current_result.retry_strategies_used + [strategy]

                if retry_result.success:
                    return retry_result

                # AC-4: Increment not-found counter
                if self._is_element_not_found(retry_result):
                    not_found_count += 1
                current_result = retry_result

            # Retries exhausted — check absence before replan escalation
            if not_found_count >= 2 and original_element and self._is_element_absent(original_element):
                current_result.error = f"Element absent: {original_element}"
                # Ensure the modified result is in step_results for AC-5
                if current_result is not result:
                    history.append(current_result)
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
                        f"{params['element']} (look carefully, may be partially hidden)"
                    )
                    return ("refine_missing_element_query", _retry_step("click", params))
                if attempt == 2:
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

        # AC-5: Collect confirmed-absent elements for replan context
        absent_elements = []
        for sr in step_results:
            if sr.error and sr.error.startswith("Element absent:"):
                absent_desc = sr.error[len("Element absent:"):].strip()
                if absent_desc and absent_desc not in absent_elements:
                    absent_elements.append(absent_desc)

        self.logger.log_event(EventType.REPLAN_START, "Replanning...")
        new_plan = await self.planner.replan(
            goal,
            screen_desc,
            step_results,
            retry_strategies,
            desktop_context=desktop_context,
            skill_context=replan_ctx,
            absent_elements=absent_elements,
        )
        replan_data = {"step_count": len(new_plan.steps)}
        if new_plan.raw_llm_response:
            replan_data["llm_response"] = new_plan.raw_llm_response
        replan_data["steps_summary"] = [
            f"{s.action}({s.params})" for s in new_plan.steps
        ]
        self.logger.log_event(
            EventType.REPLAN_COMPLETE,
            f"New plan: {len(new_plan.steps)} steps",
            data=replan_data,
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

        # P0-2: Apply full plan hardening to replans (fallback, nav, domain, on_fail)
        fallback_plan = (
            self._build_skill_fallback_plan(goal, skill_context)
            if skill_context
            else None
        )
        if skill_context and fallback_plan is None:
            slog.warning(
                "replan_fallback_build_failed",
                reason="skill_context present but could not parse into fallback plan",
                goal=goal,
            )
        new_plan = await self._harden_plan(
            new_plan, goal, skill_context, fallback_plan
        )

        # Soft validation for replans (log but don't abort — partial value possible)
        validation_errors = new_plan.validate()
        if validation_errors:
            slog.warning("replan_validation_issues", errors=validation_errors)

        # Execute new plan with proper failure handling
        _skip_next = False
        for i, step in enumerate(new_plan.steps):
            if _skip_next:
                _skip_next = False
                continue
            if iterations >= self.config.max_iterations:
                break

            result, text_field_focused = await self._execute_step(i, step, step_results, goal, new_plan)

            # AC-1: Type-and-check bypass attempt
            bypass = await self._try_type_and_check_bypass(
                i, step, result, text_field_focused, new_plan, step_results, goal,
            )
            if bypass is not None:
                click_result, next_result, _ = bypass
                step_results.append(click_result)
                step_results.append(next_result)
                iterations += 2
                _skip_next = True

                if click_result.success:
                    if next_result.step.action == "done":
                        break
                    continue

                result = click_result
            else:
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
                # Use _handle_failure for retries, but do NOT recurse into
                # another replan — one level of replanning is enough.
                recovery_result = await self._handle_failure(
                    i, step, result, step_results, goal, new_plan, iterations
                )
                if recovery_result is None:
                    # _handle_failure wants to replan again, but we're already
                    # in a replan. Log and stop rather than recurse.
                    self.logger.log_event(
                        EventType.STEP_REPLAN,
                        f"Replan step {i} exhausted retries; stopping replan execution",
                        step_index=i,
                    )
                    break  # Stop executing remaining steps
                else:
                    step_results.append(recovery_result)
                    iterations += 1
                    if not recovery_result.success:
                        # Recovery failed — stop executing remaining steps
                        # since they likely depend on this one succeeding.
                        if step.on_fail == "abort":
                            self.logger.log_event(
                                EventType.TASK_FAIL,
                                f"Replan step {i} failed with abort policy",
                                step_index=i,
                            )
                        else:
                            self.logger.log_event(
                                EventType.STEP_REPLAN,
                                f"Replan step {i} failed after recovery; stopping",
                                step_index=i,
                            )
                        break

        duration = int((time.monotonic() - start_time) * 1000)
        # Check final state: success only if last step passed or was 'done'
        last_result = step_results[-1] if step_results else None
        success = last_result.success if last_result else False

        # AC-6: Check if last step was a done-with-abort
        abort_error = None
        if (
            last_result
            and last_result.step.action == "done"
            and last_result.step.params.get("abort_reason")
        ):
            success = False
            abort_error = last_result.error

        # skill_context arg is ignored when derived_session is set;
        # _maybe_learn_skill_run rebuilds distiller context from
        # expanded_steps_for_distiller + derived_session.
        observations = await self._maybe_learn_skill_run(
            goal=goal,
            skill_name=skill_name,
            skill_context=skill_context or "",
            step_results=step_results,
            had_replan=True,
            derived_session=derived_session,
            expanded_steps_for_distiller=expanded_steps_for_distiller,
        )
        await self._maybe_promote_skill(
            goal=goal,
            skill_name=skill_name,
            observations=observations,
            derived_session=derived_session,
            step_results=step_results,
            run_id=self.logger.run_id,
            had_replan=True,
            success=success,
        )
        self.logger.finalize(success, f"Replanned: {goal}")
        if abort_error:
            return ExecutionResult(
                success=False,
                message=f"Task aborted: {abort_error}",
                error=abort_error,
                steps=step_results,
                total_duration_ms=duration,
                iterations=iterations,
                goal=goal,
                run_id=self.logger.run_id,
            )
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
    ) -> list:
        """Best-effort skill learning from execution traces.

        Builds a distiller-specific context containing only the primary
        skill's original steps plus the derived procedure. Does NOT pass
        the full multi-skill context to the distiller.

        Returns list of SkillObservation distilled from this run (empty on
        early exit or error).
        """
        if not skill_name or not step_results:
            return []
        learn = getattr(self.skill_registry, "learn_from_run", None)
        if learn is None:
            return []

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
            return []
        if observations:
            self.logger.log_event(
                EventType.SKILL_EXPAND,
                f"Learned {len(observations)} generalized skill observation(s)",
                data={"skill_name": skill_name, "count": len(observations)},
            )
        return observations if observations else []

    async def _maybe_promote_skill(
        self,
        *,
        goal: str,
        skill_name: Optional[str],
        observations: list,
        derived_session=None,
        step_results: list = None,
        run_id: str = "",
        had_replan: bool = False,
        success: bool = True,
    ) -> None:
        """Best-effort skill promotion from accumulated observations.

        Uses getattr to check for promote_from_run on the registry, consistent
        with the learn_from_run pattern.
        """
        if not skill_name:
            return
        promote = getattr(self.skill_registry, "promote_from_run", None)
        if promote is None:
            return
        try:
            decision = await promote(
                goal=goal,
                skill_name=skill_name,
                derived_session=derived_session,
                observations=observations,
                trace=step_results or [],
                run_id=run_id,
                had_replan=had_replan,
                success=success,
            )
            if decision and getattr(decision, "promotion_type", None) != "observation_only":
                slog.info(
                    "skill_promotion_applied",
                    skill_name=skill_name,
                    promotion_type=getattr(decision, "promotion_type", "unknown"),
                )
        except Exception:
            slog.warning("skill_promotion_failed", skill_name=skill_name, exc_info=True)
