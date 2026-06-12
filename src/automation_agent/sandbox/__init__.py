"""Sandbox backend — run the agent against a Docker X11 desktop.

The agent's planner/vision/orchestrator run on the host as usual, but all
actuation (clicks, typing, app launches) and screen capture target a
containerized Linux desktop instead of the live macOS desktop. This lets
e2e automation runs execute without touching the user's screen, mouse,
or keyboard.

Components:
- SandboxActuator   — Actuator protocol via `docker exec xdotool ...`
- SandboxScreenCapture — screenshots via `docker exec scrot`
- CdpClient         — browser state (URL, title, focused value) via the
                      Chrome DevTools Protocol, giving Tier-1 verification
                      parity with the AppleScript actuator's JS injection.
"""

from automation_agent.sandbox.capture import SandboxScreenCapture
from automation_agent.sandbox.cdp import CdpClient
from automation_agent.sandbox.docker_actuator import SandboxActuator

__all__ = ["SandboxActuator", "SandboxScreenCapture", "CdpClient"]
