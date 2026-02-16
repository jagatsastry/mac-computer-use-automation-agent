"""Simple action implementations."""

import pyautogui
import asyncio
import time
from typing import Tuple

from .base import ActionBase, ActionType, ActionResult


class ClickAction(ActionBase):
    """Click at coordinates with Retina display scaling support."""

    # Class-level scaling factor cache
    _scale_factor: float = None

    def __init__(self, x: int, y: int):
        super().__init__(ActionType.CLICK)
        self.x = x
        self.y = y

    @classmethod
    def _get_scale_factor(cls) -> float:
        """
        Calculate the scaling factor between screenshot pixels and logical screen pixels.
        On Retina displays, screenshots are 2x the logical resolution.
        """
        if cls._scale_factor is None:
            # Get logical screen size (what PyAutoGUI uses for clicking)
            logical_width, logical_height = pyautogui.size()

            # Take a screenshot to get actual pixel dimensions
            screenshot = pyautogui.screenshot()
            screenshot_width, screenshot_height = screenshot.size

            # Calculate scale factor (typically 2.0 on Retina, 1.0 otherwise)
            cls._scale_factor = screenshot_width / logical_width

        return cls._scale_factor

    def _scale_coordinates(self) -> Tuple[int, int]:
        """Scale coordinates from screenshot space to logical screen space."""
        scale = self._get_scale_factor()
        scaled_x = int(self.x / scale)
        scaled_y = int(self.y / scale)
        return scaled_x, scaled_y

    async def validate(self) -> bool:
        screen_w, screen_h = pyautogui.size()
        scaled_x, scaled_y = self._scale_coordinates()
        return 0 <= scaled_x <= screen_w and 0 <= scaled_y <= screen_h

    async def execute(self) -> ActionResult:
        try:
            # Scale coordinates for Retina displays
            scaled_x, scaled_y = self._scale_coordinates()
            scale = self._get_scale_factor()

            pyautogui.click(scaled_x, scaled_y)
            return ActionResult(
                success=True,
                action_type=self.action_type,
                timestamp=time.time(),
                metadata={
                    "original_x": self.x,
                    "original_y": self.y,
                    "scaled_x": scaled_x,
                    "scaled_y": scaled_y,
                    "scale_factor": scale
                }
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
