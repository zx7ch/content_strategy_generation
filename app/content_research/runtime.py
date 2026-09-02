"""Durable cost-ledger and checkpoint runtime for formal research stages."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.content_research.stores.sqlite_store import _BorrowedSQLiteConnection
from app.core.runtime_write_coordinator import (
    DomainMutationRejectedError,
    RuntimeWriteCoordinator,
    TypedMutation,
)
from app.core.runtime_write_registry import get_runtime_writer
from app.core.sqlite_connection_roles import (
    open_bootstrap_database,
    open_readonly_database,
)

STAGE_SEQUENCE = (
    "subject_structure",
    "query_plan",
    "collect",
    "selection",
    "detail",
    "coverage_decision",
    "fallback_decision",
    "relevance_revision",
    "packet",
    "facts",
    "admission",
    "reconcile",
    "aggregate",
    "compose",
    "faithfulness",
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_id(prefix: str, *values: str) -> str:
    digest = hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def canonical_fingerprint(value: dict[str, Any]) -> str:
    """Hash the immutable inputs that define whether a stage may be replayed."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LLMCostLedgerEntry:
    id: str
    research_plan_id: str
    usage_event_id: str
    amount: float | None
    status: str


@dataclass(frozen=True)
class StageCheckpoint:
    id: str
    subagent_task_id: str
    stage_name: str
    input_fingerprint: str
    status: str
    retry_count: int
    output_refs: tuple[str, ...]
    usage_event_ids: tuple[str, ...]


class LLMCostLedger:
    """Append actual provider usage after a call; deliberately never blocks a call."""

    def __init__(
        self,
        db_path: str,
        *,
        writer: RuntimeWriteCoordinator | None = None,
    ) -> None:
        self._db_path = db_path
        self._writer = writer or get_runtime_writer(self._db_path)
        self._coordinated_connection: _BorrowedSQLiteConnection | None = None

    def record_actual(
        self,
        *,
        research_plan_id: str,
        usage_event_id: str,
        amount: float,
        research_direction_id: str | None = None,
        stage_checkpoint_id: str | None = None,
    ) -> LLMCostLedgerEntry:
        if amount < 0:
            raise ValueError("actual LLM cost cannot be negative")
        return self._record(
            research_plan_id=research_plan_id,
            usage_event_id=usage_event_id,
            amount=amount,
            status="committed",
            research_direction_id=research_direction_id,
            stage_checkpoint_id=stage_checkpoint_id,
            reason=None,
        )

    def record_unknown(
        self,
        *,
        research_plan_id: str,
        usage_event_id: str,
        reason: str,
        research_direction_id: str | None = None,
        stage_checkpoint_id: str | None = None,
    ) -> LLMCostLedgerEntry:
        return self._record(
            research_plan_id=research_plan_id,
            usage_event_id=usage_event_id,
            amount=None,
            status="cost_unknown",
            research_direction_id=research_direction_id,
            stage_checkpoint_id=stage_checkpoint_id,
            reason=reason,
        )

    def _record(
        self,
        *,
        research_plan_id: str,
        usage_event_id: str,
        amount: float | None,
        status: str,
        research_direction_id: str | None,
        stage_checkpoint_id: str | None,
        reason: str | None,
    ) -> LLMCostLedgerEntry:
        if not research_plan_id or not usage_event_id:
            raise ValueError("research_plan_id and usage_event_id are required")
        if self._writer is not None:
            from app.content_research.stores.mutations import (
                decode_store_value,
                encode_store_value,
            )

            try:
                result = self._writer.submit_sync(
                    TypedMutation.create(
                        mutation_id=f"content_research_cost_{uuid.uuid4().hex}",
                        mutation_kind="execute_content_research_runtime_command",
                        domain_payload={
                            "action": "record_cost",
                            "kwargs": {
                                key: encode_store_value(value)
                                for key, value in {
                                    "research_plan_id": research_plan_id,
                                    "usage_event_id": usage_event_id,
                                    "amount": amount,
                                    "status": status,
                                    "research_direction_id": research_direction_id,
                                    "stage_checkpoint_id": stage_checkpoint_id,
                                    "reason": reason,
                                }.items()
                            },
                        },
                    )
                )
            except DomainMutationRejectedError as exc:
                raise ValueError(exc.safe_message) from None
            return decode_store_value(result.result_fields.get("result"))
        key = f"llm_usage:{usage_event_id}"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT id, research_plan_id, reservation_status, consumed_amount, payload_json "
                "FROM content_research_budget_ledger_entries WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            if row is not None:
                if row[1] != research_plan_id:
                    raise ValueError("usage event belongs to another research plan")
                payload = json.loads(row[4])
                return LLMCostLedgerEntry(
                    row[0],
                    row[1],
                    usage_event_id,
                    None if row[2] == "cost_unknown" else float(row[3]),
                    row[2],
                )
            entry_id = _stable_id("ble", research_plan_id, usage_event_id)
            payload = {
                "schema_version": "content_research_llm_cost_ledger_v1",
                "usage_event_id": usage_event_id,
                "cost_status": "unknown" if status == "cost_unknown" else "known",
                "reason": reason,
            }
            conn.execute(
                "INSERT INTO content_research_budget_ledger_entries "
                "(id, schema_version, research_plan_id, research_direction_id, idempotency_key, "
                "reservation_status, reserved_amount, consumed_amount, stage_checkpoint_id, payload_json, metadata_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, '{}', ?)",
                (
                    entry_id,
                    "content_research_budget_ledger_v1",
                    research_plan_id,
                    research_direction_id,
                    key,
                    status,
                    0 if amount is None else amount,
                    stage_checkpoint_id,
                    json.dumps(payload, sort_keys=True),
                    _utcnow(),
                ),
            )
        return LLMCostLedgerEntry(entry_id, research_plan_id, usage_event_id, amount, status)

    def _connect(self) -> sqlite3.Connection:
        if self._coordinated_connection is not None:
            return self._coordinated_connection  # type: ignore[return-value]
        if self._writer is not None:
            return open_readonly_database(self._db_path, timeout=30)
        return open_bootstrap_database(self._db_path, timeout=30)


