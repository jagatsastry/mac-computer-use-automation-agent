"""Unit tests for the StepVerifier component.

All components (actuator, coordinator) are mocked -- no real API calls.
"""

import base64
import io
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from automation_agent.logging.event_logger import EventLogger
from automation_agent.logging.models import EventType
from automation_agent.orchestrator.verifier import StepVerifier
from automation_agent.shared_models import ActionPlan, ActionStep, StepResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_jpeg_b64(width: int = 100, height: int = 100) -> str:
    from PIL import Image

    img = Image.new("RGB", (width, height), color=(240, 240, 240))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def mock_act():
    """Mock actuator for verifier tests."""
    act = MagicMock()
    act.get_state = MagicMock(
        return_value={
            "app_name": "Calculator",
            "app_bundle": "com.apple.Calculator",
            "window_title": "Calculator",
            "window_frame": '{"x":0,"y":25,"w":400,"h":300}',
        }
    )
    return act


@pytest.fixture
def mock_coord():
    """Mock coordinator for verifier tests."""
    coord = AsyncMock()
    coord.verify_condition = AsyncMock(return_value=True)
    coord.capture_screenshot = AsyncMock(return_value=_make_jpeg_b64())
    return coord


@pytest.fixture
def logger(tmp_log_dir):
    """Real EventLogger writing to a temp directory."""
    return EventLogger(tmp_log_dir)


@pytest.fixture
def mock_accessibility():
    bridge = MagicMock()
    bridge.get_frontmost_app = MagicMock(return_value={"name": "Calculator"})
    bridge.get_focused_element = MagicMock(return_value=None)
    return bridge


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTier1Verification:
    """Tests for Tier 1 (actuator state) verification."""

    async def test_tier1_confirms_app_name_match(self, mock_act, logger):
        """1. Tier 1 confirms when frontmost app matches expected app."""
        step = ActionStep(
            action="activate_app",
            params={"app_name": "Calculator"},
            verify="Calculator is the frontmost application",
        )
        verifier = StepVerifier(actuator=mock_act, logger=logger)

        result = await verifier.verify(step, {"success": True, "output": ""})

        assert result.success is True
        assert result.verification_method == "actuator_state"
        assert "Calculator" in result.evidence

    async def test_tier1_denies_wrong_app(self, mock_act, logger):
        """2. Tier 1 denies when frontmost app does not match expected."""
        mock_act.get_state.return_value = {
            "app_name": "Finder",
            "app_bundle": "com.apple.Finder",
            "window_title": "Desktop",
        }
        step = ActionStep(
            action="activate_app",
            params={"app_name": "Safari"},
            verify="Safari is the frontmost application",
        )
        verifier = StepVerifier(actuator=mock_act, logger=logger)

        result = await verifier.verify(step, {"success": True, "output": ""})

        assert result.success is False
        assert result.verification_method == "actuator_state"
        assert "Finder" in result.evidence
        assert "Safari" in result.evidence

    async def test_tier1_inconclusive_escalates_to_tier2(
        self, mock_act, mock_coord, logger
    ):
        """3. Tier 1 inconclusive (click/type action) escalates to Tier 2."""
        step = ActionStep(
            action="click",
            params={"x": 500, "y": 300},
            verify="Button appears pressed",
        )
        verifier = StepVerifier(
            actuator=mock_act, coordinator=mock_coord, logger=logger
        )

        result = await verifier.verify(step, {"success": True, "output": ""})

        # Should have escalated to tier 2 (vision)
        assert result.verification_method == "vision"
        mock_coord.verify_condition.assert_awaited_once()
        assert mock_coord.verify_condition.call_args.args[0] == "Button appears pressed"
        assert "screenshot_b64" in mock_coord.verify_condition.call_args.kwargs

        # Check that escalation was logged
        event_types = [e.event_type for e in logger.events]
        assert EventType.VERIFY_ESCALATE in event_types

    async def test_tier0_accessibility_confirms_focused_text(
        self, mock_coord, mock_accessibility, logger
    ):
        focused = MagicMock()
        focused.value = "hello world"
        focused.title = None
        focused.description = None
        mock_accessibility.get_focused_element.return_value = focused

        step = ActionStep(
            action="type_text",
            params={"text": "hello"},
            verify="Text field contains hello",
        )
        verifier = StepVerifier(
            coordinator=mock_coord,
            logger=logger,
            accessibility=mock_accessibility,
        )

        result = await verifier.verify(step, {"success": True, "output": ""})

        assert result.success is True
        assert result.verification_method == "accessibility"
        mock_coord.verify_condition.assert_not_called()


