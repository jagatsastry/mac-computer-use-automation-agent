"""Logging infrastructure for the automation agent."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from typing import Any, Optional

import structlog
from structlog.types import EventDict

from .config import AgentConfig


def configure_logging(config: AgentConfig) -> None:
    """
    Configure logging infrastructure.

    Sets up both console and file logging with structured logging support.

    Args:
        config: Agent configuration with logging settings
    """
    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.DEBUG,
    )

    # Set up file handler with rotation
    log_file = config.get_log_file_path()
    file_handler = RotatingFileHandler(
        filename=log_file,
        maxBytes=config.log_file_max_bytes,
        backupCount=config.log_file_backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(getattr(logging, config.log_file_level.value))

    # Set up console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, config.log_console_level.value))

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    root_logger.setLevel(logging.DEBUG)

    # Configure structlog
    shared_processors = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if config.log_structured:
        file_processors = shared_processors + [structlog.processors.JSONRenderer()]
    else:
        file_processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=False)
        ]

    console_processors = shared_processors + [structlog.dev.ConsoleRenderer(colors=True)]

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure formatters
    file_formatter = structlog.stdlib.ProcessorFormatter(
        processors=file_processors,
        foreign_pre_chain=shared_processors,
    )
    console_formatter = structlog.stdlib.ProcessorFormatter(
        processors=console_processors,
        foreign_pre_chain=shared_processors,
    )

    file_handler.setFormatter(file_formatter)
    console_handler.setFormatter(console_formatter)


def get_logger(name: Optional[str] = None) -> structlog.stdlib.BoundLogger:
    """
    Get a structured logger instance.

    Args:
        name: Logger name (typically __name__ of the module)

    Returns:
        Configured structlog logger
    """
    return structlog.get_logger(name)
