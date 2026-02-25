"""Event data models for structured logging."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


class EventType(str, Enum):
    """Types of events logged during agent execution."""

    # Planning events
    PLAN_START = "plan_start"
    PLAN_COMPLETE = "plan_complete"
    PLAN_ERROR = "plan_error"
    REPLAN_START = "replan_start"
    REPLAN_COMPLETE = "replan_complete"

    # Skill events
    SKILL_MATCH = "skill_match"
    SKILL_EXPAND = "skill_expand"
    SKILL_NO_MATCH = "skill_no_match"

    # Vision events
    SCREENSHOT_CAPTURE = "screenshot_capture"
    ELEMENT_SEARCH = "element_search"
    ELEMENT_FOUND = "element_found"
    ELEMENT_NOT_FOUND = "element_not_found"
    SCREEN_DESCRIBE = "screen_describe"

    # Actuator events
    ACTION_START = "action_start"
    ACTION_COMPLETE = "action_complete"
    ACTION_ERROR = "action_error"

    # Verification events
    VERIFY_START = "verify_start"
    VERIFY_PASS = "verify_pass"
    VERIFY_FAIL = "verify_fail"
    VERIFY_ESCALATE = "verify_escalate"

    # Orchestration events
    TASK_START = "task_start"
    TASK_COMPLETE = "task_complete"
    TASK_FAIL = "task_fail"
    STEP_START = "step_start"
    STEP_COMPLETE = "step_complete"
    STEP_RETRY = "step_retry"
    STEP_REPLAN = "step_replan"

    # System events
    AGENT_INIT = "agent_init"
    USER_WAIT = "user_wait"
    USER_RESUME = "user_resume"


@dataclass
class Event:
    """A single structured event in the agent execution trace."""

    event_type: EventType
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    data: Dict[str, Any] = field(default_factory=dict)
    screenshot_path: Optional[str] = None
    duration_ms: Optional[int] = None
    step_index: Optional[int] = None
    run_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for JSON logging."""
        result = {
            "event_type": self.event_type.value,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "run_id": self.run_id,
        }
        if self.data:
            result["data"] = self.data
        if self.screenshot_path:
            result["screenshot_path"] = self.screenshot_path
        if self.duration_ms is not None:
            result["duration_ms"] = self.duration_ms
        if self.step_index is not None:
            result["step_index"] = self.step_index
        return result
