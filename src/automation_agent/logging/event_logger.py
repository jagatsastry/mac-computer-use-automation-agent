"""EventLogger — structured event logging with JSONL traces and screenshots."""

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from automation_agent.logging.models import Event, EventType


class EventLogger:
    """Logs structured events to JSONL files with optional screenshot saving.

    Each run gets a unique run_id and writes to:
      {log_dir}/{run_id}/events.jsonl   — structured events
      {log_dir}/{run_id}/trace.md       — human-readable trace
      {log_dir}/{run_id}/screenshots/   — saved screenshots
    """

    def __init__(self, log_dir: Path, run_id: Optional[str] = None) -> None:
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.log_dir = log_dir
        self.run_dir = log_dir / self.run_id
        self.events_file = self.run_dir / "events.jsonl"
        self.trace_file = self.run_dir / "trace.md"
        self.screenshots_dir = self.run_dir / "screenshots"
        self._events: List[Event] = []
        self._initialized = False

    def _ensure_dirs(self) -> None:
        """Create output directories on first write."""
        if not self._initialized:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            self.screenshots_dir.mkdir(exist_ok=True)
            # Write trace header
            with open(self.trace_file, "w") as f:
                f.write(f"# Execution Trace — {self.run_id}\n\n")
                f.write(f"**Started:** {datetime.now().isoformat()}\n\n")
                f.write("---\n\n")
            self._initialized = True

    def log(self, event: Event) -> None:
        """Log an event to memory, JSONL file, and human-readable trace."""
        event.run_id = self.run_id
        self._events.append(event)
        self._ensure_dirs()
        self._write_jsonl(event)
        self._write_trace(event)

    def log_event(
        self,
        event_type: EventType,
        message: str,
        data: Optional[dict] = None,
        screenshot_path: Optional[str] = None,
        duration_ms: Optional[int] = None,
        step_index: Optional[int] = None,
    ) -> Event:
        """Convenience method to create and log an event in one call."""
        event = Event(
            event_type=event_type,
            message=message,
            data=data or {},
            screenshot_path=screenshot_path,
            duration_ms=duration_ms,
            step_index=step_index,
        )
        self.log(event)
        return event

    def save_screenshot(self, image_data: bytes, name: str) -> str:
        """Save screenshot bytes to the screenshots directory.

        Returns the path to the saved file.
        """
        self._ensure_dirs()
        timestamp = datetime.now().strftime("%H%M%S")
        filename = f"{timestamp}_{name}"
        if not filename.endswith((".png", ".jpg", ".jpeg")):
            filename += ".png"
        path = self.screenshots_dir / filename
        path.write_bytes(image_data)
        return str(path)

    def save_screenshot_file(self, source_path: str, name: str) -> str:
        """Copy an existing screenshot file into the run's screenshots directory.

        Returns the path to the saved copy.
        """
        self._ensure_dirs()
        timestamp = datetime.now().strftime("%H%M%S")
        src = Path(source_path)
        filename = f"{timestamp}_{name}{src.suffix}"
        dest = self.screenshots_dir / filename
        shutil.copy2(source_path, dest)
        return str(dest)

    @property
    def events(self) -> List[Event]:
        """All events logged in this run."""
        return list(self._events)

    def _write_jsonl(self, event: Event) -> None:
        """Append event as JSON line to events.jsonl."""
        with open(self.events_file, "a") as f:
            f.write(json.dumps(event.to_dict()) + "\n")

    def _write_trace(self, event: Event) -> None:
        """Append human-readable trace entry to trace.md."""
        ts = event.timestamp.strftime("%H:%M:%S.%f")[:-3]
        step_prefix = f"[Step {event.step_index}] " if event.step_index is not None else ""
        duration_suffix = f" ({event.duration_ms}ms)" if event.duration_ms else ""

        line = f"**{ts}** {step_prefix}`{event.event_type.value}` — {event.message}{duration_suffix}\n"

        if event.screenshot_path:
            line += f"  - Screenshot: `{event.screenshot_path}`\n"
        if event.data:
            for key, value in event.data.items():
                val_str = str(value)
                if len(val_str) > 200:
                    val_str = val_str[:200] + "..."
                line += f"  - {key}: {val_str}\n"

        line += "\n"

        with open(self.trace_file, "a") as f:
            f.write(line)

    def finalize(self, success: bool, message: str = "") -> None:
        """Write final summary to trace file."""
        self._ensure_dirs()
        with open(self.trace_file, "a") as f:
            f.write("---\n\n")
            status = "SUCCESS" if success else "FAILURE"
            f.write(f"## Result: {status}\n\n")
            if message:
                f.write(f"{message}\n\n")
            f.write(f"**Ended:** {datetime.now().isoformat()}\n")
            f.write(f"**Total events:** {len(self._events)}\n")
