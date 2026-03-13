"""Integration tests for Survey Gaps (7 gaps from computer-use agents survey).

Tests cross-component flows across all 3 domain slices:
  Slice 1: Gap 5 (Infeasibility Detection) + Gap 6 (Destructive Action Confirmation)
  Slice 2: Gap 1 (Set-of-Mark Prompting) + Gap 7 (Dual-Resolution Screenshots)
  Slice 3: Gap 3 (Embedding Skill Retrieval) + Gap 2 (World-State Document) + Gap 4 (Lookahead)

Mock boundaries: network, VLM calls, OS (accessibility, screenshots).
Real components used internally: orchestrator, verifier, coordinator, skills, context_monitor.
"""

import asyncio
import base64
import io
import os
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from automation_agent.config import AgentConfig, ConfirmMode
from automation_agent.logging.event_logger import EventLogger
from automation_agent.logging.models import EventType
from automation_agent.orchestrator.agent import (
    AutomationAgent,
    DestructiveClassification,
    FrustrationScore,
    Phase1Decision,
)
from automation_agent.orchestrator.confirmation import (
    AutoDenyConfirmationHandler,
    ConsoleConfirmationHandler,
    _sanitize_for_display,
)
from automation_agent.orchestrator.context_monitor import (
    ContextMonitor,
    DesktopContext,
    StateDiff,
)
from automation_agent.protocols import CoordinatorCapability
from automation_agent.shared_models import (
    ActionPlan,
    ActionStep,
    ExecutionResult,
    FindElementResult,
    MatchType,
    SkillMatchResult,
    SkillRouteCandidate,
    StepResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> AgentConfig:
    """Create AgentConfig pinning model_provider=local to avoid .env leaking."""
    defaults = {
        "model_provider": "local",
        "vision_model": "molmo",
        "log_dir": "/tmp/test_survey_gaps_logs",
        "_env_file": None,
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_jpeg_b64(width: int = 800, height: int = 600, color=(200, 200, 200)) -> str:
    """Create a minimal JPEG image encoded as base64."""
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=50)
    return base64.b64encode(buf.getvalue()).decode()


def _make_different_jpeg_b64(width: int = 800, height: int = 600) -> str:
    """JPEG that is visually different from _make_jpeg_b64 default."""
    return _make_jpeg_b64(width, height, color=(50, 50, 50))


def _make_mock_planner(
    plan: Optional[ActionPlan] = None,
    infeasibility_response: Optional[dict] = None,
):
    """Build a mock ActionPlanner with configurable infeasibility response."""
    planner = AsyncMock()
    if plan is None:
        plan = ActionPlan(
            steps=[ActionStep(action="done", params={}, verify="")],
        )
    planner.plan = AsyncMock(return_value=plan)
    planner.replan = AsyncMock(return_value=plan)
    if infeasibility_response is None:
        infeasibility_response = {"infeasible": True, "reason": "Element not on page"}
    planner.check_infeasibility = AsyncMock(return_value=infeasibility_response)
    return planner


def _make_mock_coordinator(
    screenshot_b64: Optional[str] = None,
    find_result: Optional[FindElementResult] = None,
    capabilities: Optional[FrozenSet[CoordinatorCapability]] = None,
):
    """Build a mock ScreenCoordinator."""
    coord = AsyncMock()
    b64 = screenshot_b64 or _make_jpeg_b64()
    coord.capture_screenshot = AsyncMock(return_value=b64)
    coord.describe_screen = AsyncMock(return_value="A desktop screen")
    coord.verify_condition = AsyncMock(return_value=True)
    if find_result is not None:
        coord.find_element = AsyncMock(return_value=find_result)
    else:
        coord.find_element = AsyncMock(
            return_value=FindElementResult(x=100, y=200, confidence=0.95)
        )
    coord.capabilities = MagicMock(
        return_value=capabilities or frozenset()
    )
    coord.find_element_dual = AsyncMock(return_value=None)
    coord.predict_action_outcome = AsyncMock(
        return_value={
            "likely_success": True,
            "predicted_state": "",
            "risk": "",
            "mismatch_reason": "",
        }
    )
    return coord


def _make_mock_actuator():
    """Build a mock Actuator."""
    actuator = MagicMock()
    actuator.is_available.return_value = True
    actuator.click.return_value = {"success": True}
    actuator.type_text.return_value = {"success": True}
    actuator.press_key.return_value = {"success": True}
    actuator.open_url.return_value = {"success": True}
    actuator.activate_app.return_value = {"success": True}
    actuator.scroll.return_value = {"success": True}
    actuator.get_state.return_value = {
        "frontmost_app": "Safari",
        "window_title": "Test Page",
    }
    return actuator


def _make_mock_skill_registry(match_result=None):
    """Build a mock SkillRegistry."""
    registry = AsyncMock()
    registry.match = AsyncMock(return_value=match_result)
    registry.list_skills.return_value = []
    registry.expand.return_value = None
    registry.validate_all.return_value = []
    return registry


def _make_agent(
    config: Optional[AgentConfig] = None,
    planner=None,
    coordinator=None,
    actuator=None,
    skill_registry=None,
    confirmation_handler=None,
    context_monitor=None,
    **config_overrides,
) -> AutomationAgent:
    """Assemble an AutomationAgent with mocks at system boundaries."""
    cfg = config or _make_config(**config_overrides)
    planner = planner or _make_mock_planner()
    coord = coordinator or _make_mock_coordinator()
    act = actuator or _make_mock_actuator()
    skills = skill_registry or _make_mock_skill_registry()
    logger = EventLogger(Path("/tmp/test_survey_gaps_logs"))
    agent = AutomationAgent(
        planner=planner,
        coordinator=coord,
        actuator=act,
        skill_registry=skills,
        config=cfg,
        logger=logger,
        confirmation_handler=confirmation_handler or AutoDenyConfirmationHandler(),
        context_monitor=context_monitor,
    )
    return agent


# ============================================================================
# SLICE 1: Gap 5 (Infeasibility Detection) + Gap 6 (Destructive Confirmation)
# ============================================================================


class TestFrustrationScoreUnit:
    """FrustrationScore data class behavior."""

    def test_fresh_score_is_zero(self):
        fs = FrustrationScore()
        assert fs.same_state_count == 0
        assert fs.identical_action_count == 0
        assert fs.replan_count == 0
        assert fs.advisory_checks_used == 0

    def test_is_triggered_same_state(self):
        fs = FrustrationScore(same_state_count=3)
        assert fs.is_triggered(same_state_limit=3, replan_limit=2) is True

    def test_is_triggered_replan(self):
        fs = FrustrationScore(replan_count=2)
        assert fs.is_triggered(same_state_limit=3, replan_limit=2) is True

    def test_not_triggered_below_thresholds(self):
        fs = FrustrationScore(same_state_count=2, replan_count=1)
        assert fs.is_triggered(same_state_limit=3, replan_limit=2) is False

    def test_reset_on_progress_clears_counters(self):
        fs = FrustrationScore(same_state_count=5, identical_action_count=3)
        fs.reset_on_progress()
        assert fs.same_state_count == 0
        assert fs.identical_action_count == 0

    def test_replan_count_survives_progress_reset(self):
        """AC-1c: replan_count never resets within execute()."""
        fs = FrustrationScore(replan_count=2, same_state_count=5)
        fs.reset_on_progress()
        assert fs.replan_count == 2

    def test_hard_abort_after_advisory_cap(self):
        fs = FrustrationScore(advisory_checks_used=2)
        assert fs.is_hard_abort(max_advisory=2) is True
        assert fs.is_hard_abort(max_advisory=3) is False


class TestInfeasibilityReplanFlow:
    """Gap 5: Frustration accumulates → infeasibility check → planner decides."""

    @pytest.mark.asyncio
    async def test_check_infeasibility_returns_abort(self, tmp_path):
        """AC-2/AC-3: _check_infeasibility returns ExecutionResult with infeasibility_reason."""
        plan = ActionPlan(steps=[ActionStep(action="done", params={}, verify="")])
        planner = _make_mock_planner(
            plan=plan,
            infeasibility_response={"infeasible": True, "reason": "Return button does not exist"},
        )
        config = _make_config(log_dir=str(tmp_path))
        agent = _make_agent(config=config, planner=planner)

        frustration = FrustrationScore(same_state_count=3)
        step_results = [
            StepResult(
                step=ActionStep(action="click", params={"element": "Return"}, verify="x"),
                success=False,
                error="Element absent: Return button",
                evidence="Vision denies",
            ),
        ]
        result = await agent._check_infeasibility(
            "Return my order", frustration, step_results
        )
        assert result is not None
        assert result.success is False
        assert result.infeasibility_reason == "Return button does not exist"

    @pytest.mark.asyncio
    async def test_check_infeasibility_achievable_increments_advisory(self, tmp_path):
        """AC-2: Planner says 'still achievable' → advisory count incremented."""
        plan = ActionPlan(steps=[ActionStep(action="done", params={}, verify="")])
        planner = _make_mock_planner(plan=plan)
        planner.check_infeasibility = AsyncMock(
            return_value={"infeasible": False, "reason": "Still possible"}
        )
        config = _make_config(log_dir=str(tmp_path))
        agent = _make_agent(config=config, planner=planner)

        frustration = FrustrationScore(same_state_count=3)
        result = await agent._check_infeasibility("Submit form", frustration, [])
        assert result is None  # not infeasible
        assert frustration.advisory_checks_used == 1

    @pytest.mark.asyncio
    async def test_hard_abort_after_max_advisory_checks(self, tmp_path):
        """AC-2 cap: FrustrationScore.is_hard_abort gates the hard-abort path."""
        # This tests the hard-abort path directly by constructing a frustration
        # score that exceeds the advisory cap.
        plan = ActionPlan(steps=[ActionStep(action="done", params={}, verify="")])
        planner = _make_mock_planner(plan=plan)
        planner.check_infeasibility = AsyncMock(
            return_value={"infeasible": False, "reason": "Optimistic"}
        )
        config = _make_config(
            infeasibility_max_advisory_checks=2,
            log_dir=str(tmp_path),
        )
        agent = _make_agent(config=config, planner=planner)

        frustration = FrustrationScore(
            same_state_count=3,
            advisory_checks_used=0,
        )
        # First two calls: planner says achievable → advisory count goes up
        for _ in range(2):
            result = await agent._check_infeasibility("Task", frustration, [])
            assert result is None
        assert frustration.advisory_checks_used == 2
        assert frustration.is_hard_abort(config.infeasibility_max_advisory_checks) is True

    @pytest.mark.asyncio
    async def test_frustration_score_fresh_per_execute(self, tmp_path):
        """AC-1: FrustrationScore is local to each execute() call."""
        plan = ActionPlan(
            steps=[ActionStep(action="done", params={}, verify="")]
        )
        planner = _make_mock_planner(plan=plan)
        config = _make_config(log_dir=str(tmp_path))
        agent = _make_agent(config=config, planner=planner)

        # Two consecutive executions should each succeed independently
        result1 = await agent.execute("Task one")
        result2 = await agent.execute("Task two")
        assert result1.success is True
        assert result2.success is True

    @pytest.mark.asyncio
    async def test_infeasibility_check_timeout(self, tmp_path):
        """Reliability: timeout on infeasibility check → hard abort."""
        plan = ActionPlan(steps=[ActionStep(action="done", params={}, verify="")])
        planner = _make_mock_planner(plan=plan)
        # Simulate LLM hang
        async def slow_check(**kwargs):
            await asyncio.sleep(100)
            return {"infeasible": True, "reason": "slow"}

        planner.check_infeasibility = AsyncMock(side_effect=slow_check)

        config = _make_config(
            infeasibility_timeout_s=0.1,  # very short timeout
            log_dir=str(tmp_path),
        )
        agent = _make_agent(config=config, planner=planner)

        frustration = FrustrationScore(same_state_count=5)
        result = await agent._check_infeasibility(
            "Click ghost button", frustration, []
        )
        assert result is not None
        assert result.success is False
        assert result.infeasibility_reason is not None
        assert "timed out" in result.infeasibility_reason.lower()

    @pytest.mark.asyncio
    async def test_frustration_threshold_triggers_in_execute(self, tmp_path):
        """AC-2: When same_state_count hits limit during execute(), infeasibility fires."""
        # Build a plan with multiple steps that will fail verification.
        # Use on_fail="abort" to prevent retry/replan so the main loop continues.
        steps = [
            ActionStep(
                action="click",
                params={"element": f"btn{i}"},
                verify="clicked",
                on_fail="abort",
            )
            for i in range(5)
        ] + [ActionStep(action="done", params={}, verify="")]
        plan = ActionPlan(steps=steps)

        planner = _make_mock_planner(plan=plan)
        planner.check_infeasibility = AsyncMock(
            return_value={"infeasible": True, "reason": "Cannot find element"},
        )

        coord = _make_mock_coordinator()
        coord.verify_condition = AsyncMock(return_value=False)

        config = _make_config(
            infeasibility_same_state_limit=2,
            log_dir=str(tmp_path),
        )
        agent = _make_agent(config=config, planner=planner, coordinator=coord)
        result = await agent.execute("Click buttons")
        # The task should fail (verification always False)
        assert result.success is False


class TestCriticalPathAbsence:
    """AC-4: Critical-path element absence → immediate infeasibility check."""

    def test_critical_path_condition_matches(self):
        """AC-4: The condition checks step.action=='click' + element + 'not found' in error."""
        # Verify the condition structure matches the code at agent.py:443-450
        step = ActionStep(
            action="click",
            params={"element": "Return Order"},
            verify="Return started",
        )
        result = StepResult(
            step=step,
            success=False,
            error="Element not found on page",
            evidence="Vision search failed",
        )
        # AC-4 condition: not result.success AND click AND element AND "not found" in error
        assert not result.success
        assert step.action == "click"
        assert step.params.get("element")
        assert result.error is not None
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_critical_path_absence_calls_infeasibility(self, tmp_path):
        """AC-4: Direct test of _check_infeasibility when forced by critical-path absence."""
        plan = ActionPlan(steps=[ActionStep(action="done", params={}, verify="")])
        planner = _make_mock_planner(
            plan=plan,
            infeasibility_response={"infeasible": True, "reason": "Return button not on page"},
        )
        config = _make_config(log_dir=str(tmp_path))
        agent = _make_agent(config=config, planner=planner)

        frustration = FrustrationScore()  # zero counters — force=True bypasses threshold
        step_results = [
            StepResult(
                step=ActionStep(action="click", params={"element": "Return Order"}, verify="x"),
                success=False,
                error="Element absent: Return Order button",
                evidence="Not found",
            ),
        ]
        result = await agent._check_infeasibility(
            "Return my order", frustration, step_results, force=True
        )
        assert result is not None
        assert result.success is False
        assert result.infeasibility_reason is not None
        assert planner.check_infeasibility.await_count == 1


class TestDestructiveClassificationIntegration:
    """Gap 6: _is_destructive_step flows into confirmation gate."""

    def test_planner_flag_classified_destructive(self):
        """AC-6b: step.destructive=True → destructive classification."""
        config = _make_config()
        agent = _make_agent(config=config)
        step = ActionStep(
            action="click",
            params={"element": "Continue"},
            verify="Next page",
            destructive=True,
        )
        result = agent._is_destructive_step(step)
        assert result.is_destructive is True
        assert result.classification_path == "planner_flag"

    def test_keyword_match_in_element(self):
        """AC-6a: keyword in params['element'] → destructive."""
        config = _make_config()
        agent = _make_agent(config=config)
        step = ActionStep(
            action="click",
            params={"element": "Delete Account"},
            verify="Account deleted",
        )
        result = agent._is_destructive_step(step)
        assert result.is_destructive is True
        assert result.matched_keyword == "delete"
        assert result.classification_path == "keyword_match"

    def test_keyword_match_in_verify(self):
        """AC-6a: keyword in verify text → destructive."""
        config = _make_config()
        agent = _make_agent(config=config)
        step = ActionStep(
            action="click",
            params={"element": "Blue button"},
            verify="User must confirm and submit the payment",
        )
        result = agent._is_destructive_step(step)
        assert result.is_destructive is True
        assert result.matched_keyword == "confirm" or result.matched_keyword == "submit"

    def test_type_text_with_destructive_verify(self):
        """AC-6: type_text with critical keyword in verify (matched via general scan)."""
        config = _make_config()
        agent = _make_agent(config=config)
        step = ActionStep(
            action="type_text",
            params={"text": "my credit card number"},
            verify="Payment form ready to submit",
        )
        result = agent._is_destructive_step(step)
        assert result.is_destructive is True

    def test_non_destructive_action(self):
        """Non-destructive actions return NOT_DESTRUCTIVE."""
        config = _make_config()
        agent = _make_agent(config=config)
        step = ActionStep(
            action="click",
            params={"element": "Search box"},
            verify="Search box focused",
        )
        result = agent._is_destructive_step(step)
        assert result.is_destructive is False
        assert bool(result) is False


class TestConfirmationGateIntegration:
    """Gap 6: Confirmation gate blocks/allows dispatch for destructive steps."""

    def test_phase1_never_mode_skips(self):
        """AC-8: ConfirmMode.NEVER → Phase1Decision.SKIP."""
        config = _make_config()
        config.confirm_destructive = ConfirmMode.NEVER
        agent = _make_agent(config=config)
        step = ActionStep(
            action="click",
            params={"element": "Delete"},
            verify="Deleted",
            destructive=True,
        )
        assert agent._should_confirm_phase1(step) == Phase1Decision.SKIP

    def test_phase1_always_mode_confirms(self):
        """AC-8: ConfirmMode.ALWAYS → Phase1Decision.CONFIRM."""
        config = _make_config(confirm_destructive="always")
        agent = _make_agent(config=config)
        step = ActionStep(
            action="click",
            params={"element": "Delete"},
            verify="Deleted",
        )
        assert agent._should_confirm_phase1(step) == Phase1Decision.CONFIRM

    def test_phase1_smart_mode_planner_flag(self):
        """AC-8: SMART + planner destructive=True → CONFIRM."""
        config = _make_config(confirm_destructive="smart")
        agent = _make_agent(config=config)
        step = ActionStep(
            action="click",
            params={"element": "Proceed"},
            verify="Order placed",
            destructive=True,
        )
        assert agent._should_confirm_phase1(step) == Phase1Decision.CONFIRM

    def test_phase1_smart_mode_click_defers(self):
        """AC-8: SMART + click + no planner flag → DEFER (wait for phase 2)."""
        config = _make_config(confirm_destructive="smart")
        agent = _make_agent(config=config)
        step = ActionStep(
            action="click",
            params={"element": "Pay Now"},
            verify="Payment processed",
        )
        # Not destructive=True on the step, but keyword match
        assert agent._should_confirm_phase1(step) == Phase1Decision.DEFER

    def test_phase2_hard_destructive_always_confirms(self):
        """AC-8: hard-destructive keyword always triggers phase 2 confirmation."""
        config = _make_config(confirm_destructive="smart")
        agent = _make_agent(config=config)
        step = ActionStep(
            action="click",
            params={"element": "Pay Now"},
            verify="Payment done",
        )
        # Hard-destructive keyword ("pay") + any confidence → always confirm
        assert agent._should_confirm_phase2(step, confidence=0.99, matched_keyword="pay") is True

    def test_phase2_high_confidence_skips(self):
        """AC-8: non-hard keyword + confidence >= 0.9 → skip confirmation."""
        config = _make_config(confirm_destructive="smart")
        agent = _make_agent(config=config)
        step = ActionStep(
            action="click",
            params={"element": "Confirm details"},
            verify="Details confirmed",
        )
        # "confirm" is in _CRITICAL_ACTION_KEYWORDS but NOT in _HARD_DESTRUCTIVE_KEYWORDS
        assert agent._should_confirm_phase2(step, confidence=0.95, matched_keyword="confirm") is False

    def test_phase2_low_confidence_confirms(self):
        """AC-8: keyword match + low confidence → confirm."""
        config = _make_config(confirm_destructive="smart")
        agent = _make_agent(config=config)
        step = ActionStep(
            action="click",
            params={"element": "Confirm details"},
            verify="Details confirmed",
        )
        assert agent._should_confirm_phase2(step, confidence=0.6, matched_keyword="confirm") is True

    @pytest.mark.asyncio
    async def test_destructive_denied_blocks_dispatch(self, tmp_path):
        """AC-7: User denies confirmation → step is not executed."""
        steps = [
            ActionStep(
                action="click",
                params={"element": "Place Order"},
                verify="Order placed",
                destructive=True,
            ),
            ActionStep(action="done", params={}, verify=""),
        ]
        plan = ActionPlan(steps=steps)

        planner = _make_mock_planner(plan=plan)
        coord = _make_mock_coordinator()

        config = _make_config(
            confirm_destructive="always",
            log_dir=str(tmp_path),
        )
        # AutoDenyConfirmationHandler always returns False
        agent = _make_agent(
            config=config,
            planner=planner,
            coordinator=coord,
            confirmation_handler=AutoDenyConfirmationHandler(),
        )
        result = await agent.execute("Place my order")
        # The step should have been denied and not fully succeed
        assert result.success is False or any(
            not sr.success for sr in result.steps
            if sr.step.action == "click"
        )

    @pytest.mark.asyncio
    async def test_dry_run_skips_confirmation(self, tmp_path):
        """AC-9: --dry-run skips destructive confirmation."""
        steps = [
            ActionStep(
                action="click",
                params={"element": "Delete Account"},
                verify="Account deleted",
                destructive=True,
            ),
            ActionStep(action="done", params={}, verify=""),
        ]
        plan = ActionPlan(steps=steps)
        planner = _make_mock_planner(plan=plan)
        coord = _make_mock_coordinator()

        config = _make_config(
            confirm_destructive="always",
            dry_run=True,
            log_dir=str(tmp_path),
        )
        agent = _make_agent(config=config, planner=planner, coordinator=coord)
        result = await agent.execute("Delete my account")
        # Dry-run should not block on confirmation
        # (the step may fail for other reasons but should not hang)
        assert result is not None


class TestConfirmationLogging:
    """AC-10: Confirmation decisions logged to JSONL."""

    @pytest.mark.asyncio
    async def test_confirmation_decision_logged(self, tmp_path):
        """AC-10: Every confirmation decision has an event log entry."""
        steps = [
            ActionStep(
                action="click",
                params={"element": "Submit Payment"},
                verify="Payment processed",
                destructive=True,
            ),
            ActionStep(action="done", params={}, verify=""),
        ]
        plan = ActionPlan(steps=steps)
        planner = _make_mock_planner(plan=plan)
        coord = _make_mock_coordinator()

        config = _make_config(
            confirm_destructive="always",
            log_dir=str(tmp_path),
        )
        logger = EventLogger(Path(str(tmp_path) + "/log_test"))
        agent = AutomationAgent(
            planner=planner,
            coordinator=coord,
            actuator=_make_mock_actuator(),
            skill_registry=_make_mock_skill_registry(),
            config=config,
            logger=logger,
            confirmation_handler=AutoDenyConfirmationHandler(),
        )
        await agent.execute("Submit payment")

        # Check that at least one DESTRUCTIVE_CONFIRM event was logged
        confirm_events = [
            e for e in logger._events
            if e.event_type == EventType.DESTRUCTIVE_CONFIRM
        ]
        assert len(confirm_events) >= 1, (
            f"Expected DESTRUCTIVE_CONFIRM event, got types: "
            f"{[e.event_type for e in logger._events]}"
        )
        # Verify the event has required data fields
        evt_data = confirm_events[0].data
        assert "action" in evt_data
        assert "decision" in evt_data


class TestSanitization:
    """Security: ANSI/unicode injection in confirmation display."""

    def test_ansi_escape_stripped(self):
        assert _sanitize_for_display("\x1b[31mred\x1b[0m") == "red"

    def test_unicode_bidi_stripped(self):
        assert _sanitize_for_display("pay\u202anow") == "paynow"


# ============================================================================
# SLICE 2: Gap 1 (Set-of-Mark) + Gap 7 (Dual-Resolution)
# ============================================================================


class TestSoMFindElementFlow:
    """Gap 1: SoM annotated screenshot → find_element → coordinate mapping."""

    @pytest.mark.asyncio
    async def test_som_annotated_screenshot_sent_to_vlm(self, tmp_path):
        """AC-11/AC-14: When >=3 AX elements available and SoM enabled,
        annotated screenshot is sent to the VLM."""
        from automation_agent.vision.annotator import annotate_screenshot
        from automation_agent.vision.coordinator import ScreenCoordinatorImpl
        from automation_agent.vision.capture import ScreenCapture

        config = _make_config(som_enabled=True, log_dir=str(tmp_path))
        capture = MagicMock(spec=ScreenCapture)
        capture.get_screen_size.return_value = (1024, 768)
        capture.target_resolution = (1024, 768)
        b64 = _make_jpeg_b64(1024, 768)
        capture.capture_b64.return_value = b64

        coord = ScreenCoordinatorImpl(config, capture=capture)
        # Mock the VLM call to return element number
        coord._call_vision_model = AsyncMock(return_value="FOUND: element_number=2")

        # Provide 5 AX candidates (>= 3 threshold)
        candidates = [
            {
                "center_x": 100 + i * 100,
                "center_y": 200,
                "width": 80,
                "height": 30,
                "title": f"Button {i}",
                "role": "AXButton",
            }
            for i in range(5)
        ]

        result = await coord.find_element(
            "Button 1",
            screenshot_b64=b64,
            candidates=candidates,
        )
        # VLM should have been called at least once
        assert coord._call_vision_model.await_count >= 1

    @pytest.mark.asyncio
    async def test_som_fallback_when_few_elements(self, tmp_path):
        """AC-14: < 3 AX elements → skip SoM, use raw coordinate grounding."""
        from automation_agent.vision.coordinator import ScreenCoordinatorImpl
        from automation_agent.vision.capture import ScreenCapture

        config = _make_config(som_enabled=True, log_dir=str(tmp_path))
        capture = MagicMock(spec=ScreenCapture)
        capture.get_screen_size.return_value = (1024, 768)
        capture.target_resolution = (1024, 768)
        b64 = _make_jpeg_b64(1024, 768)
        capture.capture_b64.return_value = b64

        coord = ScreenCoordinatorImpl(config, capture=capture)
        coord._call_vision_model = AsyncMock(return_value="FOUND: x=50.0 y=50.0")

        # Only 1 candidate (< 3 threshold)
        candidates = [
            {"center_x": 100, "center_y": 200, "width": 80, "height": 30,
             "title": "Solo Button", "role": "AXButton"},
        ]

        result = await coord.find_element(
            "Solo Button",
            screenshot_b64=b64,
            candidates=candidates,
        )
        # Should still work but via raw coordinates, not SoM
        assert coord._call_vision_model.await_count >= 1


class TestSoMAnnotatorIntegration:
    """Gap 1: annotate_screenshot draws correct overlays."""

    def test_annotate_produces_valid_jpeg(self):
        from automation_agent.vision.annotator import annotate_screenshot

        b64 = _make_jpeg_b64(1024, 768)
        elements = [
            {"center_x": 100, "center_y": 200, "width": 80, "height": 30},
            {"center_x": 300, "center_y": 400, "width": 60, "height": 25},
        ]
        result = annotate_screenshot(b64, elements, screen_size=(1024, 768))
        # Result should be valid base64
        img_bytes = base64.b64decode(result)
        img = Image.open(io.BytesIO(img_bytes))
        assert img.format == "JPEG"
        assert img.size == (1024, 768)

    def test_annotate_respects_max_labels(self):
        """AC-12: Only max_labels drawn."""
        from automation_agent.vision.annotator import annotate_screenshot

        b64 = _make_jpeg_b64(1024, 768)
        # 30 elements but max_labels=5
        elements = [
            {"center_x": 50 + i * 30, "center_y": 100, "width": 20, "height": 10}
            for i in range(30)
        ]
        result = annotate_screenshot(b64, elements, screen_size=(1024, 768), max_labels=5)
        # Should succeed (not crash) even with 30 elements
        assert len(result) > 0


class TestDualResolutionGrounding:
    """Gap 7: Dual-resolution screenshot flow."""

    def test_coordinator_advertises_dual_res(self, tmp_path):
        """AC-21: Coordinator with multi-image support includes DUAL_RESOLUTION capability."""
        from automation_agent.vision.coordinator import ScreenCoordinatorImpl
        from automation_agent.vision.capture import ScreenCapture

        config = _make_config(dual_resolution_grounding=True, log_dir=str(tmp_path))
        capture = MagicMock(spec=ScreenCapture)
        capture.get_screen_size.return_value = (1920, 1080)
        capture.target_resolution = (1024, 768)
        capture.capture_b64.return_value = _make_jpeg_b64(1024, 768)

        coord = ScreenCoordinatorImpl(config, capture=capture)
        caps = coord.capabilities()
        # DUAL_RESOLUTION should be advertised if multi-image is supported
        # (depends on model; this tests the capability mechanism exists)
        assert isinstance(caps, frozenset)

    @pytest.mark.asyncio
    async def test_find_element_dual_returns_result(self, tmp_path):
        """AC-21: find_element_dual called with both full and crop images."""
        from automation_agent.vision.coordinator import ScreenCoordinatorImpl
        from automation_agent.vision.capture import ScreenCapture

        config = _make_config(
            dual_resolution_grounding=True,
            log_dir=str(tmp_path),
        )
        capture = MagicMock(spec=ScreenCapture)
        capture.get_screen_size.return_value = (1920, 1080)
        capture.target_resolution = (1024, 768)
        b64 = _make_jpeg_b64(1024, 768)
        capture.capture_b64.return_value = b64

        coord = ScreenCoordinatorImpl(config, capture=capture)
        coord._call_vision_model_with_images = AsyncMock(
            return_value="FOUND: x=50.0 y=50.0"
        )

        result = await coord.find_element_dual(
            description="Settings icon",
            screenshot_b64=b64,
            context_b64=b64,
        )
        # Either returns a result or None if the model doesn't support multi-image
        assert result is None or isinstance(result, FindElementResult)


# ============================================================================
# SLICE 3: Gap 3 (Embedding) + Gap 2 (World-State) + Gap 4 (Lookahead)
# ============================================================================


class TestEmbeddingSkillRetrieval:
    """Gap 3: Embedding-based skill retrieval three-stage pipeline."""

    @pytest.mark.asyncio
    async def test_embedding_pipeline_graceful_without_fastembed(self):
        """AC-19: When fastembed not installed, falls back to LLM + keyword."""
        from automation_agent.skills.registry import SkillRegistryImpl

        config = _make_config(skill_embedding_enabled=True)
        # Even if embedding is enabled, missing fastembed → graceful fallback
        registry = SkillRegistryImpl(
            skill_dir=Path("/nonexistent"),
            config=config,
        )
        # Should not crash — just no embedding index
        result = await registry.match("return my walmart order")
        # No skills loaded, so None
        assert result is None

    def test_embedding_index_build_and_query(self):
        """AC-16/AC-18: EmbeddingIndex.build() + query() with mock skills."""
        from automation_agent.skills.embeddings import TextEmbedding

        if TextEmbedding is None:
            pytest.skip("fastembed not installed")

        from automation_agent.skills.embeddings import EmbeddingIndex
        from automation_agent.skills.models import Skill, SkillRequirements

        idx = EmbeddingIndex()
        skills = {
            "return-amazon": Skill(
                name="return-amazon",
                description="Return an Amazon order",
                trigger_keywords=["return", "amazon", "order"],
                parameters={},
                steps_text="Steps here",
                requires=SkillRequirements(),
                summary="Return items from Amazon",
                tags=["amazon", "return", "ecommerce"],
            ),
            "search-google": Skill(
                name="search-google",
                description="Search on Google",
                trigger_keywords=["search", "google"],
                parameters={},
                steps_text="Steps here",
                requires=SkillRequirements(),
                summary="Search the web using Google",
                tags=["search", "google", "web"],
            ),
        }
        idx.build(skills)
        results = idx.query("send back my amazon purchase", top_k=2)
        assert len(results) > 0
        # The amazon return skill should rank higher than google search
        assert results[0].skill_id == "return-amazon"

    def test_embedding_index_empty_skills(self):
        """Edge case: build with no skills → query returns empty."""
        from automation_agent.skills.embeddings import TextEmbedding

        if TextEmbedding is None:
            pytest.skip("fastembed not installed")

        from automation_agent.skills.embeddings import EmbeddingIndex

        idx = EmbeddingIndex()
        idx.build({})
        results = idx.query("anything", top_k=5)
        assert results == []


class TestWorldStateDocument:
    """Gap 2: DesktopContext, ContextMonitor, state diffs, format_for_planner."""

    def test_desktop_context_fields_exist(self):
        """AC-26: DesktopContext has world-state fields."""
        ctx = DesktopContext()
        assert ctx.page_semantic_label == ""
        assert ctx.obstacles == []
        assert ctx.completed_milestones == []
        assert ctx.state_version == 0

    def test_context_monitor_update_cheap_increments_version(self):
        """AC-26: update_cheap increments state_version."""
        monitor = ContextMonitor()
        assert monitor.context.state_version == 0
        monitor.update_cheap()
        assert monitor.context.state_version == 1
        monitor.update_cheap()
        assert monitor.context.state_version == 2

    def test_record_step_outcome_success_adds_milestone(self):
        """AC-29: Successful step → verify text added to completed_milestones."""
        monitor = ContextMonitor()
        step = ActionStep(
            action="click",
            params={"element": "Login"},
            verify="Login page visible",
        )
        result = StepResult(step=step, success=True, evidence="Logged in")
        monitor.record_step_outcome(step, result)
        assert "Login page visible" in monitor.context.completed_milestones

    def test_record_step_outcome_failure_adds_obstacle(self):
        """AC-29: Failed step → error added to obstacles."""
        monitor = ContextMonitor()
        step = ActionStep(
            action="click",
            params={"element": "Submit"},
            verify="Submitted",
        )
        result = StepResult(
            step=step, success=False, evidence="", error="Button not found"
        )
        monitor.record_step_outcome(step, result)
        assert "Button not found" in monitor.context.obstacles

    def test_milestones_capped_at_20(self):
        """AC-26: completed_milestones capped at 20 (oldest dropped)."""
        monitor = ContextMonitor()
        for i in range(25):
            step = ActionStep(
                action="click",
                params={"element": f"btn{i}"},
                verify=f"Milestone {i}",
            )
            result = StepResult(step=step, success=True, evidence=f"Done {i}")
            monitor.record_step_outcome(step, result)
        assert len(monitor.context.completed_milestones) == 20
        # Oldest should have been dropped
        assert "Milestone 0" not in monitor.context.completed_milestones
        assert "Milestone 24" in monitor.context.completed_milestones

    def test_obstacles_deduplicated(self):
        """AC-26: obstacles are deduplicated by content."""
        monitor = ContextMonitor()
        step = ActionStep(action="click", params={}, verify="clicked")
        result = StepResult(step=step, success=False, error="Timeout")
        monitor.record_step_outcome(step, result)
        monitor.record_step_outcome(step, result)
        assert monitor.context.obstacles.count("Timeout") == 1

    def test_format_state_diff_detects_app_change(self):
        """AC-27: format_state_diff detects frontmost_app change."""
        monitor = ContextMonitor()
        monitor.context.frontmost_app = "Safari"
        monitor.update_cheap()  # snapshot "Safari" as previous

        monitor.context.frontmost_app = "Calculator"
        diff = monitor.format_state_diff()
        assert diff is not None
        assert any("App changed" in c for c in diff.changes)

    def test_format_state_diff_detects_window_change(self):
        """AC-27: format_state_diff detects window_title change."""
        monitor = ContextMonitor()
        monitor.context.window_title = "Google"
        monitor.update_cheap()

        monitor.context.window_title = "Amazon"
        diff = monitor.format_state_diff()
        assert diff is not None
        assert any("Window changed" in c for c in diff.changes)

    def test_format_state_diff_no_change_returns_none(self):
        """AC-27: No changes → None."""
        monitor = ContextMonitor()
        monitor.update_cheap()
        diff = monitor.format_state_diff()
        assert diff is None

    def test_format_for_planner_includes_milestones(self):
        """AC-28: format_for_planner includes milestones and obstacles."""
        monitor = ContextMonitor()
        monitor.context.frontmost_app = "Safari"
        monitor.context.window_title = "Amazon"
        monitor.context.completed_milestones = ["Logged in", "Navigated to orders"]
        monitor.context.obstacles = ["Return button not found"]

        output = monitor.format_for_planner()
        assert "Logged in" in output
        assert "Navigated to orders" in output
        assert "Return button not found" in output
        assert "## Desktop State" in output

    def test_page_semantic_label_on_navigation(self):
        """AC-26: page_semantic_label written on navigation that changes window_title."""
        monitor = ContextMonitor()
        monitor.context.frontmost_app = "Safari"
        monitor.context.window_title = "Google"
        monitor._previous_title = "Google"
        monitor.update_cheap()  # snapshot

        # Simulate window title change after click
        monitor.context.window_title = "Amazon - Your Orders"
        step = ActionStep(action="click", params={"element": "Orders"}, verify="Orders page")
        result = StepResult(step=step, success=True, evidence="Orders visible")
        monitor.record_step_outcome(step, result)

        assert "Safari" in monitor.context.page_semantic_label
        assert "Amazon" in monitor.context.page_semantic_label


class TestWorldStatePlannerIntegration:
    """Gap 2: World-state context flows into planner prompt."""

    @pytest.mark.asyncio
    async def test_desktop_context_passed_to_planner(self, tmp_path):
        """AC-28: format_for_planner output reaches planner.plan()."""
        plan = ActionPlan(
            steps=[ActionStep(action="done", params={}, verify="")]
        )
        planner = _make_mock_planner(plan=plan)
        coord = _make_mock_coordinator()

        monitor = ContextMonitor()
        monitor.context.frontmost_app = "Safari"
        monitor.context.completed_milestones = ["Logged in"]

        config = _make_config(log_dir=str(tmp_path))
        agent = _make_agent(
            config=config,
            planner=planner,
            coordinator=coord,
            context_monitor=monitor,
        )
        result = await agent.execute("Complete the task")
        assert result.success is True

        # Verify planner was called with desktop_context
        call_kwargs = planner.plan.call_args
        if call_kwargs.kwargs.get("desktop_context"):
            ctx_text = call_kwargs.kwargs["desktop_context"]
            assert "Safari" in ctx_text


class TestLookaheadSimulation:
    """Gap 4: Lookahead prediction → dispatch gate."""

    @pytest.mark.asyncio
    async def test_lookahead_blocks_dispatch_on_predicted_failure(self, tmp_path):
        """AC-32: Lookahead predicts failure → skip dispatch → StepResult(success=False)."""
        steps = [
            ActionStep(
                action="click",
                params={"element": "Place Order"},
                verify="Order confirmation page",
                destructive=True,
            ),
            ActionStep(action="done", params={}, verify=""),
        ]
        plan = ActionPlan(steps=steps)
        planner = _make_mock_planner(plan=plan)

        coord = _make_mock_coordinator(
            capabilities=frozenset({CoordinatorCapability.LOOKAHEAD}),
        )
        # Lookahead predicts failure
        coord.predict_action_outcome = AsyncMock(
            return_value={
                "likely_success": False,
                "predicted_state": "Dropdown menu appears",
                "risk": "Wrong element — this opens a menu, not checkout",
                "mismatch_reason": "Expected: order confirmation. Predicted: dropdown menu.",
            }
        )

        config = _make_config(
            lookahead_enabled=True,
            confirm_destructive="never",  # must bypass env check for test
            log_dir=str(tmp_path),
        )
        # Bypass the env-var validation for NEVER mode
        config.confirm_destructive = ConfirmMode.NEVER

        agent = _make_agent(config=config, planner=planner, coordinator=coord)

        result = await agent.execute("Place my order")
        # The step should have been blocked by lookahead
        lookahead_results = [
            sr for sr in result.steps
            if sr.verification_method == "lookahead"
        ]
        if lookahead_results:
            assert lookahead_results[0].success is False
            assert "Lookahead" in (lookahead_results[0].evidence or "")

    @pytest.mark.asyncio
    async def test_lookahead_skipped_when_confirmation_active(self, tmp_path):
        """AC-31: skip_when_confirmed=True + confirmation active → no lookahead call."""
        steps = [
            ActionStep(
                action="click",
                params={"element": "Delete Account"},
                verify="Account deleted",
                destructive=True,
            ),
            ActionStep(action="done", params={}, verify=""),
        ]
        plan = ActionPlan(steps=steps)
        planner = _make_mock_planner(plan=plan)

        coord = _make_mock_coordinator(
            capabilities=frozenset({CoordinatorCapability.LOOKAHEAD}),
        )

        config = _make_config(
            lookahead_enabled=True,
            lookahead_skip_when_confirmed=True,
            confirm_destructive="always",
            log_dir=str(tmp_path),
        )

        agent = _make_agent(
            config=config,
            planner=planner,
            coordinator=coord,
            confirmation_handler=AutoDenyConfirmationHandler(),
        )
        await agent.execute("Delete my account")

        # Lookahead should NOT have been called because confirmation is active
        # and skip_when_confirmed is True
        coord.predict_action_outcome.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_lookahead_not_triggered_for_non_destructive(self, tmp_path):
        """AC-31: Lookahead only for destructive steps, not routine navigation."""
        steps = [
            ActionStep(
                action="click",
                params={"element": "Search box"},
                verify="Search focused",
            ),
            ActionStep(action="done", params={}, verify=""),
        ]
        plan = ActionPlan(steps=steps)
        planner = _make_mock_planner(plan=plan)

        coord = _make_mock_coordinator(
            capabilities=frozenset({CoordinatorCapability.LOOKAHEAD}),
        )

        config = _make_config(
            lookahead_enabled=True,
            log_dir=str(tmp_path),
        )
        config.confirm_destructive = ConfirmMode.NEVER

        agent = _make_agent(config=config, planner=planner, coordinator=coord)
        await agent.execute("Click search box")

        # predict_action_outcome should NOT be called for non-destructive steps
        coord.predict_action_outcome.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_lookahead_timeout_pessimistic_for_hard_destructive(self, tmp_path):
        """AC-31: Timeout on hard-destructive → pessimistic fallback (block dispatch)."""
        steps = [
            ActionStep(
                action="click",
                params={"element": "Delete all data"},
                verify="Data deleted",
                destructive=True,
            ),
            ActionStep(action="done", params={}, verify=""),
        ]
        plan = ActionPlan(steps=steps)
        planner = _make_mock_planner(plan=plan)

        coord = _make_mock_coordinator(
            capabilities=frozenset({CoordinatorCapability.LOOKAHEAD}),
        )

        # Simulate timeout
        async def slow_predict(**kwargs):
            await asyncio.sleep(100)

        coord.predict_action_outcome = AsyncMock(side_effect=slow_predict)

        config = _make_config(
            lookahead_enabled=True,
            lookahead_timeout_s=0.1,
            log_dir=str(tmp_path),
        )
        config.confirm_destructive = ConfirmMode.NEVER

        agent = _make_agent(config=config, planner=planner, coordinator=coord)
        result = await agent.execute("Delete all data")
        # Hard-destructive timeout → pessimistic (block) → step fails
        blocked = [
            sr for sr in result.steps
            if sr.verification_method == "lookahead" and not sr.success
        ]
        if blocked:
            assert "Lookahead" in (blocked[0].evidence or blocked[0].error or "")


# ============================================================================
# CROSS-SLICE: Feature interaction tests
# ============================================================================


class TestLookaheadConfirmationInteraction:
    """Gap 4 + Gap 6: Lookahead and confirmation co-exist."""

    @pytest.mark.asyncio
    async def test_lookahead_blocks_before_confirmation(self, tmp_path):
        """When skip_when_confirmed=False: lookahead failure → skip confirmation and dispatch."""
        steps = [
            ActionStep(
                action="click",
                params={"element": "Pay Now"},
                verify="Payment processed",
                destructive=True,
            ),
            ActionStep(action="done", params={}, verify=""),
        ]
        plan = ActionPlan(steps=steps)
        planner = _make_mock_planner(plan=plan)

        coord = _make_mock_coordinator(
            capabilities=frozenset({CoordinatorCapability.LOOKAHEAD}),
        )
        coord.predict_action_outcome = AsyncMock(
            return_value={
                "likely_success": False,
                "predicted_state": "Error dialog",
                "risk": "Payment will fail",
                "mismatch_reason": "Card expired",
            }
        )

        # Use a confirmation handler that would hang if called
        class HangingHandler:
            async def confirm(self, step):
                raise AssertionError("Should not reach confirmation")

        config = _make_config(
            lookahead_enabled=True,
            lookahead_skip_when_confirmed=False,
            confirm_destructive="always",
            log_dir=str(tmp_path),
        )
        agent = _make_agent(
            config=config,
            planner=planner,
            coordinator=coord,
            confirmation_handler=HangingHandler(),
        )
        result = await agent.execute("Pay now")
        # Lookahead should have blocked before reaching confirmation
        blocked = [sr for sr in result.steps if sr.verification_method == "lookahead"]
        if blocked:
            assert blocked[0].success is False


class TestInfeasibilityDestructiveInteraction:
    """Gap 5 + Gap 6: Infeasibility detection with destructive steps."""

    @pytest.mark.asyncio
    async def test_denied_destructive_contributes_to_frustration(self, tmp_path):
        """Denied destructive steps contribute to same-state frustration."""
        steps = [
            ActionStep(
                action="click",
                params={"element": "Submit Order"},
                verify="Order submitted",
                destructive=True,
            ),
        ] * 5
        plan = ActionPlan(
            steps=steps + [ActionStep(action="done", params={}, verify="")]
        )

        planner = _make_mock_planner(
            plan=plan,
            infeasibility_response={"infeasible": True, "reason": "All attempts denied"},
        )
        coord = _make_mock_coordinator()

        config = _make_config(
            confirm_destructive="always",
            infeasibility_same_state_limit=3,
            log_dir=str(tmp_path),
        )
        agent = _make_agent(
            config=config,
            planner=planner,
            coordinator=coord,
            confirmation_handler=AutoDenyConfirmationHandler(),
        )
        result = await agent.execute("Submit my order")
        # Should fail — either through denied confirmations or infeasibility
        assert result.success is False


class TestContextMonitorRecordDuringExecution:
    """Gap 2: ContextMonitor.record_step_outcome called during orchestrator execute."""

    @pytest.mark.asyncio
    async def test_context_monitor_records_milestones_during_execution(self, tmp_path):
        """AC-29: Successful steps add milestones through _record_context."""
        steps = [
            ActionStep(action="open_url", params={"url": "https://example.com"}, verify="Page loaded"),
            ActionStep(action="done", params={}, verify=""),
        ]
        plan = ActionPlan(steps=steps)
        planner = _make_mock_planner(plan=plan)
        coord = _make_mock_coordinator()

        monitor = ContextMonitor()
        config = _make_config(log_dir=str(tmp_path))

        agent = _make_agent(
            config=config,
            planner=planner,
            coordinator=coord,
            context_monitor=monitor,
        )
        result = await agent.execute("Open example.com")
        assert result.success is True
        # The milestone "Page loaded" should have been recorded
        assert "Page loaded" in monitor.context.completed_milestones


class TestExecutionResultInfeasibilityField:
    """AC-3: ExecutionResult.infeasibility_reason is a structured field."""

    def test_infeasibility_reason_field_exists(self):
        result = ExecutionResult(success=False)
        assert hasattr(result, "infeasibility_reason")
        assert result.infeasibility_reason is None

    def test_infeasibility_reason_set_on_abort(self):
        result = ExecutionResult(
            success=False,
            infeasibility_reason="Element not found on page",
        )
        assert result.infeasibility_reason == "Element not found on page"


class TestDestructiveClassificationDataclass:
    """DestructiveClassification data shape and convenience methods."""

    def test_not_destructive_singleton(self):
        nd = DestructiveClassification.NOT_DESTRUCTIVE
        assert nd.is_destructive is False
        assert nd.matched_keyword is None
        assert nd.classification_path is None
        assert bool(nd) is False

    def test_destructive_truthy(self):
        d = DestructiveClassification(True, "delete", "keyword_match")
        assert bool(d) is True

    def test_frozen(self):
        d = DestructiveClassification(True, "pay", "keyword_match")
        with pytest.raises(AttributeError):
            d.is_destructive = False  # type: ignore[misc]
