from __future__ import annotations

import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


LOGGER_NAME = "xhs_extension_mvp"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "event_name"):
            payload["event_name"] = record.event_name
        if hasattr(record, "task_id"):
            payload["task_id"] = record.task_id
        if hasattr(record, "query_text"):
            payload["query_text"] = record.query_text
        if hasattr(record, "item_count"):
            payload["item_count"] = record.item_count
        if hasattr(record, "candidate_count"):
            payload["candidate_count"] = record.candidate_count
        if hasattr(record, "page_type"):
            payload["page_type"] = record.page_type
        if hasattr(record, "imported_count"):
            payload["imported_count"] = record.imported_count
        if hasattr(record, "detail"):
            payload["detail"] = record.detail
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(log_path: str | Path | None = None, *, force: bool = False) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers and not force:
        return logger
    if force:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

    logger.setLevel(logging.INFO)
    logger.propagate = False

    formatter = JsonFormatter()

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    resolved_log_path = Path(log_path or default_log_path())
    resolved_log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        resolved_log_path,
        maxBytes=512 * 1024,
        backupCount=2,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def default_log_path() -> str:
    return os.environ.get("XHS_EXTENSION_MVP_LOG_PATH", "data/xhs_extension_mvp.log")
