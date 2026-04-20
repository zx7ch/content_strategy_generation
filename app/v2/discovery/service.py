"""Main-app adapter for MVP search observation capabilities."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import uuid

from experiments.xhs_extension_mvp.server.models import HotspotSnapshotResponse, TaskSnapshotResponse
from experiments.xhs_extension_mvp.server.storage import MVPStorage
from app.v2.discovery.query_expander import (
    DiscoveryExpandedQuery,
    DiscoveryQueryExpander,
    DiscoveryQueryExpansionFailure,
)


CURRENT_DISCOVERY_QUERY_GENERATION_VERSION = "llm_v1"
LEGACY_DISCOVERY_QUERY_GENERATION_VERSION = "legacy"


class DiscoveryError(ValueError):
    """Raised when discovery operations violate the integrated contract."""


class DiscoveryValidationError(DiscoveryError):
    """Raised when the caller sends invalid discovery input."""


class DiscoveryNotFoundError(DiscoveryError):
    """Raised when the requested discovery task cannot be found."""


class DiscoveryScopeError(DiscoveryError):
    """Raised when a discovery task is accessed outside its brand scope."""


class DiscoveryQueryExpansionError(DiscoveryError):
    """Raised when discovery query expansion fails."""


@dataclass(frozen=True)
class DiscoveryWorkspaceResult:
    task_snapshot: TaskSnapshotResponse
    hotspot_snapshot: HotspotSnapshotResponse
    query_generation_version: str
    query_generation_source: str
    capture_token: str | None = None
    capture_token_expires_at: datetime | None = None


class DiscoveryService:
    def __init__(
        self,
        *,
        database_path: str | Path,
        secret: str,
        spider_client: Any | None = None,
        query_expander: DiscoveryQueryExpander | None = None,
    ) -> None:
        self._db_path = Path(database_path)
        self._storage = MVPStorage(self._db_path, secret=secret)
        self._spider_client = spider_client
        self._query_expander = query_expander
        self._storage.init_db()
        self._init_scope_table()

    async def create_task(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        topic: str,
    ) -> DiscoveryWorkspaceResult:
        normalized_topic = " ".join(topic.split())
        if not normalized_topic:
            raise DiscoveryValidationError("topic is required")

        if self._query_expander is None:
            raise DiscoveryQueryExpansionError("搜索拓展服务未配置，暂时无法创建搜索任务。")

        try:
            expansion_result = await self._query_expander.expand_topic(normalized_topic)
        except DiscoveryQueryExpansionFailure as exc:
            raise DiscoveryQueryExpansionError(str(exc)) from exc

        if not expansion_result.queries:
            raise DiscoveryQueryExpansionError("当前未生成可用的拓展搜索词，请稍后重试。")

        task_id, _queries = self._storage.create_task(normalized_topic)
        self._replace_generated_queries(task_id=task_id, queries=expansion_result.queries)
        token, expires_at = self._storage.create_capture_token(task_id)
        self._save_scope(
            workspace_id=workspace_id,
            brand_id=brand_id,
            task_id=task_id,
            query_generation_source=expansion_result.source,
        )
        return self.get_task_workspace(
            workspace_id=workspace_id,
            brand_id=brand_id,
            task_id=task_id,
            query_generation_version=CURRENT_DISCOVERY_QUERY_GENERATION_VERSION,
            query_generation_source=expansion_result.source,
            capture_token=token,
            capture_token_expires_at=expires_at,
        )

    def get_task_workspace(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        task_id: str,
        query_generation_version: str | None = None,
        query_generation_source: str | None = None,
        capture_token: str | None = None,
        capture_token_expires_at: datetime | None = None,
    ) -> DiscoveryWorkspaceResult:
        stored_version, stored_source = self._assert_scope(
            workspace_id=workspace_id,
            brand_id=brand_id,
            task_id=task_id,
        )
        task_snapshot = self._storage.get_task_snapshot(task_id)
        if task_snapshot is None:
            raise DiscoveryNotFoundError(f"discovery task not found: {task_id}")
        hotspot_snapshot = self._storage.get_hotspots(task_id)
        if hotspot_snapshot is None:
            raise DiscoveryNotFoundError(f"discovery task not found: {task_id}")
        return DiscoveryWorkspaceResult(
            task_snapshot=task_snapshot,
            hotspot_snapshot=hotspot_snapshot,
            query_generation_version=query_generation_version or stored_version,
            query_generation_source=query_generation_source or stored_source,
            capture_token=capture_token,
            capture_token_expires_at=capture_token_expires_at,
        )

    async def refresh_hotspots(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        task_id: str,
    ) -> DiscoveryWorkspaceResult:
        self._assert_scope(workspace_id=workspace_id, brand_id=brand_id, task_id=task_id)
        await self._storage.refresh_hotspots(task_id, spider_client=self._spider_client)
        return self.get_task_workspace(workspace_id=workspace_id, brand_id=brand_id, task_id=task_id)

    def add_custom_queries(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        task_id: str,
        text: str,
    ) -> DiscoveryWorkspaceResult:
        self._assert_scope(workspace_id=workspace_id, brand_id=brand_id, task_id=task_id)
        normalized_text = text.strip()
        if not normalized_text:
            raise DiscoveryValidationError("text is required")
        try:
            self._storage.add_custom_queries(task_id=task_id, text=normalized_text)
        except KeyError as exc:
            raise DiscoveryNotFoundError(f"discovery task not found: {task_id}") from exc
        return self.get_task_workspace(workspace_id=workspace_id, brand_id=brand_id, task_id=task_id)

    def delete_custom_query(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        task_id: str,
        query_id: str,
    ) -> DiscoveryWorkspaceResult:
        self._assert_scope(workspace_id=workspace_id, brand_id=brand_id, task_id=task_id)
        try:
            self._storage.delete_custom_query(task_id=task_id, query_id=query_id)
        except KeyError as exc:
            raise DiscoveryNotFoundError(f"discovery query not found: {query_id}") from exc
        except ValueError as exc:
            raise DiscoveryValidationError(str(exc)) from exc
        return self.get_task_workspace(workspace_id=workspace_id, brand_id=brand_id, task_id=task_id)

    def _init_scope_table(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS v2_discovery_task_scope (
                    task_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    brand_id TEXT NOT NULL,
                    query_generation_version TEXT NOT NULL DEFAULT 'legacy',
                    query_generation_source TEXT NOT NULL DEFAULT 'legacy',
                    created_at TEXT NOT NULL
                )
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(v2_discovery_task_scope)").fetchall()
            }
            if "query_generation_version" not in columns:
                conn.execute(
                    """
                    ALTER TABLE v2_discovery_task_scope
                    ADD COLUMN query_generation_version TEXT NOT NULL DEFAULT 'legacy'
                    """
                )
            if "query_generation_source" not in columns:
                conn.execute(
                    """
                    ALTER TABLE v2_discovery_task_scope
                    ADD COLUMN query_generation_source TEXT NOT NULL DEFAULT 'legacy'
                    """
                )
            conn.execute(
                """
                UPDATE v2_discovery_task_scope
                SET query_generation_version = ?
                WHERE query_generation_version IS NULL OR TRIM(query_generation_version) = ''
                """,
                (LEGACY_DISCOVERY_QUERY_GENERATION_VERSION,),
            )
            conn.execute(
                """
                UPDATE v2_discovery_task_scope
                SET query_generation_source = ?
                WHERE query_generation_source IS NULL OR TRIM(query_generation_source) = ''
                """,
                ("legacy",),
            )
            conn.commit()

    def _save_scope(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        task_id: str,
        query_generation_source: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_discovery_task_scope (
                    task_id,
                    workspace_id,
                    brand_id,
                    query_generation_version,
                    query_generation_source,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    task_id,
                    workspace_id,
                    brand_id,
                    CURRENT_DISCOVERY_QUERY_GENERATION_VERSION,
                    query_generation_source,
                ),
            )
            conn.commit()

    def _assert_scope(self, *, workspace_id: str, brand_id: str, task_id: str) -> tuple[str, str]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT workspace_id, brand_id, query_generation_version, query_generation_source
                FROM v2_discovery_task_scope
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            raise DiscoveryNotFoundError(f"discovery task not found: {task_id}")
        if row["workspace_id"] != workspace_id or row["brand_id"] != brand_id:
            raise DiscoveryScopeError(f"discovery task {task_id} is outside the requested brand scope")
        version = row["query_generation_version"]
        source = row["query_generation_source"]
        normalized_version = (
            version.strip() if isinstance(version, str) and version.strip() else LEGACY_DISCOVERY_QUERY_GENERATION_VERSION
        )
        normalized_source = source.strip() if isinstance(source, str) and source.strip() else "legacy"
        return normalized_version, normalized_source

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _replace_generated_queries(self, *, task_id: str, queries: list[DiscoveryExpandedQuery]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM mvp_queries
                WHERE task_id = ? AND category != 'custom'
                """,
                (task_id,),
            )
            task_row = conn.execute(
                "SELECT created_at FROM mvp_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            created_at = task_row["created_at"] if task_row is not None else datetime.utcnow().isoformat()
            for index, query in enumerate(queries):
                conn.execute(
                    """
                    INSERT INTO mvp_queries (query_id, task_id, category, query_text, sort_order, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (str(uuid.uuid4()), task_id, query.category, query.query_text, index, created_at),
                )
            conn.commit()
