"""Structured logging configuration using structlog.

This module provides a centralized logging configuration that outputs
structured JSON logs with correlation ID support for distributed tracing.
"""

import logging
import os
import sys
from typing import Any, Callable, Dict, List, Optional

import structlog
from structlog.processors import JSONRenderer
from structlog.stdlib import BoundLogger, filter_by_level

from app.core.context import correlation_id_ctx


def _add_correlation_id(
    logger: logging.Logger, method_name: str, event_dict: Dict[str, Any]
) -> Dict[str, Any]:
    """Add correlation ID from context to log event.

    This processor automatically injects the correlation_id from the
    context variable into every log entry for request tracing.

    Args:
        logger: The logger instance.
        method_name: The name of the logging method being called.
        event_dict: The event dictionary being built.

    Returns:
        The event dictionary with correlation_id added if present.
    """
    correlation_id = correlation_id_ctx.get()
    if correlation_id is not None:
        event_dict["correlation_id"] = correlation_id
    return event_dict


def _add_timestamp(
    logger: logging.Logger, method_name: str, event_dict: Dict[str, Any]
) -> Dict[str, Any]:
    """Add ISO format timestamp to log event.

    Args:
        logger: The logger instance.
        method_name: The name of the logging method being called.
        event_dict: The event dictionary being built.

    Returns:
        The event dictionary with timestamp added.
    """
    from datetime import datetime, timezone

    event_dict["timestamp"] = datetime.now(timezone.utc).isoformat()
    return event_dict


def _add_logger_name(
    logger: logging.Logger, method_name: str, event_dict: Dict[str, Any]
) -> Dict[str, Any]:
    """Add logger name to log event.

    Args:
        logger: The logger instance.
        method_name: The name of the logging method being called.
        event_dict: The event dictionary being built.

    Returns:
        The event dictionary with logger name added.
    """
    event_dict["logger"] = logger.name
    return event_dict


def _format_log_level(
    logger: logging.Logger, method_name: str, event_dict: Dict[str, Any]
) -> Dict[str, Any]:
    """Standardize log level field in log event to uppercase.

    Args:
        logger: The logger instance.
        method_name: The name of the logging method being called.
        event_dict: The event dictionary being built.

    Returns:
        The event dictionary with level field standardized to uppercase.
    """
    # Ensure level is always uppercase
    if "level" in event_dict:
        event_dict["level"] = event_dict["level"].upper()
    else:
        event_dict["level"] = method_name.upper()
    return event_dict


def get_processors() -> List[Callable[..., Dict[str, Any]]]:
    """Get the list of structlog processors for log formatting.

    Returns:
        List of processor functions to transform log events.
    """
    return [
        filter_by_level,
        _add_timestamp,
        _add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _format_log_level,  # Ensure level is uppercase after add_log_level
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        _add_correlation_id,
        JSONRenderer(),
    ]


def configure_logging(
    log_level: Optional[str] = None,
    json_format: bool = True,
    force: bool = False,
) -> None:
    """Configure structured logging for the application.

    This function sets up structlog with JSON output and integrates
    with Python's standard logging module.

    Args:
        log_level: The logging level (DEBUG, INFO, WARNING, ERROR).
            Defaults to LOG_LEVEL environment variable or INFO.
        json_format: Whether to use JSON formatting. Defaults to True.
        force: Force reconfiguration even if already configured.

    Example:
        >>> configure_logging(log_level="DEBUG")
        >>> logger = get_logger("app.services.rag")
        >>> logger.info("Service started", operation="init")
    """
    global _logging_configured

    if _logging_configured and not force:
        return

    level = (log_level or os.environ.get("LOG_LEVEL", "INFO")).upper()

    # Configure standard library logging
    # Reset handlers to allow reconfiguration in tests
    root_logger = logging.getLogger()
    root_logger.handlers = []
    root_logger.setLevel(getattr(logging, level))

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, level))
    handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(handler)

    # Configure structlog
    processors = get_processors() if json_format else get_processors()[:-1]

    try:
        structlog.configure(
            processors=processors,
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )
    except Exception:
        # structlog may already be configured, ignore
        pass

    _logging_configured = True


# Track if logging has been configured
_logging_configured = False


def get_logger(name: str) -> BoundLogger:
    """Get a structured logger instance.

    This function returns a BoundLogger that outputs structured JSON logs.
    The logger automatically includes correlation_id from the current context.

    Args:
        name: The name of the logger, typically __name__ of the module.

    Returns:
        A structlog BoundLogger configured for structured output.

    Example:
        >>> logger = get_logger("app.services.rag")
        >>> logger.info(
        ...     "LLM call completed",
        ...     provider="claude-3-sonnet",
        ...     latency_ms=1200
        ... )
    """
    global _logging_configured
    if not _logging_configured:
        configure_logging()
        _logging_configured = True
    return structlog.get_logger(name)
