"""AppleScript-based actuator for macOS desktop automation.

Uses osascript to execute common desktop automation actions.
Works without any additional setup on macOS.
"""

import json
import subprocess
import time
from typing import Any, Dict, List, Optional

import structlog

from automation_agent.actuator.models import ActuatorResult
from automation_agent.config import AgentConfig

slog = structlog.get_logger(__name__)


class AppleScriptActuator:
    """Executes desktop actions via macOS osascript (AppleScript/JXA)."""

    TIMEOUT_SECONDS = 10

    def __init__(self, config: Optional[AgentConfig] = None):
        self.config = config
        self._js_disabled_browsers: set = set()

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
        try:
            import pyautogui
            import time
            # Ensure the browser is frontmost before clicking — other apps
            # (terminal, overlay) may have stolen focus between steps.
            self._activate_browser()
            # Move first, brief pause, then click — some web UIs need the hover
            # state to register before the link becomes clickable.
            pyautogui.moveTo(x, y)
            time.sleep(0.15)
            pyautogui.click()
            return ActuatorResult(success=True, output=f"Clicked ({x}, {y})").to_dict()
        except Exception as e:
            return ActuatorResult(success=False, error=str(e)).to_dict()

    def _activate_browser(self) -> None:
        """Re-activate the frontmost browser before a click, if one is already active.

        Only activates a browser that is already the frontmost app. This prevents
        stealing focus from non-browser apps (e.g., Finder, Calculator) where the
        agent may be clicking on native UI elements.
        """
        _BROWSERS = ("Google Chrome", "Safari", "Firefox", "Arc", "Microsoft Edge")
        try:
            # Ask System Events for the frontmost app
            result = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to get name '
                 'of first application process whose frontmost is true'],
                capture_output=True, text=True, timeout=2,
            )
            frontmost = result.stdout.strip()
            if frontmost in _BROWSERS:
                # Already a browser — just re-activate to be safe
                subprocess.run(
                    ["osascript", "-e",
                     f'tell application "{frontmost}" to activate'],
                    capture_output=True, timeout=2,
                )
            # If frontmost is NOT a browser, do nothing — don't steal focus
        except Exception:
            pass

    def type_text(self, text: str) -> Dict[str, Any]:
        escaped = self._escape_for_applescript(text)
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
            escaped_key = self._escape_for_applescript(key)
            script = f'tell application "System Events" to keystroke "{escaped_key}"{using_clause}'

        return self._run_osascript(script).to_dict()

    _BROWSERS = frozenset(
        ("safari", "chrome", "google chrome", "firefox", "arc", "edge", "brave", "opera")
    )

    def activate_app(self, app_name: str) -> Dict[str, Any]:
        # Browser substitution: if the plan says "Safari" but another browser
        # is already frontmost, use that browser instead of switching.
        # This preserves the user's context (e.g., Amazon open in Chrome).
        target = app_name
        if app_name.lower() in self._BROWSERS:
            try:
                state = self.get_state()
                current_app = state.get("app_name", "")
                if (
                    current_app
                    and current_app.lower() in self._BROWSERS
                    and current_app.lower() != app_name.lower()
                ):
                    slog.info(
                        "browser_substitution",
                        requested=app_name,
                        using=current_app,
                        reason="another browser is already frontmost",
                    )
                    target = current_app
            except Exception:
                pass

        # Use 'open -a' which is more reliable than AppleScript activate
        try:
            result = subprocess.run(
                ["open", "-a", target],
                capture_output=True,
                text=True,
                timeout=self.TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                return ActuatorResult(
                    success=False, error=result.stderr.strip()
                ).to_dict()
            return ActuatorResult(
                success=True, output=f"Activated {target}"
            ).to_dict()
        except subprocess.TimeoutExpired:
            return ActuatorResult(
                success=False, error=f"activate_app timed out"
            ).to_dict()

    def open_url(self, url: str) -> Dict[str, Any]:
        # Open the URL and then bring the default browser to the front.
        # "open location" hands off to the system default browser which may
        # open behind other windows.  The old approach got "the frontmost app"
        # which was still the terminal — we now iterate visible processes and
        # activate the first non-terminal app that has windows (the browser).
        escaped_url = self._escape_for_applescript(url)
        script = (
            f'open location "{escaped_url}"\n'
            'delay 1.0\n'
            'tell application "System Events"\n'
            '  set _procs to every application process '
            'whose visible is true and frontmost is false\n'
            '  repeat with _p in _procs\n'
            '    try\n'
            '      if (count of windows of _p) > 0 then\n'
            '        set _name to name of _p\n'
            '        if _name is not "Terminal" and _name is not "iTerm2" '
            'and _name is not "Claude" then\n'
            '          tell application _name to activate\n'
            '          exit repeat\n'
            '        end if\n'
            '      end if\n'
            '    end try\n'
            '  end repeat\n'
            'end tell'
        )
        return self._run_osascript(script).to_dict()

    def quit_app(self, app_name: str) -> Dict[str, Any]:
        escaped_name = self._escape_for_applescript(app_name)
        script = f'tell application "{escaped_name}" to quit'
        return self._run_osascript(script).to_dict()

    def scroll(
        self,
        clicks: int,
        x: Optional[int] = None,
        y: Optional[int] = None,
        horizontal: bool = False,
    ) -> Dict[str, Any]:
        """Scroll the mouse wheel.

        Args:
            clicks: Number of scroll clicks. Positive = up, negative = down.
            x: Optional x coordinate to scroll at.
            y: Optional y coordinate to scroll at.
            horizontal: If True, scroll horizontally instead of vertically.
        """
        try:
            import pyautogui
            saved_failsafe = pyautogui.FAILSAFE
            pyautogui.FAILSAFE = False
            try:
                if horizontal:
                    pyautogui.hscroll(clicks, x=x, y=y)
                else:
                    pyautogui.scroll(clicks, x=x, y=y)
            finally:
                pyautogui.FAILSAFE = saved_failsafe
            direction = "up" if clicks > 0 else "down"
            if horizontal:
                direction = "right" if clicks > 0 else "left"
            return ActuatorResult(
                success=True,
                output=f"Scrolled {direction} {abs(clicks)} clicks",
            ).to_dict()
        except Exception as e:
            return ActuatorResult(success=False, error=str(e)).to_dict()

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

    @staticmethod
    def _escape_for_applescript(text: str) -> str:
        """Sanitize text for safe embedding in AppleScript strings.

        Escapes backslashes and double quotes. Strips control characters
        (newline, carriage return, tab) because AppleScript does not
        interpret \\n as an escape — it would type literal backslash-n.
        """
        return (
            text.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "")
            .replace("\r", "")
            .replace("\t", " ")
        )

    def get_scroll_position(self, axis: str = "y") -> Optional[int]:
        """Get scrollX or scrollY from the frontmost browser tab.

        Args:
            axis: "x" for horizontal (scrollX), "y" for vertical (scrollY).

        Returns:
            Integer scroll position, or None if not in a browser or on error.

        Raises:
            ValueError: If axis is not "x" or "y".
        """
        if axis not in ("x", "y"):
            raise ValueError(f"axis must be 'x' or 'y', got {axis!r}")
        js_prop = "window.scrollX" if axis == "x" else "window.scrollY"
        state = self.get_state()
        app_name = state.get("app_name", "")
        lower = app_name.lower()

        if "safari" in lower:
            script = (
                f'tell application "Safari" to do JavaScript '
                f'"{js_prop}" in current tab of front window'
            )
        elif "chrome" in lower:
            script = (
                f'tell application "Google Chrome" to execute '
                f"front window's active tab javascript "
                f'"{js_prop}"'
            )
        else:
            return None

        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0 and result.stdout.strip():
                return int(float(result.stdout.strip()))
        except (subprocess.TimeoutExpired, ValueError, OSError) as exc:
            slog.debug("get_scroll_position_error", axis=axis, error=str(exc))
        return None

    def _get_browser_js(self, app_name: str, js: str) -> Optional[str]:
        """Run a JS expression in the frontmost browser tab and return the result.

        Supports Safari and Chrome. Returns None for other browsers or on error.
        """
        lower = app_name.lower()

        # Skip browsers we've already learned are broken this session
        for disabled in self._js_disabled_browsers:
            if disabled in lower:
                return None

        if "safari" in lower:
            script = (
                f'tell application "Safari" to do JavaScript '
                f'"{js}" in current tab of front window'
            )
        elif "chrome" in lower:
            script = (
                f'tell application "Google Chrome" to execute '
                f"front window's active tab javascript "
                f'"{js}"'
            )
        else:
            return None

        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
            # Detect Safari "Allow JavaScript from Apple Events" not enabled
            stderr = result.stderr if result.stderr else ""
            if "Allow JavaScript from Apple Events" in stderr:
                self._js_disabled_browsers.add("safari")
                slog.warning(
                    "safari_js_permission_disabled",
                    fix="Safari > Develop > Developer Settings "
                    "> check 'Allow JavaScript from Apple Events'",
                    impact="Safari JS disabled for this session — "
                    "using vision fallback",
                )
        except (subprocess.TimeoutExpired, OSError) as exc:
            slog.debug("get_browser_js_error", js=js[:60], error=str(exc))
        return None

    def _get_browser_js_batch(self, app_name: str) -> dict:
        """Run a batched JS expression returning multiple DOM values as JSON.

        Executes a single JSON.stringify() call in the frontmost browser tab
        containing focused_value, selected_text, page_title, and page_heading.

        Args:
            app_name: The application name (e.g., "Safari", "Google Chrome").

        Returns:
            Dict with string keys: "focused_value", "selected_text",
            "page_title", "page_heading". Values are str or empty str.
            Returns empty dict {} on any failure: non-browser, timeout,
            JSON parse error, subprocess error.
        """
        t0 = time.monotonic()
        js = (
            "JSON.stringify({focused_value: (document.activeElement "
            "? (document.activeElement.value || document.activeElement.textContent || '') "
            ": ''), selected_text: (window.getSelection "
            "? window.getSelection().toString() : ''), "
            "page_title: document.title || '', "
            "page_heading: (document.querySelector('h1') "
            "? document.querySelector('h1').textContent : '') || ''})"
        )
        raw = self._get_browser_js(app_name, js)
        latency_ms = (time.monotonic() - t0) * 1000
        if raw is None:
            if self._is_browser(app_name):
                slog.debug(
                    "browser_js_batch_error",
                    app_name=app_name,
                    error="raw result is None",
                    latency_ms=round(latency_ms, 1),
                )
            return {}
        try:
            result = json.loads(raw)
            slog.debug(
                "browser_js_batch_result",
                app_name=app_name,
                keys_populated=[k for k, v in result.items() if v],
                latency_ms=round(latency_ms, 1),
            )
            return result
        except (json.JSONDecodeError, TypeError) as exc:
            slog.debug(
                "browser_js_batch_error",
                app_name=app_name,
                error=str(exc),
                latency_ms=round(latency_ms, 1),
            )
            return {}

    def get_active_element_value(self) -> Optional[str]:
        """Get the value/textContent of the focused element in the frontmost browser tab.

        Returns None for non-browser apps or on timeout/error.
        """
        state = self.get_state()
        app_name = state.get("app_name", "")
        if not self._is_browser(app_name):
            return None
        return self._get_browser_js(
            app_name,
            "document.activeElement.value || document.activeElement.textContent || ''",
        )

    def get_selected_text(self) -> Optional[str]:
        """Get the current text selection in the frontmost browser tab.

        Returns None for non-browser apps or on timeout/error.
        """
        state = self.get_state()
        app_name = state.get("app_name", "")
        if not self._is_browser(app_name):
            return None
        return self._get_browser_js(
            app_name,
            "window.getSelection().toString()",
        )

    def get_page_title(self) -> Optional[str]:
        """Get document.title from frontmost browser tab.

        Returns:
            Page title string, or None for non-browser apps, timeout, or error.
        """
        state = self.get_state()
        return state.get("page_title")

    def get_page_heading(self) -> Optional[str]:
        """Get document.querySelector('h1')?.textContent from frontmost browser tab.

        Returns:
            First h1 element text, or None for non-browser apps, no h1, timeout, or error.
        """
        state = self.get_state()
        return state.get("page_heading")

    def _get_browser_url(self, app_name: str) -> str:
        """Get the current URL from a browser's frontmost tab."""
        lower = app_name.lower()
        if "safari" in lower:
            script = 'tell application "Safari" to return URL of front document'
        elif "chrome" in lower:
            script = 'tell application "Google Chrome" to return URL of active tab of front window'
        elif "firefox" in lower:
            # Firefox doesn't expose URL via AppleScript
            return ""
        elif "arc" in lower:
            script = 'tell application "Arc" to return URL of active tab of front window'
        else:
            return ""
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=2,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return ""

    def get_state(self) -> Dict[str, Any]:
        default_state = {
            "app_name": "",
            "app_bundle": "",
            "window_title": "",
            "browser_url": "",
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
            app_name = parts[0] if len(parts) > 0 else ""
            state = {
                "app_name": app_name,
                "app_bundle": parts[1] if len(parts) > 1 else "",
                "window_title": parts[2] if len(parts) > 2 else "",
                "browser_url": "",
                "window_x": int(parts[3]) if len(parts) > 3 and parts[3] else 0,
                "window_y": int(parts[4]) if len(parts) > 4 and parts[4] else 0,
                "window_w": int(parts[5]) if len(parts) > 5 and parts[5] else 0,
                "window_h": int(parts[6]) if len(parts) > 6 and parts[6] else 0,
            }
            if self._is_browser(app_name):
                state["browser_url"] = self._get_browser_url(app_name)
                # JS injection for verification (focused_value, selected_text,
                # page_title, page_heading) — single batched subprocess call
                js_enabled = True
                if self.config is not None:
                    js_enabled = getattr(self.config, "js_verification_enabled", True)
                if js_enabled:
                    batch = self._get_browser_js_batch(app_name)
                    state["focused_value"] = batch.get("focused_value") or None
                    state["selected_text"] = batch.get("selected_text") or None
                    state["page_title"] = batch.get("page_title") or None
                    state["page_heading"] = batch.get("page_heading") or None
            return state
        except (ValueError, IndexError):
            return default_state

    @staticmethod
    def _is_browser(app_name: str) -> bool:
        lowered = app_name.lower()
        return any(b in lowered for b in ("safari", "chrome", "firefox", "arc", "edge", "brave", "opera"))
