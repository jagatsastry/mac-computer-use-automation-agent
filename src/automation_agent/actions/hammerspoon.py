import shutil
import subprocess
import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class ActionResult:
    success: bool
    output: str = ""
    error: Optional[str] = None

class HammerspoonExecutor:
    """Executes actions using Hammerspoon's Lua API via the 'hs' command line tool."""

    def __init__(self):
        self.hs_path = shutil.which("hs")
        if not self.hs_path:
            logger.warning("Hammerspoon CLI 'hs' not found. Execution will fail unless in dry-run.")

    def is_available(self) -> bool:
        return self.hs_path is not None

    def execute_lua(self, lua_code: str) -> ActionResult:
        """Executes the given Lua code using Hammerspoon."""
        if not self.hs_path:
            return ActionResult(success=False, error="Hammerspoon 'hs' CLI not found")
        
        try:
            result = subprocess.run(
                [self.hs_path, "-c", lua_code],
                capture_output=True,
                text=True,
                check=True
            )
            logger.info(f"Hammerspoon executed: {lua_code}")
            return ActionResult(success=True, output=result.stdout)
        except subprocess.CalledProcessError as e:
            return ActionResult(success=False, error=e.stderr)

    def click(self, x: int, y: int) -> ActionResult:
        lua = f"hs.eventtap.leftClick({{x={x}, y={y}}})"
        return self.execute_lua(lua)

    def type_text(self, text: str) -> ActionResult:
        safe_text = text.replace('"', '\\"').replace("'", "\\'")
        lua = f'hs.eventtap.keyStrokes("{safe_text}")'
        return self.execute_lua(lua)

    def press_key(self, keys: List[str]) -> ActionResult:
        # Map generic keys to Hammerspoon keys if needed
        # basic implementation
        modifiers = []
        key = None
        
        valid_mods = {"cmd", "alt", "shift", "ctrl", "fn"}
        
        for k in keys:
            k_lower = k.lower()
            if k_lower in valid_mods or k_lower == "command":
                if k_lower == "command": modifiers.append("cmd")
                else: modifiers.append(k_lower)
            else:
                key = k # Last non-modifier is the key
        
        if not key:
            return ActionResult(success=False, error="No key specified")

        mods_lua = "{" + ", ".join([f'"{m}"' for m in modifiers]) + "}"
        lua = f'hs.eventtap.keyStroke({mods_lua}, "{key}")'
        return self.execute_lua(lua)

    def activate_app(self, app_name: str) -> ActionResult:
        lua = f'hs.application.launchOrFocus("{app_name}")'
        return self.execute_lua(lua)

    def quit_app(self, app_name: str) -> ActionResult:
        lua = f'app = hs.application.get("{app_name}"); if app then app:kill() end'
        return self.execute_lua(lua)

    def open_url(self, url: str) -> ActionResult:
        lua = f'hs.urlevent.openURL("{url}")'
        return self.execute_lua(lua)


# Action Wrappers compatible with ActionRegistry

class HammerspoonAction:
    def __init__(self):
        self.executor = HammerspoonExecutor()

    async def execute(self) -> ActionResult:
        raise NotImplementedError

class ClickAction(HammerspoonAction):
    def __init__(self, x: int, y: int, **kwargs):
        super().__init__()
        self.x = x
        self.y = y

    async def execute(self) -> ActionResult:
        return self.executor.click(self.x, self.y)

class TypeTextAction(HammerspoonAction):
    def __init__(self, text: str, **kwargs):
        super().__init__()
        self.text = text

    async def execute(self) -> ActionResult:
        return self.executor.type_text(self.text)

class PressKeyAction(HammerspoonAction):
    def __init__(self, keys: List[str], **kwargs):
        super().__init__()
        self.keys = keys

    async def execute(self) -> ActionResult:
        return self.executor.press_key(self.keys)

class ActivateAppAction(HammerspoonAction):
    def __init__(self, app_name: str, **kwargs):
        super().__init__()
        self.app_name = app_name

    async def execute(self) -> ActionResult:
        return self.executor.activate_app(self.app_name)

class QuitAppAction(HammerspoonAction):
    def __init__(self, app_name: str, **kwargs):
        super().__init__()
        self.app_name = app_name

    async def execute(self) -> ActionResult:
        return self.executor.quit_app(self.app_name)

class OpenURLAction(HammerspoonAction):
    def __init__(self, url: str, browser: str = "Safari", **kwargs):
        super().__init__()
        self.url = url
        # Hammerspoon opens in default browser, browser param ignored for now
        # unless we use AppleScript inside HS, but hs.urlevent is simpler

    async def execute(self) -> ActionResult:
        return self.executor.open_url(self.url)
