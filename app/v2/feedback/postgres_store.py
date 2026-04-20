"""Postgres-backed feedback store for V2 P1-5."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.v2.feedback.models import (
    EvaluationRunRecord,
    EvaluationRunSliceRecord,
    FeedbackEventRecord,
    PerformanceSnapshotRecord,
    PublishRecordRecord,
)


def _load_psycopg_jsonb():
    try:
        from psycopg import connect  # type: ignore
        from psycopg.rows import dict_row  # type: ignore
        from psycopg.types.json import Jsonb  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RuntimeError(
            "psycopg is required when POSTGRES_DSN is configured. "
            "Install project dependencies with psycopg[binary] support."
        ) from exc
    return connect, dict_row, Jsonb


def _default_connector(dsn: str):
    connect, dict_row, _jsonb = _load_psycopg_jsonb()
    return connect(dsn, row_factory=dict_row)


class PostgresFeedbackStore:
    def __init__(self, dsn: str, *, connector: Callable[[str], Any] | None = None) -> None:
        self._dsn = dsn
        self._connector = connector or _default_connector

    def save_publish_record(self, record: PublishRecordRecord) -> PublishRecordRecord:
        row = self._fetchone(
            """
            INSERT INTO publish_records (
                id, workspace_id, brand_id, channel_id, topic_pool_item_id, decision_event_id,
                decision_batch_id, publish_status, published_at, content_item_id, creative_variant,
                metadata, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                topic_pool_item_id = EXCLUDED.topic_pool_item_id,
                decision_event_id = EXCLUDED.decision_event_id,
                decision_batch_id = EXCLUDED.decision_batch_id,
                publish_status = EXCLUDED.publish_status,
                published_at = EXCLUDED.published_at,
                content_item_id = EXCLUDED.content_item_id,
                creative_variant = EXCLUDED.creative_variant,
                metadata = EXCLUDED.metadata,
                updated_at = EXCLUDED.updated_at
            RETURNING *
            """,
            (
                record.id,
                record.workspace_id,
                record.brand_id,
                record.channel_id,
                record.topic_pool_item_id,
                record.decision_event_id,
                record.decision_batch_id,
                record.publish_status,
                record.published_at,
                record.content_item_id,
                record.creative_variant,
                self._jsonb(record.metadata),
                record.created_at,
                record.updated_at,
            ),
        )
        return self._publish_from_row(row)

    def get_publish_record(self, publish_record_id: str) -> PublishRecordRecord | None:
        row = self._fetchone("SELECT * FROM publish_records WHERE id = %s LIMIT 1", (publish_record_id,))
        return self._publish_from_row(row) if row else None

    def list_publish_records(self, brand_id: str) -> list[PublishRecordRecord]:
        rows = self._fetchall(
            """
            SELECT *
            FROM publish_records
            WHERE brand_id = %s
            ORDER BY COALESCE(published_at, created_at) DESC, created_at DESC
            """,
            (brand_id,),
        )
        return [self._publish_from_row(row) for row in rows]

    def save_performance_snapshot(self, snapshot: PerformanceSnapshotRecord) -> PerformanceSnapshotRecord:
        row = self._fetchone(
            """
            INSERT INTO performance_snapshots (
                id, workspace_id, brand_id, publish_record_id, observation_window_hours, snapshot_at,
                reward_version, raw_metrics, normalized_metrics, short_term_reward, long_term_reward,
                composite_reward, metadata, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                snapshot_at = EXCLUDED.snapshot_at,
                reward_version = EXCLUDED.reward_version,
                raw_metrics = EXCLUDED.raw_metrics,
                normalized_metrics = EXCLUDED.normalized_metrics,
                short_term_reward = EXCLUDED.short_term_reward,
                long_term_reward = EXCLUDED.long_term_reward,
                composite_reward = EXCLUDED.composite_reward,
                metadata = EXCLUDED.metadata
            RETURNING *
            """,
            (
                snapshot.id,
                snapshot.workspace_id,
                snapshot.brand_id,
                snapshot.publish_record_id,
                snapshot.observation_window_hours,
                snapshot.snapshot_at,
                snapshot.reward_version,
                self._jsonb(snapshot.raw_metrics),
                self._jsonb(snapshot.normalized_metrics),
                snapshot.short_term_reward,
                snapshot.long_term_reward,
                snapshot.composite_reward,
                self._jsonb(snapshot.metadata),
                snapshot.created_at,
            ),
        )
        return self._performance_from_row(row)

    def list_performance_snapshots(self, brand_id: str) -> list[PerformanceSnapshotRecord]:
        rows = self._fetchall(
            """
            SELECT *
            FROM performance_snapshots
            WHERE brand_id = %s
            ORDER BY snapshot_at DESC, created_at DESC
            """,
            (brand_id,),
        )
        return [self._performance_from_row(row) for row in rows]

    def save_feedback_event(self, event: FeedbackEventRecord) -> FeedbackEventRecord:
        row = self._fetchone(
            """
            INSERT INTO feedback_events (
                id, workspace_id, brand_id, publish_record_id, decision_event_id, event_type,
                observation_window_hours, reward_version, reward_window_start_at, reward_window_end_at,
                reward_payload, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                reward_payload = EXCLUDED.reward_payload
            RETURNING *
            """,
            (
                event.id,
                event.workspace_id,
                event.brand_id,
                event.publish_record_id,
                event.decision_event_id,
                event.event_type,
                event.observation_window_hours,
                event.reward_version,
                event.reward_window_start_at,
                event.reward_window_end_at,
                self._jsonb(event.reward_payload),
                event.created_at,
            ),
        )
        return self._feedback_from_row(row)

    def list_feedback_events(self, brand_id: str) -> list[FeedbackEventRecord]:
        rows = self._fetchall(
            """
            SELECT *
            FROM feedback_events
            WHERE brand_id = %s
            ORDER BY created_at DESC
            """,
            (brand_id,),
        )
        return [self._feedback_from_row(row) for row in rows]

    def save_evaluation_run(self, run: EvaluationRunRecord) -> EvaluationRunRecord:
        row = self._fetchone(
            """
            INSERT INTO evaluation_runs (
                id, workspace_id, brand_id, evaluation_type, policy_name, policy_version,
                baseline_policy_name, baseline_policy_version, dataset_start_at, dataset_end_at,
                sample_count, status, summary, created_by_type, created_by_id, created_at, finished_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                status = EXCLUDED.status,
                sample_count = EXCLUDED.sample_count,
                summary = EXCLUDED.summary,
                finished_at = EXCLUDED.finished_at
            RETURNING *
            """,
            (
                run.id,
                run.workspace_id,
                run.brand_id,
                run.evaluation_type,
                run.policy_name,
                run.policy_version,
                run.baseline_policy_name,
                run.baseline_policy_version,
                run.dataset_start_at,
                run.dataset_end_at,
                run.sample_count,
                run.status,
                self._jsonb(run.summary),
                run.created_by_type,
                run.created_by_id,
                run.created_at,
                run.finished_at,
            ),
        )
        return self._evaluation_run_from_row(row)

    def get_evaluation_run(self, evaluation_run_id: str) -> EvaluationRunRecord | None:
        row = self._fetchone("SELECT * FROM evaluation_runs WHERE id = %s LIMIT 1", (evaluation_run_id,))
        return self._evaluation_run_from_row(row) if row else None

    def list_evaluation_runs(self, brand_id: str) -> list[EvaluationRunRecord]:
        rows = self._fetchall(
            """
            SELECT *
            FROM evaluation_runs
            WHERE brand_id = %s
            ORDER BY created_at DESC
            """,
            (brand_id,),
        )
        return [self._evaluation_run_from_row(row) for row in rows]

    def save_evaluation_run_slice(self, slice_record: EvaluationRunSliceRecord) -> EvaluationRunSliceRecord:
        row = self._fetchone(
            """
            INSERT INTO evaluation_run_slices (
                id, evaluation_run_id, slice_key, slice_value, sample_count, metrics, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                sample_count = EXCLUDED.sample_count,
                metrics = EXCLUDED.metrics
            RETURNING *
            """,
            (
                slice_record.id,
                slice_record.evaluation_run_id,
                slice_record.slice_key,
                slice_record.slice_value,
                slice_record.sample_count,
                self._jsonb(slice_record.metrics),
                slice_record.created_at,
            ),
        )
        return self._evaluation_slice_from_row(row)

    def list_evaluation_run_slices(self, evaluation_run_id: str) -> list[EvaluationRunSliceRecord]:
        rows = self._fetchall(
            """
            SELECT *
            FROM evaluation_run_slices
            WHERE evaluation_run_id = %s
            ORDER BY slice_key ASC, slice_value ASC
            """,
            (evaluation_run_id,),
        )
        return [self._evaluation_slice_from_row(row) for row in rows]

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
    def _publish_from_row(row: dict[str, Any]) -> PublishRecordRecord:
        return PublishRecordRecord(
            id=row["id"],
            workspace_id=row["workspace_id"],
            brand_id=row["brand_id"],
            channel_id=row["channel_id"],
            topic_pool_item_id=row.get("topic_pool_item_id"),
            decision_event_id=row.get("decision_event_id"),
            decision_batch_id=row.get("decision_batch_id"),
            publish_status=row["publish_status"],
            published_at=row.get("published_at"),
            content_item_id=row.get("content_item_id"),
            creative_variant=row.get("creative_variant"),
            metadata=row.get("metadata") or {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _performance_from_row(row: dict[str, Any]) -> PerformanceSnapshotRecord:
        return PerformanceSnapshotRecord(
            id=row["id"],
            workspace_id=row["workspace_id"],
            brand_id=row["brand_id"],
            publish_record_id=row["publish_record_id"],
            observation_window_hours=int(row["observation_window_hours"]),
            snapshot_at=row["snapshot_at"],
            reward_version=row["reward_version"],
            raw_metrics=row.get("raw_metrics") or {},
            normalized_metrics=row.get("normalized_metrics") or {},
            short_term_reward=float(row["short_term_reward"]),
            long_term_reward=float(row["long_term_reward"]),
            composite_reward=float(row["composite_reward"]),
            metadata=row.get("metadata") or {},
            created_at=row["created_at"],
        )

    @staticmethod
    def _feedback_from_row(row: dict[str, Any]) -> FeedbackEventRecord:
        return FeedbackEventRecord(
            id=row["id"],
            workspace_id=row["workspace_id"],
            brand_id=row["brand_id"],
            publish_record_id=row["publish_record_id"],
            decision_event_id=row.get("decision_event_id"),
            event_type=row["event_type"],
            observation_window_hours=row.get("observation_window_hours"),
            reward_version=row["reward_version"],
            reward_window_start_at=row.get("reward_window_start_at"),
            reward_window_end_at=row.get("reward_window_end_at"),
            reward_payload=row.get("reward_payload") or {},
            created_at=row["created_at"],
        )

    @staticmethod
    def _evaluation_run_from_row(row: dict[str, Any]) -> EvaluationRunRecord:
        return EvaluationRunRecord(
            id=row["id"],
            workspace_id=row["workspace_id"],
            brand_id=row["brand_id"],
            evaluation_type=row["evaluation_type"],
            policy_name=row["policy_name"],
            policy_version=row["policy_version"],
            baseline_policy_name=row.get("baseline_policy_name"),
            baseline_policy_version=row.get("baseline_policy_version"),
            dataset_start_at=row.get("dataset_start_at"),
            dataset_end_at=row.get("dataset_end_at"),
            sample_count=int(row["sample_count"]),
            status=row["status"],
            summary=row.get("summary") or {},
            created_by_type=row["created_by_type"],
            created_by_id=row.get("created_by_id"),
            created_at=row["created_at"],
            finished_at=row.get("finished_at"),
        )

    @staticmethod
    def _evaluation_slice_from_row(row: dict[str, Any]) -> EvaluationRunSliceRecord:
        return EvaluationRunSliceRecord(
            id=row["id"],
            evaluation_run_id=row["evaluation_run_id"],
            slice_key=row["slice_key"],
            slice_value=row["slice_value"],
            sample_count=int(row["sample_count"]),
            metrics=row.get("metrics") or {},
            created_at=row["created_at"],
        )