class TestTier2Verification:
    """Tests for Tier 2 (Vision) verification."""

    async def test_tier2_confirms(self, mock_coord, logger):
        """4. Tier 2 confirms condition via vision."""
        step = ActionStep(
            action="click",
            params={"x": 100, "y": 200},
            verify="Submit button visible",
            expected_observation="The submit button appears pressed",
        )
        # No actuator -- goes straight to tier 2
        verifier = StepVerifier(coordinator=mock_coord, logger=logger)

        result = await verifier.verify(step, {"success": True, "output": ""})

        assert result.success is True
        assert result.verification_method == "vision"
        assert "Vision confirms" in result.evidence
        mock_coord.verify_condition.assert_awaited_once_with(
            "The submit button appears pressed",
            screenshot_b64=ANY,
        )

    async def test_tier2_falls_back_from_expected_observation_to_verify(self, mock_coord, logger):
        """Tier 2 should try expected_observation before the generic verify text."""
        mock_coord.verify_condition = AsyncMock(side_effect=[False, True])
        mock_coord.capture_screenshot = AsyncMock(return_value=_make_jpeg_b64())

        step = ActionStep(
            action="click",
            params={"x": 100, "y": 200},
            verify="Submit button visible",
            expected_observation="The submit button appears pressed",
        )
        verifier = StepVerifier(coordinator=mock_coord, logger=logger)

        result = await verifier.verify(step, {"success": True, "output": ""})

        assert result.success is True
        assert mock_coord.verify_condition.call_args_list[0].args[0] == "The submit button appears pressed"
        assert mock_coord.verify_condition.call_args_list[1].args[0] == "Submit button visible"

    async def test_tier2_denies(self, mock_coord, logger):
        """5. Tier 2 denies condition with evidence."""
        mock_coord.verify_condition = AsyncMock(return_value=False)

        step = ActionStep(
            action="type_text",
            params={"text": "hello"},
            verify="Text field contains 'hello'",
        )
        verifier = StepVerifier(coordinator=mock_coord, logger=logger)

        result = await verifier.verify(step, {"success": True, "output": ""})

        assert result.success is False
        assert result.verification_method == "vision"
        assert "Vision denies" in result.evidence
        assert "hello" in result.evidence
        assert mock_coord.verify_condition.await_count == 2

    async def test_open_url_tier1_matches_window_title(self, mock_act, logger):
        mock_act.get_state.return_value = {
            "app_name": "Safari",
            "window_title": "Example Domain",
        }
        step = ActionStep(
            action="open_url",
            params={"url": "https://example.com"},
            verify="Example page visible",
        )
        verifier = StepVerifier(actuator=mock_act, logger=logger)

        result = await verifier.verify(step, {"success": True, "output": ""})

        assert result.success is True
        assert result.verification_method == "actuator_state"
        assert "matches destination URL" in result.evidence

    async def test_click_region_verification_uses_local_crop(self, mock_coord, logger):
        step = ActionStep(
            action="click",
            params={"element": "Continue button"},
            verify="Continue button appears pressed",
        )
        verifier = StepVerifier(coordinator=mock_coord, logger=logger)

        result = await verifier.verify(
            step,
            {"success": True, "output": "", "image_x": 50, "image_y": 50},
        )

        assert result.success is True
        assert "clicked region" in result.evidence
        first_call = mock_coord.verify_condition.call_args_list[0]
        assert first_call.args[0] == "Continue button appears pressed"
        assert "screenshot_b64" in first_call.kwargs


