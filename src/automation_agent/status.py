"""Helpers for launching and formatting the live status UI."""

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from automation_agent.config import AgentConfig, StatusUIMode


TERMINAL_EVENT_TYPES = {"task_complete", "task_fail"}


@dataclass
class StatusSnapshot:
    """A formatted status update for the on-screen UI."""

    title: str
    line: str
    terminal: bool = False
    step_label: str = ""
    goal: str = ""


def format_status_event(event: Dict[str, Any]) -> Optional[StatusSnapshot]:
    """Convert a raw JSONL event into a compact UI-friendly status message."""
    event_type = str(event.get("event_type", "")).strip()
    message = str(event.get("message", "")).strip()
    if not event_type or not message:
        return None

    timestamp = event.get("timestamp")
    prefix = _format_timestamp(timestamp)
    step_index = event.get("step_index")
    if step_index is not None:
        prefix = f"{prefix} [step {step_index}]"

    title = _title_for_event(event_type, message, event)
    step_label = ""
    if event_type == "step_start":
        action = (event.get("data") or {}).get("action")
        if action and step_index is not None:
            step_label = f"Step {step_index}: {action}"
        elif action:
            step_label = str(action)
    goal = ""
    if event_type == "task_start":
        goal = str((event.get("data") or {}).get("goal") or message.removeprefix("Goal: ").strip())
    return StatusSnapshot(
        title=title,
        line=f"{prefix} {message}",
        terminal=event_type in TERMINAL_EVENT_TYPES,
        step_label=step_label,
        goal=goal,
    )


def build_status_overlay_command(
    events_file: Path,
    parent_pid: int,
    config: AgentConfig,
) -> list[str]:
    """Build the subprocess command for the floating overlay."""
    return [
        sys.executable,
        "-m",
        "automation_agent.status_overlay",
        "--events-file",
        str(events_file),
        "--parent-pid",
        str(parent_pid),
        "--poll-interval",
        str(config.status_overlay_poll_interval),
        "--max-lines",
        str(config.status_overlay_max_lines),
        "--linger-seconds",
        str(config.status_overlay_linger_seconds),
    ]


class StatusOverlayController:
    """Launches a separate floating status overlay process when enabled."""

    def __init__(self, config: AgentConfig, events_file: Path):
        self.config = config
        self.events_file = events_file
        self.process: Optional[subprocess.Popen] = None

    def start(self) -> None:
        """Start the overlay subprocess if the platform and config allow it."""
        if self.config.status_ui != StatusUIMode.OVERLAY:
            return
        if sys.platform != "darwin":
            return
        if self.process is not None and self.process.poll() is None:
            return

        cmd = build_status_overlay_command(
            events_file=self.events_file,
            parent_pid=os.getpid(),
            config=self.config,
        )
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop(self) -> None:
        """Stop the overlay subprocess if it is still running."""
        if self.process is None:
            return
        if self.process.poll() is not None:
            return
        self.process.terminate()


def _format_timestamp(raw_timestamp: Any) -> str:
    """Format the event timestamp as HH:MM:SS."""
    if not raw_timestamp:
        return "[--:--:--]"
    try:
        parsed = datetime.fromisoformat(str(raw_timestamp))
        return parsed.strftime("[%H:%M:%S]")
    except ValueError:
        return "[--:--:--]"


def _title_for_event(event_type: str, message: str, event: Dict[str, Any]) -> str:
    """Choose a short current-status title for an event."""
    data = event.get("data") or {}
    if event_type == "task_start":
        return "Starting task"
    if event_type == "skill_match":
        skill_name = data.get("skill_name")
        return f"Matched skill: {skill_name}" if skill_name else "Matched skill"
    if event_type == "plan_start":
        return "Planning"
    if event_type == "plan_complete":
        steps = data.get("step_count")
        return f"Plan ready ({steps} steps)" if steps else "Plan ready"
    if event_type == "step_start":
        action = data.get("action")
        return f"Executing: {action}" if action else "Executing step"
    if event_type == "action_start":
        return "Acting"
    if event_type == "verify_start":
        return "Verifying"
    if event_type == "step_retry":
        strategy = data.get("strategy")
        return f"Retrying ({strategy})" if strategy else "Retrying"
    if event_type == "step_replan":
        return "Replanning"
    if event_type == "user_wait":
        return "Waiting for user"
    if event_type == "task_complete":
        return "Completed"
    if event_type == "task_fail":
        return "Failed"
    return message[:80]


def load_status_snapshot(line: str) -> Optional[StatusSnapshot]:
    """Parse one JSONL event line into a status snapshot."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return format_status_event(event)
