"""Data models for the vision coordination component."""

from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass
class ElementLocation:
    """A located UI element on screen."""

    x: int
    y: int
    confidence: float = 0.0
    label: str = ""
    method: str = ""  # "vision", "accessibility", "set_of_mark"


@dataclass
class ScreenState:
    """Captured state of the screen at a point in time."""

    description: str
    screenshot_b64: str
    resolution: Tuple[int, int] = (0, 0)
    elements: List[ElementLocation] = field(default_factory=list)
    app_name: str = ""
    window_title: str = ""
