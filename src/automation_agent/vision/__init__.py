"""Vision coordination component for screen capture and element finding."""

from automation_agent.vision.capture import ScreenCapture
from automation_agent.vision.coordinator import COORDINATE_SPACES, ScreenCoordinatorImpl
from automation_agent.vision.models import ElementLocation, ScreenState

__all__ = [
    "COORDINATE_SPACES",
    "ElementLocation",
    "ScreenCapture",
    "ScreenCoordinatorImpl",
    "ScreenState",
]
