"""Hammerspoon Bridge Actuator — communicates with hs.claude via HTTP server.

Uses the hs.claude.server HTTP bridge running inside Hammerspoon on port 27741.
This gives us access to Hammerspoon's native eventtap, application, and screen
APIs plus Claude-powered vision capabilities.

API surface:
  - GET  /health           → health check
  - GET  /state            → frontmost app + screen info
  - POST /action           → synchronous action dispatch (click, type, key, etc.)
  - POST /describe         → async vision: describe screen (returns taskId, poll)
  - POST /findElement      → async vision: find UI element (returns taskId, poll)
  - POST /check            → async vision: check condition (returns taskId, poll)
  - POST /execute          → async agentic loop (returns taskId, poll)
  - GET  /task/{id}        → poll async task result
"""

import time
from typing import Any, Dict, List, Optional

import httpx

from automation_agent.actuator.models import ActuatorResult
from automation_agent.config import AgentConfig


class HammerspoonBridgeActuator:
    """Executes desktop actions via hs.claude HTTP server bridge.

    The bridge runs inside Hammerspoon on localhost:27741 and provides
    both synchronous action execution and async vision-powered operations.
    """

    DEFAULT_PORT = 27741
    TIMEOUT_SECONDS = 15
    POLL_INTERVAL = 0.5   # seconds between task polls
    POLL_TIMEOUT = 30     # max seconds to wait for async task

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

    def _action(self, action: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute a synchronous action via POST /action.

        Args:
            action: The action name (e.g., "click", "type_text", "activate_app").
            params: Parameters for the action.

        Returns:
            Dict with 'success' and optionally 'error'.
        """
        payload: Dict[str, Any] = {"action": action}
        if params:
            payload["params"] = params
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(f"{self._base_url}/action", json=payload)
                resp.raise_for_status()
                return resp.json()
        except httpx.TimeoutException:
            return {"success": False, "error": f"Bridge timeout after {self._timeout}s"}
        except httpx.ConnectError:
            return {"success": False, "error": "Cannot connect to Hammerspoon bridge (is it running?)"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _async_call(self, endpoint: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Call an async vision endpoint and poll for result.

        Args:
            endpoint: The endpoint path (e.g., "/describe", "/findElement").
            payload: JSON body to send.

        Returns:
            Dict with 'success', 'result', and optionally 'error'.
        """
        try:
            with httpx.Client(timeout=self._timeout) as client:
                if payload:
                    resp = client.post(f"{self._base_url}{endpoint}", json=payload)
                else:
                    resp = client.post(f"{self._base_url}{endpoint}")
                resp.raise_for_status()
                data = resp.json()

                if not data.get("success"):
                    return data

                task_id = data.get("taskId")
                if not task_id:
                    return data  # Synchronous response (no polling needed)

                # Poll for completion
                return self._poll_task(client, task_id)
        except httpx.TimeoutException:
            return {"success": False, "error": f"Bridge timeout after {self._timeout}s"}
        except httpx.ConnectError:
            return {"success": False, "error": "Cannot connect to Hammerspoon bridge (is it running?)"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _poll_task(self, client: httpx.Client, task_id: str) -> Dict[str, Any]:
        """Poll GET /task/{id} until completion or timeout."""
        deadline = time.monotonic() + self.POLL_TIMEOUT
        while time.monotonic() < deadline:
            try:
                resp = client.get(f"{self._base_url}/task/{task_id}")
                resp.raise_for_status()
                data = resp.json()
                status = data.get("status")
                if status in ("completed", "failed"):
                    return data
                time.sleep(self.POLL_INTERVAL)
            except Exception as e:
                return {"success": False, "error": f"Poll error: {e}"}
        return {"success": False, "error": f"Task {task_id} timed out after {self.POLL_TIMEOUT}s"}

    def click(self, x: int, y: int) -> Dict[str, Any]:
        result = self._action("click", {"x": x, "y": y})
        return ActuatorResult(
            success=result.get("success", False),
            output=str(result.get("result", "")),
            error=result.get("error"),
        ).to_dict()

    def type_text(self, text: str) -> Dict[str, Any]:
        result = self._action("type_text", {"text": text})
        return ActuatorResult(
            success=result.get("success", False),
            output=str(result.get("result", "")),
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

        result = self._action("press_key", {"key": key, "modifiers": modifiers})
        return ActuatorResult(
            success=result.get("success", False),
            output=str(result.get("result", "")),
            error=result.get("error"),
        ).to_dict()

    def activate_app(self, app_name: str) -> Dict[str, Any]:
        result = self._action("activate_app", {"appName": app_name})
        return ActuatorResult(
            success=result.get("success", False),
            output=str(result.get("result", "")),
            error=result.get("error"),
        ).to_dict()

    def open_url(self, url: str) -> Dict[str, Any]:
        result = self._action("open_url", {"url": url})
        return ActuatorResult(
            success=result.get("success", False),
            output=str(result.get("result", "")),
            error=result.get("error"),
        ).to_dict()

    def quit_app(self, app_name: str) -> Dict[str, Any]:
        result = self._action("quit_app", {"appName": app_name})
        return ActuatorResult(
            success=result.get("success", False),
            output=str(result.get("result", "")),
            error=result.get("error"),
        ).to_dict()

    def get_state(self) -> Dict[str, Any]:
        """Get current desktop state via GET /state."""
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.get(f"{self._base_url}/state")
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return {
                "app_name": "",
                "app_bundle": "",
                "window_title": "",
            }

        if not data.get("success"):
            return {
                "app_name": "",
                "app_bundle": "",
                "window_title": "",
            }

        result = data.get("result", {})
        return {
            "app_name": result.get("frontmostApp", ""),
            "app_bundle": "",  # Not available from /state endpoint
            "window_title": "",  # Not available from /state endpoint
        }

    # --- Extended hs.claude capabilities (async, vision-powered) ---

    def describe_screen(self) -> str:
        """Use hs.claude vision to describe the current screen."""
        result = self._async_call("/describe")
        if result.get("success"):
            r = result.get("result", {})
            if isinstance(r, dict):
                return r.get("description", "")
            return str(r)
        return ""

    def find_element(self, description: str) -> Optional[Dict[str, Any]]:
        """Use hs.claude vision to find a UI element by description.

        Returns dict with coordinate systems or None if not found.
        """
        result = self._async_call("/findElement", {"description": description})
        if result.get("success") and result.get("result"):
            coords = result["result"]
            # Server wraps in {"coordinates": {...}}
            if isinstance(coords, dict) and "coordinates" in coords:
                coords = coords["coordinates"]
            return {
                "x": coords.get("logicalX", 0),
                "y": coords.get("logicalY", 0),
                "raw_response": str(coords),
            }
        return None

    def check_condition(self, condition: str) -> bool:
        """Use hs.claude vision to check if a condition is true on screen."""
        result = self._async_call("/check", {"condition": condition})
        if result.get("success") and result.get("result"):
            r = result["result"]
            if isinstance(r, dict):
                return r.get("conditionMet", False)
            return bool(r)
        return False

    def execute_goal(self, command: str) -> Dict[str, Any]:
        """Use hs.claude's full agentic loop to execute a natural language command.

        This delegates the entire observe/think/act/check loop to Hammerspoon,
        bypassing our Python orchestrator entirely.
        """
        result = self._async_call("/execute", {"command": command})
        return result
