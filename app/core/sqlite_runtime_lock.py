"""Process-wide ownership lock for one configured SQLite database."""

from __future__ import annotations

import fcntl
import hashlib
import os
import tempfile
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
