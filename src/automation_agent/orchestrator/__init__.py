"""Orchestrator package for automation agent.

Exports the new component-based AutomationAgent and StepVerifier,
plus legacy model types for backward compatibility.
"""

from automation_agent.orchestrator.agent import AutomationAgent
from automation_agent.orchestrator.verifier import StepVerifier
from automation_agent.orchestrator.models import (
    ActionStep,
    Intent,
    Observation,
    ExecutionResult,
    ActionResult,
    NextAction,
    Coordinates,
)

__all__ = [
    # New components
    "AutomationAgent",
    "StepVerifier",
    # Legacy model types (backward compat)
    "ActionStep",
    "Intent",
    "Observation",
    "ExecutionResult",
    "ActionResult",
    "NextAction",
    "Coordinates",
]
