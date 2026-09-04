"""Process-wide ownership lock for one configured SQLite database."""

from __future__ import annotations

import fcntl
import hashlib
import os
import tempfile
import threading
from pathlib import Path


class RuntimeDatabaseLockedError(RuntimeError):
    """Raised before Runtime bootstrap when another process owns the database."""

    error_code = "LOCAL_RUNTIME_DATABASE_LOCKED"

    def __init__(self) -> None:
        super().__init__(self.error_code)


def canonical_database_identity(database_path: str) -> Path | None:
    """Resolve spelling and symlink aliases to one filesystem identity."""

    if database_path.strip() == ":memory:":
        return None
    return Path(database_path).expanduser().resolve(strict=False)


class SQLiteRuntimeProcessLock:
    """Hold an advisory OS lock for the complete Runtime lifespan."""

    def __init__(self, database_path: str) -> None:
        identity = canonical_database_identity(database_path)
        self.database_identity = identity
        self.platform_file_identity: tuple[int, int] | None = None
        self._lock_descriptors: list[int] = []

    @staticmethod
    def _lock_descriptor(path: Path, *, blocking: bool) -> int:
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        operation = fcntl.LOCK_EX
        if not blocking:
            operation |= fcntl.LOCK_NB
        try:
            fcntl.flock(descriptor, operation)
        except BlockingIOError:
            os.close(descriptor)
            raise
        return descriptor

    @staticmethod
    def _unlock_descriptor(descriptor: int) -> None:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def acquire(self) -> None:
        if self.database_identity is None:
            return
        if self._lock_descriptors:
            raise RuntimeError("SQLite Runtime process lock is already acquired")
        self.database_identity.parent.mkdir(parents=True, exist_ok=True)
        registry_dir = Path(tempfile.gettempdir()) / (
            f"xhs-growth-agent-runtime-locks-{os.getuid()}"
        )
        registry_dir.mkdir(parents=True, exist_ok=True)
        registry_descriptor = self._lock_descriptor(
            registry_dir / "registry.lock",
            blocking=True,
        )
        acquired: list[int] = []
        try:
            path_digest = hashlib.sha256(
                str(self.database_identity).encode("utf-8")
            ).hexdigest()
            acquired.append(
                self._lock_descriptor(
                    registry_dir / f"path-{path_digest}.lock",
                    blocking=False,
                )
            )
            database_descriptor = os.open(
                self.database_identity,
                os.O_CREAT | os.O_RDWR,
                0o600,
            )
            try:
                stat = os.fstat(database_descriptor)
            finally:
                os.close(database_descriptor)
            self.platform_file_identity = (stat.st_dev, stat.st_ino)
            acquired.append(
                self._lock_descriptor(
                    registry_dir / f"file-{stat.st_dev}-{stat.st_ino}.lock",
                    blocking=False,
                )
            )
        except BlockingIOError as exc:
            for descriptor in reversed(acquired):
                self._unlock_descriptor(descriptor)
            self.platform_file_identity = None
            raise RuntimeDatabaseLockedError() from exc
        finally:
            self._unlock_descriptor(registry_descriptor)
        self._lock_descriptors = acquired

    def release(self) -> None:
        descriptors = self._lock_descriptors
        self._lock_descriptors = []
        self.platform_file_identity = None
        for descriptor in reversed(descriptors):
            self._unlock_descriptor(descriptor)


_reserved_lock_guard = threading.Lock()
_reserved_runtime_locks: dict[str, SQLiteRuntimeProcessLock] = {}


def _reservation_key(database_path: str) -> str | None:
    identity = canonical_database_identity(database_path)
    return str(identity) if identity is not None else None


def reserve_runtime_process_lock(database_path: str) -> SQLiteRuntimeProcessLock:
    """Acquire before the packaged server starts and expose it to its lifespan."""

    lock = SQLiteRuntimeProcessLock(database_path)
    lock.acquire()
    key = _reservation_key(database_path)
    if key is None:
        return lock
    with _reserved_lock_guard:
        if key in _reserved_runtime_locks:
            lock.release()
            raise RuntimeError("SQLite Runtime process lock is already reserved")
        _reserved_runtime_locks[key] = lock
    return lock


def claim_runtime_process_lock(database_path: str) -> SQLiteRuntimeProcessLock:
    """Adopt a packaged preflight lock or acquire for a normal source server."""

    key = _reservation_key(database_path)
    if key is not None:
        with _reserved_lock_guard:
            reserved = _reserved_runtime_locks.pop(key, None)
        if reserved is not None:
            return reserved
    lock = SQLiteRuntimeProcessLock(database_path)
    lock.acquire()
    return lock


def release_reserved_runtime_process_lock(
    database_path: str,
    lock: SQLiteRuntimeProcessLock,
) -> None:
    """Release a preflight lock if startup ended before or after adoption."""

    key = _reservation_key(database_path)
    if key is not None:
        with _reserved_lock_guard:
            if _reserved_runtime_locks.get(key) is lock:
                del _reserved_runtime_locks[key]
    lock.release()
