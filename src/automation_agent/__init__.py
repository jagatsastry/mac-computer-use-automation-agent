"""macOS Desktop Automation Agent."""

from .config import AgentConfig, load_config
from .logging import configure_logging, get_logger
from .version import __author__, __description__, __version__

__all__ = [
    "__version__",
    "__author__",
    "__description__",
    "AgentConfig",
    "load_config",
    "configure_logging",
    "get_logger",
]
