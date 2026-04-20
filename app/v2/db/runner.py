"""Migration runner for the V2 Postgres Phase 1 schema."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.v2.db.migrations import get_p1_1_migrations, get_p1_2_migrations, get_p1_5_migrations


def _load_psycopg_connect():
    try:
        from psycopg import connect  # type: ignore
        from psycopg.rows import dict_row  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional runtime package
        raise RuntimeError(
            "psycopg is required when POSTGRES_DSN is configured. "
            "Install project dependencies with psycopg[binary] support."
        ) from exc
    return connect, dict_row


def _default_connector(dsn: str):
    connect, dict_row = _load_psycopg_connect()
    return connect(dsn, row_factory=dict_row)


def run_p1_1_migrations(
    dsn: str,
    *,
    connector: Callable[[str], Any] | None = None,
) -> tuple[str, ...]:
    connector = connector or _default_connector
    applied: list[str] = []

    with connector(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    migration_id TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute("SELECT migration_id FROM schema_migrations ORDER BY migration_id")
            existing_rows = cursor.fetchall() or []
            existing_ids = {row["migration_id"] for row in existing_rows}

            for step in get_p1_1_migrations():
                if step.migration_id in existing_ids:
                    continue
                cursor.execute(step.sql)
                cursor.execute(
                    """
                    INSERT INTO schema_migrations (migration_id, description)
                    VALUES (%s, %s)
                    """,
                    (step.migration_id, step.description),
                )
                applied.append(step.migration_id)

        commit = getattr(connection, "commit", None)
        if callable(commit):
            commit()

    return tuple(applied)


def run_p1_2_migrations(
    dsn: str,
    *,
    connector: Callable[[str], Any] | None = None,
) -> tuple[str, ...]:
    connector = connector or _default_connector
    applied: list[str] = []

    with connector(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    migration_id TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute("SELECT migration_id FROM schema_migrations ORDER BY migration_id")
            existing_rows = cursor.fetchall() or []
            existing_ids = {row["migration_id"] for row in existing_rows}

            for step in get_p1_2_migrations():
                if step.migration_id in existing_ids:
                    continue
                cursor.execute(step.sql)
                cursor.execute(
                    """
                    INSERT INTO schema_migrations (migration_id, description)
                    VALUES (%s, %s)
                    """,
                    (step.migration_id, step.description),
                )
                applied.append(step.migration_id)

        commit = getattr(connection, "commit", None)
        if callable(commit):
            commit()

    return tuple(applied)


def run_p1_5_migrations(
    dsn: str,
    *,
    connector: Callable[[str], Any] | None = None,
) -> tuple[str, ...]:
    connector = connector or _default_connector
    applied: list[str] = []

    with connector(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    migration_id TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute("SELECT migration_id FROM schema_migrations ORDER BY migration_id")
            existing_rows = cursor.fetchall() or []
            existing_ids = {row["migration_id"] for row in existing_rows}

            for step in get_p1_5_migrations():
                if step.migration_id in existing_ids:
                    continue
                cursor.execute(step.sql)
                cursor.execute(
                    """
                    INSERT INTO schema_migrations (migration_id, description)
                    VALUES (%s, %s)
                    """,
                    (step.migration_id, step.description),
                )
                applied.append(step.migration_id)

        commit = getattr(connection, "commit", None)
        if callable(commit):
            commit()

    return tuple(applied)
