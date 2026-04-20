"""Context variables for request context propagation.

This module provides context variables for tracking correlation IDs
across sync and async contexts using Python's contextvars module.
"""

from contextvars import ContextVar
from typing import Optional

# Context variable for correlation ID propagation across async/sync boundaries
correlation_id_ctx: ContextVar[Optional[str]] = ContextVar(
    "correlation_id_ctx", default=None
)


def set_correlation_id(correlation_id: str) -> None:
    """Set the correlation ID in the current context.

    Args:
        correlation_id: Unique identifier for tracking requests across services.

    Example:
        >>> set_correlation_id("sess_abc123")
        >>> get_correlation_id()
        'sess_abc123'
    """
    correlation_id_ctx.set(correlation_id)


def get_correlation_id() -> Optional[str]:
    """Get the correlation ID from the current context.

    Returns:
        The correlation ID if set, None otherwise.

    Example:
        >>> set_correlation_id("sess_abc123")
        >>> get_correlation_id()
        'sess_abc123'
        >>> clear_correlation_id()
        >>> get_correlation_id() is None
        True
    """
    return correlation_id_ctx.get()


def clear_correlation_id() -> None:
    """Clear the correlation ID from the current context.

    Example:
        >>> set_correlation_id("sess_abc123")
        >>> get_correlation_id()
        'sess_abc123'
        >>> clear_correlation_id()
        >>> get_correlation_id() is None
        True
    """
    correlation_id_ctx.set(None)
