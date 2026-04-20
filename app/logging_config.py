"""Logging contract facade for the project.

This module wraps app.core.logging to enforce the event contract defined in dev_spec §1.5.9.
"""

from __future__ import annotations

from typing import Any, Optional

from app.config import settings
from app.core.context import correlation_id_ctx
from app.core.logging import configure_logging as _configure_logging
from app.core.logging import get_logger as _get_logger

# Mandatory event names from dev_spec §1.5.9
REQUIRED_EVENTS = {
    "session_created",
    "stage_changed",
    "session_frozen",
    "session_resumed",
    "session_purged",
    "job_enqueued",
    "job_leased",
    "job_retry_scheduled",
    "job_completed",
    "job_failed",
    "llm_call_completed",
    "llm_call_failed",
    "budget_degrade_applied",
    "budget_exceeded",
    "recovery_started",
    "recovery_completed",
    "reindex_scheduled",
    "reindex_started",
    "reindex_succeeded",
    "pending",
    "reindex_deadlettered",
    "sse_heartbeat",
}

_REQUIRED_CONTRACT_KEYS = (
    "event_name",
    "trace_id",
    "session_id",
    "job_id",
    "stage",
    "component",
)
_SUPPORTED_LEVELS = {"debug", "info", "warning", "error"}


def setup_logging(force: bool = False, log_level: Optional[str] = None) -> None:
    """Configure logging format by environment.

    - development: readable console output
    - production: single-line JSON output
    """
    env = (settings.APP_ENV or "development").lower()
    json_format = env not in {"development", "dev", "local"}
    _configure_logging(log_level=log_level or settings.LOG_LEVEL, json_format=json_format, force=force)


def _build_contract_payload(
    *,
    event_name: str,
    component: str,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
    job_id: Optional[str] = None,
    stage: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event_name": event_name,
        "trace_id": trace_id or correlation_id_ctx.get(),
        "session_id": session_id,
        "job_id": job_id,
        "stage": stage,
        "component": component,
    }
    if extra:
        payload.update(extra)
    return payload


def get_logger(
    name: str,
    *,
    component: str = "app",
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
    job_id: Optional[str] = None,
    stage: Optional[str] = None,
):
    """Get logger pre-bound with logging contract baseline fields."""
    logger = _get_logger(name)
    payload = _build_contract_payload(
        event_name="app_log",
        component=component,
        trace_id=trace_id,
        session_id=session_id,
        job_id=job_id,
        stage=stage,
    )
    return logger.bind(**payload)


def is_required_event(event_name: str) -> bool:
    return event_name in REQUIRED_EVENTS


def missing_contract_keys(payload: dict[str, Any]) -> list[str]:
    return [key for key in _REQUIRED_CONTRACT_KEYS if key not in payload]


def log_event(
    logger,
    *,
    event_name: str,
    level: str = "info",
    component: Optional[str] = None,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
    job_id: Optional[str] = None,
    stage: Optional[str] = None,
    **kwargs: Any,
) -> None:
    """Emit a structured event while enforcing contract fields."""
    normalized_level = level.lower()
    if normalized_level not in _SUPPORTED_LEVELS:
        raise ValueError(f"Unsupported log level: {level}")

    payload = _build_contract_payload(
        event_name=event_name,
        component=component or kwargs.pop("component", "app"),
        trace_id=trace_id,
        session_id=session_id,
        job_id=job_id,
        stage=stage,
        extra=kwargs,
    )
    method = getattr(logger, normalized_level)
    method(event_name, **payload)
