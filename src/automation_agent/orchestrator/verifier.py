"""StepVerifier — tiered verification for action step postconditions.

Three-tier verification strategy:
  Tier 0: Accessibility API state (future — not implemented yet)
  Tier 1: Actuator state query via actuator.get_state() (~50ms)
  Tier 2: Vision screenshot verification via coordinator.verify_condition() (~2-5s)
"""

import base64
import time
from typing import Any, Dict, Optional, Tuple

import structlog

from automation_agent.shared_models import ActionStep, StepResult
from automation_agent.logging.event_logger import EventLogger
from automation_agent.logging.models import EventType

slog = structlog.get_logger(__name__)


class StepVerifier:
    """Three-tier verification. Used by both Orchestrator and tests.

    Tier 0: Accessibility API state (future -- not implemented yet)
    Tier 1: Actuator state query via actuator.get_state() (~50ms)
    Tier 2: Vision screenshot verification via coordinator.verify_condition() (~2-5s)
    """

    def __init__(
        self,
        actuator=None,
        coordinator=None,
        logger: Optional[EventLogger] = None,
    ):
        self.actuator = actuator
        self.coordinator = coordinator
        self.logger = logger

    async def verify(
        self,
        step: ActionStep,
        actuator_result: Dict[str, Any],
        coordinator=None,
        actuator=None,
    ) -> StepResult:
        """Verify a step's postcondition using tiered verification.

        Returns StepResult with verification_method and evidence (always non-empty).
        """
        start = time.monotonic()
        act = actuator or self.actuator
        coord = coordinator or self.coordinator

        # If step has no verify condition, check whether this is a terminal action
        # (done, wait_for_user) that legitimately has no postcondition, or a regular
        # action that should have been given a verify string.
        if not step.verify:
            duration = int((time.monotonic() - start) * 1000)
            if step.action in ("done", "wait_for_user"):
                return StepResult(
                    step=step,
                    success=actuator_result.get("success", False),
                    verification_method="",
                    evidence=f"Actuator result: {actuator_result.get('output', 'no output')}",
                    duration_ms=duration,
                )
            # BUG 5 FIX: Non-terminal action with empty verify is an error
            return StepResult(
                step=step,
                success=False,
                verification_method="",
                evidence=f"Step '{step.action}' has no verify condition — cannot verify postcondition",
                error="empty_verify",
                duration_ms=duration,
            )

        # Tier 1: Actuator state query (fast)
        if act:
            tier1_result = self._verify_tier1(step, act)
            if tier1_result is not None:  # Conclusive (pass or fail)
                duration = int((time.monotonic() - start) * 1000)
                emoji = "✅" if tier1_result[0] else "❌"
                slog.info(
                    f"{emoji} Verified (tier1)",
                    condition=step.verify,
                    passed=tier1_result[0],
                    duration_ms=duration,
                )
                if self.logger:
                    self.logger.log_event(
                        EventType.VERIFY_PASS if tier1_result[0] else EventType.VERIFY_FAIL,
                        f"Tier 1: {tier1_result[1]}",
                    )
                return StepResult(
                    step=step,
                    success=tier1_result[0],
                    verification_method="actuator_state",
                    evidence=tier1_result[1],
                    duration_ms=duration,
                )
            else:
                # Tier 1 inconclusive -- escalate
                if self.logger:
                    self.logger.log_event(
                        EventType.VERIFY_ESCALATE,
                        "Tier 1 inconclusive, escalating to Tier 2",
                    )

        # Tier 2: Vision verification (slower but more thorough)
        if coord:
            tier2_result = await self._verify_tier2(step, coord)
            duration = int((time.monotonic() - start) * 1000)
            screenshot_path = None
            # Always capture screenshot at verification
            try:
                screenshot_b64 = await coord.capture_screenshot()
                if self.logger:
                    screenshot_path = self.logger.save_screenshot(
                        base64.b64decode(screenshot_b64),
                        f"verify_step_{step.action}",
                    )
            except Exception:
                pass

            emoji = "✅" if tier2_result[0] else "❌"
            slog.info(
                f"{emoji} Verified (tier2, vision)",
                condition=step.verify,
                passed=tier2_result[0],
                duration_ms=duration,
            )
            if self.logger:
                event_type = EventType.VERIFY_PASS if tier2_result[0] else EventType.VERIFY_FAIL
                self.logger.log_event(event_type, f"Tier 2: {tier2_result[1]}")

            return StepResult(
                step=step,
                success=tier2_result[0],
                verification_method="vision",
                evidence=tier2_result[1],
                duration_ms=duration,
                screenshot_path=screenshot_path,
            )

        # No verification backend available -- use actuator result
        duration = int((time.monotonic() - start) * 1000)
        return StepResult(
            step=step,
            success=actuator_result.get("success", False),
            verification_method="",
            evidence=f"No verifier available. Actuator: {actuator_result}",
            duration_ms=duration,
        )

    def _verify_tier1(
        self, step: ActionStep, actuator
    ) -> Optional[Tuple[bool, str]]:
        """Tier 1: Fast verification via actuator state.

        Returns (success, evidence) if conclusive, None if inconclusive.
        """
        state = actuator.get_state()
        verify_lower = step.verify.lower()

        # For activate_app actions, always check frontmost app via state
        if step.action == "activate_app" and step.params.get("app_name"):
            expected_app = step.params["app_name"]
            actual_app = state.get("app_name", "")
            if not actual_app:
                # Actuator not responding — inconclusive, escalate
                return None
            if (
                expected_app.lower() in actual_app.lower()
                or actual_app.lower() in expected_app.lower()
            ):
                return (
                    True,
                    f"Frontmost app is '{actual_app}' (expected '{expected_app}')",
                )
            else:
                return (
                    False,
                    f"Frontmost app is '{actual_app}', expected '{expected_app}'",
                )

        # Check app-related conditions mentioned in verify text
        app_keywords = ["frontmost", "foreground", "is the active", "is open", "is running"]
        if any(kw in verify_lower for kw in app_keywords):
            expected_app = step.params.get("app_name", "")
            actual_app = state.get("app_name", "")
            if expected_app and actual_app:
                if (
                    expected_app.lower() in actual_app.lower()
                    or actual_app.lower() in expected_app.lower()
                ):
                    return (
                        True,
                        f"Frontmost app is '{actual_app}' (expected '{expected_app}')",
                    )
                else:
                    return (
                        False,
                        f"Frontmost app is '{actual_app}', expected '{expected_app}'",
                    )

        # For click, type_text, etc. -- Tier 1 is inconclusive, escalate to Tier 2
        return None

    async def _verify_tier2(
        self, step: ActionStep, coordinator
    ) -> Tuple[bool, str]:
        """Tier 2: Vision-based verification.

        Returns (success, evidence).
        """
        result = await coordinator.verify_condition(step.verify)
        if result:
            return (True, f"Vision confirms: {step.verify}")
        else:
            return (False, f"Vision denies: {step.verify}")