class TestNoVerifyCondition:
    """Tests for steps without a verify condition."""

    async def test_no_verify_uses_actuator_result(self, logger):
        """6. No verify condition (done step) uses actuator result."""
        step = ActionStep(action="done", params={}, verify="")
        verifier = StepVerifier(logger=logger)

        result = await verifier.verify(step, {"success": True, "output": "done"})

        assert result.success is True
        assert "Actuator result" in result.evidence

    async def test_no_verify_with_failure(self, logger):
        """6b. No verify condition with actuator failure."""
        step = ActionStep(action="done", params={}, verify="")
        verifier = StepVerifier(logger=logger)

        result = await verifier.verify(step, {"success": False, "output": "error"})

        assert result.success is False


class TestScreenshotCapture:
    """Tests for screenshot capture during verification."""

    async def test_screenshot_saved_at_verification(self, mock_coord, logger):
        """7. Screenshot is always saved during Tier 2 verification."""
        step = ActionStep(
            action="click",
            params={"x": 100, "y": 200},
            verify="Element clicked",
        )
        verifier = StepVerifier(coordinator=mock_coord, logger=logger)

        result = await verifier.verify(step, {"success": True, "output": ""})

        # Screenshot should have been captured
        mock_coord.capture_screenshot.assert_awaited_once()
        # Screenshot path should be set on the result
        assert result.screenshot_path is not None
        assert "verify_step_click" in result.screenshot_path


class TestEvidenceAndDuration:
    """Tests for evidence and duration properties."""

    async def test_evidence_always_populated(self, mock_act, mock_coord, logger):
        """8. Evidence string is always populated (never empty)."""
        # Test with tier 1
        step_t1 = ActionStep(
            action="activate_app",
            params={"app_name": "Calculator"},
            verify="Calculator is the frontmost application",
        )
        verifier = StepVerifier(actuator=mock_act, logger=logger)
        r1 = await verifier.verify(step_t1, {"success": True})
        assert r1.evidence != ""

        # Test with tier 2
        step_t2 = ActionStep(
            action="click",
            params={"x": 1, "y": 1},
            verify="Something visible",
        )
        verifier2 = StepVerifier(coordinator=mock_coord, logger=logger)
        r2 = await verifier2.verify(step_t2, {"success": True})
        assert r2.evidence != ""

        # Test with no verify
        step_none = ActionStep(action="done", params={}, verify="")
        verifier3 = StepVerifier(logger=logger)
        r3 = await verifier3.verify(step_none, {"success": True, "output": "ok"})
        assert r3.evidence != ""

        # Test with no backends
        step_no_backend = ActionStep(
            action="click", params={}, verify="Something"
        )
        verifier4 = StepVerifier(logger=logger)
        r4 = await verifier4.verify(step_no_backend, {"success": True})
        assert r4.evidence != ""

    async def test_duration_measured(self, mock_coord, logger):
        """9. Duration is measured (>= 0)."""
        step = ActionStep(
            action="click",
            params={"x": 100, "y": 200},
            verify="Element visible",
        )
        verifier = StepVerifier(coordinator=mock_coord, logger=logger)

        result = await verifier.verify(step, {"success": True, "output": ""})

        assert result.duration_ms >= 0


class TestStandalone:
    """Tests for standalone usage (outside orchestrator)."""

    async def test_works_standalone(self, tmp_log_dir):
        """10. Can be instantiated and called directly without orchestrator."""
        act = MagicMock()
        act.get_state = MagicMock(
            return_value={
                "app_name": "TextEdit",
                "window_title": "Untitled",
            }
        )
        coord = AsyncMock()
        coord.verify_condition = AsyncMock(return_value=True)
        coord.capture_screenshot = AsyncMock(
            return_value=base64.b64encode(b"img").decode()
        )

        logger = EventLogger(tmp_log_dir)
        verifier = StepVerifier(actuator=act, coordinator=coord, logger=logger)

        step = ActionStep(
            action="type_text",
            params={"text": "Hello"},
            verify="Text field shows Hello",
        )

        result = await verifier.verify(step, {"success": True, "output": ""})

        assert isinstance(result, StepResult)
        assert result.evidence != ""
        assert result.verification_method in ("actuator_state", "vision", "")


