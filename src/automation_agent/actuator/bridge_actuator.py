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

import logging
import subprocess
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

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

    # AppleScript key code map for special keys
    _KEY_CODES = {
        "return": 36, "enter": 76, "tab": 48, "space": 49,
        "delete": 51, "escape": 53, "up": 126, "down": 125,
        "left": 123, "right": 124,
    }
    _MOD_MAP = {
        "cmd": "command down", "command": "command down",
        "shift": "shift down", "alt": "option down", "option": "option down",
        "ctrl": "control down", "control": "control down",
        "fn": "fn down",
    }

    ACCESSIBILITY_CACHE_TTL = 60  # seconds before re-checking accessibility

    def __init__(self, config: Optional[AgentConfig] = None, port: int = 0):
        self._port = port or self.DEFAULT_PORT
        self._base_url = f"http://localhost:{self._port}"
        self._timeout = self.TIMEOUT_SECONDS
        self._accessibility: Optional[bool] = None  # cached
        self._accessibility_checked_at: float = 0.0

    def is_available(self) -> bool:
        """Check if the hs.claude HTTP bridge is running."""
        try:
            with httpx.Client(timeout=3) as client:
                resp = client.get(f"{self._base_url}/health")
                data = resp.json()
                return data.get("status") == "ok"
        except Exception:
            return False

    def has_accessibility(self) -> bool:
        """Check if Hammerspoon has accessibility permissions (cached with TTL)."""
        now = time.monotonic()
        if (
            self._accessibility is not None
            and (now - self._accessibility_checked_at) < self.ACCESSIBILITY_CACHE_TTL
        ):
            return self._accessibility
        try:
            with httpx.Client(timeout=3) as client:
                resp = client.get(f"{self._base_url}/health")
                data = resp.json()
                self._accessibility = data.get("accessibility", False)
        except Exception:
            self._accessibility = False
        self._accessibility_checked_at = now
        return self._accessibility

    def _osascript(self, script: str) -> bool:
        """Run an AppleScript via /usr/bin/osascript subprocess."""
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _osascript_type_text(self, text: str) -> Dict[str, Any]:
        """Type text via AppleScript System Events (fallback)."""
        escaped = (
            text.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
        ok = self._osascript(
            f'tell application "System Events" to keystroke "{escaped}"'
        )
        return {"success": ok, "error": None if ok else "osascript keystroke failed"}

    # Shift+digit produces these symbols on US keyboard layout
    _SHIFT_DIGIT = {
        "1": "!", "2": "@", "3": "#", "4": "$", "5": "%",
        "6": "^", "7": "&", "8": "*", "9": "(", "0": ")",
    }

    def _osascript_press_key(self, key: str, modifiers: List[str]) -> Dict[str, Any]:
        """Press a key via AppleScript System Events (fallback)."""
        # If shift + single digit, convert to the actual symbol and type it directly.
        # AppleScript keystroke "8" using {shift down} doesn't always produce "*"
        # in apps like Calculator that handle key events at a low level.
        if (
            "shift" in modifiers
            and key in self._SHIFT_DIGIT
            and all(m == "shift" for m in modifiers)
        ):
            symbol = self._SHIFT_DIGIT[key]
            return self._osascript_type_text(symbol)

        if "fn" in modifiers:
            logger.warning(
                "AppleScript System Events does not natively support the 'fn' modifier; "
                "it may not behave as expected"
            )
        mod_parts = [self._MOD_MAP[m] for m in modifiers if m in self._MOD_MAP]
        mod_str = f" using {{{', '.join(mod_parts)}}}" if mod_parts else ""

        key_code = self._KEY_CODES.get(key.lower())
        if key_code is not None:
            script = f'tell application "System Events" to key code {key_code}{mod_str}'
        else:
            escaped = key.replace("\\", "\\\\").replace('"', '\\"')
            script = f'tell application "System Events" to keystroke "{escaped}"{mod_str}'

        ok = self._osascript(script)
        return {"success": ok, "error": None if ok else "osascript key press failed"}

    def _fallback_click(self, x: int, y: int) -> Dict[str, Any]:
        """Click at coordinates via cliclick or Quartz (fallback)."""
        # Prefer cliclick (handles absolute coordinates reliably)
        try:
            result = subprocess.run(
                ["cliclick", f"c:{x},{y}"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return {"success": True}
        except FileNotFoundError:
            pass  # cliclick not installed, try Quartz
        except Exception:
            pass

        # Fallback to Quartz CGEvent mouse click
        try:
            from Quartz.CoreGraphics import (
                CGEventCreateMouseEvent,
                CGEventPost,
                kCGEventLeftMouseDown,
                kCGEventLeftMouseUp,
                kCGHIDEventTap,
                CGPointMake,
            )
            point = CGPointMake(x, y)
            event_down = CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, point, 0)
            event_up = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, point, 0)
            CGEventPost(kCGHIDEventTap, event_down)
            CGEventPost(kCGHIDEventTap, event_up)
            return {"success": True}
        except ImportError:
            return {
                "success": False,
                "error": "Click fallback failed: neither cliclick nor Quartz available",
            }

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
        if not self.has_accessibility():
            result = self._fallback_click(x, y)
        else:
            result = self._action("click", {"x": x, "y": y})
        return ActuatorResult(
            success=result.get("success", False),
            output=str(result.get("result", "")),
            error=result.get("error"),
        ).to_dict()

    def type_text(self, text: str) -> Dict[str, Any]:
        if not self.has_accessibility():
            result = self._osascript_type_text(text)
        else:
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

        if not self.has_accessibility():
            result = self._osascript_press_key(key, modifiers)
        else:
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
        default_state = {
            "app_name": "",
            "app_bundle": "",
            "window_title": "",
            "window_x": 0,
            "window_y": 0,
            "window_w": 0,
            "window_h": 0,
        }
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.get(f"{self._base_url}/state")
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return default_state

        if not data.get("success"):
            return default_state

        result = data.get("result", {})
        frame = result.get("windowFrame", {})
        return {
            "app_name": result.get("frontmostApp", ""),
            "app_bundle": result.get("bundleId", ""),
            "window_title": result.get("windowTitle", ""),
            "window_x": frame.get("x", 0) if isinstance(frame, dict) else 0,
            "window_y": frame.get("y", 0) if isinstance(frame, dict) else 0,
            "window_w": frame.get("w", 0) if isinstance(frame, dict) else 0,
            "window_h": frame.get("h", 0) if isinstance(frame, dict) else 0,
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
