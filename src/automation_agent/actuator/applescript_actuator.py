"""AppleScript-based actuator for macOS desktop automation.

Uses osascript to execute common desktop automation actions.
Works without any additional setup on macOS.
"""

import subprocess
from typing import Any, Dict, List, Optional

from automation_agent.actuator.models import ActuatorResult
from automation_agent.config import AgentConfig


class AppleScriptActuator:
    """Executes desktop actions via macOS osascript (AppleScript/JXA)."""

    TIMEOUT_SECONDS = 10

    def __init__(self, config: Optional[AgentConfig] = None):
        pass

    def is_available(self) -> bool:
        """Always available on macOS."""
        try:
            result = subprocess.run(
                ["osascript", "-e", 'return "ok"'],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _run_osascript(self, script: str) -> ActuatorResult:
        """Execute an AppleScript and return the result."""
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=self.TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                return ActuatorResult(
                    success=False, output=result.stdout, error=result.stderr.strip()
                )
            return ActuatorResult(success=True, output=result.stdout.strip())
        except subprocess.TimeoutExpired:
            return ActuatorResult(
                success=False,
                error=f"osascript timed out after {self.TIMEOUT_SECONDS}s",
            )

    def click(self, x: int, y: int) -> Dict[str, Any]:
        # AppleScript can't easily do coordinate clicks; use cliclick if available
        # Fallback to pyautogui
        try:
            import pyautogui
            pyautogui.click(x, y)
            return ActuatorResult(success=True, output=f"Clicked ({x}, {y})").to_dict()
        except Exception as e:
            return ActuatorResult(success=False, error=str(e)).to_dict()

    def type_text(self, text: str) -> Dict[str, Any]:
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        script = f'tell application "System Events" to keystroke "{escaped}"'
        return self._run_osascript(script).to_dict()

    def press_key(self, keys: List[str]) -> Dict[str, Any]:
        # Map key names to AppleScript key codes
        key_codes = {
            "return": 36, "enter": 36, "tab": 48, "space": 49,
            "delete": 51, "escape": 53, "esc": 53,
            "up": 126, "down": 125, "left": 123, "right": 124,
            "f1": 122, "f2": 120, "f3": 99, "f4": 118,
        }
        mod_map = {
            "cmd": "command down", "command": "command down",
            "shift": "shift down",
            "alt": "option down", "option": "option down",
            "ctrl": "control down", "control": "control down",
        }

        modifiers = []
        key = None
        for k in keys:
            k_lower = k.lower()
            if k_lower in mod_map:
                modifiers.append(mod_map[k_lower])
            else:
                key = k

        if not key:
            return ActuatorResult(success=False, error="No key specified").to_dict()

        using_clause = ""
        if modifiers:
            using_clause = " using {" + ", ".join(modifiers) + "}"

        if key.lower() in key_codes:
            code = key_codes[key.lower()]
            script = f'tell application "System Events" to key code {code}{using_clause}'
        else:
            escaped_key = key.replace('"', '\\"')
            script = f'tell application "System Events" to keystroke "{escaped_key}"{using_clause}'

        return self._run_osascript(script).to_dict()

    def activate_app(self, app_name: str) -> Dict[str, Any]:
        # Use 'open -a' which is more reliable than AppleScript activate
        try:
            result = subprocess.run(
                ["open", "-a", app_name],
                capture_output=True,
                text=True,
                timeout=self.TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                return ActuatorResult(
                    success=False, error=result.stderr.strip()
                ).to_dict()
            return ActuatorResult(
                success=True, output=f"Activated {app_name}"
            ).to_dict()
        except subprocess.TimeoutExpired:
            return ActuatorResult(
                success=False, error=f"activate_app timed out"
            ).to_dict()

    def open_url(self, url: str) -> Dict[str, Any]:
        script = f'open location "{url}"'
        return self._run_osascript(script).to_dict()

    def quit_app(self, app_name: str) -> Dict[str, Any]:
        script = f'tell application "{app_name}" to quit'
        return self._run_osascript(script).to_dict()

    def get_accessibility_elements(self, app_name: str = "") -> list:
        """Query macOS Accessibility API for visible, interactive UI elements.

        Uses JXA (JavaScript for Automation) via osascript to walk the
        accessibility tree of the frontmost application (or the named app).

        Args:
            app_name: Application to query. Empty string means frontmost app.

        Returns:
            List of dicts, each with keys:
                - "label": str  (AXTitle or AXDescription or AXValue)
                - "role": str   (AXButton, AXTextField, AXLink, etc.)
                - "x": int      (left edge in screen pixels)
                - "y": int      (top edge in screen pixels)
                - "width": int
                - "height": int
                - "center_x": int
                - "center_y": int
            Returns empty list on any failure (permission denied, no elements, timeout).
        """
        script = r"""
function run(argv) {
    var sysEvents = Application("System Events");
    var proc;
    if (argv.length > 0 && argv[0]) {
        proc = sysEvents.processes.byName(argv[0]);
    } else {
        var frontmost = sysEvents.processes.whose({frontmost: true});
        if (frontmost.length === 0) return "[]";
        proc = frontmost[0];
    }

    var elements = [];
    var interactiveRoles = [
        "AXButton", "AXLink", "AXTextField", "AXTextArea",
        "AXCheckBox", "AXRadioButton", "AXPopUpButton",
        "AXComboBox", "AXMenuItem", "AXTab", "AXIncrementor"
    ];

    function walk(elem, depth) {
        if (depth > 6) return;
        try {
            var role = elem.role();
            if (interactiveRoles.indexOf(role) !== -1) {
                var pos = elem.position();
                var size = elem.size();
                if (pos && size && size[0] > 0 && size[1] > 0) {
                    elements.push({
                        label: (function() {
                            try { return elem.title() || ""; } catch(e) { return ""; }
                        })() || (function() {
                            try { return elem.description() || ""; } catch(e) { return ""; }
                        })() || (function() {
                            try { var v = elem.value(); return typeof v === "string" ? v : ""; } catch(e) { return ""; }
                        })(),
                        role: role,
                        x: pos[0], y: pos[1],
                        width: size[0], height: size[1],
                        center_x: pos[0] + Math.round(size[0] / 2),
                        center_y: pos[1] + Math.round(size[1] / 2)
                    });
                }
            }
            var children = elem.uiElements();
            for (var i = 0; i < children.length; i++) {
                walk(children[i], depth + 1);
            }
        } catch(e) {}
    }

    walk(proc, 0);
    return JSON.stringify(elements);
}
"""
        try:
            args = ["osascript", "-l", "JavaScript", "-e", script]
            if app_name:
                args.append(app_name)
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return []
            import json
            return json.loads(result.stdout.strip())
        except subprocess.TimeoutExpired:
            return []
        except Exception:
            return []

    def get_state(self) -> Dict[str, Any]:
        default_state = {
            "app_name": "",
            "app_bundle": "",
            "window_title": "",
            "window_x": 0,
            "window_y": 0,
            "window_w": 0,
            "window_h": 0,
        }
        script = '''
tell application "System Events"
    set frontApp to name of first application process whose frontmost is true
    set frontBundle to bundle identifier of first application process whose frontmost is true
    try
        set winTitle to name of front window of (first application process whose frontmost is true)
    on error
        set winTitle to ""
    end try
    try
        set winPos to position of front window of (first application process whose frontmost is true)
        set winSize to size of front window of (first application process whose frontmost is true)
        set winX to item 1 of winPos
        set winY to item 2 of winPos
        set winW to item 1 of winSize
        set winH to item 2 of winSize
    on error
        set winX to 0
        set winY to 0
        set winW to 0
        set winH to 0
    end try
end tell
return frontApp & "|" & frontBundle & "|" & winTitle & "|" & winX & "|" & winY & "|" & winW & "|" & winH
'''
        result = self._run_osascript(script)
        if not result.success:
            return default_state
        parts = result.output.split("|", 6)
        try:
            return {
                "app_name": parts[0] if len(parts) > 0 else "",
                "app_bundle": parts[1] if len(parts) > 1 else "",
                "window_title": parts[2] if len(parts) > 2 else "",
                "window_x": int(parts[3]) if len(parts) > 3 and parts[3] else 0,
                "window_y": int(parts[4]) if len(parts) > 4 and parts[4] else 0,
                "window_w": int(parts[5]) if len(parts) > 5 and parts[5] else 0,
                "window_h": int(parts[6]) if len(parts) > 6 and parts[6] else 0,
            }
        except (ValueError, IndexError):
            return default_state