class TestVerifierRejectsEmptyVerify:
    """Tests that verifier rejects empty verify on non-terminal actions."""

    async def test_verifier_rejects_empty_verify(self, logger):
        """BUG 5: Verifier returns error StepResult when step.verify is empty for non-terminal action."""
        step = ActionStep(
            action="click",
            params={"x": 100, "y": 200},
            verify="",  # Empty verify on a click action
        )
        verifier = StepVerifier(logger=logger)

        result = await verifier.verify(step, {"success": True, "output": ""})

        assert result.success is False
        assert result.error == "empty_verify"
        assert "no verify condition" in result.evidence.lower()

    async def test_verifier_allows_empty_verify_for_done(self, logger):
        """done steps are allowed to have empty verify."""
        step = ActionStep(action="done", params={}, verify="")
        verifier = StepVerifier(logger=logger)

        result = await verifier.verify(step, {"success": True, "output": "done"})

        assert result.success is True

    async def test_verifier_allows_empty_verify_for_wait_for_user(self, logger):
        """wait_for_user steps are allowed to have empty verify."""
        step = ActionStep(action="wait_for_user", params={"message": "Check"}, verify="")
        verifier = StepVerifier(logger=logger)

        result = await verifier.verify(step, {"success": True, "output": ""})

        assert result.success is True


class TestTier1ImprovedAppDetection:
    """Tests for improved Tier 1 verification that handles activate_app actions
    regardless of verify text phrasing."""

    async def test_activate_app_tier1_resolves_without_frontmost_keyword(
        self, mock_act, logger
    ):
        """Tier 1 resolves activate_app even when verify says 'is open' instead of 'frontmost'."""
        step = ActionStep(
            action="activate_app",
            params={"app_name": "Calculator"},
            verify="Calculator is open and ready",
        )
        verifier = StepVerifier(actuator=mock_act, logger=logger)

        result = await verifier.verify(step, {"success": True, "output": ""})

        assert result.success is True
        assert result.verification_method == "actuator_state"

    async def test_activate_app_tier1_resolves_generic_verify(
        self, mock_act, logger
    ):
        """Tier 1 resolves activate_app even with generic verify text."""
        step = ActionStep(
            action="activate_app",
            params={"app_name": "Calculator"},
            verify="Calculator should be visible on screen",
        )
        verifier = StepVerifier(actuator=mock_act, logger=logger)

        result = await verifier.verify(step, {"success": True, "output": ""})

        # The activate_app action with app_name triggers Tier 1 regardless of verify text
        assert result.success is True
        assert result.verification_method == "actuator_state"

    async def test_tier1_is_open_keyword_matches(self, mock_act, logger):
        """Tier 1 matches 'is open' keyword in verify text."""
        step = ActionStep(
            action="click",
            params={"app_name": "Safari"},
            verify="Safari is open in foreground",
        )
        verifier = StepVerifier(actuator=mock_act, logger=logger)
        mock_act.get_state.return_value = {
            "app_name": "Safari",
            "app_bundle": "com.apple.Safari",
        }

        result = await verifier.verify(step, {"success": True, "output": ""})

        assert result.success is True
        assert result.verification_method == "actuator_state"

    async def test_activate_app_tier1_inconclusive_when_actuator_down(
        self, mock_coord, logger
    ):
        """Tier 1 is inconclusive when actuator returns empty state (not running)."""
        mock_act = MagicMock()
        mock_act.get_state.return_value = {
            "app_name": "",
            "app_bundle": "",
            "window_title": "",
        }
        step = ActionStep(
            action="activate_app",
            params={"app_name": "Safari"},
            verify="Safari is the frontmost application",
        )
        verifier = StepVerifier(actuator=mock_act, coordinator=mock_coord, logger=logger)

        result = await verifier.verify(step, {"success": True, "output": ""})

        # Should escalate to Tier 2 (not falsely deny)
        assert result.verification_method == "vision"


# ---------------------------------------------------------------------------
# P2-3: Domain verification tests
# ---------------------------------------------------------------------------


