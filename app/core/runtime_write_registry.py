"""Process-local lookup for the one active Writer per canonical SQLite file."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.runtime_write_coordinator import RuntimeWriteCoordinator

_lock = threading.Lock()
_writers: dict[str, RuntimeWriteCoordinator] = {}


def _identity(database_path: str | Path) -> str:
    spelling = str(database_path)
    if spelling == ":memory:":
        return spelling
    return str(Path(spelling).expanduser().resolve())


def register_runtime_writer(
    database_path: str | Path,
    writer: RuntimeWriteCoordinator,
) -> None:
    identity = _identity(database_path)
    with _lock:
        existing = _writers.get(identity)
        if existing is not None and existing is not writer:
            raise RuntimeError("canonical SQLite file already has an active Writer")
        _writers[identity] = writer


def unregister_runtime_writer(
    database_path: str | Path,
    writer: RuntimeWriteCoordinator,
) -> None:
    identity = _identity(database_path)
    with _lock:
        if _writers.get(identity) is writer:
            del _writers[identity]


def get_runtime_writer(database_path: str | Path) -> RuntimeWriteCoordinator | None:
    with _lock:
        return _writers.get(_identity(database_path))
