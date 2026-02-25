"""Actuator component — executes desktop actions via Hammerspoon, HTTP bridge, or AppleScript.

Priority order:
1. HammerspoonBridgeActuator (HTTP server in hs.claude — fastest, most capable)
2. HammerspoonActuator (hs CLI — requires working IPC)
3. AppleScriptActuator (osascript — always available on macOS)
"""

from automation_agent.actuator.actuator import HammerspoonActuator
from automation_agent.actuator.applescript_actuator import AppleScriptActuator
from automation_agent.actuator.bridge_actuator import HammerspoonBridgeActuator
from automation_agent.actuator.models import ActuatorResult


def create_actuator(config=None):
    """Create the best available actuator.

    Priority: HTTP bridge > hs CLI > AppleScript.
    """
    # Try hs.claude HTTP bridge first (fastest, most capable)
    bridge = HammerspoonBridgeActuator(config)
    if bridge.is_available():
        return bridge

    # Try hs CLI
    hs = HammerspoonActuator(config)
    if hs.is_available():
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


__all__ = [
    "HammerspoonActuator",
    "HammerspoonBridgeActuator",
    "AppleScriptActuator",
    "ActuatorResult",
    "create_actuator",
]
