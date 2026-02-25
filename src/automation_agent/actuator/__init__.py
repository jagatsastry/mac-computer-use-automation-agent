"""Actuator component — executes desktop actions via Hammerspoon or AppleScript fallback."""

from automation_agent.actuator.actuator import HammerspoonActuator
from automation_agent.actuator.applescript_actuator import AppleScriptActuator
from automation_agent.actuator.models import ActuatorResult


def create_actuator(config=None):
    """Create the best available actuator. Hammerspoon preferred, AppleScript fallback."""
    hs = HammerspoonActuator(config)
    if hs.is_available():
        # Quick liveness check — try to get state within 3s
        import subprocess
        try:
            result = subprocess.run(
                [hs._hs_path, "-c", 'print("ok")'],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0 and "ok" in result.stdout:
                return hs
        except (subprocess.TimeoutExpired, Exception):
            pass

    # Fallback to AppleScript
    return AppleScriptActuator(config)


__all__ = ["HammerspoonActuator", "AppleScriptActuator", "ActuatorResult", "create_actuator"]
