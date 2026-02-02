"""Simple action implementations."""

import pyautogui
import asyncio
import time
from typing import Tuple

from .base import ActionBase, ActionType, ActionResult


class ClickAction(ActionBase):
    """Click at coordinates."""

    def __init__(self, x: int, y: int):
        super().__init__(ActionType.CLICK)
        self.x = x
        self.y = y

    async def validate(self) -> bool:
        screen_w, screen_h = pyautogui.size()
        return 0 <= self.x <= screen_w and 0 <= self.y <= screen_h

    async def execute(self) -> ActionResult:
        try:
            pyautogui.click(self.x, self.y)
            return ActionResult(
                success=True,
                action_type=self.action_type,
                timestamp=time.time(),
                metadata={"x": self.x, "y": self.y}
            )
        except Exception as e:
            return ActionResult(
                success=False,
                action_type=self.action_type,
                timestamp=time.time(),
                error=str(e)
            )


class TypeAction(ActionBase):
    """Type text."""

    def __init__(self, text: str):
        super().__init__(ActionType.TYPE)
        self.text = text

    async def validate(self) -> bool:
        return len(self.text) > 0

    async def execute(self) -> ActionResult:
        try:
            pyautogui.write(self.text, interval=0.05)
            return ActionResult(
                success=True,
                action_type=self.action_type,
                timestamp=time.time(),
                metadata={"text_length": len(self.text)}
            )
        except Exception as e:
            return ActionResult(
                success=False,
                action_type=self.action_type,
                timestamp=time.time(),
                error=str(e)
            )


class HotkeyAction(ActionBase):
    """Execute hotkey combination."""

    def __init__(self, *keys: str):
        super().__init__(ActionType.HOTKEY)
        self.keys = keys

    async def validate(self) -> bool:
        return len(self.keys) > 0

    async def execute(self) -> ActionResult:
        try:
            pyautogui.hotkey(*self.keys)
            return ActionResult(
                success=True,
                action_type=self.action_type,
                timestamp=time.time(),
                metadata={"keys": self.keys}
            )
        except Exception as e:
            return ActionResult(
                success=False,
                action_type=self.action_type,
                timestamp=time.time(),
                error=str(e)
            )


class WaitAction(ActionBase):
    """Wait for duration."""

    def __init__(self, duration: float):
        super().__init__(ActionType.WAIT)
        self.duration = duration

    async def validate(self) -> bool:
        return self.duration > 0

    async def execute(self) -> ActionResult:
        try:
            await asyncio.sleep(self.duration)
            return ActionResult(
                success=True,
                action_type=self.action_type,
                timestamp=time.time(),
                metadata={"duration": self.duration}
            )
        except Exception as e:
            return ActionResult(
                success=False,
                action_type=self.action_type,
                timestamp=time.time(),
                error=str(e)
            )
