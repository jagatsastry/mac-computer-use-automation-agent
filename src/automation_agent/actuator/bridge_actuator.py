"""Hammerspoon Bridge Actuator — communicates with hs.claude via HTTP server.

Uses the hs.claude.server HTTP bridge running inside Hammerspoon on port 27741.
This gives us access to Hammerspoon's native eventtap, application, and screen
APIs without the IPC timeout issues of the `hs` CLI.
"""

import json
from typing import Any, Dict, List, Optional

import httpx

from automation_agent.actuator.models import ActuatorResult
from automation_agent.config import AgentConfig


class HammerspoonBridgeActuator:
    """Executes desktop actions via hs.claude HTTP server bridge.

    The bridge runs inside Hammerspoon on localhost:27741 and provides
    synchronous JSON request/response for all hs.claude actions.
    """

    DEFAULT_PORT = 27741
    TIMEOUT_SECONDS = 15

    def __init__(self, config: Optional[AgentConfig] = None, port: int = 0):
        self._port = port or self.DEFAULT_PORT
        self._base_url = f"http://localhost:{self._port}"
        self._timeout = self.TIMEOUT_SECONDS

    def is_available(self) -> bool:
        """Check if the hs.claude HTTP bridge is running."""
        try:
            with httpx.Client(timeout=3) as client:
                resp = client.get(f"{self._base_url}/health")
                data = resp.json()
                return data.get("status") == "ok"
        except Exception:
            return False

    def _call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Call an hs.claude method via the HTTP bridge.

        Args:
            method: The hs.claude method to invoke (e.g., "click", "execute").
            params: Parameters to pass to the method.

        Returns:
            Dict with 'success', 'result', and optionally 'error'.
        """
        payload = {"method": method}
        if params:
            payload["params"] = params
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(self._base_url, json=payload)
                resp.raise_for_status()
                return resp.json()
        except httpx.TimeoutException:
            return {"success": False, "error": f"Bridge timeout after {self._timeout}s"}
        except httpx.ConnectError:
            return {"success": False, "error": "Cannot connect to Hammerspoon bridge (is it running?)"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def click(self, x: int, y: int) -> Dict[str, Any]:
        result = self._call("click", {"x": x, "y": y})
        return ActuatorResult(
            success=result.get("success", False),
            output=result.get("result", ""),
            error=result.get("error"),
        ).to_dict()

    def type_text(self, text: str) -> Dict[str, Any]:
        result = self._call("typeText", {"text": text})
        return ActuatorResult(
            success=result.get("success", False),
            output=result.get("result", ""),
            error=result.get("error"),
        ).to_dict()

    def press_key(self, keys: List[str]) -> Dict[str, Any]:
        # Separate modifiers from the main key
        mod_names = {"cmd", "command", "alt", "option", "shift", "ctrl", "control", "fn"}
        modifiers = []
        key = None
        for k in keys:
            if k.lower() in mod_names:
                modifiers.append(k.lower())
            else:
                key = k

        if not key:
            return ActuatorResult(
                success=False, error="No key specified, only modifiers"
            ).to_dict()

        result = self._call("pressKey", {"key": key, "modifiers": modifiers})
        return ActuatorResult(
            success=result.get("success", False),
            output=result.get("result", ""),
            error=result.get("error"),
        ).to_dict()

    def activate_app(self, app_name: str) -> Dict[str, Any]:
        result = self._call("activateApp", {"appName": app_name})
        return ActuatorResult(
            success=result.get("success", False),
            output=result.get("result", ""),
            error=result.get("error"),
        ).to_dict()

    def open_url(self, url: str) -> Dict[str, Any]:
        result = self._call("openUrl", {"url": url})
        return ActuatorResult(
            success=result.get("success", False),
            output=result.get("result", ""),
            error=result.get("error"),
        ).to_dict()

    def quit_app(self, app_name: str) -> Dict[str, Any]:
        result = self._call("quitApp", {"appName": app_name})
        return ActuatorResult(
            success=result.get("success", False),
            output=result.get("result", ""),
            error=result.get("error"),
        ).to_dict()

    def get_state(self) -> Dict[str, Any]:
        """Get current desktop state (frontmost app, window title, etc.)."""
        result = self._call("getState")
        if not result.get("success"):
            return {
                "app_name": "",
                "app_bundle": "",
                "window_title": "",
            }
        state = result.get("result", {})
        if isinstance(state, str):
            try:
                state = json.loads(state)
            except json.JSONDecodeError:
                return {
                    "app_name": "",
                    "app_bundle": "",
                    "window_title": "",
                    "raw": state,
                }
        return {
            "app_name": state.get("app_name", state.get("appName", "")),
            "app_bundle": state.get("app_bundle", state.get("appBundle", "")),
            "window_title": state.get("window_title", state.get("windowTitle", "")),
        }

    # --- Extended hs.claude capabilities (beyond basic actuator) ---

    def describe_screen(self) -> str:
        """Use hs.claude vision to describe the current screen."""
        result = self._call("describe")
        if result.get("success"):
            return result.get("result", "")
        return ""

    def find_element(self, description: str) -> Optional[Dict[str, Any]]:
        """Use hs.claude vision to find a UI element by description.

        Returns dict with coordinate systems or None if not found.
        """
        result = self._call("findElement", {"description": description})
        if result.get("success") and result.get("result"):
            coords = result["result"]
            return {
                "x": coords.get("logicalX", 0),
                "y": coords.get("logicalY", 0),
                "raw_response": str(coords),
            }
        return None

    def check_condition(self, condition: str) -> bool:
        """Use hs.claude vision to check if a condition is true on screen."""
        result = self._call("check", {"condition": condition})
        return result.get("success", False) and result.get("result", False)

    def execute_goal(self, command: str) -> Dict[str, Any]:
        """Use hs.claude's full agentic loop to execute a natural language command.

        This delegates the entire observe/think/act/check loop to Hammerspoon,
        bypassing our Python orchestrator entirely.
        """
        result = self._call("execute", {"command": command})
        return result
