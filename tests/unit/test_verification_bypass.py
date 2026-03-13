"""Unit tests for Slice 1: Verification bypass for text field clicks.

Covers AC-1 (type-and-check bypass), AC-2 (Tier 0 focus check),
and AC-3 (ternary verification).

All components are mocked — no real API calls.
"""

import base64
import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.config import AgentConfig
from automation_agent.logging.event_logger import EventLogger
from automation_agent.logging.models import EventType
from automation_agent.orchestrator.agent import AutomationAgent
from automation_agent.orchestrator.verifier import StepVerifier
from automation_agent.shared_models import (
    ActionPlan,
    ActionStep,
    FindElementResult,
    StepResult,
    TEXT_INPUT_AX_ROLES,
    TEXT_INPUT_KEYWORDS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> AgentConfig:
    defaults = {
        "_env_file": None,
        "anthropic_api_key": "test-key-not-real",
        "model_provider": "local",
        "grounding_model": "",
        "grounding_server_url": "",
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_agent(planner, skill_registry, coordinator, actuator, logger, config=None):
    if config is None:
        config = _make_config()
    return AutomationAgent(
        planner=planner,
        skill_registry=skill_registry,
        coordinator=coordinator,
        actuator=actuator,
        config=config,
        logger=logger,
    )


def _make_jpeg_b64(width: int = 100, height: int = 100) -> str:
    from PIL import Image

    img = Image.new("RGB", (width, height), color=(240, 240, 240))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------------------
# AC-3 Ternary Parsing Tests (coordinator)
# ---------------------------------------------------------------------------


class TestTernaryParsing:
    """AC-3: verify_condition returns True, False, or None."""

    async def test_verify_condition_returns_true_on_yes(self, tmp_path):
        """1. LLM returns 'YES' -> True."""
        from automation_agent.vision.coordinator import ScreenCoordinatorImpl

        config = _make_config(vision_model="molmo")
        capture = MagicMock()
        capture.capture_b64.return_value = "fakedata"
        coord = ScreenCoordinatorImpl(config, capture=capture)
        coord._call_vision_model = AsyncMock(return_value="YES")

        result = await coord.verify_condition("test condition", screenshot_b64="fakedata")
        assert result is True

    async def test_verify_condition_returns_true_on_yes_with_explanation(self):
        """2. LLM returns 'YES, the condition is met' -> True."""
        from automation_agent.vision.coordinator import ScreenCoordinatorImpl

        config = _make_config(vision_model="molmo")
        capture = MagicMock()
        capture.capture_b64.return_value = "fakedata"
        coord = ScreenCoordinatorImpl(config, capture=capture)
        coord._call_vision_model = AsyncMock(return_value="YES, the condition is clearly met")

        result = await coord.verify_condition("test condition", screenshot_b64="fakedata")
        assert result is True

    async def test_verify_condition_returns_false_on_no(self):
        """3. LLM returns 'NO' -> False."""
        from automation_agent.vision.coordinator import ScreenCoordinatorImpl

        config = _make_config(vision_model="molmo")
        capture = MagicMock()
        capture.capture_b64.return_value = "fakedata"
        coord = ScreenCoordinatorImpl(config, capture=capture)
        coord._call_vision_model = AsyncMock(return_value="NO")

        result = await coord.verify_condition("test condition", screenshot_b64="fakedata")
        assert result is False

    async def test_verify_condition_returns_none_on_unclear(self):
        """4. LLM returns 'UNCLEAR' -> None."""
        from automation_agent.vision.coordinator import ScreenCoordinatorImpl

        config = _make_config(vision_model="molmo")
        capture = MagicMock()
        capture.capture_b64.return_value = "fakedata"
        coord = ScreenCoordinatorImpl(config, capture=capture)
        coord._call_vision_model = AsyncMock(return_value="UNCLEAR")

        result = await coord.verify_condition("test condition", screenshot_b64="fakedata")
        assert result is None

    async def test_verify_condition_returns_none_on_unclear_with_explanation(self):
        """5. LLM returns 'UNCLEAR - cannot determine...' -> None."""
        from automation_agent.vision.coordinator import ScreenCoordinatorImpl

        config = _make_config(vision_model="molmo")
        capture = MagicMock()
        capture.capture_b64.return_value = "fakedata"
        coord = ScreenCoordinatorImpl(config, capture=capture)
        coord._call_vision_model = AsyncMock(
            return_value="UNCLEAR - cannot determine focus state from screenshot"
        )

        result = await coord.verify_condition("test condition", screenshot_b64="fakedata")
        assert result is None

    async def test_verify_condition_returns_false_on_garbage(self):
        """6. LLM returns 'I think maybe...' -> False."""
        from automation_agent.vision.coordinator import ScreenCoordinatorImpl

        config = _make_config(vision_model="molmo")
        capture = MagicMock()
        capture.capture_b64.return_value = "fakedata"
        coord = ScreenCoordinatorImpl(config, capture=capture)
        coord._call_vision_model = AsyncMock(return_value="I think maybe the condition is true")

        result = await coord.verify_condition("test condition", screenshot_b64="fakedata")
        assert result is False

    async def test_verify_condition_returns_false_on_empty(self):
        """7. LLM returns '' -> False."""
        from automation_agent.vision.coordinator import ScreenCoordinatorImpl

        config = _make_config(vision_model="molmo")
        capture = MagicMock()
        capture.capture_b64.return_value = "fakedata"
        coord = ScreenCoordinatorImpl(config, capture=capture)
        coord._call_vision_model = AsyncMock(return_value="")

        result = await coord.verify_condition("test condition", screenshot_b64="fakedata")
        assert result is False


# ---------------------------------------------------------------------------
# AC-2 Tier 0 Click Focus Tests (verifier)
# ---------------------------------------------------------------------------


class TestTier0ClickFocus:
    """AC-2: Tier 0 returns pass when click focuses a text field."""

    async def test_tier0_click_returns_pass_when_text_field_focused(self, tmp_log_dir):
        """8. Click + AX focused element role=AXTextField -> (True, ...)."""
        accessibility = MagicMock()
        accessibility.get_frontmost_app = MagicMock(return_value={"name": "Safari"})
        focused = MagicMock()
        focused.role = "AXTextField"
        accessibility.get_focused_element = MagicMock(return_value=focused)

        verifier = StepVerifier(logger=EventLogger(tmp_log_dir), accessibility=accessibility)
        step = ActionStep(
            action="click",
            params={"element": "Search bar"},
            verify="Search field is focused",
        )

        result = await verifier.verify(step, {"success": True}, actuator=MagicMock(get_state=MagicMock(return_value={})))
        assert result.success is True
        assert "text field focused" in result.evidence.lower()

    async def test_tier0_click_returns_pass_for_search_field(self, tmp_log_dir):
        """9. Click + AX focused role=AXSearchField -> (True, ...)."""
        accessibility = MagicMock()
        accessibility.get_frontmost_app = MagicMock(return_value={"name": "Safari"})
        focused = MagicMock()
        focused.role = "AXSearchField"
        accessibility.get_focused_element = MagicMock(return_value=focused)

        verifier = StepVerifier(logger=EventLogger(tmp_log_dir), accessibility=accessibility)
        step = ActionStep(
            action="click",
            params={"element": "URL bar"},
            verify="Address bar is active",
        )

        result = await verifier.verify(step, {"success": True}, actuator=MagicMock(get_state=MagicMock(return_value={})))
        assert result.success is True
        assert "AXSearchField" in result.evidence

    async def test_tier0_click_returns_none_when_no_focused_element(self, tmp_log_dir):
        """10. AX returns None focused -> Tier 0 inconclusive, falls through."""
        accessibility = MagicMock()
        accessibility.get_frontmost_app = MagicMock(return_value={"name": "Safari"})
        accessibility.get_focused_element = MagicMock(return_value=None)

        coord = AsyncMock()
        coord.verify_condition = AsyncMock(return_value=True)
        coord.capture_screenshot = AsyncMock(return_value=_make_jpeg_b64())

        verifier = StepVerifier(
            coordinator=coord,
            logger=EventLogger(tmp_log_dir),
            accessibility=accessibility,
        )
        step = ActionStep(
            action="click",
            params={"element": "Some button"},
            verify="Button clicked",
        )

        result = await verifier.verify(step, {"success": True})
        # Should fall through to Tier 2 (vision), not be caught by Tier 0
        assert result.verification_method != "accessibility"

    async def test_tier0_click_returns_none_when_non_text_role(self, tmp_log_dir):
        """11. AX focused role=AXButton -> Tier 0 inconclusive."""
        accessibility = MagicMock()
        accessibility.get_frontmost_app = MagicMock(return_value={"name": "Safari"})
        focused = MagicMock()
        focused.role = "AXButton"
        accessibility.get_focused_element = MagicMock(return_value=focused)

        coord = AsyncMock()
        coord.verify_condition = AsyncMock(return_value=True)
        coord.capture_screenshot = AsyncMock(return_value=_make_jpeg_b64())

        verifier = StepVerifier(
            coordinator=coord,
            logger=EventLogger(tmp_log_dir),
            accessibility=accessibility,
        )
        step = ActionStep(
            action="click",
            params={"element": "Submit"},
            verify="Form submitted",
        )

        result = await verifier.verify(step, {"success": True})
        # Should NOT be verified by accessibility (AXButton is not a text field)
        assert result.verification_method != "accessibility"

    async def test_tier0_click_does_not_interfere_with_existing_type_text(self, tmp_log_dir):
        """12. type_text still handled by existing Tier 0 code, not new click branch."""
        accessibility = MagicMock()
        accessibility.get_frontmost_app = MagicMock(return_value={"name": "Safari"})
        focused = MagicMock()
        focused.role = "AXTextField"
        focused.value = "hello world"
        focused.title = ""
        focused.description = ""
        accessibility.get_focused_element = MagicMock(return_value=focused)

        verifier = StepVerifier(logger=EventLogger(tmp_log_dir), accessibility=accessibility)
        step = ActionStep(
            action="type_text",
            params={"text": "hello world"},
            verify="Text field contains hello world",
        )

        result = await verifier.verify(step, {"success": True}, actuator=MagicMock(get_state=MagicMock(return_value={})))
        assert result.success is True
        assert result.verification_method == "accessibility"
        # Should use existing type_text handler, not click handler
        assert "contains 'hello world'" in result.evidence


# ---------------------------------------------------------------------------
# AC-3 Tier 2 None Handling (verifier)
# ---------------------------------------------------------------------------


class TestTier2NoneHandling:
    """AC-3: _verify_tier2 returns None when all conditions are UNCLEAR."""

    async def test_tier2_returns_none_when_all_conditions_unclear(self, tmp_log_dir):
        """13. All verify_condition calls return None -> tier2 returns None."""
        coord = AsyncMock()
        coord.verify_condition = AsyncMock(return_value=None)  # UNCLEAR
        coord.capture_screenshot = AsyncMock(return_value=_make_jpeg_b64())

        verifier = StepVerifier(
            coordinator=coord,
            logger=EventLogger(tmp_log_dir),
        )
        step = ActionStep(
            action="click",
            params={"element": "Search field"},
            verify="Search field is focused",
        )

        # With no accessibility and no actuator, goes straight to Tier 2
        result = await verifier.verify(step, {"success": True})
        # When all UNCLEAR, should fall back to actuator result
        assert result.success is True  # actuator said success
        assert "No verifier conclusive" in result.evidence or result.verification_method == ""

    async def test_tier2_returns_false_when_any_condition_denied(self, tmp_log_dir):
        """14. At least one verify_condition returns False -> tier2 returns (False, ...)."""
        coord = AsyncMock()
        # First call returns None, second returns False
        coord.verify_condition = AsyncMock(side_effect=[None, False])
        coord.capture_screenshot = AsyncMock(return_value=_make_jpeg_b64())

        verifier = StepVerifier(
            coordinator=coord,
            logger=EventLogger(tmp_log_dir),
        )
        step = ActionStep(
            action="click",
            params={"element": "Button"},
            verify="Button state changed",
            expected_observation="The button is pressed",
        )

        result = await verifier.verify(step, {"success": True})
        assert result.success is False
        assert "denies" in result.evidence.lower()

    async def test_verify_falls_through_to_actuator_when_tier2_inconclusive(self, tmp_log_dir):
        """15. Tier 0 None, Tier 1 None, Tier 2 all UNCLEAR -> actuator fallback."""
        accessibility = MagicMock()
        accessibility.get_frontmost_app = MagicMock(return_value={"name": "TestApp"})
        accessibility.get_focused_element = MagicMock(return_value=None)

        act = MagicMock()
        act.get_state = MagicMock(return_value={"app_name": "TestApp"})

        coord = AsyncMock()
        coord.verify_condition = AsyncMock(return_value=None)  # UNCLEAR
        coord.capture_screenshot = AsyncMock(return_value=_make_jpeg_b64())

        verifier = StepVerifier(
            actuator=act,
            coordinator=coord,
            logger=EventLogger(tmp_log_dir),
            accessibility=accessibility,
        )
        step = ActionStep(
            action="click",
            params={"element": "Some element"},
            verify="Something happened",
        )

        result = await verifier.verify(step, {"success": True})
        # All tiers inconclusive -> actuator fallback -> success=True
        assert result.success is True


# ---------------------------------------------------------------------------
# AC-1 Type-and-Check Bypass Tests (orchestrator)
# ---------------------------------------------------------------------------


class TestTypeAndCheckBypass:
    """AC-1: Failed click on text field bypassed when next type_text succeeds."""

    async def test_type_and_check_bypass_click_then_type_success(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """16. Click fails verification (screenshot_diff), next type_text succeeds -> click passes.

        Uses screenshot_diff to force the click failure (no visible effect).
        AX detects text field focus via _check_text_field_focused, enabling bypass.
        """
        # AX on coordinator for _check_text_field_focused
        accessibility = MagicMock()
        focused = MagicMock()
        focused.role = "AXSearchField"
        # Set value/title/description to None so Tier 0 type_text handler
        # returns inconclusive (not False) for the type_text step
        focused.value = None
        focused.title = None
        focused.description = None
        accessibility.get_focused_element = MagicMock(return_value=focused)
        mock_coordinator.accessibility = accessibility

        # screenshot_diff says no visible effect -> forces click failure
        screenshot_diff = MagicMock()
        screenshot_diff.capture_before = MagicMock()
        screenshot_diff.region_changed = MagicMock(return_value=False)
        screenshot_diff.screen_changed = MagicMock(return_value=False)

        mock_planner.plan = AsyncMock(return_value=ActionPlan(
            steps=[
                ActionStep(
                    action="click",
                    params={"element": "Search all orders"},
                    verify="Search field is focused with cursor",
                ),
                ActionStep(
                    action="type_text",
                    params={"text": "headphones"},
                    verify="Search field contains headphones",
                ),
                ActionStep(action="done", params={}, verify=""),
            ],
            goal="Search for headphones",
        ))

        # type_text verify succeeds
        mock_coordinator.verify_condition = AsyncMock(return_value=True)

        logger = EventLogger(tmp_log_dir)
        config = _make_config()
        agent = AutomationAgent(
            planner=mock_planner,
            skill_registry=mock_skill_registry,
            coordinator=mock_coordinator,
            actuator=mock_actuator,
            config=config,
            logger=logger,
            screenshot_diff=screenshot_diff,
        )

        result = await agent.execute("Search for headphones")
        assert result.success is True

        # Check that the click step was retroactively marked as success
        click_result = result.steps[0]
        assert click_result.success is True
        assert click_result.verification_method == "type_and_check"
        assert "type-and-check" in click_result.evidence

    async def test_type_and_check_bypass_click_then_type_both_fail(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """17. Both click (screenshot_diff failure) and type_text fail -> both reported."""
        accessibility = MagicMock()
        focused = MagicMock()
        focused.role = "AXSearchField"
        focused.value = None
        focused.title = None
        focused.description = None
        accessibility.get_focused_element = MagicMock(return_value=focused)
        mock_coordinator.accessibility = accessibility

        # screenshot_diff forces click failure
        screenshot_diff = MagicMock()
        screenshot_diff.capture_before = MagicMock()
        screenshot_diff.region_changed = MagicMock(return_value=False)
        screenshot_diff.screen_changed = MagicMock(return_value=False)

        mock_planner.plan = AsyncMock(return_value=ActionPlan(
            steps=[
                ActionStep(
                    action="click",
                    params={"element": "Search field"},
                    verify="Search field focused",
                    on_fail="abort",
                ),
                ActionStep(
                    action="type_text",
                    params={"text": "test"},
                    verify="Text entered",
                ),
                ActionStep(action="done", params={}, verify=""),
            ],
            goal="Type in search",
        ))

        # type_text verification also fails
        mock_coordinator.verify_condition = AsyncMock(return_value=False)

        logger = EventLogger(tmp_log_dir)
        config = _make_config()
        agent = AutomationAgent(
            planner=mock_planner,
            skill_registry=mock_skill_registry,
            coordinator=mock_coordinator,
            actuator=mock_actuator,
            config=config,
            logger=logger,
            screenshot_diff=screenshot_diff,
        )

        result = await agent.execute("Type in search")
        assert result.success is False

    async def test_type_and_check_bypass_not_triggered_for_non_text_field(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """18. Click on button (no AX text field, no keywords) -> normal failure, no bypass."""
        # No accessibility backend
        mock_coordinator.accessibility = None

        mock_planner.plan = AsyncMock(return_value=ActionPlan(
            steps=[
                ActionStep(
                    action="click",
                    params={"element": "Submit button"},
                    verify="Form submitted",
                    on_fail="abort",
                ),
                ActionStep(
                    action="type_text",
                    params={"text": "test"},
                    verify="Text entered",
                ),
                ActionStep(action="done", params={}, verify=""),
            ],
            goal="Submit form",
        ))

        mock_coordinator.verify_condition = AsyncMock(return_value=False)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Submit form")
        assert result.success is False
        # The click step should NOT have type_and_check as verification method
        click_result = result.steps[0]
        assert click_result.verification_method != "type_and_check"

    async def test_type_and_check_bypass_keyword_fallback(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """19. No AX backend, but element desc contains 'search' -> bypass triggered."""
        mock_coordinator.accessibility = None  # No AX

        mock_planner.plan = AsyncMock(return_value=ActionPlan(
            steps=[
                ActionStep(
                    action="click",
                    params={"element": "search bar"},  # keyword match
                    verify="Search bar focused",
                ),
                ActionStep(
                    action="type_text",
                    params={"text": "headphones"},
                    verify="Search contains headphones",
                ),
                ActionStep(action="done", params={}, verify=""),
            ],
            goal="Search for headphones",
        ))

        # Click verify fails, type_text verify succeeds
        mock_coordinator.verify_condition = AsyncMock(
            side_effect=[False, True]
        )

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Search for headphones")
        assert result.success is True
        click_result = result.steps[0]
        assert click_result.verification_method == "type_and_check"

    async def test_type_and_check_bypass_not_across_replan(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """20. Bypass only within single plan execution, not across replan boundaries."""
        # This test verifies that bypass only operates within plan.steps[i] -> plan.steps[i+1]
        # The bypass should not fire across replan boundaries.
        mock_coordinator.accessibility = None

        # The plan has a click with no matching next step (next is done, not type_text)
        mock_planner.plan = AsyncMock(return_value=ActionPlan(
            steps=[
                ActionStep(
                    action="click",
                    params={"element": "search input"},  # keyword match
                    verify="Search focused",
                    on_fail="abort",
                ),
                ActionStep(action="done", params={}, verify=""),
            ],
            goal="Click search",
        ))

        mock_coordinator.verify_condition = AsyncMock(return_value=False)

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        result = await agent.execute("Click search")
        # Next step is "done", not type_text/press_key, so bypass should NOT fire
        assert result.success is False

    async def test_type_and_check_bypass_screenshot_diff_failure_path(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """21. screenshot_diff forces failure, but AX detects text field -> bypass still triggers."""
        accessibility = MagicMock()
        focused = MagicMock()
        focused.role = "AXTextField"
        focused.value = None
        focused.title = None
        focused.description = None
        accessibility.get_focused_element = MagicMock(return_value=focused)
        mock_coordinator.accessibility = accessibility

        # Set up screenshot_diff that says no visible effect
        screenshot_diff = MagicMock()
        screenshot_diff.capture_before = MagicMock()
        screenshot_diff.region_changed = MagicMock(return_value=False)
        screenshot_diff.screen_changed = MagicMock(return_value=False)

        mock_planner.plan = AsyncMock(return_value=ActionPlan(
            steps=[
                ActionStep(
                    action="click",
                    params={"element": "Search field"},
                    verify="Search field focused",
                ),
                ActionStep(
                    action="type_text",
                    params={"text": "query"},
                    verify="Search contains query",
                ),
                ActionStep(action="done", params={}, verify=""),
            ],
            goal="Search for query",
        ))

        # type_text verification succeeds
        mock_coordinator.verify_condition = AsyncMock(return_value=True)

        logger = EventLogger(tmp_log_dir)
        config = _make_config()
        agent = AutomationAgent(
            planner=mock_planner,
            skill_registry=mock_skill_registry,
            coordinator=mock_coordinator,
            actuator=mock_actuator,
            config=config,
            logger=logger,
            screenshot_diff=screenshot_diff,
        )

        result = await agent.execute("Search for query")
        assert result.success is True
        # Click should be retroactively confirmed via type-and-check
        click_result = result.steps[0]
        assert click_result.verification_method == "type_and_check"


# ---------------------------------------------------------------------------
# Constants tests
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify shared constants are defined correctly."""

    def test_text_input_ax_roles_contains_expected(self):
        assert "AXTextField" in TEXT_INPUT_AX_ROLES
        assert "AXTextArea" in TEXT_INPUT_AX_ROLES
        assert "AXSearchField" in TEXT_INPUT_AX_ROLES
        assert "AXComboBox" in TEXT_INPUT_AX_ROLES
        assert "AXButton" not in TEXT_INPUT_AX_ROLES

    def test_text_input_keywords_contains_expected(self):
        assert "search" in TEXT_INPUT_KEYWORDS
        assert "input" in TEXT_INPUT_KEYWORDS
        assert "text field" in TEXT_INPUT_KEYWORDS
        assert "address bar" in TEXT_INPUT_KEYWORDS

    def test_type_and_check_is_valid_verification_method(self):
        """type_and_check must be in StepResult valid_methods."""
        step = ActionStep(action="click", params={}, verify="test")
        # Should not raise
        result = StepResult(
            step=step,
            success=True,
            verification_method="type_and_check",
            evidence="test",
        )
        assert result.verification_method == "type_and_check"


# ---------------------------------------------------------------------------
# AC-3 _wait_for_user None Handling Tests
# ---------------------------------------------------------------------------


class TestWaitForUserNoneHandling:
    """AC-3: _wait_for_user correctly handles ternary verify_condition results."""

    async def test_wait_for_user_skips_when_condition_false(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """25. verify_condition returns False -> wait skipped immediately."""
        mock_coordinator.verify_condition = AsyncMock(return_value=False)
        mock_coordinator.accessibility = None

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        step = ActionStep(
            action="wait_for_user",
            params={"message": "Wait for login", "condition": "Login dialog visible"},
            verify="",
        )
        result = await agent._wait_for_user(step)
        assert result.success is True
        assert "skipped" in result.evidence.lower() or "not present" in result.evidence.lower()

    async def test_wait_for_user_does_not_skip_when_condition_none(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """26. verify_condition returns None (UNCLEAR) -> wait is NOT skipped.

        This is the safety-critical path: `not None` is `True`, but we use
        `is False` so UNCLEAR falls through to the polling loop.
        """
        mock_coordinator.verify_condition = AsyncMock(return_value=None)
        # capture_screenshot will be called in the polling loop — return fake image
        mock_coordinator.capture_screenshot = AsyncMock(return_value=_make_jpeg_b64())
        mock_coordinator.accessibility = None

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        # Shorten timeout so we don't wait forever
        agent._WAIT_TIMEOUT_S = 0.1
        agent._WAIT_POLL_INTERVAL_S = 0.05

        step = ActionStep(
            action="wait_for_user",
            params={"message": "Wait for page", "condition": "Login dialog visible"},
            verify="",
        )
        result = await agent._wait_for_user(step)
        # Should NOT have been skipped — should time out from polling loop instead
        assert result.success is True
        assert "not present" not in result.evidence.lower()
        assert "skipped" not in result.evidence.lower()
        # Should have timed out or detected a screen change
        assert "timed out" in result.evidence.lower() or "changed" in result.evidence.lower()

    async def test_wait_for_user_skips_when_condition_true(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """27. verify_condition returns True -> does NOT skip (condition IS present)."""
        mock_coordinator.verify_condition = AsyncMock(return_value=True)
        mock_coordinator.capture_screenshot = AsyncMock(return_value=_make_jpeg_b64())
        mock_coordinator.accessibility = None

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        # Shorten timeout so test doesn't block
        agent._WAIT_TIMEOUT_S = 0.1
        agent._WAIT_POLL_INTERVAL_S = 0.05

        step = ActionStep(
            action="wait_for_user",
            params={"message": "Wait for dialog", "condition": "Dialog visible"},
            verify="",
        )
        result = await agent._wait_for_user(step)
        # condition_visible is True -> NOT False -> enters polling loop, eventually times out
        assert "skipped" not in result.evidence.lower()
        assert "not present" not in result.evidence.lower()


# ---------------------------------------------------------------------------
# AC-3 _validate_candidate None Handling Test
# ---------------------------------------------------------------------------


class TestValidateCandidateNoneHandling:
    """AC-3: _validate_candidate treats None from verify_condition as falsy."""

    async def test_validate_candidate_treats_none_as_false(
        self, mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """28. verify_condition returns None -> _validate_candidate returns falsy.

        The spec says 'no code change needed beyond the type' because
        None is already falsy in Python. This test confirms the implicit
        behavior: the caller of _validate_candidate receives a falsy
        value when vision is UNCLEAR, preventing auto-clicking uncertain targets.
        """
        mock_coordinator.verify_condition = AsyncMock(return_value=None)
        mock_coordinator.capture_screenshot = AsyncMock(return_value=_make_jpeg_b64(200, 200))
        mock_coordinator.accessibility = None

        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, logger
        )

        is_valid = await agent._validate_candidate(100, 100, "Save button")
        # None propagates through and is falsy — caller treats as invalid
        assert not is_valid


# ---------------------------------------------------------------------------
# AC-1 Bypass in _replan_and_continue Loop
# ---------------------------------------------------------------------------


class TestBypassInReplanLoop:
    """AC-1: Type-and-check bypass fires correctly inside _replan_and_continue."""

    async def test_bypass_triggers_in_replan_loop(
        self, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """29. Bypass triggers inside replan: click (screenshot_diff fail) + type_text succeeds."""
        accessibility = MagicMock()
        focused = MagicMock()
        focused.role = "AXTextField"
        focused.value = None
        focused.title = None
        focused.description = None
        accessibility.get_focused_element = MagicMock(return_value=focused)
        mock_coordinator.accessibility = accessibility

        # screenshot_diff forces click failure
        screenshot_diff = MagicMock()
        screenshot_diff.capture_before = MagicMock()
        screenshot_diff.region_changed = MagicMock(return_value=False)
        screenshot_diff.screen_changed = MagicMock(return_value=False)

        # Initial plan: one step that triggers replan
        initial_plan = ActionPlan(
            steps=[
                ActionStep(
                    action="click",
                    params={"element": "Orders tab"},
                    verify="Orders visible",
                    on_fail="replan",
                ),
            ],
            goal="Search for order",
        )
        # Replan plan: click search field + type_text + done
        replan_plan = ActionPlan(
            steps=[
                ActionStep(
                    action="click",
                    params={"element": "Search field"},
                    verify="Search field focused",
                ),
                ActionStep(
                    action="type_text",
                    params={"text": "headphones"},
                    verify="Search contains headphones",
                ),
                ActionStep(action="done", params={}, verify=""),
            ],
            goal="Search for order",
        )

        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=initial_plan)
        planner.replan = AsyncMock(return_value=replan_plan)

        # For verification: fail initial click, succeed type_text verify in replan
        verify_side_effects = [
            False,  # Initial click verify -> fails -> triggers replan
            True,   # Replan type_text verify -> succeeds
        ]
        mock_coordinator.verify_condition = AsyncMock(side_effect=verify_side_effects)

        logger = EventLogger(tmp_log_dir)
        config = _make_config()
        agent = AutomationAgent(
            planner=planner,
            skill_registry=mock_skill_registry,
            coordinator=mock_coordinator,
            actuator=mock_actuator,
            config=config,
            logger=logger,
            screenshot_diff=screenshot_diff,
        )

        result = await agent.execute("Search for order")
        assert result.success is True

        # Find the replan click result — should be type_and_check
        replan_results = [r for r in result.steps if r.step.params.get("element") == "Search field"]
        assert len(replan_results) >= 1
        assert replan_results[0].verification_method == "type_and_check"

    async def test_bypass_both_fail_in_replan_loop(
        self, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir
    ):
        """30. Bypass both-fail in replan loop: click + type_text both fail -> failure returned."""
        accessibility = MagicMock()
        focused = MagicMock()
        focused.role = "AXSearchField"
        focused.value = None
        focused.title = None
        focused.description = None
        accessibility.get_focused_element = MagicMock(return_value=focused)
        mock_coordinator.accessibility = accessibility

        # screenshot_diff forces click failure
        screenshot_diff = MagicMock()
        screenshot_diff.capture_before = MagicMock()
        screenshot_diff.region_changed = MagicMock(return_value=False)
        screenshot_diff.screen_changed = MagicMock(return_value=False)

        initial_plan = ActionPlan(
            steps=[
                ActionStep(
                    action="click",
                    params={"element": "Trigger"},
                    verify="Triggered",
                    on_fail="replan",
                ),
            ],
            goal="Type in search",
        )
        replan_plan = ActionPlan(
            steps=[
                ActionStep(
                    action="click",
                    params={"element": "Search bar"},
                    verify="Search focused",
                    on_fail="abort",
                ),
                ActionStep(
                    action="type_text",
                    params={"text": "test"},
                    verify="Text entered",
                ),
                ActionStep(action="done", params={}, verify=""),
            ],
            goal="Type in search",
        )

        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=initial_plan)
        planner.replan = AsyncMock(return_value=replan_plan)

        # All verifications fail
        mock_coordinator.verify_condition = AsyncMock(return_value=False)

        logger = EventLogger(tmp_log_dir)
        config = _make_config()
        agent = AutomationAgent(
            planner=planner,
            skill_registry=mock_skill_registry,
            coordinator=mock_coordinator,
            actuator=mock_actuator,
            config=config,
            logger=logger,
            screenshot_diff=screenshot_diff,
        )

        result = await agent.execute("Type in search")
        assert result.success is False
