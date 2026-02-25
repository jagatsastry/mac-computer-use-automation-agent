"""Planner-specific data models.

Re-exports shared models and defines any planner-specific types.
"""

from dataclasses import dataclass, field
from typing import Optional

from automation_agent.shared_models import ActionPlan, ActionStep, StepResult

__all__ = ["ActionPlan", "ActionStep", "StepResult", "PlannerConfig"]


@dataclass
class PlannerConfig:
    """Planner-specific configuration options.

    These supplement the main AgentConfig with planner-tuning parameters.
    """

    max_plan_steps: int = 20
    require_verify: bool = True
    default_on_fail: str = "retry_different"
    planning_timeout_ms: int = 30000
    prompt_template_dir: Optional[str] = None
