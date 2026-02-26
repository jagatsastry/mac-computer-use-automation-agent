import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from automation_agent.actuator.models import ActuatorResult
from automation_agent.config import AgentConfig


class HammerspoonActuator:
    """Executes desktop actions via Hammerspoon Lua templates."""

    TIMEOUT_SECONDS = 10

    def __init__(self, config: Optional[AgentConfig] = None):
        self._templates_dir = Path(__file__).parent / "lua_templates"
        if config and config.hammerspoon_cli_path:
            self._hs_path = config.hammerspoon_cli_path
        else:
            self._hs_path = shutil.which("hs")

    @property
    def hs_path(self) -> Optional[str]:
        """Public access to the resolved hs CLI path."""
        return self._hs_path

    def is_available(self) -> bool:
        return self._hs_path is not None

    def _render_template(self, template_name: str, params: Dict[str, str]) -> str:
        """Load and render a Lua template with parameter substitution."""
        path = self._templates_dir / template_name
        if not path.exists():
            raise FileNotFoundError(f"Lua template not found: {path}")
        template = path.read_text()
        for key, value in params.items():
            template = template.replace(f"{{{{{key}}}}}", str(value))
        return template

    def _execute_lua(self, lua_code: str) -> ActuatorResult:
        """Execute Lua code via hs CLI."""
        if not self._hs_path:
            return ActuatorResult(success=False, error="Hammerspoon 'hs' CLI not found")
        try:
            result = subprocess.run(
                [self._hs_path, "-c", lua_code],
                capture_output=True,
                text=True,
                timeout=self.TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                error = result.stderr.strip() or f"hs CLI exited with code {result.returncode}"
                return ActuatorResult(
                    success=False, output=result.stdout, error=error
                )
            return ActuatorResult(success=True, output=result.stdout.strip())
        except subprocess.TimeoutExpired:
            return ActuatorResult(
                success=False,
                error=f"Hammerspoon command timed out after {self.TIMEOUT_SECONDS}s",
            )

    def click(self, x: int, y: int) -> Dict[str, Any]:
        lua = self._render_template("click.lua", {"x": str(x), "y": str(y)})
        return self._execute_lua(lua).to_dict()

    def type_text(self, text: str) -> Dict[str, Any]:
        # Escape special chars for Lua string
        safe_text = self._escape_lua_string(text)
        lua = self._render_template("type_text.lua", {"text": safe_text})
        return self._execute_lua(lua).to_dict()

    def press_key(self, keys: List[str]) -> Dict[str, Any]:
        modifiers = []
        key = None
        mod_names = {"cmd", "command", "alt", "option", "shift", "ctrl", "control", "fn"}
        mod_map = {"command": "cmd", "option": "alt", "control": "ctrl"}

        for k in keys:
            k_lower = k.lower()
            if k_lower in mod_names:
                mapped = mod_map.get(k_lower, k_lower)
                modifiers.append(mapped)
            else:
                key = k

        if not key:
            return ActuatorResult(
                success=False, error="No key specified, only modifiers"
            ).to_dict()

        mods_lua = "{" + ", ".join(f'"{m}"' for m in modifiers) + "}"
        safe_key = self._escape_lua_string(key)
        lua = self._render_template("press_key.lua", {"modifiers": mods_lua, "key": safe_key})
        return self._execute_lua(lua).to_dict()

    def activate_app(self, app_name: str) -> Dict[str, Any]:
        safe_name = self._escape_lua_string(app_name)
        lua = self._render_template("activate_app.lua", {"app_name": safe_name})
        return self._execute_lua(lua).to_dict()

    def open_url(self, url: str) -> Dict[str, Any]:
        safe_url = self._escape_lua_string(url)
        lua = self._render_template("open_url.lua", {"url": safe_url})
        return self._execute_lua(lua).to_dict()

    def quit_app(self, app_name: str) -> Dict[str, Any]:
        safe_name = self._escape_lua_string(app_name)
        lua = self._render_template("quit_app.lua", {"app_name": safe_name})
        result = self._execute_lua(lua)
        if result.success and result.output.strip() == "not_found":
            return ActuatorResult(
                success=True, output=f"App '{app_name}' was not running"
            ).to_dict()
        return result.to_dict()

    def get_state(self) -> Dict[str, Any]:
        lua = self._render_template("get_state.lua", {})
        result = self._execute_lua(lua)
        if not result.success:
            return {
                "app_name": "",
                "app_bundle": "",
                "window_title": "",
                "window_x": 0,
                "window_y": 0,
                "window_w": 0,
                "window_h": 0,
            }
        try:
            return json.loads(result.output)
        except json.JSONDecodeError:
            return {
                "app_name": "",
                "app_bundle": "",
                "window_title": "",
                "window_x": 0,
                "window_y": 0,
                "window_w": 0,
                "window_h": 0,
                "raw": result.output,
            }

    @staticmethod
    def _escape_lua_string(text: str) -> str:
        """Escape text for safe embedding in a double-quoted Lua string literal."""
        text = text.replace("\\", "\\\\")
        text = text.replace('"', '\\"')
        text = text.replace("\n", "\\n")
        text = text.replace("\r", "\\r")
        text = text.replace("\t", "\\t")
        return text
