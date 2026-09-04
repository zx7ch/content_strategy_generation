"""Writer-owned mutations for the integrated discovery workspace."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.runtime_write_coordinator import (
    DomainMutationRejectedError,
    MutationApplication,
    MutationIdentityConflictError,
    RuntimeMutationHandler,
    TypedMutation,
)


def _normalize_query(value: str) -> str:
    return " ".join(value.strip().split())[:120]


class _DiscoveryMutationHandler:
    mutation_kind = "mutate_discovery"

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        payload = dict(mutation.domain_payload)
        action = payload.get("action")
        if action == "create_task":
            result = self._create_task(connection, payload)
        elif action == "add_custom_queries":
            result = self._add_custom_queries(connection, payload)
        elif action == "delete_custom_query":
            result = self._delete_custom_query(connection, payload)
        elif action == "persist_hotspots":
            result = self._persist_hotspots(connection, payload)
        else:
            raise MutationIdentityConflictError()
        return MutationApplication(
            result_contract="discovery_mutation_result",
            result_fields=result,
        )

    @staticmethod
    def _create_task(connection: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
        required = (
            "task_id",
            "topic",
            "created_at",
            "workspace_id",
            "brand_id",
            "query_generation_source",
        )
        if any(not isinstance(payload.get(name), str) for name in required):
            raise MutationIdentityConflictError()
        queries = payload.get("queries")
        if not isinstance(queries, list) or not all(isinstance(item, dict) for item in queries):
            raise MutationIdentityConflictError()

        task_id = payload["task_id"]
        created_at = payload["created_at"]
        connection.execute(
            "INSERT INTO mvp_tasks (task_id, topic, created_at) VALUES (?, ?, ?)",
            (task_id, payload["topic"], created_at),
        )
        for index, query in enumerate(queries):
            category = query.get("category")
            query_text = query.get("query_text")
            if not isinstance(category, str) or not isinstance(query_text, str):
                raise MutationIdentityConflictError()
            connection.execute(
                """
                INSERT INTO mvp_queries (query_id, task_id, category, query_text, sort_order, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), task_id, category, query_text, index, created_at),
            )
        connection.execute(
            """
            INSERT INTO v2_discovery_task_scope (
                task_id, workspace_id, brand_id, query_generation_version,
                query_generation_source, created_at
            ) VALUES (?, ?, ?, 'llm_v1', ?, ?)
            """,
            (
                task_id,
                payload["workspace_id"],
                payload["brand_id"],
                payload["query_generation_source"],
                created_at,
            ),
        )
        return {"task_id": task_id}

    @staticmethod
    def _add_custom_queries(connection: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
        task_id = payload.get("task_id")
        text = payload.get("text")
        if not isinstance(task_id, str) or not isinstance(text, str):
            raise MutationIdentityConflictError()
        if connection.execute("SELECT 1 FROM mvp_tasks WHERE task_id = ?", (task_id,)).fetchone() is None:
            raise DomainMutationRejectedError(f"discovery task not found: {task_id}")

        requested = [normalized for line in text.splitlines() if (normalized := _normalize_query(line))]
        existing_rows = connection.execute(
            "SELECT query_text FROM mvp_queries WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        existing = {_normalize_query(str(row[0])).lower() for row in existing_rows}
        sort_order = int(
            connection.execute(
                "SELECT COALESCE(MAX(sort_order), -1) AS max_order FROM mvp_queries WHERE task_id = ?",
                (task_id,),
            ).fetchone()[0]
        )
        created_count = 0
        skipped_count = 0
        created_at = datetime.now(timezone.utc).isoformat()
        for query_text in requested:
            key = query_text.lower()
            if not key or key in existing:
                skipped_count += 1
                continue
            sort_order += 1
            connection.execute(
                """
                INSERT INTO mvp_queries (query_id, task_id, category, query_text, sort_order, created_at)
                VALUES (?, ?, 'custom', ?, ?, ?)
                """,
                (str(uuid.uuid4()), task_id, query_text, sort_order, created_at),
            )
            existing.add(key)
            created_count += 1
        return {"created_count": created_count, "skipped_count": skipped_count}

    @staticmethod
    def _delete_custom_query(connection: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
        task_id = payload.get("task_id")
        query_id = payload.get("query_id")
        if not isinstance(task_id, str) or not isinstance(query_id, str):
            raise MutationIdentityConflictError()
        row = connection.execute(
            "SELECT category FROM mvp_queries WHERE task_id = ? AND query_id = ?",
            (task_id, query_id),
        ).fetchone()
        if row is None:
            raise DomainMutationRejectedError(f"discovery query not found: {query_id}")
        if row[0] != "custom":
            raise DomainMutationRejectedError("Only custom queries can be deleted")
        connection.execute(
            "DELETE FROM mvp_queries WHERE task_id = ? AND query_id = ?",
            (task_id, query_id),
        )
        return {"query_id": query_id}

    @staticmethod
    def _persist_hotspots(connection: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
        task_id = payload.get("task_id")
        status = payload.get("status")
        generated_at = payload.get("generated_at")
        error_message = payload.get("error_message", "")
        lists = payload.get("lists")
        if (
            not isinstance(task_id, str)
            or not isinstance(status, str)
            or not isinstance(generated_at, str)
            or not isinstance(error_message, str)
            or not isinstance(lists, list)
        ):
            raise MutationIdentityConflictError()
        snapshot_id = str(uuid.uuid4())
        connection.execute(
            """
            INSERT INTO mvp_hotspot_snapshots (snapshot_id, task_id, status, generated_at, error_message)
            VALUES (?, ?, ?, ?, ?)
            """,
            (snapshot_id, task_id, status, generated_at, error_message),
        )
        for hotspot_list in lists:
            if not isinstance(hotspot_list, dict):
                raise MutationIdentityConflictError()
            metric = hotspot_list.get("metric")
            items = hotspot_list.get("items")
            if not isinstance(metric, str) or not isinstance(items, list):
                raise MutationIdentityConflictError()
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    raise MutationIdentityConflictError()
                connection.execute(
                    """
                    INSERT INTO mvp_hotspot_items (
                        snapshot_item_id, snapshot_id, task_id, metric, rank_index, note_id, title,
                        source_url, author, excerpt, likes, comments, collections, query_sources_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        snapshot_id,
                        task_id,
                        metric,
                        index,
                        item.get("note_id") or "",
                        item.get("title") or "",
                        item.get("source_url") or "",
                        item.get("author") or "",
                        item.get("excerpt") or "",
                        int(item.get("likes") or 0),
                        int(item.get("comments") or 0),
                        int(item.get("collections") or 0),
                        json.dumps(item.get("query_sources") or [], ensure_ascii=False),
                    ),
                )
        return {"snapshot_id": snapshot_id}


def discovery_mutation_handlers() -> tuple[RuntimeMutationHandler, ...]:
    return (_DiscoveryMutationHandler(),)
