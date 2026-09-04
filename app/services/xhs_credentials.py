"""Local-only, redacted Xiaohongshu login credential persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.content_research.bootstrap import bootstrap_content_research_schema
from app.core.runtime_write_coordinator import RuntimeWriteCoordinator, TypedMutation
from app.core.runtime_write_registry import get_runtime_writer
from app.core.sqlite_connection_roles import (
    open_bootstrap_database,
    open_readonly_database,
)


@dataclass(frozen=True)
class XHSLoginStatus:
    authenticated: bool
    source: str | None = None
    updated_at: str | None = None
    failure_code: str | None = None


class XHSCredentialStore:
    """Stores exactly one local Cookie; public methods never expose it."""

    def __init__(
        self,
        db_path: str,
        *,
        writer: RuntimeWriteCoordinator | None = None,
    ) -> None:
        self._db_path = db_path
        self._writer = writer or get_runtime_writer(self._db_path)
        if self._writer is None:
            bootstrap_content_research_schema(db_path)

    def get_active(self) -> str | None:
        with open_readonly_database(self._db_path) as conn:
            row = conn.execute(
                "SELECT cookie FROM xhs_local_credentials WHERE singleton = 1 AND status = 'authenticated'"
            ).fetchone()
        return str(row[0]) if row is not None else None

    def get_status(self) -> XHSLoginStatus:
        with open_readonly_database(self._db_path) as conn:
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
        if self._writer is not None:
            self._writer.submit_sync(
                TypedMutation.create(
                    mutation_id=f"replace_xhs_credential_{now}",
                    mutation_kind="mutate_runtime_accounting",
                    domain_payload={
                        "action": "replace_xhs_credential",
                        "cookie": cleaned,
                        "source": source,
                        "now": now,
                    },
                )
            )
            return self.get_status()
        with open_bootstrap_database(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO xhs_local_credentials (singleton, cookie, source, status, created_at, updated_at, failure_code)
                   VALUES (1, ?, ?, 'authenticated', ?, ?, NULL)
                   ON CONFLICT(singleton) DO UPDATE SET cookie=excluded.cookie, source=excluded.source,
                   status=excluded.status, updated_at=excluded.updated_at, failure_code=NULL""",
                (cleaned, source, now, now),
            )
        return self.get_status()

    async def replace_async(self, cookie: str, source: str) -> XHSLoginStatus:
        if self._writer is None:
            return self.replace(cookie, source)
        cleaned = cookie.strip()
        if not cleaned:
            raise ValueError("invalid_cookie")
        if source not in {"qr", "manual_cookie"}:
            raise ValueError("invalid_credential_source")
        now = datetime.now(timezone.utc).isoformat()
        await self._writer.submit(
            TypedMutation.create(
                mutation_id=f"replace_xhs_credential_{now}",
                mutation_kind="mutate_runtime_accounting",
                domain_payload={
                    "action": "replace_xhs_credential",
                    "cookie": cleaned,
                    "source": source,
                    "now": now,
                },
            )
        )
        return self.get_status()

    def mark_stale(self, failure_code: str) -> XHSLoginStatus:
        if failure_code not in {"auth_required", "auth_expired"}:
            raise ValueError("invalid_credential_failure_code")
        now = datetime.now(timezone.utc).isoformat()
        if self._writer is not None:
            self._writer.submit_sync(
                TypedMutation.create(
                    mutation_id=f"stale_xhs_credential_{now}",
                    mutation_kind="mutate_runtime_accounting",
                    domain_payload={
                        "action": "mark_xhs_credential_stale",
                        "failure_code": failure_code,
                        "now": now,
                    },
                )
            )
            return self.get_status()
        with open_bootstrap_database(self._db_path) as conn:
            conn.execute(
                """UPDATE xhs_local_credentials
                   SET status = 'stale', failure_code = ?, updated_at = ?
                   WHERE singleton = 1 AND status = 'authenticated'""",
                (failure_code, now),
            )
        return self.get_status()

    async def mark_stale_async(self, failure_code: str) -> XHSLoginStatus:
        if self._writer is None:
            return self.mark_stale(failure_code)
        if failure_code not in {"auth_required", "auth_expired"}:
            raise ValueError("invalid_credential_failure_code")
        now = datetime.now(timezone.utc).isoformat()
        await self._writer.submit(
            TypedMutation.create(
                mutation_id=f"stale_xhs_credential_{now}",
                mutation_kind="mutate_runtime_accounting",
                domain_payload={
                    "action": "mark_xhs_credential_stale",
                    "failure_code": failure_code,
                    "now": now,
                },
            )
        )
        return self.get_status()

    def clear(self) -> XHSLoginStatus:
        if self._writer is not None:
            self._writer.submit_sync(
                TypedMutation.create(
                    mutation_id=f"clear_xhs_credential_{datetime.now(timezone.utc).isoformat()}",
                    mutation_kind="mutate_runtime_accounting",
                    domain_payload={"action": "clear_xhs_credential"},
                )
            )
            return XHSLoginStatus(authenticated=False)
        with open_bootstrap_database(self._db_path) as conn:
            conn.execute("DELETE FROM xhs_local_credentials WHERE singleton = 1")
        return XHSLoginStatus(authenticated=False)

    async def clear_async(self) -> XHSLoginStatus:
        if self._writer is None:
            return self.clear()
        now = datetime.now(timezone.utc).isoformat()
        await self._writer.submit(
            TypedMutation.create(
                mutation_id=f"clear_xhs_credential_{now}",
                mutation_kind="mutate_runtime_accounting",
                domain_payload={"action": "clear_xhs_credential"},
            )
        )
        return XHSLoginStatus(authenticated=False)