class TestDomainVerification:
    """Tests for domain constraint checking in Tier 1 verification."""

    def test_extract_base_domain_simple(self):
        """Basic URL -> base domain extraction."""
        result = StepVerifier._extract_base_domain(
            "https://www.target.com/s?searchTerm=sheets"
        )
        assert result == "target.com"

    def test_extract_base_domain_no_www(self):
        """URL without www prefix."""
        result = StepVerifier._extract_base_domain("https://target.com/path")
        assert result == "target.com"

    def test_extract_base_domain_subdomain(self):
        """URL with subdomain."""
        result = StepVerifier._extract_base_domain(
            "https://shop.target.com/cart"
        )
        assert result == "shop.target.com"

    @pytest.mark.asyncio
    async def test_domain_match_target(self, mock_act, mock_coord, logger):
        """Verify text has domain constraint, browser URL matches -> pass."""
        mock_act.get_state.return_value = {
            "app_name": "Safari",
            "window_title": "Target",
            "browser_url": "https://www.target.com/s?searchTerm=sheets",
        }
        step = ActionStep(
            action="open_url",
            params={"url": "https://www.target.com/s?searchTerm=sheets"},
            verify="Target search results visible AND browser domain is target.com",
        )
        verifier = StepVerifier(
            actuator=mock_act, coordinator=mock_coord, logger=logger
        )
        result = verifier._verify_tier1(step, mock_act)
        # Should pass (URL matches + domain matches)
        assert result is not None
        assert result[0] is True

    @pytest.mark.asyncio
    async def test_domain_mismatch_amazon(self, mock_act, mock_coord, logger):
        """Verify text says target.com, browser URL is amazon.com -> fail."""
        mock_act.get_state.return_value = {
            "app_name": "Safari",
            "window_title": "Amazon",
            "browser_url": "https://www.amazon.com/s?k=sheets",
        }
        step = ActionStep(
            action="open_url",
            params={"url": "https://www.target.com/s?searchTerm=sheets"},
            verify=(
                "Target search results visible"
                " AND browser domain is target.com"
            ),
        )
        verifier = StepVerifier(
            actuator=mock_act, coordinator=mock_coord, logger=logger
        )
        result = verifier._verify_tier1(step, mock_act)
        # Should fail because domain mismatch
        assert result is not None
        assert result[0] is False
        assert "target.com" in result[1]

    @pytest.mark.asyncio
    async def test_domain_subdomain_match(self, mock_act, mock_coord, logger):
        """shop.target.com should match domain constraint target.com."""
        mock_act.get_state.return_value = {
            "app_name": "Safari",
            "window_title": "Target",
            "browser_url": "https://shop.target.com/cart",
        }
        step = ActionStep(
            action="open_url",
            params={"url": "https://www.target.com/s?searchTerm=sheets"},
            verify="Target page AND browser domain is target.com",
        )
        verifier = StepVerifier(
            actuator=mock_act, coordinator=mock_coord, logger=logger
        )
        result = verifier._verify_tier1(step, mock_act)
        assert result is not None
        assert result[0] is True

    @pytest.mark.asyncio
    async def test_domain_nottarget_rejected(
        self, mock_act, mock_coord, logger
    ):
        """nottarget.com should NOT match target.com."""
        mock_act.get_state.return_value = {
            "app_name": "Safari",
            "window_title": "NotTarget",
            "browser_url": "https://www.nottarget.com/page",
        }
        step = ActionStep(
            action="open_url",
            params={"url": "https://www.target.com/s?searchTerm=sheets"},
            verify="page visible AND browser domain is target.com",
        )
        verifier = StepVerifier(
            actuator=mock_act, coordinator=mock_coord, logger=logger
        )
        result = verifier._verify_tier1(step, mock_act)
        assert result is not None
        assert result[0] is False

    @pytest.mark.asyncio
    async def test_domain_no_constraint(self, mock_act, mock_coord, logger):
        """No 'browser domain is' in verify -> no domain check at all."""
        mock_act.get_state.return_value = {
            "app_name": "Safari",
            "window_title": "Target",
            "browser_url": "https://www.target.com/s?searchTerm=sheets",
        }
        step = ActionStep(
            action="open_url",
            params={"url": "https://www.target.com/s?searchTerm=sheets"},
            verify="Target search results visible",
        )
        verifier = StepVerifier(
            actuator=mock_act, coordinator=mock_coord, logger=logger
        )
        result = verifier._verify_tier1(step, mock_act)
        # Should pass via normal URL token match, no domain-specific check
        assert result is not None
        assert result[0] is True


