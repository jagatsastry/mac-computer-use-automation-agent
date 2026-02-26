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
        self,
        screenshot_b64: Optional[str] = None,
        hammerspoon_state: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Describe screen using hs.claude.describe, optionally merging Hammerspoon state.

        The BridgeCoordinator already gets state from Hammerspoon natively,
        but if hammerspoon_state is explicitly provided, it is prepended
        to the vision description for consistency with ScreenCoordinatorImpl.
        """
        vision_description = self._bridge.describe_screen()

        if hammerspoon_state:
            app_name = hammerspoon_state.get("app_name", "Unknown")
            window_title = hammerspoon_state.get("window_title", "Unknown")
            return (
                f"Frontmost app: {app_name} (window: '{window_title}'). "
                f"{vision_description}"
            )

        return vision_description

    async def verify_condition(
        self, condition: str, screenshot_b64: Optional[str] = None
    ) -> bool:
        """Check condition using hs.claude.check."""
        return self._bridge.check_condition(condition)

    async def capture_screenshot(self) -> str:
        """Capture screenshot via bridge.

        Raises NotImplementedError because the hs.claude.server does not
        support raw screenshot capture. Use describe_screen() for vision instead.
        """
        raise NotImplementedError(
            "BridgeCoordinator does not support raw screenshot capture. "
            "Use describe_screen() for vision-based screen analysis."
        )
