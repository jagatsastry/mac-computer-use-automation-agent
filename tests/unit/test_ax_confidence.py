"""Tests for AX confidence calibration (Speed Phase 1, Change 2).

Validates:
- _compute_match_score() returns correct scores with/without accessibility
- _ground_accessibility_match() applies formula: confidence = 0.6 + 0.35 * min(score, 1.0)
- find_element() propagates calibrated confidence
- agent.py skip_validation gate uses confidence, not source, for accessibility results
"""

import base64
import io
from unittest.mock import AsyncMock, MagicMock

import pytest

from automation_agent.config import AgentConfig
from automation_agent.orchestrator.grounding_router import (
    GroundingResult,
    GroundingRouter,
    GroundingStrategy,
)
from automation_agent.perception.accessibility import AXElement
from automation_agent.shared_models import FindElementResult


def _make_config(**overrides):
    defaults = dict(model_provider="local")
    defaults.update(overrides)
    return AgentConfig(**defaults)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ax_element(
    role="AXButton",
    title="Submit",
    position=(100, 200),
    size=(80, 30),
    value=None,
    description=None,
    enabled=True,
    focused=False,
):
    """Create an AXElement with sensible defaults."""
    return AXElement(
        role=role,
        title=title,
        position=position,
        size=size,
        value=value,
        description=description,
        enabled=enabled,
        focused=focused,
    )


def _make_scoring_mock(score_value):
    """Create a MagicMock accessibility backend with scoring support.

    Sets _extract_match_target and _element_match_score as explicit attributes
    so the MagicMock detection pattern in _compute_match_score() finds them.
    """
    ax = MagicMock()
    ax._extract_match_target = MagicMock(return_value=("AXButton", "submit"))
    ax._element_match_score = MagicMock(return_value=score_value)
    ax.find_element_by_description = MagicMock(return_value=_make_ax_element())
    return ax


def _make_jpeg_b64(width: int = 100, height: int = 100, color=(128, 128, 128)) -> str:
    """Return a minimal valid JPEG as base64."""
    from PIL import Image

    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def _make_narrow_jpeg_b64(width: int = 1024, height: int = 768) -> str:
    """Return a normal-res JPEG (at or below 1440px threshold)."""
    return _make_jpeg_b64(width=width, height=height)


def _make_agent(tmp_log_dir, actuator=None, coordinator=None):
    """Construct a minimal AutomationAgent with fully mocked dependencies."""
    from automation_agent.orchestrator.agent import AutomationAgent
    from automation_agent.shared_models import ActionPlan, ActionStep

    config = AgentConfig(
        vision_model="molmo",
        log_dir=str(tmp_log_dir),
        max_iterations=10,
        action_delay=0.0,
    )

    if actuator is None:
        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(
            return_value={"app_name": "Safari", "window_title": "Test"}
        )

    if coordinator is None:
        coordinator = AsyncMock()
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(x=400, y=300, confidence=0.8, source="vision")
        )
        coordinator.describe_screen = AsyncMock(return_value="Test screen")
        coordinator.verify_condition = AsyncMock(return_value=True)
        coordinator.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())

    planner = AsyncMock()
    planner.plan = AsyncMock(
        return_value=ActionPlan(
            steps=[ActionStep(action="done", params={}, verify="")],
            goal="test",
        )
    )
    planner.replan = AsyncMock(
        return_value=ActionPlan(
            steps=[ActionStep(action="done", params={}, verify="")],
            goal="test",
        )
    )

    skill_registry = MagicMock()
    skill_registry.match = AsyncMock(return_value=None)
    skill_registry.list_skills = MagicMock(return_value=[])
    skill_registry.expand = MagicMock(return_value=None)

    return AutomationAgent(
        planner=planner,
        skill_registry=skill_registry,
        coordinator=coordinator,
        actuator=actuator,
        config=config,
    )


# ---------------------------------------------------------------------------
# Test 1-5: Confidence calibration formula
# ---------------------------------------------------------------------------


