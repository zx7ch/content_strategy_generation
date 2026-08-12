"""Local-only, redacted Xiaohongshu login credential persistence."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from app.content_research.bootstrap import bootstrap_content_research_schema


@dataclass(frozen=True)
class XHSLoginStatus:
    authenticated: bool
    source: str | None = None
    updated_at: str | None = None
    failure_code: str | None = None


class XHSCredentialStore:
    """Stores exactly one local Cookie; public methods never expose it."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        bootstrap_content_research_schema(db_path)

    def get_active(self) -> str | None:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT cookie FROM xhs_local_credentials WHERE singleton = 1 AND status = 'authenticated'"
            ).fetchone()
        return str(row[0]) if row is not None else None

    def get_status(self) -> XHSLoginStatus:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT source, status, updated_at, failure_code FROM xhs_local_credentials WHERE singleton = 1"
            ).fetchone()
        if row is None:
            return XHSLoginStatus(authenticated=False)
        if row[1] != "authenticated":
            return XHSLoginStatus(
                authenticated=False,
                source=str(row[0]),
                updated_at=str(row[2]),
                failure_code=str(row[3]) if row[3] is not None else None,
            )
        return XHSLoginStatus(authenticated=True, source=str(row[0]), updated_at=str(row[2]))

    def replace(self, cookie: str, source: str) -> XHSLoginStatus:
        cleaned = cookie.strip()
        if not cleaned:
            raise ValueError("invalid_cookie")
        if source not in {"qr", "manual_cookie"}:
            raise ValueError("invalid_credential_source")
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO xhs_local_credentials (singleton, cookie, source, status, created_at, updated_at, failure_code)
                   VALUES (1, ?, ?, 'authenticated', ?, ?, NULL)
                   ON CONFLICT(singleton) DO UPDATE SET cookie=excluded.cookie, source=excluded.source,
                   status=excluded.status, updated_at=excluded.updated_at, failure_code=NULL""",
                (cleaned, source, now, now),
            )
        return self.get_status()

    def mark_stale(self, failure_code: str) -> XHSLoginStatus:
        if failure_code not in {"auth_required", "auth_expired"}:
            raise ValueError("invalid_credential_failure_code")
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """UPDATE xhs_local_credentials
                   SET status = 'stale', failure_code = ?, updated_at = ?
                   WHERE singleton = 1 AND status = 'authenticated'""",
                (failure_code, now),
            )
        return self.get_status()

    def clear(self) -> XHSLoginStatus:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("DELETE FROM xhs_local_credentials WHERE singleton = 1")
        return XHSLoginStatus(authenticated=False)
