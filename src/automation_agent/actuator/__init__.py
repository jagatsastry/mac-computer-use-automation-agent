"""Actuator component — executes desktop actions via AppleScript.

Uses osascript (AppleScript / JXA) for all desktop automation.
"""

from automation_agent.actuator.applescript_actuator import AppleScriptActuator
from automation_agent.actuator.models import ActuatorResult


def create_actuator(config=None):
    """Create the configured actuator backend.

    Default is the AppleScript actuator (live macOS desktop). With
    actuator_backend='sandbox', actions target the Docker X11 sandbox
    instead — used for e2e testing without touching the host desktop.
    """
    if config is not None and getattr(config, "actuator_backend", "applescript") == "sandbox":
        from automation_agent.sandbox.docker_actuator import SandboxActuator

        return SandboxActuator(config)
    return AppleScriptActuator(config)


__all__ = [
    "AppleScriptActuator",
    "ActuatorResult",
    "create_actuator",
]