class TestConfidenceCalibration:
    """Tests for the confidence formula: 0.6 + 0.35 * min(score, 1.0)."""

    @pytest.mark.unit
    def test_exact_match_confidence(self):
        """score=1.0 -> confidence=0.95 (AC-7, AC-9)."""
        router = GroundingRouter()
        elem = _make_ax_element()
        result = router._ground_accessibility_match(elem, match_score=1.0)
        assert result is not None
        assert result.confidence == pytest.approx(0.95)

    @pytest.mark.unit
    def test_substring_match_confidence(self):
        """score=0.9 -> confidence=0.915 (AC-7, AC-9)."""
        router = GroundingRouter()
        elem = _make_ax_element()
        result = router._ground_accessibility_match(elem, match_score=0.9)
        assert result is not None
        assert result.confidence == pytest.approx(0.915)

    @pytest.mark.unit
    def test_token_overlap_confidence(self):
        """score=0.7 -> confidence=0.845 (AC-7, AC-9)."""
        router = GroundingRouter()
        elem = _make_ax_element()
        result = router._ground_accessibility_match(elem, match_score=0.7)
        assert result is not None
        assert result.confidence == pytest.approx(0.845)

    @pytest.mark.unit
    def test_zero_score_confidence(self):
        """score=0.0 -> confidence=0.6 (AC-9)."""
        router = GroundingRouter()
        elem = _make_ax_element()
        result = router._ground_accessibility_match(elem, match_score=0.0)
        assert result is not None
        assert result.confidence == pytest.approx(0.6)

    @pytest.mark.unit
    def test_capped_score_confidence(self):
        """score=1.07 (focused+enabled bonus) -> capped to 1.0 -> confidence=0.95 (AC-9)."""
        router = GroundingRouter()
        elem = _make_ax_element()
        result = router._ground_accessibility_match(elem, match_score=1.07)
        assert result is not None
        assert result.confidence == pytest.approx(0.95)


# ---------------------------------------------------------------------------
# Test 6-8: _compute_match_score
# ---------------------------------------------------------------------------


class TestComputeMatchScore:
    """Tests for _compute_match_score() fallback behavior."""

    @pytest.mark.unit
    def test_compute_match_score_no_accessibility(self):
        """No accessibility backend -> returns 1.0 (AC-7)."""
        router = GroundingRouter(accessibility=None)
        score = router._compute_match_score("Submit button", _make_ax_element())
        assert score == 1.0

    @pytest.mark.unit
    def test_compute_match_score_mock_detection(self):
        """MagicMock accessibility with explicit score_fn works (AC-7)."""
        ax = _make_scoring_mock(0.7)
        router = GroundingRouter(accessibility=ax)
        elem = _make_ax_element()
        score = router._compute_match_score("Submit button", elem)
        assert score == 0.7
        ax._extract_match_target.assert_called_once_with("Submit button")
        ax._element_match_score.assert_called_once_with(elem, "submit")

    @pytest.mark.unit
    def test_compute_match_score_no_score_fn_returns_1(self):
        """MagicMock without score_fn returns 1.0 fallback."""
        ax = MagicMock()
        # Don't set _extract_match_target or _element_match_score
        router = GroundingRouter(accessibility=ax)
        score = router._compute_match_score("Submit button", _make_ax_element())
        assert score == 1.0

    @pytest.mark.unit
    def test_compute_match_score_exception_returns_1(self):
        """Exception in score computation returns 1.0 fallback."""
        ax = _make_scoring_mock(0.7)
        ax._extract_match_target.side_effect = RuntimeError("boom")
        router = GroundingRouter(accessibility=ax)
        score = router._compute_match_score("Submit button", _make_ax_element())
        assert score == 1.0


# ---------------------------------------------------------------------------
# Test: find_element uses calibrated confidence
# ---------------------------------------------------------------------------


