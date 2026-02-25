"""Screen coordinator that delegates to hs.claude's vision via HTTP bridge.

Instead of capturing screenshots and calling Claude API from Python,
this coordinator delegates to Hammerspoon's built-in vision pipeline
which handles screen capture, compression, coordinate transformation,
and Retina display handling natively.
"""

from typing import Any, Dict, Optional

from automation_agent.actuator.bridge_actuator import HammerspoonBridgeActuator


class BridgeCoordinator:
    """Screen coordinator using hs.claude HTTP bridge for vision tasks.

    Delegates to Hammerspoon's native screen capture and Claude API integration,
    avoiding the need for separate Python-side screenshot capture and API calls.
    """

    def __init__(self, bridge: HammerspoonBridgeActuator):
        self._bridge = bridge

    async def find_element(
        self, description: str, screenshot_b64: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Find UI element using hs.claude.findElement.

        The screenshot_b64 parameter is ignored — Hammerspoon captures its own.
        """
        return self._bridge.find_element(description)

    async def describe_screen(
        self, screenshot_b64: Optional[str] = None
    ) -> str:
        """Describe screen using hs.claude.describe."""
        return self._bridge.describe_screen()

    async def verify_condition(
        self, condition: str, screenshot_b64: Optional[str] = None
    ) -> bool:
        """Check condition using hs.claude.check."""
        return self._bridge.check_condition(condition)

    async def capture_screenshot(self) -> str:
        """Capture screenshot via bridge.

        The hs.claude.server does not support raw screenshot capture.
        Returns empty string — use describe_screen() for vision instead.
        """
        return ""
