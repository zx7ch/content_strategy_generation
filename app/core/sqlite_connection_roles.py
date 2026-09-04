"""Central connection factories with explicit SQLite authority roles."""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


@dataclass(frozen=True)
class SQLiteConnectionOpened:
    role: str
    database_identity: str
    connection_identity: int
    thread_identity: int


_observer_lock = threading.Lock()
_observers: list[Callable[[SQLiteConnectionOpened], None]] = []


def _identity(database_path: str | Path) -> str:
    spelling = os.fspath(database_path)
    if spelling.startswith("file:"):
        spelling = spelling[5:].split("?", 1)[0]
    if spelling == ":memory:":
        return spelling
    return str(Path(spelling).expanduser().resolve())


def _notify(role: str, database_path: str | Path, connection: object) -> None:
    event = SQLiteConnectionOpened(
        role=role,
        database_identity=_identity(database_path),
        connection_identity=id(connection),
        thread_identity=threading.get_ident(),
    )
    with _observer_lock:
        observers = tuple(_observers)
    for observer in observers:
        observer(event)


@contextmanager
def observe_sqlite_connections(
    observer: Callable[[SQLiteConnectionOpened], None],
) -> Iterator[None]:
    with _observer_lock:
        _observers.append(observer)
    try:
        yield
    finally:
        with _observer_lock:
            _observers.remove(observer)


def open_writer_database(database_path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    _notify("writer", database_path, connection)
    return connection


def open_readonly_database(
    database_path: str | Path,
    *,
    timeout: float = 5,
) -> sqlite3.Connection:
    path = Path(database_path).expanduser().resolve()
    uri = f"{path.as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=timeout)
    connection.execute("PRAGMA query_only=ON")
    _notify("reader", path, connection)
    return connection


async def open_readonly_async_database(
    database_path: str | Path,
    *,
    timeout: float = 5,
) -> aiosqlite.Connection:
    path = Path(database_path).expanduser().resolve()
    uri = f"{path.as_uri()}?mode=ro"
    connection = await aiosqlite.connect(uri, uri=True, timeout=timeout)
    await connection.execute("PRAGMA query_only=ON")
    _notify("reader", path, connection)
    return connection


def open_migration_database(
    database_path: str | Path,
    *,
    readonly: bool = False,
    immutable: bool = False,
    timeout: float = 5,
) -> sqlite3.Connection:
    if readonly:
        path = Path(database_path).expanduser().resolve()
        immutable_query = "&immutable=1" if immutable else ""
        uri = f"{path.as_uri()}?mode=ro{immutable_query}"
        connection = sqlite3.connect(uri, uri=True, timeout=timeout)
    else:
        connection = sqlite3.connect(database_path, timeout=timeout)
    _notify("migration", database_path, connection)
    return connection


def open_bootstrap_database(
    database_path: str | Path,
    *,
    timeout: float = 5,
) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path, timeout=timeout)
    _notify("bootstrap", database_path, connection)
    return connection


async def open_bootstrap_async_database(
    database_path: str | Path,
    *,
    timeout: float = 5,
) -> aiosqlite.Connection:
    connection = await aiosqlite.connect(database_path, timeout=timeout)
    _notify("bootstrap", database_path, connection)
    return connection
