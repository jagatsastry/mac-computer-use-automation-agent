"""Actuator component — executes desktop actions via AppleScript.

Uses osascript (AppleScript / JXA) for all desktop automation.
"""

from automation_agent.actuator.applescript_actuator import AppleScriptActuator
from automation_agent.actuator.models import ActuatorResult


def create_actuator(config=None):
    """Create an AppleScript-based actuator."""
    return AppleScriptActuator(config)


__all__ = [
    "AppleScriptActuator",
    "ActuatorResult",
    "create_actuator",
]
