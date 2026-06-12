"""Actuator that drives a Docker X11 sandbox desktop via xdotool.

Implements the same Actuator protocol (and get_state() contract) as
AppleScriptActuator, but every action lands inside the sandbox container —
the host's screen, mouse, and keyboard are never touched.

macOS-isms from the planner are translated transparently:
- cmd/command modifiers become ctrl (browser shortcuts like cmd+l work)
- "Safari" / "browser" launches the sandbox Chromium
- "Calculator" launches galculator, "TextEdit" launches mousepad
- reported app names embed macOS-friendly tokens ("Calculator (galculator)")
  so the verifier's app-name matching works unchanged.
"""

from __future__ import annotations

import re
import subprocess
import time
from typing import Any

import structlog

from automation_agent.actuator.models import ActuatorResult
from automation_agent.sandbox.cdp import CdpClient

slog = structlog.get_logger(__name__)

# macOS modifier names -> X11 modifier names
_MODIFIER_MAP = {
    "cmd": "ctrl",
    "command": "ctrl",
    "ctrl": "ctrl",
    "control": "ctrl",
    "shift": "shift",
    "alt": "alt",
    "option": "alt",
}

# macOS/planner key names -> xdotool keysyms
_KEY_MAP = {
    "return": "Return",
    "enter": "Return",
    "tab": "Tab",
    "space": "space",
    "escape": "Escape",
    "esc": "Escape",
    "delete": "BackSpace",  # macOS "delete" is backspace
    "backspace": "BackSpace",
    "forwarddelete": "Delete",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "home": "Home",
    "end": "End",
    "pageup": "Page_Up",
    "pagedown": "Page_Down",
    **{f"f{i}": f"F{i}" for i in range(1, 13)},
}

_BROWSER_TOKENS = (
    "safari",
    "chrome",
    "chromium",
    "firefox",
    "browser",
    "arc",
    "edge",
    "brave",
    "opera",
)

# Planner app names -> sandbox executables (also their X11 window classes)
_APP_LAUNCH = {
    "calculator": "galculator",
    "galculator": "galculator",
    "textedit": "mousepad",
    "text editor": "mousepad",
    "editor": "mousepad",
    "notes": "mousepad",
    "mousepad": "mousepad",
    "terminal": "xterm",
    "xterm": "xterm",
}

# X11 window class -> reported app name. Names deliberately contain the
# macOS-equivalent token so StepVerifier._matches_expected_app() and
# _is_browser_app() behave exactly as on the live desktop.
_CLASS_FRIENDLY = {
    "chromium": "Google Chrome (Chromium)",
    "chromium-browser": "Google Chrome (Chromium)",
    "google-chrome": "Google Chrome",
    "firefox": "Firefox",
    "galculator": "Calculator (galculator)",
    "mousepad": "TextEdit (mousepad)",
    "xterm": "Terminal (xterm)",
}

_BROWSER_CLASSES = ("chromium", "chromium-browser", "google-chrome", "firefox")