class CheckpointRuntime:
    def __init__(
        self,
        db_path: str,
        *,
        writer: RuntimeWriteCoordinator | None = None,
    ) -> None:
        self._db_path = db_path
        self._writer = writer or get_runtime_writer(self._db_path)
        self._coordinated_connection: _BorrowedSQLiteConnection | None = None

    def checkpoint(
        self,
        *,
        subagent_task_id: str,
        stage_name: str,
        input_fingerprint: str,
        status: str,
        output_refs: tuple[str, ...] = (),
        usage_event_ids: tuple[str, ...] = (),
        failure: dict[str, Any] | None = None,
        retry_count: int = 0,
    ) -> StageCheckpoint:
        if stage_name not in STAGE_SEQUENCE:
            raise ValueError("invalid formal research stage")
        if status not in {"pending", "running", "completed", "failed_recoverable"}:
            raise ValueError("invalid checkpoint status")
        if retry_count > 2:
            raise ValueError("a stage permits at most two user-triggered recoveries")
        if self._writer is not None:
            from app.content_research.stores.mutations import (
                decode_store_value,
                encode_store_value,
            )

            try:
                result = self._writer.submit_sync(
                    TypedMutation.create(
                        mutation_id=f"content_research_checkpoint_{uuid.uuid4().hex}",
                        mutation_kind="execute_content_research_runtime_command",
                        domain_payload={
                            "action": "checkpoint",
                            "kwargs": {
                                key: encode_store_value(value)
                                for key, value in {
                                    "subagent_task_id": subagent_task_id,
                                    "stage_name": stage_name,
                                    "input_fingerprint": input_fingerprint,
                                    "status": status,
                                    "output_refs": output_refs,
                                    "usage_event_ids": usage_event_ids,
                                    "failure": failure,
                                    "retry_count": retry_count,
                                }.items()
                            },
                        },
                    )
                )
            except DomainMutationRejectedError as exc:
                raise ValueError(exc.safe_message) from None
            return decode_store_value(result.result_fields.get("result"))
        checkpoint_id = _stable_id("scp", subagent_task_id, stage_name, input_fingerprint)
        payload = {
            "schema_version": "content_research_stage_checkpoint_v1",
            "output_refs": list(output_refs),
            "usage_event_ids": list(usage_event_ids),
            "failure": failure,
        }
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, retry_count FROM content_research_stage_checkpoints WHERE id = ?",
                (checkpoint_id,),
            ).fetchone()
            if row is not None and row[0] == "completed" and status != "completed":
                raise ValueError("completed checkpoint cannot be reopened")
            retries = max(int(row[1]) if row else 0, retry_count)
            conn.execute(
                "INSERT INTO content_research_stage_checkpoints "
                "(id, schema_version, subagent_task_id, stage_name, input_fingerprint, status, retry_count, payload_json, metadata_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}', ?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status, retry_count=excluded.retry_count, payload_json=excluded.payload_json",
                (
                    checkpoint_id,
                    "content_research_stage_checkpoint_v1",
                    subagent_task_id,
                    stage_name,
                    input_fingerprint,
                    status,
                    retries,
                    json.dumps(payload, sort_keys=True),
                    _utcnow(),
                ),
            )
        return StageCheckpoint(
            checkpoint_id,
            subagent_task_id,
            stage_name,
            input_fingerprint,
            status,
            retries,
            output_refs,
            usage_event_ids,
        )

    def is_completed(
        self, *, subagent_task_id: str, stage_name: str, input_fingerprint: str
    ) -> bool:
        checkpoint_id = _stable_id("scp", subagent_task_id, stage_name, input_fingerprint)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM content_research_stage_checkpoints WHERE id = ?",
                (checkpoint_id,),
            ).fetchone()
        return row is not None and row[0] == "completed"

    def resume_stage(self, subagent_task_id: str) -> str | None:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT stage_name, status FROM content_research_stage_checkpoints WHERE subagent_task_id = ?",
                (subagent_task_id,),
            ).fetchall()
        completed = {row[0] for row in rows if row[1] == "completed"}
        return next((stage for stage in STAGE_SEQUENCE if stage not in completed), None)

    def _connect(self) -> sqlite3.Connection:
        if self._coordinated_connection is not None:
            return self._coordinated_connection  # type: ignore[return-value]
        if self._writer is not None:
            return open_readonly_database(self._db_path, timeout=30)
        return open_bootstrap_database(self._db_path)
