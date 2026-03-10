"""Tests for the live status UI helpers."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from automation_agent.config import AgentConfig, StatusUIMode
from automation_agent.status import (
    StatusOverlayController,
    build_status_overlay_command,
    format_status_event,
    load_status_snapshot,
)


def test_format_status_event_for_step_start():
    """Step start events should become a compact title + line."""
    snapshot = format_status_event(
        {
            "event_type": "step_start",
            "message": "Step 2: open_url",
            "timestamp": "2026-03-10T03:23:34.577195",
            "step_index": 2,
            "data": {"action": "open_url"},
        }
    )

    assert snapshot is not None
    assert snapshot.title == "Executing: open_url"
    assert "[03:23:34] [step 2] Step 2: open_url" == snapshot.line
    assert snapshot.terminal is False
    assert snapshot.step_label == "Step 2: open_url"


def test_format_status_event_tracks_goal_on_task_start():
    """Task start events should preserve the active goal for menu bar display."""
    snapshot = format_status_event(
        {
            "event_type": "task_start",
            "message": "Goal: Return headphones on Amazon",
            "timestamp": "2026-03-10T03:23:19.231554",
            "data": {"goal": "Return headphones on Amazon"},
        }
    )

    assert snapshot is not None
    assert snapshot.title == "Starting task"
    assert snapshot.goal == "Return headphones on Amazon"


def test_load_status_snapshot_rejects_invalid_json():
    """Invalid JSONL lines should be ignored safely."""
    assert load_status_snapshot("not-json") is None


def test_build_status_overlay_command_includes_runtime_flags():
    """Overlay command should carry the configured runtime values."""
    config = AgentConfig(
        _env_file=None,
        status_ui=StatusUIMode.OVERLAY,
        status_overlay_poll_interval=0.5,
        status_overlay_max_lines=150,
        status_overlay_linger_seconds=9.0,
    )

    cmd = build_status_overlay_command(Path("logs/runs/test/events.jsonl"), 1234, config)

    assert cmd[:3] == [cmd[0], "-m", "automation_agent.status_overlay"]
    assert "--events-file" in cmd
    assert "logs/runs/test/events.jsonl" in cmd
    assert "--parent-pid" in cmd and "1234" in cmd
    assert "--poll-interval" in cmd and "0.5" in cmd
    assert "--max-lines" in cmd and "150" in cmd
    assert "--linger-seconds" in cmd and "9.0" in cmd


def test_status_overlay_controller_launches_only_when_enabled():
    """Overlay controller should no-op unless overlay mode is enabled on macOS."""
    config = AgentConfig(_env_file=None, status_ui=StatusUIMode.OVERLAY)
    controller = StatusOverlayController(config=config, events_file=Path("logs/runs/x/events.jsonl"))

    with patch("automation_agent.status.sys.platform", "darwin"), patch(
        "automation_agent.status.subprocess.Popen"
    ) as popen:
        popen.return_value = MagicMock()
        controller.start()

    popen.assert_called_once()


def test_status_overlay_controller_skips_when_disabled():
    """Disabled status UI should not spawn a subprocess."""
    config = AgentConfig(_env_file=None, status_ui=StatusUIMode.OFF)
    controller = StatusOverlayController(config=config, events_file=Path("logs/runs/x/events.jsonl"))

    with patch("automation_agent.status.subprocess.Popen") as popen:
        controller.start()

    popen.assert_not_called()
