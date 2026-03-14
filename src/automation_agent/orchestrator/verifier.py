"""StepVerifier — tiered verification for action step postconditions.

Three-tier verification strategy:
  Tier 0: Accessibility API state
  Tier 1: Actuator state query via actuator.get_state() (~50ms)
  Tier 2: Vision screenshot verification via coordinator.verify_condition() (~2-5s)
"""

import base64
import inspect
import io
import re
import time
from urllib.parse import urlparse
from typing import Any, Dict, Optional, Tuple

import structlog

from automation_agent.logging.event_logger import EventLogger
from automation_agent.logging.models import EventType
from automation_agent.shared_models import ActionStep, StepResult, TEXT_INPUT_AX_ROLES

slog = structlog.get_logger(__name__)


class StepVerifier:
    """Three-tier verification. Used by both Orchestrator and tests.

    Tier 0: Accessibility API state
    Tier 1: Actuator state query via actuator.get_state() (~50ms)
    Tier 2: Vision screenshot verification via coordinator.verify_condition() (~2-5s)
    """

    def __init__(
        self,
        actuator=None,
        coordinator=None,
        logger: Optional[EventLogger] = None,
        accessibility=None,
    ):
        self.actuator = actuator
        self.coordinator = coordinator
        self.logger = logger
        self.accessibility = accessibility

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

        accessibility = self._get_accessibility_backend(coord)

        # Tier 0: Accessibility state (fastest structured signal)
        if accessibility:
            tier0_result = self._verify_tier0(step, accessibility)
            if tier0_result is not None:
                duration = int((time.monotonic() - start) * 1000)
                if self.logger:
                    self.logger.log_event(
                        EventType.VERIFY_PASS if tier0_result[0] else EventType.VERIFY_FAIL,
                        f"Tier 0: {tier0_result[1]}",
                    )
                return StepResult(
                    step=step,
                    success=tier0_result[0],
                    verification_method="accessibility",
                    evidence=tier0_result[1],
                    duration_ms=duration,
                )

        # Tier 1: Actuator state query (fast)
        if act:
            tier1_result = self._verify_tier1(step, act, actuator_result)
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
            tier2_raw, screenshot_b64 = await self._verify_tier2(
                step, coord, actuator_result
            )
            duration = int((time.monotonic() - start) * 1000)
            screenshot_path = None
            if screenshot_b64 and self.logger:
                try:
                    screenshot_path = self.logger.save_screenshot(
                        base64.b64decode(screenshot_b64),
                        f"verify_step_{step.action}",
                    )
                except Exception:
                    pass

            if tier2_raw is not None:
                # Tier 2 reached a conclusive result (pass or fail)
                emoji = "✅" if tier2_raw[0] else "❌"
                slog.info(
                    f"{emoji} Verified (tier2, vision)",
                    condition=step.verify,
                    passed=tier2_raw[0],
                    duration_ms=duration,
                )
                if self.logger:
                    event_type = EventType.VERIFY_PASS if tier2_raw[0] else EventType.VERIFY_FAIL
                    self.logger.log_event(event_type, f"Tier 2: {tier2_raw[1]}")

                return StepResult(
                    step=step,
                    success=tier2_raw[0],
                    verification_method="vision",
                    evidence=tier2_raw[1],
                    duration_ms=duration,
                    screenshot_path=screenshot_path,
                )
            else:
                # AC-3: All Tier 2 conditions returned UNCLEAR.
                # Fall through to actuator-result fallback below.
                if self.logger:
                    self.logger.log_event(
                        EventType.VERIFY_ESCALATE,
                        "Tier 2 inconclusive (all UNCLEAR), falling back to actuator result",
                    )

        # No verification backend conclusive -- use actuator result
        duration = int((time.monotonic() - start) * 1000)
        return StepResult(
            step=step,
            success=actuator_result.get("success", False),
            verification_method="",
            evidence=f"No verifier conclusive. Actuator: {actuator_result}",
            duration_ms=duration,
        )

    def _get_accessibility_backend(self, coordinator=None):
        """Return the active accessibility backend if one exists."""
        candidate = self.accessibility
        if candidate is None and coordinator is not None:
            candidate = getattr(coordinator, "accessibility", None)
        if candidate is None:
            candidate = getattr(self.coordinator, "accessibility", None)
        if candidate is None:
            return None

        required_methods = ("get_frontmost_app", "get_focused_element")
        for method_name in required_methods:
            method = getattr(candidate, method_name, None)
            if method is None:
                return None
            if inspect.iscoroutinefunction(method) or "AsyncMock" in type(method).__name__:
                return None

        return candidate

    @staticmethod
    def _matches_expected_app(expected_app: str, actual_app: str) -> bool:
        """Return True when the active app matches the requested app name."""
        if not expected_app or not actual_app:
            return False
        expected = expected_app.lower()
        actual = actual_app.lower()
        return expected in actual or actual in expected

    @staticmethod
    def _is_browser_app(app_name: str) -> bool:
        """Return True when *app_name* looks like a web browser."""
        lowered = app_name.lower()
        return any(
            browser in lowered
            for browser in ("safari", "chrome", "firefox", "arc", "edge", "brave", "opera")
        )

    @staticmethod
    def _url_tokens(url: str) -> list[str]:
        """Extract high-signal tokens from a URL for state and vision checks."""
        if not url:
            return []
        parsed = urlparse(url)
        host = (parsed.netloc or parsed.path).lower().replace("www.", "")
        path_tokens = [token for token in parsed.path.lower().split("/") if token]
        host_parts = [part for part in host.replace(".", " ").split() if part not in {"com", "org", "net"}]
        path_parts = []
        for token in path_tokens[:4]:
            path_parts.extend(part for part in token.replace("-", " ").replace("_", " ").split() if part)
        # Filter out short/generic path segments
        return [token for token in host_parts + path_parts if token and len(token) > 2]

    @staticmethod
    def _extract_base_domain(url: str) -> str:
        """Extract the base domain from a URL, stripping www. prefix."""
        parsed = urlparse(url)
        host = (parsed.netloc or parsed.path).lower().replace("www.", "")
        return host

    def _build_url_condition(self, url: str) -> Optional[str]:
        """Build an action-specific vision condition for navigation checks."""
        tokens = self._url_tokens(url)
        if not tokens:
            return None
        joined = ", ".join(tokens[:4])
        return f"The frontmost browser page matches the destination URL and clearly shows content related to: {joined}"

    def _verify_tier0(
        self, step: ActionStep, accessibility
    ) -> Optional[Tuple[bool, str]]:
        """Tier 0: Fast structured verification via Accessibility."""
        verify_lower = step.verify.lower()

        try:
            frontmost = accessibility.get_frontmost_app()
        except Exception:
            frontmost = None

        expected_app = step.params.get("app_name", "")
        actual_app = ""
        if isinstance(frontmost, dict):
            actual_app = frontmost.get("name", "") or frontmost.get("bundle_id", "")

        if step.action == "activate_app" and expected_app:
            if not actual_app:
                return None
            if self._matches_expected_app(expected_app, actual_app):
                return (True, f"Accessibility reports frontmost app '{actual_app}'")
            return (False, f"Accessibility reports frontmost app '{actual_app}', expected '{expected_app}'")

        app_keywords = ["frontmost", "foreground", "is the active", "is open", "is running"]
        if expected_app and any(kw in verify_lower for kw in app_keywords):
            if not actual_app:
                return None
            if self._matches_expected_app(expected_app, actual_app):
                return (True, f"Accessibility reports frontmost app '{actual_app}'")
            return (False, f"Accessibility reports frontmost app '{actual_app}', expected '{expected_app}'")

        if step.action == "type_text" and step.params.get("text"):
            try:
                focused = accessibility.get_focused_element()
            except Exception:
                focused = None
            if focused is None:
                return None
            expected_text = step.params["text"]
            actual_value = ""
            for candidate in (focused.value, focused.title, focused.description):
                if candidate:
                    actual_value = str(candidate)
                    break
            if not actual_value:
                return None
            if expected_text in actual_value:
                return (
                    True,
                    f"Accessibility reports focused element contains '{expected_text}'",
                )
            return (
                False,
                f"Accessibility reports focused element value '{actual_value}', expected '{expected_text}'",
            )

        # AC-2: Click action — check if focused element is a text input field
        if step.action == "click":
            try:
                focused = accessibility.get_focused_element()
            except Exception:
                focused = None
            if focused is not None:
                role = getattr(focused, "role", None) or ""
                if role in TEXT_INPUT_AX_ROLES:
                    return (
                        True,
                        f"Accessibility confirms text field focused: {role}",
                    )
            return None  # Inconclusive -- let Tier 1/2 decide

        return None

    def _verify_tier1(
        self,
        step: ActionStep,
        actuator,
        actuator_result: Optional[Dict[str, Any]] = None,
    ) -> Optional[Tuple[bool, str]]:
        """Tier 1: Fast verification via actuator state.

        Returns (success, evidence) if conclusive, None if inconclusive.
        """
        if actuator_result is None:
            actuator_result = {}
        state = actuator.get_state()
        verify_lower = step.verify.lower()

        # For activate_app actions, always check frontmost app via state
        if step.action == "activate_app" and step.params.get("app_name"):
            expected_app = step.params["app_name"]
            actual_app = state.get("app_name", "")
            if not actual_app:
                # Actuator not responding — inconclusive, escalate
                return None
            if self._matches_expected_app(expected_app, actual_app):
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
                if self._matches_expected_app(expected_app, actual_app):
                    return (
                        True,
                        f"Frontmost app is '{actual_app}' (expected '{expected_app}')",
                    )
                else:
                    return (
                        False,
                        f"Frontmost app is '{actual_app}', expected '{expected_app}'",
                    )

        if step.action == "open_url" and step.params.get("url"):
            actual_app = state.get("app_name", "")
            window_title = state.get("window_title", "")
            browser_url = state.get("browser_url", "")
            if actual_app and not self._is_browser_app(actual_app):
                return (
                    False,
                    f"Navigation opened '{actual_app}' instead of a browser",
                )
            if not actual_app and not window_title and not browser_url:
                return None

            # P2-3: Domain constraint check from verify text.
            # Runs BEFORE URL token matching (fail-fast): if the browser is on
            # the wrong domain, there's no point checking URL tokens.
            _domain_match = re.search(
                r"browser domain is (\S+)", step.verify, re.IGNORECASE
            )
            if _domain_match and browser_url:
                _expected_domain = _domain_match.group(1).lower().replace(
                    "www.", ""
                )
                _actual_domain = self._extract_base_domain(browser_url)
                if not (
                    _actual_domain == _expected_domain
                    or _actual_domain.endswith(f".{_expected_domain}")
                ):
                    return (
                        False,
                        f"Browser domain '{_actual_domain}' does not match"
                        f" expected domain '{_expected_domain}'",
                    )

            expected_url = step.params["url"]
            tokens = self._url_tokens(expected_url)

            # Direct URL match (most reliable)
            if browser_url:
                expected_lower = expected_url.lower().rstrip("/")
                actual_lower = browser_url.lower().rstrip("/")

                # Login redirect detection: if the actual URL contains login/signin
                # paths but the expected URL does not, this is a redirect — not a match
                _login_segments = ("/login", "/signin", "/sign-in", "/auth", "/sso", "/ap/signin")
                redirected_to_login = (
                    any(seg in actual_lower for seg in _login_segments)
                    and not any(seg in expected_lower for seg in _login_segments)
                )
                if redirected_to_login:
                    return (
                        False,
                        f"Browser URL '{browser_url}' was redirected to login page "
                        f"(expected '{expected_url}')",
                    )

                if expected_lower in actual_lower or actual_lower in expected_lower:
                    return (
                        True,
                        f"Browser URL '{browser_url}' matches destination",
                    )
                # Token match against actual URL
                if tokens and any(token in actual_lower for token in tokens):
                    return (
                        True,
                        f"Browser URL '{browser_url}' contains destination tokens",
                    )

            # Fallback: window title match
            title_lower = window_title.lower()
            if tokens and title_lower and any(token in title_lower for token in tokens):
                return (
                    True,
                    f"Window title '{window_title}' matches destination URL",
                )
            return None

        if step.action == "type_text" and step.params.get("text"):
            expected_text = step.params["text"]
            for key in ("focused_value", "focused_text", "selected_text"):
                actual_value = state.get(key)
                if actual_value is None:
                    continue
                if expected_text in str(actual_value):
                    return (
                        True,
                        f"Actuator state {key} contains '{expected_text}'",
                    )
                return (
                    False,
                    f"Actuator state {key} is '{actual_value}', expected '{expected_text}'",
                )

        # P1-3: Scroll verification via tiered signals
        if step.action == "scroll":
            direction = step.params.get("direction", "down")
            scroll_before = actuator_result.get("_scroll_y_before")

            # Tier S1: JS scrollY delta
            get_scroll = getattr(actuator, "get_scroll_position", None)
            if get_scroll is not None and scroll_before is not None:
                scroll_after = get_scroll()
                if scroll_after is not None:
                    delta = scroll_after - scroll_before
                    if direction == "down" and delta > 0:
                        return (
                            True,
                            f"Scroll confirmed via scrollY delta "
                            f"({scroll_before} -> {scroll_after})",
                        )
                    if direction == "up" and delta < 0:
                        return (
                            True,
                            f"Scroll confirmed via scrollY delta "
                            f"({scroll_before} -> {scroll_after})",
                        )
                    # delta == 0 falls through to S2

            # Tier S2: Screenshot pixel-diff
            pixel_changed = actuator_result.get("_scroll_pixel_changed")
            if pixel_changed is True:
                return (
                    True,
                    "Scroll confirmed via screenshot pixel diff",
                )

            # Tier S3: Actuator success fallback
            if actuator_result.get("success", False):
                return (
                    True,
                    "Scroll accepted via actuator success (no JS or pixel signal)",
                )

        # For click, type_text, etc. -- Tier 1 is inconclusive, escalate to Tier 2
        return None

    @staticmethod
    def _tier2_conditions(step: ActionStep) -> list[str]:
        """Return unique tier-2 conditions in preferred order."""
        ordered = []
        for condition in (step.expected_observation, step.verify):
            normalized = condition.strip()
            if normalized and normalized not in ordered:
                ordered.append(normalized)
        return ordered

    async def _verify_tier2(
        self, step: ActionStep, coordinator, actuator_result: Dict[str, Any]
    ) -> Tuple[Optional[Tuple[bool, str]], Optional[str]]:
        """Tier 2: Vision-based verification.

        Returns (result, screenshot_b64) where result is:
          (True, evidence)  -- condition confirmed
          (False, evidence) -- condition denied by at least one check
          None              -- all checks inconclusive (UNCLEAR)
        """
        screenshot_b64 = None
        try:
            screenshot_b64 = await coordinator.capture_screenshot()
        except Exception:
            screenshot_b64 = None

        # AC-3: Track whether ANY condition was explicitly denied (False)
        # vs. all returning None (inconclusive).
        any_denied = False

        if screenshot_b64:
            # Site 1: Crop-region check for click actions
            region_b64 = self._crop_click_region(
                screenshot_b64,
                actuator_result.get("image_x"),
                actuator_result.get("image_y"),
            )
            if region_b64 is not None:
                local_condition = step.expected_observation.strip() or step.verify
                result = await coordinator.verify_condition(local_condition, screenshot_b64=region_b64)
                if result is True:
                    return (
                        (
                            True,
                            f"Vision confirms the clicked region satisfies: {local_condition}",
                        ),
                        screenshot_b64,
                    )
                if result is False:
                    any_denied = True

            # Site 2: type_text special case
            if step.action == "type_text" and step.params.get("text"):
                expected_text = step.params["text"]
                focused_text_condition = f'The focused text field contains "{expected_text}"'
                result = await coordinator.verify_condition(
                    focused_text_condition,
                    screenshot_b64=screenshot_b64,
                )
                if result is True:
                    return (
                        (
                            True,
                            f"Vision confirms focused field contains '{expected_text}'",
                        ),
                        screenshot_b64,
                    )
                if result is False:
                    any_denied = True

            # Site 3: open_url special case
            if step.action == "open_url" and step.params.get("url"):
                url_condition = self._build_url_condition(step.params["url"])
                if url_condition:
                    result = await coordinator.verify_condition(
                        url_condition,
                        screenshot_b64=screenshot_b64,
                    )
                    if result is True:
                        return (
                            (
                                True,
                                f"Vision confirms destination page for {step.params['url']}",
                            ),
                            screenshot_b64,
                        )
                    if result is False:
                        any_denied = True

            # Sites 4-5: Generic conditions (expected_observation, verify)
            for condition in self._tier2_conditions(step):
                result = await coordinator.verify_condition(
                    condition,
                    screenshot_b64=screenshot_b64,
                )
                if result is True:
                    return ((True, f"Vision confirms: {condition}"), screenshot_b64)
                if result is False:
                    any_denied = True
        else:
            for condition in self._tier2_conditions(step):
                result = await coordinator.verify_condition(condition)
                if result is True:
                    return ((True, f"Vision confirms: {condition}"), screenshot_b64)
                if result is False:
                    any_denied = True

        # No condition passed. Distinguish denial from inconclusive.
        if any_denied:
            denied_condition = step.expected_observation.strip() or step.verify
            return ((False, f"Vision denies: {denied_condition}"), screenshot_b64)
        # All conditions returned None (UNCLEAR) -- truly inconclusive
        return (None, screenshot_b64)

    def _crop_click_region(
        self,
        screenshot_b64: Optional[str],
        image_x: Optional[int],
        image_y: Optional[int],
        half_size: int = 200,
    ) -> Optional[str]:
        """Crop a square region around the clicked point for local verification."""
        if screenshot_b64 is None or image_x is None or image_y is None:
            return None

        try:
            from PIL import Image
        except ImportError:
            return None

        try:
            img = Image.open(io.BytesIO(base64.b64decode(screenshot_b64)))
            left = max(0, int(image_x) - half_size)
            top = max(0, int(image_y) - half_size)
            right = min(img.size[0], int(image_x) + half_size)
            bottom = min(img.size[1], int(image_y) + half_size)
            cropped = img.crop((left, top, right, bottom))
            buf = io.BytesIO()
            cropped.save(buf, format="JPEG", quality=85)
            return base64.b64encode(buf.getvalue()).decode()
        except Exception:
            return None