class SandboxActuator:
    """Executes desktop actions inside the sandbox container via xdotool."""

    TIMEOUT_SECONDS = 15

    def __init__(
        self,
        config=None,
        container: str | None = None,
        cdp: CdpClient | None = None,
        cdp_port: int | None = None,
        docker_bin: str = "docker",
    ):
        self.config = config
        self.container = container or getattr(config, "sandbox_container", None) or "agent-sandbox"
        self.docker_bin = docker_bin
        if cdp is not None:
            self.cdp = cdp
        else:
            port = cdp_port or getattr(config, "sandbox_cdp_port", None) or 19222
            self.cdp = CdpClient(port=port)
        self._display_size: tuple[int, int] | None = None

    # ------------------------------------------------------------------
    # Low-level exec helpers
    # ------------------------------------------------------------------

    def _exec(self, *args: str, timeout: int | None = None) -> ActuatorResult:
        """Run a command inside the container; never raises."""
        cmd = [self.docker_bin, "exec", self.container, *args]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout or self.TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return ActuatorResult(success=False, error=f"sandbox exec timed out: {args[0]}")
        except Exception as exc:
            return ActuatorResult(success=False, error=str(exc))
        if result.returncode != 0:
            return ActuatorResult(
                success=False,
                output=(result.stdout or "").strip(),
                error=(result.stderr or "").strip() or f"exit {result.returncode}",
            )
        return ActuatorResult(success=True, output=(result.stdout or "").strip())

    def _spawn(self, *args: str) -> None:
        """Start a long-lived process in the container without blocking."""
        subprocess.Popen(
            [self.docker_bin, "exec", "-d", self.container, *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _find_window(self, window_class: str) -> str | None:
        """Return the first visible window id for a class, or None."""
        result = self._exec(
            "xdotool", "search", "--onlyvisible", "--class", window_class
        )
        if result.success and result.output:
            return result.output.split()[0]
        return None

    # ------------------------------------------------------------------
    # Actuator protocol
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        return self._exec("true").success

    def click(self, x: int, y: int) -> dict[str, Any]:
        result = self._exec(
            "xdotool",
            "mousemove",
            "--sync",
            str(int(x)),
            str(int(y)),
            "click",
            "1",
        )
        if result.success:
            return ActuatorResult(success=True, output=f"Clicked ({x}, {y})").to_dict()
        return result.to_dict()

    def type_text(self, text: str) -> dict[str, Any]:
        # Parity with AppleScriptActuator: strip control characters rather
        # than typing literal escapes or submitting forms mid-string.
        sanitized = text.replace("\n", "").replace("\r", "").replace("\t", " ")
        return self._exec(
            "xdotool",
            "type",
            "--clearmodifiers",
            "--delay",
            "40",
            "--",
            sanitized,
        ).to_dict()

    def press_key(self, keys: list[str]) -> dict[str, Any]:
        modifiers: list[str] = []
        key: str | None = None
        for k in keys or []:
            k_lower = str(k).lower()
            if k_lower in _MODIFIER_MAP:
                mapped = _MODIFIER_MAP[k_lower]
                if mapped not in modifiers:
                    modifiers.append(mapped)
            else:
                key = str(k)

        if not key:
            return ActuatorResult(success=False, error="No key specified").to_dict()

        keysym = _KEY_MAP.get(key.lower(), key)
        combo = "+".join(modifiers + [keysym])
        return self._exec("xdotool", "key", "--clearmodifiers", combo).to_dict()

    def activate_app(self, app_name: str) -> dict[str, Any]:
        lowered = app_name.lower().strip()

        # Browsers: focus the running Chromium, or start it fresh
        if any(token in lowered for token in _BROWSER_TOKENS):
            window = self._find_window("chromium")
            if window:
                self._exec("xdotool", "windowactivate", window)
                return ActuatorResult(
                    success=True, output=f"Activated sandbox browser for '{app_name}'"
                ).to_dict()
            result = self._exec("launch-browser", timeout=30)
            if result.success:
                return ActuatorResult(
                    success=True, output=f"Launched sandbox browser for '{app_name}'"
                ).to_dict()
            return result.to_dict()

        executable = _APP_LAUNCH.get(lowered)
        window_class = executable or lowered

        window = self._find_window(window_class)
        if window:
            self._exec("xdotool", "windowactivate", window)
            return ActuatorResult(success=True, output=f"Activated {app_name}").to_dict()

        if executable is None:
            # Unknown app: only launch if the binary actually exists in the sandbox
            if not self._exec("which", lowered).success:
                return ActuatorResult(
                    success=False,
                    error=f"App not available in sandbox: {app_name}",
                ).to_dict()
            executable = lowered

        self._spawn(executable)
        # Best effort: focus the window once it maps
        for _ in range(4):
            time.sleep(0.3)
            window = self._find_window(window_class)
            if window:
                self._exec("xdotool", "windowactivate", window)
                break
        return ActuatorResult(success=True, output=f"Launched {executable}").to_dict()

    def open_url(self, url: str) -> dict[str, Any]:
        result = self._exec("launch-browser", url, timeout=30)
        if result.success:
            return ActuatorResult(success=True, output=f"Opened {url}").to_dict()
        return result.to_dict()

    def quit_app(self, app_name: str) -> dict[str, Any]:
        lowered = app_name.lower().strip()
        if any(token in lowered for token in _BROWSER_TOKENS):
            self._exec("pkill", "-f", "chromium")
            return ActuatorResult(success=True, output="Closed sandbox browser").to_dict()

        window_class = _APP_LAUNCH.get(lowered, lowered)
        result = self._exec("xdotool", "search", "--class", window_class, "windowclose")
        if result.success or not result.error or "exit 1" in result.error:
            # xdotool exits 1 when no windows matched — treat as already quit
            return ActuatorResult(success=True, output=f"Closed {app_name}").to_dict()
        return result.to_dict()

    def scroll(
        self,
        clicks: int,
        x: int | None = None,
        y: int | None = None,
        horizontal: bool = False,
    ) -> dict[str, Any]:
        try:
            clicks = int(clicks)
        except (TypeError, ValueError):
            clicks = -3
        repeat = max(abs(clicks), 1)
        if horizontal:
            button = "7" if clicks > 0 else "6"  # positive = right
        else:
            button = "4" if clicks > 0 else "5"  # positive = up

        if x is None or y is None:
            # Wheel events land at the pointer; a stale position over window
            # chrome scrolls nothing. Default to the content area center.
            x, y = self._display_center()
        args = ["xdotool", "mousemove", "--sync", str(int(x)), str(int(y))]
        args += ["click", "--repeat", str(repeat), "--delay", "60", button]

        result = self._exec(*args)
        if not result.success:
            return result.to_dict()
        if horizontal:
            direction = "right" if clicks > 0 else "left"
        else:
            direction = "up" if clicks > 0 else "down"
        return ActuatorResult(success=True, output=f"Scrolled {direction} {repeat} clicks").to_dict()

    def _display_center(self) -> tuple[int, int]:
        """Center of the sandbox display, biased below any toolbar chrome."""
        if self._display_size is None:
            result = self._exec("xdotool", "getdisplaygeometry")
            try:
                width, height = result.output.split()[:2]
                self._display_size = (int(width), int(height))
            except (ValueError, IndexError, AttributeError):
                self._display_size = (1024, 768)
        width, height = self._display_size
        return width // 2, int(height * 0.55)

    def get_state(self) -> dict[str, Any]:
        state: dict[str, Any] = {
            "app_name": "",
            "app_bundle": "",
            "window_title": "",
            "browser_url": "",
            "window_x": 0,
            "window_y": 0,
            "window_w": 0,
            "window_h": 0,
        }
        try:
            name_result = self._exec("xdotool", "getactivewindow", "getwindowname")
            if name_result.success:
                state["window_title"] = name_result.output

            class_result = self._exec(
                "sh", "-c", "xprop -id $(xdotool getactivewindow) WM_CLASS"
            )
            window_class = ""
            if class_result.success:
                match = re.search(r'"([^"]*)"\s*,\s*"([^"]*)"', class_result.output)
                if match:
                    # Prefer the class field (stable, e.g. "Chromium") over the
                    # instance field, which chromium pollutes with the profile
                    # dir ('chromium (/tmp/chromium-profile)'). Normalize to
                    # the first whitespace token.
                    raw = match.group(2) or match.group(1)
                    window_class = raw.lower().split()[0] if raw.strip() else ""

            if window_class:
                state["app_name"] = _CLASS_FRIENDLY.get(
                    window_class, window_class.capitalize()
                )
                state["app_bundle"] = f"sandbox.{window_class}"

            geometry_result = self._exec(
                "xdotool", "getactivewindow", "getwindowgeometry", "--shell"
            )
            if geometry_result.success:
                geometry = dict(
                    line.split("=", 1)
                    for line in geometry_result.output.splitlines()
                    if "=" in line
                )
                state["window_x"] = int(geometry.get("X", 0))
                state["window_y"] = int(geometry.get("Y", 0))
                state["window_w"] = int(geometry.get("WIDTH", 0))
                state["window_h"] = int(geometry.get("HEIGHT", 0))

            if window_class in _BROWSER_CLASSES:
                try:
                    page = self.cdp.active_page()
                except Exception:
                    page = None
                if page:
                    state["browser_url"] = page.get("url", "")
                batch = {}
                try:
                    batch = self.cdp.batch_state()
                except Exception:
                    batch = {}
                state["focused_value"] = batch.get("focused_value") or None
                state["selected_text"] = batch.get("selected_text") or None
                state["page_title"] = (
                    batch.get("page_title") or (page.get("title") if page else None) or None
                )
                state["page_heading"] = batch.get("page_heading") or None
        except Exception as exc:
            slog.debug("sandbox_get_state_error", error=str(exc))
        return state

    # ------------------------------------------------------------------
    # Optional protocol extensions (parity with AppleScriptActuator)
    # ------------------------------------------------------------------

    def get_scroll_position(self, axis: str = "y") -> int | None:
        if axis not in ("x", "y"):
            raise ValueError(f"axis must be 'x' or 'y', got {axis!r}")
        try:
            return self.cdp.scroll_position(axis)
        except Exception as exc:
            slog.debug("sandbox_scroll_position_error", error=str(exc))
            return None

    def get_page_title(self) -> str | None:
        return self.get_state().get("page_title")

    def get_page_heading(self) -> str | None:
        return self.get_state().get("page_heading")

    def get_active_element_value(self) -> str | None:
        return self.get_state().get("focused_value")

    def get_selected_text(self) -> str | None:
        return self.get_state().get("selected_text")
