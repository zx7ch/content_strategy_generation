"""Postgres-backed decision store for V2 P1-4."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.v2.decision.models import (
    CandidateSetSnapshotRecord,
    DecisionBatchItemRecord,
    DecisionBatchRecord,
    DecisionEventRecord,
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


class PostgresDecisionStore:
    def __init__(
        self,
        dsn: str,
        *,
        connector: Callable[[str], Any] | None = None,
    ) -> None:
        self._dsn = dsn
        self._connector = connector or _default_connector

    def save_decision_batch(self, batch: DecisionBatchRecord) -> DecisionBatchRecord:
        row = self._fetchone(
            """
            INSERT INTO decision_batches (
                id, workspace_id, brand_id, brand_state_snapshot_id, brand_policy_config_id,
                objective, exploration_mode, context_snapshot, policy_name, policy_version,
                candidate_count, chosen_count, requested_slot_count, batch_status,
                created_by_type, created_by_id, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                candidate_count = EXCLUDED.candidate_count,
                chosen_count = EXCLUDED.chosen_count,
                requested_slot_count = EXCLUDED.requested_slot_count,
                batch_status = EXCLUDED.batch_status
            RETURNING *
            """,
            (
                batch.id,
                batch.workspace_id,
                batch.brand_id,
                batch.brand_state_snapshot_id,
                batch.brand_policy_config_id,
                batch.objective,
                batch.exploration_mode,
                self._jsonb(batch.context_snapshot),
                batch.policy_name,
                batch.policy_version,
                batch.candidate_count,
                batch.chosen_count,
                batch.requested_slot_count,
                batch.batch_status,
                batch.created_by_type,
                batch.created_by_id,
                batch.created_at,
            ),
        )
        return self._batch_from_row(row)

    def get_decision_batch(self, batch_id: str) -> DecisionBatchRecord | None:
        row = self._fetchone(
            "SELECT * FROM decision_batches WHERE id = %s LIMIT 1",
            (batch_id,),
        )
        return self._batch_from_row(row) if row else None

    def list_decision_batches(self, brand_id: str) -> list[DecisionBatchRecord]:
        rows = self._fetchall(
            """
            SELECT *
            FROM decision_batches
            WHERE brand_id = %s
            ORDER BY created_at DESC
            """,
            (brand_id,),
        )
        return [self._batch_from_row(row) for row in rows]

    def save_decision_event(self, event: DecisionEventRecord) -> DecisionEventRecord:
        row = self._fetchone(
            """
            INSERT INTO decision_events (
                id, workspace_id, brand_id, decision_batch_id, brand_state_snapshot_id,
                brand_policy_config_id, slot_index, serving_policy_name, serving_policy_version,
                logging_policy_name, logging_policy_version, decision_mode, exploration_mode,
                objective, context_features, candidate_set, ranked_list, chosen_action_id,
                propensities, reward_version, normalization_window_spec, sampling_metadata, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                ranked_list = EXCLUDED.ranked_list,
                propensities = EXCLUDED.propensities
            RETURNING *
            """,
            (
                event.id,
                event.workspace_id,
                event.brand_id,
                event.decision_batch_id,
                event.brand_state_snapshot_id,
                event.brand_policy_config_id,
                event.slot_index,
                event.serving_policy_name,
                event.serving_policy_version,
                event.logging_policy_name,
                event.logging_policy_version,
                event.decision_mode,
                event.exploration_mode,
                event.objective,
                self._jsonb(event.context_features),
                self._jsonb(event.candidate_set),
                self._jsonb(event.ranked_list),
                event.chosen_action_id,
                self._jsonb(event.propensities),
                event.reward_version,
                self._jsonb(event.normalization_window_spec),
                self._jsonb(event.sampling_metadata),
                event.created_at,
            ),
        )
        return self._event_from_row(row)

    def get_decision_event(self, event_id: str) -> DecisionEventRecord | None:
        row = self._fetchone(
            "SELECT * FROM decision_events WHERE id = %s LIMIT 1",
            (event_id,),
        )
        return self._event_from_row(row) if row else None

    def list_decision_events(self, brand_id: str) -> list[DecisionEventRecord]:
        rows = self._fetchall(
            """
            SELECT *
            FROM decision_events
            WHERE brand_id = %s
            ORDER BY created_at DESC, slot_index DESC
            """,
            (brand_id,),
        )
        return [self._event_from_row(row) for row in rows]

    def save_decision_batch_item(self, item: DecisionBatchItemRecord) -> DecisionBatchItemRecord:
        row = self._fetchone(
            """
            INSERT INTO decision_batch_items (
                batch_id, topic_pool_item_id, selected_slot_index, final_rank_position,
                source_decision_event_id, review_status, reviewed_at, reviewed_by_type, reviewed_by_id,
                edited_title, edited_angle, edited_hypothesis, review_notes, score, reason_codes, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (batch_id, topic_pool_item_id) DO UPDATE SET
                review_status = EXCLUDED.review_status,
                reviewed_at = EXCLUDED.reviewed_at,
                reviewed_by_type = EXCLUDED.reviewed_by_type,
                reviewed_by_id = EXCLUDED.reviewed_by_id,
                edited_title = EXCLUDED.edited_title,
                edited_angle = EXCLUDED.edited_angle,
                edited_hypothesis = EXCLUDED.edited_hypothesis,
                review_notes = EXCLUDED.review_notes,
                score = EXCLUDED.score,
                reason_codes = EXCLUDED.reason_codes,
                metadata = EXCLUDED.metadata
            RETURNING *
            """,
            (
                item.batch_id,
                item.topic_pool_item_id,
                item.selected_slot_index,
                item.final_rank_position,
                item.source_decision_event_id,
                item.review_status,
                item.reviewed_at,
                item.reviewed_by_type,
                item.reviewed_by_id,
                item.edited_title,
                item.edited_angle,
                item.edited_hypothesis,
                item.review_notes,
                item.score,
                self._jsonb(item.reason_codes),
                self._jsonb(item.metadata),
            ),
        )
        return self._batch_item_from_row(row)

    def get_decision_batch_item_by_slot(
        self,
        *,
        batch_id: str,
        slot_index: int,
    ) -> DecisionBatchItemRecord | None:
        row = self._fetchone(
            """
            SELECT *
            FROM decision_batch_items
            WHERE batch_id = %s AND selected_slot_index = %s
            LIMIT 1
            """,
            (batch_id, slot_index),
        )
        return self._batch_item_from_row(row) if row else None

    def list_decision_batch_items(self, batch_id: str) -> list[DecisionBatchItemRecord]:
        rows = self._fetchall(
            """
            SELECT *
            FROM decision_batch_items
            WHERE batch_id = %s
            ORDER BY selected_slot_index ASC
            """,
            (batch_id,),
        )
        return [self._batch_item_from_row(row) for row in rows]

    def save_candidate_set_snapshot(
        self,
        snapshot: CandidateSetSnapshotRecord,
    ) -> CandidateSetSnapshotRecord:
        row = self._fetchone(
            """
            INSERT INTO candidate_set_snapshots (
                id, workspace_id, brand_id, decision_batch_id, decision_event_id, snapshot_scope,
                slot_index, candidate_count, candidate_set, metrics, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                candidate_set = EXCLUDED.candidate_set,
                metrics = EXCLUDED.metrics
            RETURNING *
            """,
            (
                snapshot.id,
                snapshot.workspace_id,
                snapshot.brand_id,
                snapshot.decision_batch_id,
                snapshot.decision_event_id,
                snapshot.snapshot_scope,
                snapshot.slot_index,
                snapshot.candidate_count,
                self._jsonb(snapshot.candidate_set),
                self._jsonb(snapshot.metrics),
                snapshot.created_at,
            ),
        )
        return self._snapshot_from_row(row)

    def list_candidate_set_snapshots(
        self,
        *,
        brand_id: str,
        decision_batch_id: str | None = None,
    ) -> list[CandidateSetSnapshotRecord]:
        if decision_batch_id is None:
            rows = self._fetchall(
                """
                SELECT *
                FROM candidate_set_snapshots
                WHERE brand_id = %s
                ORDER BY created_at DESC, slot_index DESC NULLS LAST
                """,
                (brand_id,),
            )
        else:
            rows = self._fetchall(
                """
                SELECT *
                FROM candidate_set_snapshots
                WHERE brand_id = %s AND decision_batch_id = %s
                ORDER BY created_at DESC, slot_index DESC NULLS LAST
                """,
                (brand_id, decision_batch_id),
            )
        return [self._snapshot_from_row(row) for row in rows]

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
    def _batch_from_row(row: dict[str, Any]) -> DecisionBatchRecord:
        return DecisionBatchRecord(
            id=row["id"],
            workspace_id=row["workspace_id"],
            brand_id=row["brand_id"],
            brand_state_snapshot_id=row["brand_state_snapshot_id"],
            brand_policy_config_id=row["brand_policy_config_id"],
            objective=row["objective"],
            exploration_mode=row["exploration_mode"],
            context_snapshot=row.get("context_snapshot") or {},
            policy_name=row["policy_name"],
            policy_version=row["policy_version"],
            candidate_count=int(row["candidate_count"]),
            chosen_count=int(row["chosen_count"]),
            requested_slot_count=int(row["requested_slot_count"]),
            batch_status=row["batch_status"],
            created_by_type=row["created_by_type"],
            created_by_id=row.get("created_by_id"),
            created_at=row["created_at"],
        )

    @staticmethod
    def _event_from_row(row: dict[str, Any]) -> DecisionEventRecord:
        return DecisionEventRecord(
            id=row["id"],
            workspace_id=row["workspace_id"],
            brand_id=row["brand_id"],
            decision_batch_id=row["decision_batch_id"],
            brand_state_snapshot_id=row["brand_state_snapshot_id"],
            brand_policy_config_id=row["brand_policy_config_id"],
            slot_index=int(row["slot_index"]),
            serving_policy_name=row["serving_policy_name"],
            serving_policy_version=row["serving_policy_version"],
            logging_policy_name=row["logging_policy_name"],
            logging_policy_version=row["logging_policy_version"],
            decision_mode=row["decision_mode"],
            exploration_mode=row["exploration_mode"],
            objective=row["objective"],
            context_features=row.get("context_features") or {},
            candidate_set=row.get("candidate_set") or [],
            ranked_list=row.get("ranked_list") or [],
            chosen_action_id=row["chosen_action_id"],
            propensities=row.get("propensities") or [],
            reward_version=row["reward_version"],
            normalization_window_spec=row.get("normalization_window_spec") or {},
            sampling_metadata=row.get("sampling_metadata") or {},
            created_at=row["created_at"],
        )

    @staticmethod
    def _batch_item_from_row(row: dict[str, Any]) -> DecisionBatchItemRecord:
        return DecisionBatchItemRecord(
            batch_id=row["batch_id"],
            topic_pool_item_id=row["topic_pool_item_id"],
            selected_slot_index=int(row["selected_slot_index"]),
            final_rank_position=int(row["final_rank_position"]),
            source_decision_event_id=row.get("source_decision_event_id"),
            review_status=row["review_status"],
            reviewed_at=row.get("reviewed_at"),
            reviewed_by_type=row.get("reviewed_by_type"),
            reviewed_by_id=row.get("reviewed_by_id"),
            edited_title=row.get("edited_title"),
            edited_angle=row.get("edited_angle"),
            edited_hypothesis=row.get("edited_hypothesis"),
            review_notes=row.get("review_notes"),
            score=float(row["score"]),
            reason_codes=row.get("reason_codes") or [],
            metadata=row.get("metadata") or {},
        )

    @staticmethod
    def _snapshot_from_row(row: dict[str, Any]) -> CandidateSetSnapshotRecord:
        return CandidateSetSnapshotRecord(
            id=row["id"],
            workspace_id=row["workspace_id"],
            brand_id=row["brand_id"],
            decision_batch_id=row["decision_batch_id"],
            decision_event_id=row.get("decision_event_id"),
            snapshot_scope=row["snapshot_scope"],
            slot_index=row.get("slot_index"),
            candidate_count=int(row["candidate_count"]),
            candidate_set=row.get("candidate_set") or [],
            metrics=row.get("metrics") or {},
            created_at=row["created_at"],
        )
