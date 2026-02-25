"""Orchestrator data models.

Provides backward compatibility with old orchestrator model types while
re-exporting the new shared models used by the component architecture.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

# Re-export shared models for backward compatibility
from automation_agent.shared_models import (
    ActionStep as NewActionStep,
    ActionPlan,
    StepResult,
    ExecutionResult as NewExecutionResult,
)


# --------------------------------------------------------------------------
# Legacy model types preserved for backward compatibility
# --------------------------------------------------------------------------


@dataclass
class Coordinates:
    """Screen coordinates for UI element interaction."""

    x: int
    y: int
    width: Optional[int] = None
    height: Optional[int] = None

    @property
    def center_x(self) -> int:
        """Get center X coordinate."""
        if self.width:
            return self.x + self.width // 2
        return self.x

    @property
    def center_y(self) -> int:
        """Get center Y coordinate."""
        if self.height:
            return self.y + self.height // 2
        return self.y

    @classmethod
    def from_bbox(cls, x1: int, y1: int, x2: int, y2: int) -> "Coordinates":
        """Create from bounding box coordinates."""
        return cls(x=x1, y=y1, width=x2 - x1, height=y2 - y1)


@dataclass
class ActionStep:
    """A single action step parsed from user intent (legacy model)."""

    action: str
    params: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ActionStep":
        """Create ActionStep from dictionary."""
        return cls(action=data.get("action", ""), params=data.get("params", {}))


@dataclass
class Intent:
    """Parsed intent from user's natural language command."""

    steps: List[ActionStep]
    raw_prompt: str
    requires_observation: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, Any], raw_prompt: str = "") -> "Intent":
        """Create Intent from parsed JSON dictionary."""
        steps = [ActionStep.from_dict(s) for s in data.get("steps", [])]
        requires_observation = data.get("requires_observation", False)
        return cls(
            steps=steps, raw_prompt=raw_prompt, requires_observation=requires_observation
        )


@dataclass
class Observation:
    """Screen observation from vision model."""

    screenshot_b64: str
    description: str
    timestamp: datetime = field(default_factory=datetime.now)
    elements: Optional[List[Dict[str, Any]]] = None

    def __str__(self) -> str:
        """String representation for history."""
        return f"[Observation at {self.timestamp.strftime('%H:%M:%S')}]: {self.description}"


@dataclass
class ActionResult:
    """Result of executing a single action."""

    success: bool
    action: str
    params: Dict[str, Any] = field(default_factory=dict)
    output: str = ""
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)

    def __str__(self) -> str:
        """String representation for history."""
        status = "SUCCESS" if self.success else "FAILED"
        return f"[Action {status}]: {self.action}({self.params})"


@dataclass
class ExecutionResult:
    """Result of executing an entire task (legacy model)."""

    success: bool
    message: str = ""
    steps: List[ActionResult] = field(default_factory=list)
    error: Optional[str] = None
    iterations: int = 0


@dataclass
class NextAction:
    """Next action determined by the agent."""

    action: str
    params: Dict[str, Any] = field(default_factory=dict)
    reasoning: str = ""
    is_complete: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NextAction":
        """Create NextAction from parsed JSON dictionary."""
        if data.get("complete", False):
            return cls(action="", is_complete=True, reasoning=data.get("reasoning", ""))
        return cls(
            action=data.get("action", ""),
            params=data.get("params", {}),
            reasoning=data.get("reasoning", ""),
            is_complete=False,
        )

    @classmethod
    def complete(cls, reasoning: str = "") -> "NextAction":
        """Create a completion marker."""
        return cls(action="", is_complete=True, reasoning=reasoning)


@dataclass
class HistoryEntry:
    """Entry in the agent's action history."""

    entry_type: str  # "observation" or "action"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    raw_data: Optional[Any] = None

    @classmethod
    def from_observation(cls, observation: Observation) -> "HistoryEntry":
        """Create history entry from observation."""
        return cls(
            entry_type="observation",
            content=str(observation),
            timestamp=observation.timestamp,
            raw_data=observation,
        )

    @classmethod
    def from_action(cls, result: ActionResult) -> "HistoryEntry":
        """Create history entry from action result."""
        return cls(
            entry_type="action",
            content=str(result),
            timestamp=result.timestamp,
            raw_data=result,
        )