class TestInjectDomainVerification:
    """Tests for _inject_domain_verification on the agent."""

    def _make_agent(self):
        from automation_agent.orchestrator.agent import AutomationAgent
        from automation_agent.config import AgentConfig

        config = AgentConfig(
            _env_file=None,
            anthropic_api_key="test-key-not-real",
            model_provider="local",
            grounding_model="",
            grounding_server_url="",
        )
        planner = AsyncMock()
        skill_registry = MagicMock()
        skill_registry.match = AsyncMock(return_value=None)
        skill_registry.learn_from_run = AsyncMock(return_value=[])
        skill_registry.promote_from_run = AsyncMock(return_value=None)
        coordinator = AsyncMock()
        coordinator.capabilities = MagicMock(return_value=frozenset())
        coordinator.capture_screenshot = AsyncMock(return_value="base64data")
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={})
        return AutomationAgent(
            planner, skill_registry, coordinator, actuator, config
        )

    def test_inject_domain_verification(self):
        """open_url step gets domain constraint appended to verify."""
        agent = self._make_agent()
        step = ActionStep(
            action="open_url",
            params={"url": "https://www.target.com/s?q=sheets"},
            verify="Target search page visible",
        )
        plan = ActionPlan(steps=[step])
        agent._inject_domain_verification(plan, "target.com")
        assert "browser domain is target.com" in step.verify

    def test_inject_domain_idempotent(self):
        """Calling twice should not add duplicate domain clauses."""
        agent = self._make_agent()
        step = ActionStep(
            action="open_url",
            params={"url": "https://www.target.com/s?q=sheets"},
            verify="Target search page visible",
        )
        plan = ActionPlan(steps=[step])
        agent._inject_domain_verification(plan, "target.com")
        agent._inject_domain_verification(plan, "target.com")
        # Count occurrences
        count = step.verify.count("browser domain is target.com")
        assert count == 1

    def test_inject_domain_skips_non_open_url(self):
        """Non-open_url steps should not get domain verification."""
        agent = self._make_agent()
        step = ActionStep(
            action="click",
            params={"element": "search button"},
            verify="Results visible",
        )
        plan = ActionPlan(steps=[step])
        agent._inject_domain_verification(plan, "target.com")
        assert "browser domain is" not in step.verify

    def test_inject_domain_type_safety(self):
        """Domain should be a string like 'target.com', not list repr."""
        agent = self._make_agent()
        step = ActionStep(
            action="open_url",
            params={"url": "https://www.target.com/s?q=sheets"},
            verify="Target search page visible",
        )
        plan = ActionPlan(steps=[step])
        # Ensure we pass a proper string, not list
        agent._inject_domain_verification(plan, "target.com")
        assert "['target'].com" not in step.verify
        assert "target.com" in step.verify

    def test_inject_domain_skips_non_matching_url(self):
        """PB7: open_url to a different domain should NOT get domain constraint."""
        agent = self._make_agent()
        target_step = ActionStep(
            action="open_url",
            params={"url": "https://www.target.com"},
            verify="Target homepage visible",
        )
        other_step = ActionStep(
            action="open_url",
            params={"url": "https://www.google.com/search?q=target"},
            verify="Google results visible",
        )
        plan = ActionPlan(steps=[target_step, other_step])
        agent._inject_domain_verification(plan, "target.com")
        assert "browser domain is target.com" in target_step.verify
        assert "browser domain is" not in other_step.verify

    def test_inject_domain_no_url_param_skips_injection(self):
        """open_url with no url param (empty) should skip domain injection."""
        agent = self._make_agent()
        step = ActionStep(
            action="open_url",
            params={},
            verify="Page visible",
        )
        plan = ActionPlan(steps=[step])
        agent._inject_domain_verification(plan, "target.com")
        # Empty URL = no domain to verify, skip injection
        assert "browser domain is target.com" not in step.verify
