"""Base action classes."""

from abc import ABC, abstractmethod
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Dict, Any
import time


class ActionType(Enum):
    """Types of actions."""
    CLICK = "click"
    TYPE = "type"
    HOTKEY = "hotkey"
    WAIT = "wait"


@dataclass
class ActionResult:
    """Result of action execution."""
    success: bool
    action_type: ActionType
    timestamp: float
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class ActionBase(ABC):
    """Base class for all actions."""

    def __init__(self, action_type: ActionType):
        self.action_type = action_type

    @abstractmethod
    async def execute(self) -> ActionResult:
        """Execute the action."""
        pass

    @abstractmethod
    async def validate(self) -> bool:
        """Validate action can be executed."""
        pass