class TestFindElementCalibrated:
    """Tests for find_element() passing calibrated confidence through."""

    @pytest.mark.unit
    async def test_find_element_uses_calibrated_confidence(self):
        """find_element() returns varying confidence based on match quality (AC-8)."""
        elem = _make_ax_element(title="Submit")
        ax = MagicMock()
        ax._extract_match_target = MagicMock(return_value=("AXButton", "submit"))
        ax._element_match_score = MagicMock(return_value=0.7)
        ax.find_elements = MagicMock(return_value=[elem])
        router = GroundingRouter(accessibility=ax)

        result = await router.find_element("Submit button")

        assert result is not None
        assert result.strategy_used == GroundingStrategy.ACCESSIBILITY
        # score=0.7 -> confidence = 0.6 + 0.35 * 0.7 = 0.845
        assert result.confidence == pytest.approx(0.845)

    @pytest.mark.unit
    async def test_find_element_exact_match_high_confidence(self):
        """find_element() returns 0.95 for exact match."""
        elem = _make_ax_element(title="Submit")
        ax = MagicMock()
        ax._extract_match_target = MagicMock(return_value=("AXButton", "submit"))
        ax._element_match_score = MagicMock(return_value=1.0)
        ax.find_elements = MagicMock(return_value=[elem])
        router = GroundingRouter(accessibility=ax)

        result = await router.find_element("Submit button")

        assert result is not None
        assert result.confidence == pytest.approx(0.95)

    @pytest.mark.unit
    async def test_ground_accessibility_uses_calibrated_confidence(self):
        """_ground_accessibility() passes computed score through."""
        elem = _make_ax_element(title="Submit")
        ax = MagicMock()
        ax._extract_match_target = MagicMock(return_value=("AXButton", "submit"))
        ax._element_match_score = MagicMock(return_value=0.5)
        ax.find_element_by_description = MagicMock(return_value=elem)
        router = GroundingRouter(accessibility=ax)

        result = await router._ground_accessibility("Submit button")

        assert result is not None
        # score=0.5 -> confidence = 0.6 + 0.35 * 0.5 = 0.775
        assert result.confidence == pytest.approx(0.775)


# ---------------------------------------------------------------------------
# Test 9: agent.py skip_validation gate
# ---------------------------------------------------------------------------


class TestAgentSkipValidation:
    """Test that agent.py skip_validation uses confidence, not source, for AX."""

    @pytest.mark.asyncio
    async def test_agent_skip_validation_uses_confidence_not_source(self, tmp_path):
        """AX result with confidence=0.85 goes through _validate_candidate(),
        while confidence=0.95 skips it.

        Uses non-critical element/verify text to avoid the elevated 0.9
        confidence gate (which blocks before reaching skip_validation).
        """
        from automation_agent.shared_models import ActionStep

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # --- Case 1: partial AX match (confidence=0.85 < 0.9) triggers validation ---
        coordinator = AsyncMock()
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(
                x=100, y=100, confidence=0.85, source="accessibility"
            )
        )
        coordinator.describe_screen = AsyncMock(return_value="screen")
        coordinator.verify_condition = AsyncMock(return_value=True)
        coordinator.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())

        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(
            return_value={"app_name": "X", "window_title": "Y"}
        )

        agent = _make_agent(log_dir, actuator=actuator, coordinator=coordinator)
        agent._validate_candidate = AsyncMock(return_value=True)

        # Use non-critical element/verify to stay below 0.5 default threshold
        step = ActionStep(
            action="click",
            params={"element": "Next button"},
            verify="Next page shown",
        )

        await agent._dispatch_action(step)
        agent._validate_candidate.assert_awaited_once()

        # --- Case 2: exact AX match (confidence=0.95 >= 0.9) skips validation ---
        log_dir2 = tmp_path / "logs2"
        log_dir2.mkdir()

        coordinator2 = AsyncMock()
        coordinator2.find_element = AsyncMock(
            return_value=FindElementResult(
                x=100, y=100, confidence=0.95, source="accessibility"
            )
        )
        coordinator2.describe_screen = AsyncMock(return_value="screen")
        coordinator2.verify_condition = AsyncMock(return_value=True)
        coordinator2.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())

        actuator2 = MagicMock()
        actuator2.click = MagicMock(return_value={"success": True})
        actuator2.get_state = MagicMock(
            return_value={"app_name": "X", "window_title": "Y"}
        )

        agent2 = _make_agent(log_dir2, actuator=actuator2, coordinator=coordinator2)
        agent2._validate_candidate = AsyncMock(return_value=True)

        step2 = ActionStep(
            action="click",
            params={"element": "Next button"},
            verify="Next page shown",
        )

        await agent2._dispatch_action(step2)
        agent2._validate_candidate.assert_not_awaited()


# ---------------------------------------------------------------------------
# Backward compatibility: default match_score=1.0 preserves 0.95
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    """Verify that default match_score=1.0 preserves existing behavior."""

    @pytest.mark.unit
    def test_default_match_score_gives_095(self):
        """Callers that don't pass match_score get confidence=0.95."""
        router = GroundingRouter()
        elem = _make_ax_element()
        result = router._ground_accessibility_match(elem)
        assert result is not None
        assert result.confidence == pytest.approx(0.95)
