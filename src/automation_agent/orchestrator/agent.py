"""AutomationAgent -- main orchestrator that coordinates planner, skills, vision, actuator, and verifier."""

import asyncio
import base64
import io
import time
from typing import Optional, Tuple

import structlog

from automation_agent.shared_models import ActionPlan, ActionStep, ExecutionResult, FindElementResult, StepResult
from automation_agent.config import AgentConfig
from automation_agent.logging.event_logger import EventLogger
from automation_agent.logging.models import EventType
from automation_agent.orchestrator.verifier import StepVerifier

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
            actuator=actuator, coordinator=coordinator, logger=self.logger
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

        try:
            # 1. Check for matching skill
            skill_context = None
            skill_match = await self.skill_registry.match(goal)
            if skill_match:
                skill_name = skill_match["skill_name"]
                params = skill_match.get("params", {})
                skill_context = self.skill_registry.expand(skill_name, params)
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
            slog.info(
                "🏁 Task completed",
                duration_s=round(duration / 1000, 1),
                iterations=iterations,
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

        # Capture screenshot before click actions for fast diff-based verification
        if self.screenshot_diff and step.action == "click":
            self.screenshot_diff.capture_before()

        # For element-based actions (click with element description), find the element first
        actuator_result = await self._dispatch_action(step)

        # After click, quick diff check: if no visible effect, mark as failed for retry
        if (
            self.screenshot_diff
            and step.action == "click"
            and actuator_result.get("success", False)
        ):
            await asyncio.sleep(0.3)  # Brief wait for UI update
            click_x = actuator_result.get("x", step.params.get("x", 0))
            click_y = actuator_result.get("y", step.params.get("y", 0))
            if not self.screenshot_diff.region_changed(click_x, click_y):
                actuator_result["success"] = False
                actuator_result["error"] = (
                    "Click had no visible effect (screenshot unchanged)"
                )

        # BUG 1 FIX: If the actuator action failed (e.g. element not found), skip
        # verification and return failure immediately. Vision verification must not
        # override a real action failure.
        if not actuator_result.get("success", False):
            self.logger.log_event(
                EventType.STEP_COMPLETE,
                f"Step {index}: FAIL -- actuator failed: {actuator_result.get('error', 'unknown')}",
                step_index=index,
                data={"success": False, "method": "actuator"},
            )
            return StepResult(
                step=step,
                success=False,
                verification_method="",
                evidence=f"Action failed: {actuator_result.get('error', 'unknown error')}",
            )

        # Brief delay after actions that need time to take effect (app launch, URL open)
        if step.action in ("activate_app", "open_url", "quit_app"):
            await asyncio.sleep(self.config.action_delay)

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

        # Rec 4: record the successful click region for resolution-aware narrowing
        if step.action == "click" and verification.success:
            click_x = actuator_result.get("x", step.params.get("x", 0))
            click_y = actuator_result.get("y", step.params.get("y", 0))
            half = 256
            self.last_successful_region = (
                max(0, click_x - half),
                max(0, click_y - half),
                click_x + half,
                click_y + half,
            )

        return verification

    async def _dispatch_action(self, step: ActionStep) -> dict:
        """Dispatch an action to the actuator."""
        action = step.action
        params = step.params

        slog.debug("🎯 Dispatching action", action=action, params=params)
        self.logger.log_event(EventType.ACTION_START, f"{action}({params})")

        try:
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
                        f"Found at ({location.x}, {location.y})",
                    )
                    result = self.actuator.click(location.x, location.y)
                    result["x"] = location.x
                    result["y"] = location.y
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

    async def _find_element(self, description: str) -> Optional[FindElementResult]:
        """Find a UI element by description, using grounding router if available.

        Also wires accessibility candidates (Rec 1) and resolution-aware
        screenshot cropping (Rec 4).

        Returns FindElementResult on success, or None if not found.
        """
        if self.grounding_router is not None:
            gr = await self.grounding_router.find_element(description)
            if gr is not None:
                return FindElementResult(
                    x=gr.x, y=gr.y, source=gr.strategy_used.value
                )
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
        screenshot_b64 = await self.coordinator.capture_screenshot()
        crop_offset = None

        if self.last_successful_region is not None:
            crop_result = self._maybe_crop_screenshot(screenshot_b64)
            if crop_result is not None:
                screenshot_b64, crop_offset = crop_result

        result = await self.coordinator.find_element(
            description, screenshot_b64=screenshot_b64, candidates=candidates
        )

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
            self._save_debug_image(screenshot_b64, result, description)

        return result

    def _save_debug_image(
        self,
        screenshot_b64: str,
        location: FindElementResult,
        description: str,
    ) -> None:
        """Save a debug screenshot with a crosshair at the predicted click point."""
        try:
            from PIL import Image, ImageDraw

            img_bytes = base64.b64decode(screenshot_b64)
            img = Image.open(io.BytesIO(img_bytes)).convert("RGB")

            draw = ImageDraw.Draw(img)
            x, y = location.x, location.y
            r = 15  # crosshair radius
            color = (255, 0, 0)  # red
            width = 3

            # Crosshair
            draw.line([(x - r, y), (x + r, y)], fill=color, width=width)
            draw.line([(x, y - r), (x, y + r)], fill=color, width=width)
            # Circle
            draw.ellipse(
                [(x - r, y - r), (x + r, y + r)], outline=color, width=width
            )
            # Label
            label = f"({x},{y}) {description[:40]}"
            draw.text((x + r + 4, y - 8), label, fill=color)

            debug_dir = self.logger.run_dir / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time() * 1000)
            path = debug_dir / f"find_{ts}.jpg"
            img.save(str(path), format="JPEG", quality=90)
            slog.debug("Debug image saved", path=str(path))
        except Exception:
            pass  # Never block execution for debug images

    async def _wait_for_user(self, step: ActionStep) -> StepResult:
        """Wait for the user to complete an action by polling for screen changes.

        Captures a baseline screenshot, prints the message, then polls every
        _WAIT_POLL_INTERVAL_S seconds comparing against baseline. Returns when
        the screen changes significantly or after _WAIT_TIMEOUT_S seconds.
        """
        message = step.params.get("message", "Please complete the required action")
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

    async def _validate_candidate(
        self,
        candidate_x: int,
        candidate_y: int,
        target_description: str,
        screenshot_b64: Optional[str] = None,
    ) -> bool:
        """Pre-click validation: crop region around candidate and ask vision model (Rec 3).

        Crops a 200x200 pixel region centered on (candidate_x, candidate_y) from a
        screenshot, then asks the coordinator's verify_condition() with:
            "The element at the center of this image is: {target_description}"

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
        w, h = img.size

        crop_size = 200
        half = crop_size // 2
        left = max(0, candidate_x - half)
        top = max(0, candidate_y - half)
        right = min(w, candidate_x + half)
        bottom = min(h, candidate_y + half)

        cropped = img.crop((left, top, right, bottom))
        buf = io.BytesIO()
        cropped.save(buf, format="JPEG", quality=85)
        cropped_b64 = base64.b64encode(buf.getvalue()).decode()

        condition = f"The element at the center of this image is: {target_description}"
        return await self.coordinator.verify_condition(condition, screenshot_b64=cropped_b64)

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
                strategy, modified_params = self._vary_strategy(step, current_result)
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
                retry_step = ActionStep(
                    action=step.action,
                    params=modified_params,
                    verify=step.verify,
                    on_fail=step.on_fail,
                    max_retries=step.max_retries,
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

        Returns (strategy_name, modified_params).
        """
        params = dict(step.params)
        attempt = prev_result.retry_count + 1

        if step.action == "click" and "element" in params:
            if attempt == 1:
                # Strategy: re-query vision with more context
                params["element"] = f"{params['element']} (look carefully, may be partially hidden)"
                return ("refine_element_query", params)
            elif attempt == 2:
                # Strategy: try keyboard shortcut instead
                return ("keyboard_fallback", params)
            else:
                return ("fresh_screenshot_retry", params)

        elif step.action == "type_text":
            if attempt == 1:
                # Strategy: click to ensure focus first
                return ("click_to_focus_first", params)
            else:
                # Strategy: use press_key for individual characters
                return ("slow_type_retry", params)

        elif step.action == "press_key":
            if attempt == 1:
                # Strategy: add modifier key variation (e.g., try with Cmd)
                keys = list(params.get("keys", []))
                if keys and "cmd" not in [k.lower() for k in keys]:
                    params["keys"] = keys  # same keys but with a pre-delay
                    params["_pre_delay"] = 0.5
                    return ("delayed_key_press", params)
                else:
                    params["_pre_delay"] = 0.5
                    return ("delayed_key_press", params)
            else:
                params["_pre_delay"] = 1.0 * attempt
                return (f"extended_delay_key_press_{attempt}", params)

        elif step.action == "open_url":
            if attempt == 1:
                params["_pre_delay"] = 1.0
                return ("delayed_open_url", params)
            else:
                params["_pre_delay"] = 2.0 * attempt
                return (f"extended_delay_open_url_{attempt}", params)

        elif step.action == "activate_app":
            if attempt == 1:
                # Strategy: quit and relaunch
                params["_quit_first"] = True
                return ("quit_and_relaunch", params)
            else:
                params["_spotlight"] = True
                params["_pre_delay"] = 1.0 * attempt
                return ("spotlight_launch", params)

        elif step.action == "quit_app":
            if attempt == 1:
                params["_force"] = True
                return ("force_quit", params)
            else:
                params["_force"] = True
                params["_pre_delay"] = 1.0 * attempt
                return (f"force_quit_with_delay_{attempt}", params)

        else:
            # Generic fallback: add increasing delay
            params["_pre_delay"] = 0.5 * attempt
            return (f"generic_retry_with_delay_{attempt}", params)

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
