"""Postgres-backed topic pool store for V2 P1-3 topic inventory."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.v2.topic_pool.models import TopicPoolItemRecord


def _load_psycopg_jsonb():
    try:
        from psycopg import connect  # type: ignore
        from psycopg.rows import dict_row  # type: ignore
        from psycopg.types.json import Jsonb  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional runtime package
        raise RuntimeError(
            "psycopg is required when POSTGRES_DSN is configured. "
            "Install project dependencies with psycopg[binary] support."
        ) from exc
    return connect, dict_row, Jsonb


def _default_connector(dsn: str):
    connect, dict_row, _jsonb = _load_psycopg_jsonb()
    return connect(dsn, row_factory=dict_row)


class PostgresTopicPoolStore:
    def __init__(
        self,
        dsn: str,
        *,
        connector: Callable[[str], Any] | None = None,
    ) -> None:
        self._dsn = dsn
        self._connector = connector or _default_connector

    def save_topic_pool_item(self, item: TopicPoolItemRecord) -> TopicPoolItemRecord:
        existing = self.get_topic_pool_item_by_topic(brand_id=item.brand_id, topic_id=item.topic_id)
        item_id = existing.id if existing else item.id
        created_at = existing.created_at if existing else item.created_at
        row = self._fetchone(
            """
            INSERT INTO topic_pool_items (
                id, workspace_id, brand_id, topic_id, title, angle, hypothesis, evidence_summary,
                source_agent, source_run_id, status, novelty_score, fit_score, trend_score,
                historical_reward_score, policy_score, final_score, last_scored_at, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                title = EXCLUDED.title,
                angle = EXCLUDED.angle,
                hypothesis = EXCLUDED.hypothesis,
                evidence_summary = EXCLUDED.evidence_summary,
                source_agent = EXCLUDED.source_agent,
                source_run_id = EXCLUDED.source_run_id,
                status = EXCLUDED.status,
                novelty_score = EXCLUDED.novelty_score,
                fit_score = EXCLUDED.fit_score,
                trend_score = EXCLUDED.trend_score,
                historical_reward_score = EXCLUDED.historical_reward_score,
                policy_score = EXCLUDED.policy_score,
                final_score = EXCLUDED.final_score,
                last_scored_at = EXCLUDED.last_scored_at,
                updated_at = EXCLUDED.updated_at
            RETURNING id, workspace_id, brand_id, topic_id, title, angle, hypothesis, evidence_summary,
                      source_agent, source_run_id, status, novelty_score, fit_score, trend_score,
                      historical_reward_score, policy_score, final_score, last_scored_at, created_at, updated_at
            """,
            (
                item_id,
                item.workspace_id,
                item.brand_id,
                item.topic_id,
                item.title,
                item.angle,
                item.hypothesis,
                self._jsonb(item.evidence_summary),
                item.source_agent,
                item.source_run_id,
                item.status,
                item.novelty_score,
                item.fit_score,
                item.trend_score,
                item.historical_reward_score,
                item.policy_score,
                item.final_score,
                item.last_scored_at,
                created_at,
                item.updated_at,
            ),
        )
        return self._item_from_row(row)

    def get_topic_pool_item(self, item_id: str) -> TopicPoolItemRecord | None:
        row = self._fetchone(
            """
            SELECT id, workspace_id, brand_id, topic_id, title, angle, hypothesis, evidence_summary,
                   source_agent, source_run_id, status, novelty_score, fit_score, trend_score,
                   historical_reward_score, policy_score, final_score, last_scored_at, created_at, updated_at
            FROM topic_pool_items
            WHERE id = %s
            LIMIT 1
            """,
            (item_id,),
        )
        return self._item_from_row(row) if row else None

    def get_topic_pool_item_by_topic(self, *, brand_id: str, topic_id: str) -> TopicPoolItemRecord | None:
        row = self._fetchone(
            """
            SELECT id, workspace_id, brand_id, topic_id, title, angle, hypothesis, evidence_summary,
                   source_agent, source_run_id, status, novelty_score, fit_score, trend_score,
                   historical_reward_score, policy_score, final_score, last_scored_at, created_at, updated_at
            FROM topic_pool_items
            WHERE brand_id = %s AND topic_id = %s
            LIMIT 1
            """,
            (brand_id, topic_id),
        )
        return self._item_from_row(row) if row else None

    def list_topic_pool_items(
        self,
        brand_id: str,
        *,
        include_archived: bool = False,
    ) -> list[TopicPoolItemRecord]:
        if include_archived:
            rows = self._fetchall(
                """
                SELECT id, workspace_id, brand_id, topic_id, title, angle, hypothesis, evidence_summary,
                       source_agent, source_run_id, status, novelty_score, fit_score, trend_score,
                       historical_reward_score, policy_score, final_score, last_scored_at, created_at, updated_at
                FROM topic_pool_items
                WHERE brand_id = %s
                ORDER BY final_score DESC, updated_at DESC, title ASC
                """,
                (brand_id,),
            )
        else:
            rows = self._fetchall(
                """
                SELECT id, workspace_id, brand_id, topic_id, title, angle, hypothesis, evidence_summary,
                       source_agent, source_run_id, status, novelty_score, fit_score, trend_score,
                       historical_reward_score, policy_score, final_score, last_scored_at, created_at, updated_at
                FROM topic_pool_items
                WHERE brand_id = %s AND status <> 'archived'
                ORDER BY final_score DESC, updated_at DESC, title ASC
                """,
                (brand_id,),
            )
        return [self._item_from_row(row) for row in rows]

    def _jsonb(self, value: Any):
        _, _, Jsonb = _load_psycopg_jsonb()
        return Jsonb(value)

    def _fetchone(self, sql: str, params: tuple[Any, ...]):
        with self._connector(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                row = cursor.fetchone()
            commit = getattr(connection, "commit", None)
            if callable(commit):
                commit()
        return row

    def _fetchall(self, sql: str, params: tuple[Any, ...]):
        with self._connector(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                rows = cursor.fetchall() or []
        return rows

    @staticmethod
    def _item_from_row(row: dict[str, Any]) -> TopicPoolItemRecord:
        return TopicPoolItemRecord(
            id=row["id"],
            workspace_id=row["workspace_id"],
            brand_id=row["brand_id"],
            topic_id=row["topic_id"],
            title=row["title"],
            angle=row["angle"],
            hypothesis=row["hypothesis"],
            evidence_summary=row.get("evidence_summary") or {},
            source_agent=row["source_agent"],
            source_run_id=row.get("source_run_id"),
            status=row["status"],
            novelty_score=float(row["novelty_score"]),
            fit_score=float(row["fit_score"]),
            trend_score=float(row["trend_score"]),
            historical_reward_score=float(row["historical_reward_score"]),
            policy_score=float(row["policy_score"]),
            final_score=float(row["final_score"]),
            last_scored_at=row.get("last_scored_at"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
