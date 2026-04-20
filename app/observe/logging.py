"""
Logging configuration for XHS Agent
=====================================
Two sinks:
  - stderr     : human-readable coloured output for local development
  - logs/app.json : JSON-serialised output for production ingestion (Datadog / Loki)

Usage:
    from app.logging_config import setup_logging
    setup_logging()           # call once at app startup (main.py)

    from app.logging_config import get_logger
    logger = get_logger(__name__)
    logger.info("something happened", extra_key="value")

Structured extras are passed as keyword arguments to any loguru log call:
    logger.info("retrieve done", retrieved=42, duration_ms=310)
These appear as top-level fields in the JSON sink, making them queryable.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter
from typing import Generator

from loguru import logger


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def setup_logging(
    level: str = "DEBUG",
    log_dir: str = "logs",
    json_file: str = "app.json",
    rotation: str = "100 MB",
    retention: str = "7 days",
) -> None:
    """
    Configure loguru sinks. Call once at application startup.

    Args:
        level:      minimum log level for both sinks
        log_dir:    directory for JSON log file
        json_file:  filename for the JSON log sink
        rotation:   loguru rotation policy (size or time-based)
        retention:  how long to keep rotated files
    """
    # Remove the default loguru handler
    logger.remove()

    # --- Sink 1: stderr — coloured, human-readable for local dev ---
    logger.add(
        sys.stderr,
        level=level,
        colorize=True,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
            "{extra}"
        ),
    )

    # --- Sink 2: JSON file — structured, for production ingestion ---
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    logger.add(
        f"{log_dir}/{json_file}",
        level=level,
        serialize=True,        # loguru built-in: emits each record as a JSON line
        rotation=rotation,
        retention=retention,
        enqueue=True,          # async-safe: writes happen in a background thread
    )


def get_logger(name: str):
    """
    Return a loguru logger bound to the given module name.
    Equivalent to logging.getLogger(__name__) but loguru-flavoured.

    Usage:
        logger = get_logger(__name__)
        logger.info("done", duration_ms=42, count=10)
    """
    return logger.bind(module=name)


# ---------------------------------------------------------------------------
# Duration context manager
# ---------------------------------------------------------------------------

@contextmanager
def log_duration(
    bound_logger,
    stage: str,
    level: str = "INFO",
    **extra,
) -> Generator[dict, None, None]:
    """
    Context manager that logs duration of any code block.

    Usage:
        with log_duration(logger, "retrieve_web_data", query=user_query) as ctx:
            results = await retrieve(...)
            ctx["result_count"] = len(results)   # add runtime data to final log

    The final log line includes:
        - stage: name of the stage
        - duration_ms: elapsed time in milliseconds
        - any kwargs passed to log_duration
        - any keys set on the yielded ctx dict during execution

    On exception, logs at ERROR level with the exception message and re-raises.
    """
    ctx: dict = {}
    t_start = perf_counter()
    log_fn = getattr(bound_logger, level.lower())

    bound_logger.debug(f"{stage} started", stage=stage, **extra)
    try:
        yield ctx
        duration_ms = round((perf_counter() - t_start) * 1000)
        log_fn(
            f"{stage} finished",
            stage=stage,
            duration_ms=duration_ms,
            **extra,
            **ctx,
        )
    except Exception as exc:
        duration_ms = round((perf_counter() - t_start) * 1000)
        bound_logger.error(
            f"{stage} failed: {exc}",
            stage=stage,
            duration_ms=duration_ms,
            error=str(exc),
            **extra,
            **ctx,
        )
        raise