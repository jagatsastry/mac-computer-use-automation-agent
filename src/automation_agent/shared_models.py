"""Shared data models used across all components of the automation agent."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class ActionStep:
    """A single action step in an execution plan.

    Every step MUST have a non-empty 'verify' field describing
    the expected screen state after the step executes.
    """

    action: str  # "click", "type_text", "press_key", "open_url", "activate_app", "observe", "wait_for_user", "done"
    params: Dict[str, Any] = field(default_factory=dict)
    verify: str = ""  # MANDATORY — what must be true after this step
    on_fail: str = "retry_different"  # "retry_different" | "replan" | "abort" | "wait_for_user"
    max_retries: int = 3

    def __post_init__(self) -> None:
        valid_actions = {
            "click",
            "type_text",
            "press_key",
            "open_url",
            "activate_app",
            "quit_app",
            "observe",
            "wait_for_user",
            "done",
        }
        if self.action and self.action not in valid_actions:
            raise ValueError(
                f"Unknown action '{self.action}'. Valid actions: {sorted(valid_actions)}"
            )
        valid_on_fail = {"retry_different", "replan", "abort", "wait_for_user"}
        if self.on_fail not in valid_on_fail:
            raise ValueError(
                f"Unknown on_fail '{self.on_fail}'. Valid: {sorted(valid_on_fail)}"
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ActionStep":
        """Create ActionStep from dictionary (e.g., parsed LLM JSON)."""
        return cls(
            action=data.get("action", ""),
            params=data.get("params", {}),
            verify=data.get("verify", ""),
            on_fail=data.get("on_fail", "retry_different"),
            max_retries=data.get("max_retries", 3),
        )


@dataclass
class ActionPlan:
    """A plan consisting of ordered action steps."""

    steps: List[ActionStep]
    goal: str = ""
    skill_name: Optional[str] = None
    raw_llm_response: Optional[str] = None
    planning_duration_ms: int = 0
    token_usage: Optional[Dict[str, int]] = None

    def validate(self) -> List[str]:
        """Validate the plan. Returns list of error messages (empty if valid)."""
        errors = []
        if not self.steps:
            errors.append("Plan has no steps")
        for i, step in enumerate(self.steps):
            if not step.verify and step.action not in ("done", "wait_for_user"):
                errors.append(
                    f"Step {i} ({step.action}) has empty 'verify' field — "
                    "every step must have a postcondition"
                )
        return errors


@dataclass
class StepResult:
    """Result of executing and verifying a single action step."""

    step: ActionStep
    success: bool
    verification_method: str = ""  # "accessibility" | "hammerspoon_state" | "vision" | "both"
    evidence: str = ""  # MANDATORY — what was observed
    error: Optional[str] = None
    duration_ms: int = 0
    screenshot_path: Optional[str] = None
    retry_count: int = 0
    retry_strategies_used: List[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        valid_methods = {"", "accessibility", "hammerspoon_state", "vision", "both"}
        if self.verification_method not in valid_methods:
            raise ValueError(
                f"Unknown verification_method '{self.verification_method}'. "
                f"Valid: {sorted(valid_methods)}"
            )


@dataclass
class ExecutionResult:
    """Result of executing an entire task (all steps)."""

    success: bool
    message: str = ""
    steps: List[StepResult] = field(default_factory=list)
    error: Optional[str] = None
    total_duration_ms: int = 0
    iterations: int = 0
    goal: str = ""
    run_id: str = ""
