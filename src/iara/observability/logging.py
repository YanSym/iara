"""Structured logging configuration with redaction.

Uses structlog for structured log output. The redaction processor is always
applied before output to prevent PII and secrets from appearing in logs.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from iara.security.redaction import RedactionProcessor


def configure_logging(level: str = "INFO", log_format: str = "json") -> None:
    """Configure structlog with redaction and structured output.

    This must be called once at application startup before any logging occurs.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_format: Output format (``json`` or ``console``).
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Standard library logging setup
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # Shared processors (always applied)
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        RedactionProcessor(),  # Always redact before output
        structlog.processors.StackInfoRenderer(),
    ]

    if log_format == "console":
        renderer: Any = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(log_level)


def get_logger(name: str | None = None, **initial_values: Any) -> Any:
    """Get a structured logger bound with initial context values.

    Args:
        name: Logger name (defaults to the calling module's name).
        **initial_values: Initial key-value pairs to bind to the logger.

    Returns:
        A structlog bound logger with redaction applied.
    """
    logger = structlog.get_logger(name)
    if initial_values:
        logger = logger.bind(**initial_values)
    return logger
