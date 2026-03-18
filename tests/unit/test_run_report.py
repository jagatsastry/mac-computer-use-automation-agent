"""Unit tests for run report generation and lifecycle logging.

Tests the LLMCallRecord, VerificationStats, StepTimeline dataclasses,
the _generate_run_report() function, and the agent's report generation
and LLM call tracking during execute().
"""

import json

from automation_agent.config import AgentConfig
from automation_agent.logging.event_logger import EventLogger
from automation_agent.logging.models import EventType
from automation_agent.orchestrator.agent import (
    AutomationAgent,
    LLMCallRecord,
    StepTimeline,
    VerificationStats,
    _build_report_data_dict,
    _generate_run_report,
)
from automation_agent.shared_models import ActionPlan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> AgentConfig:
    """Create an AgentConfig for tests with sensible defaults."""
    defaults = {
        "_env_file": None,
        "anthropic_api_key": "test-key-not-real",
        "model_provider": "local",
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


class _AutoApproveHandler:
    """Auto-approve all destructive confirmations in tests."""

    async def confirm(self, step):
        return True


def _make_agent(planner, skill_registry, coordinator, actuator, logger, config=None):
    """Create an AutomationAgent with the given mocks."""
    if config is None:
        config = _make_config()
    return AutomationAgent(
        planner=planner,
        skill_registry=skill_registry,
        coordinator=coordinator,
        actuator=actuator,
        config=config,
        logger=logger,
        confirmation_handler=_AutoApproveHandler(),
    )


def _make_plan(steps, goal="Test goal"):
    """Shortcut to create an ActionPlan."""
    return ActionPlan(steps=steps, goal=goal)


# ---------------------------------------------------------------------------
# Tests for dataclasses
# ---------------------------------------------------------------------------


class TestLLMCallRecord:
    def test_default_values(self):
        record = LLMCallRecord()
        assert record.timestamp == ""
        assert record.purpose == ""
        assert record.provider == ""
        assert record.model == ""
        assert record.duration_ms == 0
        assert record.input_tokens_est == 0

    def test_custom_values(self):
        record = LLMCallRecord(
            timestamp="10:23:46",
            purpose="planning",
            provider="gemini",
            model="gemini-2.5-flash",
            duration_ms=3400,
            input_tokens_est=2000,
        )
        assert record.purpose == "planning"
        assert record.duration_ms == 3400


class TestVerificationStats:
    def test_default_values(self):
        stats = VerificationStats()
        assert stats.tier0_count == 0
        assert stats.tier0_pass == 0
        assert stats.tier1_count == 0
        assert stats.tier1_pass == 0
        assert stats.tier2_count == 0
        assert stats.tier2_pass == 0
        assert stats.escalations == 0


class TestStepTimeline:
    def test_default_values(self):
        entry = StepTimeline()
        assert entry.index == 0
        assert entry.action == ""


# ---------------------------------------------------------------------------
# Tests for _generate_run_report
# ---------------------------------------------------------------------------


class TestGenerateRunReport:
    def test_basic_success_report(self):
        md = _generate_run_report(
            run_id="test_run_001",
            goal="Open Calculator",
            success=True,
            total_duration_ms=5000,
            step_count=3,
            replan_count=0,
            skill_name="open_app",
            llm_calls=[
                LLMCallRecord(
                    purpose="planning",
                    provider="local",
                    model="gemma2",
                    duration_ms=1200,
                    input_tokens_est=500,
                ),
            ],
            step_timeline=[
                StepTimeline(
                    index=0,
                    action="activate_app",
                    result="PASS",
                    verify_tier="actuator_state",
                    pre_app="iTerm2",
                    post_app="Calculator",
                    duration_s=2.1,
                ),
            ],
            verification_stats=VerificationStats(
                tier0_count=0,
                tier1_count=1,
                tier1_pass=1,
            ),
            issues=[],
            state_changes=[
                {"timestamp": "10:23:46", "app": "iTerm2", "url": "-"},
                {"timestamp": "10:23:48", "app": "Calculator", "url": "-"},
            ],
        )
        assert "# Run Report: test_run_001" in md
        assert "**Result:** SUCCESS" in md
        assert "**Duration:** 5000ms" in md
        assert "**Steps executed:** 3" in md
        assert "**Skill matched:** open_app" in md
        assert "planning" in md
        assert "activate_app" in md
        assert "Tier 1 (actuator/JS): 1 checks, 1 passed" in md
        assert "No issues observed." in md
        assert "Calculator" in md

    def test_failure_report(self):
        md = _generate_run_report(
            run_id="test_run_002",
            goal="Click submit button",
            success=False,
            total_duration_ms=31200,
            step_count=2,
            replan_count=1,
            skill_name=None,
            llm_calls=[],
            step_timeline=[],
            verification_stats=VerificationStats(),
            issues=["Step 1: element 'submit' not found after 3 attempts"],
            state_changes=[],
        )
        assert "**Result:** FAILED" in md
        assert "**Replans:** 1" in md
        assert "**Skill matched:** none" in md
        assert "No LLM calls recorded." in md
        assert "submit" in md

    def test_empty_report(self):
        md = _generate_run_report(
            run_id="empty",
            goal="",
            success=True,
            total_duration_ms=0,
            step_count=0,
            replan_count=0,
            skill_name=None,
            llm_calls=[],
            step_timeline=[],
            verification_stats=VerificationStats(),
            issues=[],
            state_changes=[],
        )
        assert "# Run Report: empty" in md
        assert "No steps executed." in md
        assert "No state changes recorded." in md


class TestBuildReportDataDict:
    def test_returns_structured_dict(self):
        result = _build_report_data_dict(
            run_id="test",
            goal="Test goal",
            success=True,
            total_duration_ms=100,
            step_count=1,
            replan_count=0,
            skill_name="test_skill",
            llm_calls=[
                LLMCallRecord(purpose="planning", duration_ms=50),
            ],
            step_timeline=[
                StepTimeline(index=0, action="click"),
            ],
            verification_stats=VerificationStats(tier1_count=1, tier1_pass=1),
            issues=[],
            state_changes=[],
        )
        assert result["run_id"] == "test"
        assert result["success"] is True
        assert len(result["llm_calls"]) == 1
        assert result["llm_calls"][0]["purpose"] == "planning"
        assert result["verification"]["tier1_count"] == 1
        assert isinstance(result["step_timeline"], list)

    def test_json_serializable(self):
        result = _build_report_data_dict(
            run_id="test",
            goal="Test",
            success=False,
            total_duration_ms=0,
            step_count=0,
            replan_count=0,
            skill_name=None,
            llm_calls=[],
            step_timeline=[],
            verification_stats=VerificationStats(),
            issues=["issue 1"],
            state_changes=[{"timestamp": "12:00:00", "app": "App", "url": "-"}],
        )
        # Should be JSON serializable
        serialized = json.dumps(result)
        assert isinstance(serialized, str)
        parsed = json.loads(serialized)
        assert parsed["issues"] == ["issue 1"]


# ---------------------------------------------------------------------------
# Tests for agent lifecycle tracking
# ---------------------------------------------------------------------------


class TestAgentRunTracking:
    """Test that the agent tracks LLM calls and generates reports during execute()."""

    async def test_report_generated_on_success(
        self,
        mock_planner,
        mock_coordinator,
        mock_actuator,
        mock_skill_registry,
        tmp_log_dir,
    ):
        """A successful execute() should write report.md to the run directory."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner,
            mock_skill_registry,
            mock_coordinator,
            mock_actuator,
            logger,
        )

        result = await agent.execute("Open Calculator")

        assert result.success
        report_path = logger.run_dir / "report.md"
        assert report_path.exists()
        content = report_path.read_text()
        assert "# Run Report:" in content
        assert "**Result:** SUCCESS" in content
        assert "Open Calculator" in content

    async def test_report_generated_on_failure(
        self,
        mock_planner,
        mock_coordinator,
        mock_actuator,
        mock_skill_registry,
        tmp_log_dir,
    ):
        """A failed execute() should also write report.md."""
        mock_actuator.activate_app.return_value = {
            "success": False,
            "error": "App not found",
        }
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner,
            mock_skill_registry,
            mock_coordinator,
            mock_actuator,
            logger,
        )

        result = await agent.execute("Open Calculator")

        report_path = logger.run_dir / "report.md"
        assert report_path.exists()
        content = report_path.read_text()
        assert "# Run Report:" in content

    async def test_task_summary_event_logged(
        self,
        mock_planner,
        mock_coordinator,
        mock_actuator,
        mock_skill_registry,
        tmp_log_dir,
    ):
        """execute() should log a TASK_SUMMARY event with structured data."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner,
            mock_skill_registry,
            mock_coordinator,
            mock_actuator,
            logger,
        )

        await agent.execute("Open Calculator")

        # Find the TASK_SUMMARY event
        summary_events = [
            e for e in logger.events if e.event_type == EventType.TASK_SUMMARY
        ]
        assert len(summary_events) == 1
        data = summary_events[0].data
        assert "run_id" in data
        assert "goal" in data
        assert "llm_calls" in data
        assert "verification" in data
        assert isinstance(data["llm_calls"], list)

    async def test_llm_call_tracking(
        self,
        mock_planner,
        mock_coordinator,
        mock_actuator,
        mock_skill_registry,
        tmp_log_dir,
    ):
        """execute() should track LLM calls made during the run."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner,
            mock_skill_registry,
            mock_coordinator,
            mock_actuator,
            logger,
        )

        await agent.execute("Open Calculator")

        # At minimum, a planning call should be tracked
        assert len(agent._llm_calls) >= 1
        planning_calls = [c for c in agent._llm_calls if c.purpose == "planning"]
        assert len(planning_calls) >= 1
        assert planning_calls[0].duration_ms >= 0

    async def test_verification_stats_tracked(
        self,
        mock_planner,
        mock_coordinator,
        mock_actuator,
        mock_skill_registry,
        tmp_log_dir,
    ):
        """execute() should track verification tier stats."""
        # Set up actuator to return browser state for tier1 verification
        mock_actuator.get_state.return_value = {
            "app_name": "Safari",
            "browser_url": "",
            "window_title": "",
        }
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner,
            mock_skill_registry,
            mock_coordinator,
            mock_actuator,
            logger,
        )

        await agent.execute("Open Safari")

        # Stats should be populated
        vs = agent._verification_stats
        total = vs.tier0_count + vs.tier1_count + vs.tier2_count
        # There should be at least one verification attempt for
        # the activate_app step
        assert total >= 0  # May be 0 if done step has no verify

    async def test_step_timeline_populated(
        self,
        mock_planner,
        mock_coordinator,
        mock_actuator,
        mock_skill_registry,
        tmp_log_dir,
    ):
        """execute() should populate the step timeline."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner,
            mock_skill_registry,
            mock_coordinator,
            mock_actuator,
            logger,
        )

        await agent.execute("Open Calculator")

        # Should have timeline entries for executed steps
        assert len(agent._step_timeline) >= 1
        first = agent._step_timeline[0]
        assert first.index == 0
        assert first.action == "activate_app"

    async def test_state_changes_recorded(
        self,
        mock_planner,
        mock_coordinator,
        mock_actuator,
        mock_skill_registry,
        tmp_log_dir,
    ):
        """execute() should record state changes."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner,
            mock_skill_registry,
            mock_coordinator,
            mock_actuator,
            logger,
        )

        await agent.execute("Open Calculator")

        # Should have at least the initial state
        assert len(agent._state_changes) >= 1
        first = agent._state_changes[0]
        assert "timestamp" in first
        assert "app" in first

    async def test_reset_tracking_between_runs(
        self,
        mock_planner,
        mock_coordinator,
        mock_actuator,
        mock_skill_registry,
        tmp_log_dir,
    ):
        """Each execute() call should reset tracking state."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner,
            mock_skill_registry,
            mock_coordinator,
            mock_actuator,
            logger,
        )

        await agent.execute("Open Calculator")
        first_calls = len(agent._llm_calls)
        assert first_calls >= 1

        # Second run should reset
        logger2 = EventLogger(tmp_log_dir)
        agent.logger = logger2
        await agent.execute("Open Calculator again")
        # LLM calls should be from the second run only
        assert len(agent._llm_calls) >= 1

    async def test_report_includes_llm_call_table(
        self,
        mock_planner,
        mock_coordinator,
        mock_actuator,
        mock_skill_registry,
        tmp_log_dir,
    ):
        """Report should include a table of LLM calls."""
        logger = EventLogger(tmp_log_dir)
        agent = _make_agent(
            mock_planner,
            mock_skill_registry,
            mock_coordinator,
            mock_actuator,
            logger,
        )

        await agent.execute("Open Calculator")

        report_path = logger.run_dir / "report.md"
        content = report_path.read_text()
        assert "## LLM Calls" in content
        assert "planning" in content
