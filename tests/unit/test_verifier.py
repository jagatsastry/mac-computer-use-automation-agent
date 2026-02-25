"""Unit tests for the StepVerifier component.

All components (actuator, coordinator) are mocked -- no real API calls.
"""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.logging.event_logger import EventLogger
from automation_agent.logging.models import EventType
from automation_agent.orchestrator.verifier import StepVerifier
from automation_agent.shared_models import ActionStep, StepResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
    coord.capture_screenshot = AsyncMock(
        return_value=base64.b64encode(b"fake_screenshot_data").decode()
    )
    return coord


@pytest.fixture
def logger(tmp_log_dir):
    """Real EventLogger writing to a temp directory."""
    return EventLogger(tmp_log_dir)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTier1Verification:
    """Tests for Tier 1 (Hammerspoon state) verification."""

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
        assert result.verification_method == "hammerspoon_state"
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
        assert result.verification_method == "hammerspoon_state"
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
        mock_coord.verify_condition.assert_awaited_once_with("Button appears pressed")

        # Check that escalation was logged
        event_types = [e.event_type for e in logger.events]
        assert EventType.VERIFY_ESCALATE in event_types


class TestTier2Verification:
    """Tests for Tier 2 (Vision) verification."""

    async def test_tier2_confirms(self, mock_coord, logger):
        """4. Tier 2 confirms condition via vision."""
        step = ActionStep(
            action="click",
            params={"x": 100, "y": 200},
            verify="Submit button visible",
        )
        # No actuator -- goes straight to tier 2
        verifier = StepVerifier(coordinator=mock_coord, logger=logger)

        result = await verifier.verify(step, {"success": True, "output": ""})

        assert result.success is True
        assert result.verification_method == "vision"
        assert "Vision confirms" in result.evidence

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
        assert result.verification_method in ("hammerspoon_state", "vision", "")
