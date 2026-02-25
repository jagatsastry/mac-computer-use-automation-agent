"""Logging infrastructure for the automation agent."""

from automation_agent.logging.event_logger import EventLogger
from automation_agent.logging.models import Event, EventType
from automation_agent.logging.structured import configure_logging, get_logger

__all__ = [
    "EventLogger",
    "Event",
    "EventType",
    "configure_logging",
    "get_logger",
]
