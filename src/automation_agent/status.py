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
    is_plan: bool = False  # True for plan_complete/replan_complete


def format_status_event(
    event: Dict[str, Any], verbose: bool = False
) -> Optional[StatusSnapshot]:
    """Convert a raw JSONL event into a UI-friendly status message.

    When verbose=True, includes detailed LLM responses and reasoning.
    """
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
        goal = str(
            (event.get("data") or {}).get("goal")
            or message.removeprefix("Goal: ").strip()
        )

    line = f"{prefix} {message}"

    # Always show plan steps in overlay (not just verbose mode)
    data = event.get("data") or {}
    if event_type in ("plan_complete", "replan_complete"):
        steps = data.get("steps_summary")
        if steps:
            label = "PLAN" if event_type == "plan_complete" else "REPLAN"
            line += f"\n  ── {label} ──"
            for i, s in enumerate(steps):
                line += f"\n  {i}. {s}"
            line += "\n  ──────────"

    if verbose:
        detail = _verbose_detail(event_type, data)
        if detail:
            line = f"{line}\n{detail}"

    return StatusSnapshot(
        title=title,
        line=line,
        terminal=event_type in TERMINAL_EVENT_TYPES,
        step_label=step_label,
        goal=goal,
        is_plan=event_type in ("plan_complete", "replan_complete"),
    )


def _verbose_detail(event_type: str, data: Dict[str, Any]) -> str:
    """Build verbose detail lines from event data for the overlay."""
    parts: list[str] = []

    if event_type in ("plan_complete", "replan_complete"):
        steps = data.get("steps_summary")
        if steps:
            parts.append("  Plan steps:")
            for i, s in enumerate(steps):
                parts.append(f"    {i}. {s}")
        llm_resp = data.get("llm_response")
        if llm_resp:
            parts.append("  LLM response:")
            for resp_line in str(llm_resp).splitlines():
                parts.append(f"    {resp_line}")

    elif event_type == "element_found":
        for key in ("element", "confidence", "source", "vision_response"):
            val = data.get(key)
            if val is not None:
                parts.append(f"  {key}: {val}")

    elif event_type == "element_search":
        for key in ("missing_target", "suggested_affordance", "reason", "vision_response"):
            val = data.get(key)
            if val:
                parts.append(f"  {key}: {val}")

    elif event_type == "step_complete":
        for key in ("method", "evidence", "reflection_hint", "suggested_element", "reflection_observed"):
            val = data.get(key)
            if val:
                parts.append(f"  {key}: {val}")

    elif event_type in ("verify_pass", "verify_fail", "verify_escalate"):
        parts.append(f"  detail: {data}") if data else None

    elif event_type == "step_start":
        params = data.get("params")
        if params:
            parts.append(f"  params: {params}")

    elif event_type in ("narrate_intent", "narrate_observe"):
        app = data.get("app")
        url = data.get("url")
        if app:
            parts.append(f"  app: {app}")
        if url:
            parts.append(f"  url: {url[:80]}")

    elif event_type == "action_start":
        pass  # message already contains full info

    elif event_type in ("step_retry", "step_replan"):
        for key in ("strategy", "reason"):
            val = data.get(key)
            if val:
                parts.append(f"  {key}: {val}")

    elif event_type == "task_summary":
        # Show key summary metrics from run report
        for key in (
            "total_duration_ms", "step_count", "replan_count", "skill_name",
        ):
            val = data.get(key)
            if val is not None:
                parts.append(f"  {key}: {val}")
        llm_calls = data.get("llm_calls")
        if llm_calls:
            parts.append(f"  llm_calls: {len(llm_calls)}")
            for i, call in enumerate(llm_calls[:5], 1):
                parts.append(
                    f"    {i}. {call.get('purpose', '?')}"
                    f" ({call.get('model', '?')})"
                    f" {call.get('duration_ms', 0)}ms"
                )
        verification = data.get("verification")
        if verification:
            parts.append(
                f"  verification: T0={verification.get('tier0_count', 0)}"
                f" T1={verification.get('tier1_count', 0)}"
                f" T2={verification.get('tier2_count', 0)}"
            )

    return "\n".join(parts)


def build_status_overlay_command(
    events_file: Path,
    parent_pid: int,
    config: AgentConfig,
) -> list[str]:
    """Build the subprocess command for the floating overlay."""
    cmd = [
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
    if config.verbose_overlay:
        cmd.append("--verbose")
    return cmd


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
    if event_type == "narrate_intent":
        return f"💭 {message[:60]}"
    if event_type == "narrate_observe":
        return f"👁 {message[:60]}"
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
    if event_type == "task_summary":
        return "Run Summary"
    return message[:80]


def load_status_snapshot(
    line: str, verbose: bool = False
) -> Optional[StatusSnapshot]:
    """Parse one JSONL event line into a status snapshot."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return format_status_event(event, verbose=verbose)
