"""SQLite adapter for Content Research P0 records."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, TypeVar

from app.content_research.bootstrap import bootstrap_content_research_schema
from app.content_research.contracts import DirectionContract, RunPolicySnapshot, SamplePolicy
from app.content_research.evidence.models import (
    EvidenceLineageRecord,
    EvidenceRecord,
)
from app.content_research.models import (
    HumanDecisionRecord,
    ObservationEventRecord,
    ResearchBriefRecord,
    ResearchDirectionRecord,
    ResearchPlanRecord,
    ResearchResultSnapshotRecord,
    SubagentTaskRecord,
    TraceRecord,
)
from app.content_research.persistence_models import (
    AggregateClaimRecord,
    BudgetLedgerEntryRecord,
    CanonicalSourceRecord,
    ClaimAdmissionDecisionRecord,
    ClaimCandidateRecord,
    CrossDirectionRecord,
    DirectionalEvidencePacketRecord,
    DirectionResultDecisionRecord,
    DirectionSourceProjectionRecord,
    MarketingConclusionCandidateRecord,
    MarketingConclusionDecisionRecord,
    ReportDraftRecord,
    ReportFaithfulnessDecisionRecord,
    ReportPublicationRecord,
    StageCheckpointRecord,
    TypedPersistenceRecord,
    WeakSignalRecord,
)
from app.content_research.scope_contract import (
    CoverageSnapshot,
    ResearchScopeContract,
    ScopeAuditEvent,
    ScopeConstraint,
    ScopeQueryGroup,
)


def _fmt_dt(value: datetime) -> str:
    return value.isoformat()


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(timezone.utc)


def _loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        result = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return result if isinstance(result, dict) else {}


def _loads_list(value: str | None) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        result = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return result if isinstance(result, list) else []


def _loads_any_list(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        result = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return result if isinstance(result, list) else []


def _dumps(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _dumps_list(value: list[dict[str, Any]]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _dumps_any_list(value: list[Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _validate_payload(record_type: str, payload: dict[str, Any]) -> None:
    if not payload.get("schema_version"):
        raise ValueError(f"{record_type} payload must include schema_version")


_TypedRecordT = TypeVar("_TypedRecordT", bound=TypedPersistenceRecord)

_TYPED_RECORD_TABLES: dict[type[TypedPersistenceRecord], tuple[str, tuple[str, ...]]] = {
    CanonicalSourceRecord: ("content_research_canonical_sources", ("platform", "platform_source_kind", "platform_source_id", "canonical_url")),
    DirectionSourceProjectionRecord: ("content_research_direction_source_projections", ("workflow_run_id", "research_direction_id", "canonical_source_id", "evidence_packet_id")),
    DirectionalEvidencePacketRecord: ("content_research_directional_evidence_packets", ("workflow_run_id", "research_direction_id", "canonical_source_id", "field_projection_hash")),
    ClaimCandidateRecord: ("content_research_claim_candidates", ("workflow_run_id", "research_direction_id", "evidence_packet_id", "statement", "intent_id", "claim_type", "requested_state")),
    ClaimAdmissionDecisionRecord: ("content_research_claim_admission_decisions", ("research_direction_id", "claim_candidate_id", "decision", "policy_snapshot_id")),
    DirectionResultDecisionRecord: ("content_research_direction_result_decisions", ("research_direction_id", "policy_snapshot_id")),
    WeakSignalRecord: ("content_research_weak_signals", ("admission_decision_id",)),
    CrossDirectionRecord: ("content_research_cross_direction_records", ("research_plan_id", "record_type")),
    AggregateClaimRecord: ("content_research_aggregate_claims", ("research_plan_id", "aggregate_type")),
    MarketingConclusionCandidateRecord: ("content_research_marketing_conclusion_candidates", ("workflow_run_id", "research_plan_id", "track")),
    MarketingConclusionDecisionRecord: ("content_research_marketing_conclusion_decisions", ("workflow_run_id", "research_plan_id", "candidate_id", "track", "state")),
    StageCheckpointRecord: ("content_research_stage_checkpoints", ("workflow_run_id", "subagent_task_id", "stage_name", "input_fingerprint", "status", "retry_count", "started_at", "finished_at")),
    BudgetLedgerEntryRecord: ("content_research_budget_ledger_entries", ("research_plan_id", "research_direction_id", "idempotency_key", "reservation_status", "reserved_amount", "consumed_amount", "stage_checkpoint_id")),
    ReportDraftRecord: ("content_research_report_drafts", ("workflow_run_id", "research_plan_id", "governed_snapshot_id", "governed_snapshot_version", "input_fingerprint", "policy_version", "algorithm_version", "previous_version_id")),
    ReportFaithfulnessDecisionRecord: ("content_research_report_faithfulness_decisions", ("workflow_run_id", "research_plan_id", "governed_snapshot_id", "governed_snapshot_version", "input_fingerprint", "policy_version", "algorithm_version", "report_draft_id", "previous_version_id")),
    ReportPublicationRecord: ("content_research_report_publications", ("workflow_run_id", "research_plan_id", "governed_snapshot_id", "governed_snapshot_version", "input_fingerprint", "policy_version", "algorithm_version", "report_draft_id", "faithfulness_decision_id", "publication_state", "previous_version_id")),
}


class SQLiteContentResearchStore:
    """Local SQLite persistence for Content Research business records."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        bootstrap_content_research_schema(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        # WAL is a database-level bootstrap setting. Re-applying it on every
        # Trace/read connection can wait behind a checkpoint writer and turns
        # an otherwise read-only request into a lock-contending operation.
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def save_brief(self, brief: ResearchBriefRecord) -> ResearchBriefRecord:
        _validate_payload("ResearchBrief", brief.payload)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO content_research_briefs
                     (id, workflow_run_id, thread_id, schema_version, status,
                      created_at, updated_at, payload_json, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     workflow_run_id=excluded.workflow_run_id,
                     thread_id=excluded.thread_id,
                     schema_version=excluded.schema_version,
                     status=excluded.status,
                     updated_at=excluded.updated_at,
                     payload_json=excluded.payload_json,
                     metadata_json=excluded.metadata_json""",
                (
                    brief.id,
                    brief.workflow_run_id,
                    brief.thread_id,
                    brief.schema_version,
                    brief.status,
                    _fmt_dt(brief.created_at),
                    _fmt_dt(brief.updated_at),
                    _dumps(brief.payload),
                    _dumps(brief.metadata),
                ),
            )
        return brief

    def get_brief(self, brief_id: str) -> ResearchBriefRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM content_research_briefs WHERE id = ?",
                (brief_id,),
            ).fetchone()
        return self._row_to_brief(row) if row else None

    def get_brief_by_presearch_attempt(self, attempt_id: str) -> ResearchBriefRecord | None:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM content_research_briefs ORDER BY created_at DESC",
            ).fetchall()
        for row in rows:
            brief = self._row_to_brief(row)
            if brief.payload.get("attempt_id") == attempt_id:
                return brief
        return None

    def get_brief_by_workflow(self, workflow_run_id: str) -> ResearchBriefRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM content_research_briefs WHERE workflow_run_id = ? ORDER BY updated_at DESC LIMIT 1",
                (workflow_run_id,),
            ).fetchone()
        return self._row_to_brief(row) if row else None

    def delete_workflow(self, workflow_run_id: str) -> None:
        with self._connect() as conn:
            evidence_ids = [row[0] for row in conn.execute("SELECT id FROM content_research_evidence_records WHERE workflow_run_id = ?", (workflow_run_id,))]
            for evidence_id in evidence_ids:
                conn.execute("DELETE FROM content_research_evidence_lineage WHERE evidence_record_id = ?", (evidence_id,))
            for table in ("content_research_evidence_records", "content_research_human_decisions", "content_research_observation_events", "content_research_traces", "content_research_result_snapshots", "content_research_subagent_tasks", "content_research_directions", "content_research_plans", "content_research_briefs"):
                conn.execute(f"DELETE FROM {table} WHERE workflow_run_id = ?", (workflow_run_id,))

    def save_plan(self, plan: ResearchPlanRecord) -> ResearchPlanRecord:
        _validate_payload("ResearchPlan", plan.payload)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO content_research_plans
                     (id, brief_id, workflow_run_id, thread_id, schema_version, status,
                      created_at, updated_at, payload_json, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     brief_id=excluded.brief_id,
                     workflow_run_id=excluded.workflow_run_id,
                     thread_id=excluded.thread_id,
                     schema_version=excluded.schema_version,
                     status=excluded.status,
                     updated_at=excluded.updated_at,
                     payload_json=excluded.payload_json,
                     metadata_json=excluded.metadata_json""",
                (
                    plan.id,
                    plan.brief_id,
                    plan.workflow_run_id,
                    plan.thread_id,
                    plan.schema_version,
                    plan.status,
                    _fmt_dt(plan.created_at),
                    _fmt_dt(plan.updated_at),
                    _dumps(plan.payload),
                    _dumps(plan.metadata),
                ),
            )
        return plan

    def get_plan(self, plan_id: str) -> ResearchPlanRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM content_research_plans WHERE id = ?",
                (plan_id,),
            ).fetchone()
        return self._row_to_plan(row) if row else None

    def list_plans_for_brief(self, brief_id: str) -> list[ResearchPlanRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM content_research_plans WHERE brief_id = ? ORDER BY created_at ASC",
                (brief_id,),
            ).fetchall()
        return [self._row_to_plan(row) for row in rows]

    def save_direction(self, direction: ResearchDirectionRecord) -> ResearchDirectionRecord:
        _validate_payload("ResearchDirection", direction.payload)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO content_research_directions
                     (id, plan_id, workflow_run_id, thread_id, schema_version, status, priority,
                      created_at, updated_at, payload_json, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     plan_id=excluded.plan_id,
                     workflow_run_id=excluded.workflow_run_id,
                     thread_id=excluded.thread_id,
                     schema_version=excluded.schema_version,
                     status=excluded.status,
                     priority=excluded.priority,
                     updated_at=excluded.updated_at,
                     payload_json=excluded.payload_json,
                     metadata_json=excluded.metadata_json""",
                (
                    direction.id,
                    direction.plan_id,
                    direction.workflow_run_id,
                    direction.thread_id,
                    direction.schema_version,
                    direction.status,
                    direction.priority,
                    _fmt_dt(direction.created_at),
                    _fmt_dt(direction.updated_at),
                    _dumps(direction.payload),
                    _dumps(direction.metadata),
                ),
            )
        return direction

    def list_directions_for_plan(self, plan_id: str) -> list[ResearchDirectionRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM content_research_directions WHERE plan_id = ? ORDER BY priority ASC, created_at ASC",
                (plan_id,),
            ).fetchall()
        return [self._row_to_direction(row) for row in rows]

    def save_subagent_task(self, task: SubagentTaskRecord) -> SubagentTaskRecord:
        _validate_payload("SubagentTask", task.payload)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO content_research_subagent_tasks
                     (id, workflow_run_id, thread_id, schema_version, status, plan_id,
                      direction_id, created_at, updated_at, payload_json, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     workflow_run_id=excluded.workflow_run_id,
                     thread_id=excluded.thread_id,
                     schema_version=excluded.schema_version,
                     status=excluded.status,
                     plan_id=excluded.plan_id,
                     direction_id=excluded.direction_id,
                     updated_at=excluded.updated_at,
                     payload_json=excluded.payload_json,
                     metadata_json=excluded.metadata_json""",
                (
                    task.id,
                    task.workflow_run_id,
                    task.thread_id,
                    task.schema_version,
                    task.status,
                    task.plan_id,
                    task.direction_id,
                    _fmt_dt(task.created_at),
                    _fmt_dt(task.updated_at),
                    _dumps(task.payload),
                    _dumps(task.metadata),
                ),
            )
        return task

    def get_subagent_task(self, task_id: str) -> SubagentTaskRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM content_research_subagent_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        return self._row_to_task(row) if row else None

    def list_subagent_tasks_for_workflow(self, workflow_run_id: str) -> list[SubagentTaskRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM content_research_subagent_tasks WHERE workflow_run_id = ? ORDER BY created_at ASC",
                (workflow_run_id,),
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def save_trace(self, trace: TraceRecord) -> TraceRecord:
        _validate_payload("Trace", trace.payload)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO content_research_traces
                     (id, workflow_run_id, thread_id, schema_version, status, started_at,
                      created_at, updated_at, payload_json, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     workflow_run_id=excluded.workflow_run_id,
                     thread_id=excluded.thread_id,
                     schema_version=excluded.schema_version,
                     status=excluded.status,
                     started_at=excluded.started_at,
                     updated_at=excluded.updated_at,
                     payload_json=excluded.payload_json,
                     metadata_json=excluded.metadata_json""",
                (
                    trace.id,
                    trace.workflow_run_id,
                    trace.thread_id,
                    trace.schema_version,
                    trace.status,
                    _fmt_dt(trace.started_at),
                    _fmt_dt(trace.created_at),
                    _fmt_dt(trace.updated_at),
                    _dumps(trace.payload),
                    _dumps(trace.metadata),
                ),
            )
        return trace

    def get_trace(self, trace_id: str) -> TraceRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM content_research_traces WHERE id = ?",
                (trace_id,),
            ).fetchone()
        return self._row_to_trace(row) if row else None

    def list_traces_for_workflow(self, workflow_run_id: str) -> list[TraceRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM content_research_traces WHERE workflow_run_id = ? ORDER BY started_at ASC",
                (workflow_run_id,),
            ).fetchall()
        return [self._row_to_trace(row) for row in rows]

    def append_observation_event(self, event: ObservationEventRecord) -> ObservationEventRecord:
        _validate_payload("ObservationEvent", event.payload)
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO content_research_observation_events
                         (id, trace_id, workflow_run_id, thread_id, schema_version, status,
                          sequence_no, event_type, event_name, timestamp, created_at, updated_at,
                          payload_json, metadata_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event.id,
                        event.trace_id,
                        event.workflow_run_id,
                        event.thread_id,
                        event.schema_version,
                        event.status,
                        event.sequence_no,
                        event.event_type,
                        event.event_name,
                        _fmt_dt(event.timestamp),
                        _fmt_dt(event.created_at),
                        _fmt_dt(event.updated_at),
                        _dumps(event.payload),
                        _dumps(event.metadata),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Observation event is append-only and already exists: {event.id}") from exc
        return event

    def list_observation_events(self, trace_id: str) -> list[ObservationEventRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM content_research_observation_events WHERE trace_id = ? ORDER BY sequence_no ASC",
                (trace_id,),
            ).fetchall()
        return [self._row_to_observation_event(row) for row in rows]

    def save_scope_contract(self, contract: ResearchScopeContract) -> ResearchScopeContract:
        constraints = [
            {
                "id": item.id,
                "label": item.label,
                "value": item.value,
                "retrieval_priority": item.retrieval_priority,
                "evidence_gate": item.evidence_gate,
                "allowed_aliases": list(item.allowed_aliases),
            }
            for item in contract.constraints
        ]
        groups = [
            {
                "id": item.id,
                "suggested_query": item.suggested_query,
                "final_query": item.final_query,
                "origin": item.origin,
                "execution_role": item.execution_role,
            }
            for item in contract.query_groups
        ]
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO content_research_scope_contracts
                       (id, workflow_run_id, research_plan_id, version, schema_version,
                        constraints_json, query_groups_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        contract.id,
                        contract.workflow_run_id,
                        contract.research_plan_id,
                        contract.version,
                        contract.schema_version,
                        _dumps_any_list(constraints),
                        _dumps_any_list(groups),
                        _fmt_dt(contract.created_at),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Scope contract is append-only and already exists: {contract.id}") from exc
        return contract

    def get_scope_contract(
        self, workflow_run_id: str, *, version: int
    ) -> ResearchScopeContract | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT * FROM content_research_scope_contracts
                   WHERE workflow_run_id = ? AND version = ?""",
                (workflow_run_id, version),
            ).fetchone()
        return self._row_to_scope_contract(row) if row else None

    def list_scope_contracts(self, workflow_run_id: str) -> list[ResearchScopeContract]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM content_research_scope_contracts
                   WHERE workflow_run_id = ? ORDER BY version ASC""",
                (workflow_run_id,),
            ).fetchall()
        return [self._row_to_scope_contract(row) for row in rows]

    def save_coverage_snapshot(self, snapshot: CoverageSnapshot) -> CoverageSnapshot:
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO content_research_scope_coverage_snapshots
                       (id, workflow_run_id, scope_contract_id, scope_contract_version, state,
                        constraint_counts_json, unmet_constraint_ids_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        snapshot.id,
                        snapshot.workflow_run_id,
                        snapshot.scope_contract_id,
                        snapshot.scope_contract_version,
                        snapshot.state,
                        _dumps(snapshot.constraint_counts),
                        _dumps_any_list(list(snapshot.unmet_constraint_ids)),
                        _fmt_dt(snapshot.created_at),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Coverage snapshot is append-only and already exists: {snapshot.id}") from exc
        return snapshot

    def get_coverage_snapshot(
        self, workflow_run_id: str, *, version: int
    ) -> CoverageSnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT * FROM content_research_scope_coverage_snapshots
                   WHERE workflow_run_id = ? AND scope_contract_version = ?""",
                (workflow_run_id, version),
            ).fetchone()
        return self._row_to_coverage_snapshot(row) if row else None

    def append_scope_audit_event(self, event: ScopeAuditEvent) -> ScopeAuditEvent:
        _validate_payload("ScopeAuditEvent", event.payload)
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO content_research_scope_audit_events
                       (id, workflow_run_id, scope_contract_id, scope_contract_version,
                        event_name, payload_json, metadata_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event.id,
                        event.workflow_run_id,
                        event.scope_contract_id,
                        event.scope_contract_version,
                        event.event_name,
                        _dumps(event.payload),
                        _dumps(event.metadata or {}),
                        _fmt_dt(event.created_at),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Scope audit event is append-only and already exists: {event.id}") from exc
        return event

    def list_scope_audit_events(
        self, workflow_run_id: str, *, version: int
    ) -> list[ScopeAuditEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM content_research_scope_audit_events
                   WHERE workflow_run_id = ? AND scope_contract_version = ?
                   ORDER BY created_at ASC, id ASC""",
                (workflow_run_id, version),
            ).fetchall()
        return [self._row_to_scope_audit_event(row) for row in rows]

    def save_evidence_record(self, record: EvidenceRecord) -> EvidenceRecord:
        _validate_payload("EvidenceRecord", record.normalized_payload)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO content_research_evidence_records
                     (id, workflow_run_id, research_brief_id, research_plan_id,
                      research_direction_id, subagent_task_id, trace_id, schema_version,
                      status, source_type, source_platform, source_url, source_id,
                      source_author_id, source_author_name, source_published_at, collected_at,
                      title, text_excerpt, raw_content_ref, evidence_type, claim,
                      metrics_json, language, content_hash, dedupe_key, retrieval_query,
                      retrieval_rank, retrieval_score, normalized_payload_json, metadata_json,
                      created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     workflow_run_id=excluded.workflow_run_id,
                     research_brief_id=excluded.research_brief_id,
                     research_plan_id=excluded.research_plan_id,
                     research_direction_id=excluded.research_direction_id,
                     subagent_task_id=excluded.subagent_task_id,
                     trace_id=excluded.trace_id,
                     schema_version=excluded.schema_version,
                     status=excluded.status,
                     source_type=excluded.source_type,
                     source_platform=excluded.source_platform,
                     source_url=excluded.source_url,
                     source_id=excluded.source_id,
                     source_author_id=excluded.source_author_id,
                     source_author_name=excluded.source_author_name,
                     source_published_at=excluded.source_published_at,
                     collected_at=excluded.collected_at,
                     title=excluded.title,
                     text_excerpt=excluded.text_excerpt,
                     raw_content_ref=excluded.raw_content_ref,
                     evidence_type=excluded.evidence_type,
                     claim=excluded.claim,
                     metrics_json=excluded.metrics_json,
                     language=excluded.language,
                     content_hash=excluded.content_hash,
                     dedupe_key=excluded.dedupe_key,
                     retrieval_query=excluded.retrieval_query,
                     retrieval_rank=excluded.retrieval_rank,
                     retrieval_score=excluded.retrieval_score,
                     normalized_payload_json=excluded.normalized_payload_json,
                     metadata_json=excluded.metadata_json,
                     updated_at=excluded.updated_at""",
                (
                    record.id,
                    record.workflow_run_id,
                    record.research_brief_id,
                    record.research_plan_id,
                    record.research_direction_id,
                    record.subagent_task_id,
                    record.trace_id,
                    record.schema_version,
                    record.status,
                    record.source_type,
                    record.source_platform,
                    record.source_url,
                    record.source_id,
                    record.source_author_id,
                    record.source_author_name,
                    _fmt_dt(record.source_published_at) if record.source_published_at else None,
                    _fmt_dt(record.collected_at),
                    record.title,
                    record.text_excerpt,
                    record.raw_content_ref,
                    record.evidence_type,
                    record.claim,
                    _dumps(record.metrics),
                    record.language,
                    record.content_hash,
                    record.dedupe_key,
                    record.retrieval_query,
                    record.retrieval_rank,
                    record.retrieval_score,
                    _dumps(record.normalized_payload),
                    _dumps(record.metadata),
                    _fmt_dt(record.created_at),
                    _fmt_dt(record.updated_at),
                ),
            )
        return record

    def get_evidence_record(self, evidence_id: str) -> EvidenceRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM content_research_evidence_records WHERE id = ?",
                (evidence_id,),
            ).fetchone()
        return self._row_to_evidence_record(row) if row else None

    def list_evidence_records(
        self,
        *,
        workflow_run_id: str | None = None,
        research_plan_id: str | None = None,
        research_direction_id: str | None = None,
        subagent_task_id: str | None = None,
    ) -> list[EvidenceRecord]:
        filters: list[str] = []
        values: list[str] = []
        if workflow_run_id is not None:
            filters.append("workflow_run_id = ?")
            values.append(workflow_run_id)
        if research_plan_id is not None:
            filters.append("research_plan_id = ?")
            values.append(research_plan_id)
        if research_direction_id is not None:
            filters.append("research_direction_id = ?")
            values.append(research_direction_id)
        if subagent_task_id is not None:
            filters.append("subagent_task_id = ?")
            values.append(subagent_task_id)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM content_research_evidence_records {where} ORDER BY collected_at ASC",
                values,
            ).fetchall()
        return [self._row_to_evidence_record(row) for row in rows]

    def append_evidence_lineage(self, lineage: EvidenceLineageRecord) -> EvidenceLineageRecord:
        _validate_payload("EvidenceLineage", lineage.lineage_payload)
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO content_research_evidence_lineage
                         (id, workflow_run_id, evidence_record_id, research_brief_id,
                          research_plan_id, research_direction_id, subagent_task_id, trace_id,
                          parent_evidence_record_id, schema_version, transformation_type,
                          transformation_version, lineage_payload_json, metadata_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        lineage.id,
                        lineage.workflow_run_id,
                        lineage.evidence_record_id,
                        lineage.research_brief_id,
                        lineage.research_plan_id,
                        lineage.research_direction_id,
                        lineage.subagent_task_id,
                        lineage.trace_id,
                        lineage.parent_evidence_record_id,
                        lineage.schema_version,
                        lineage.transformation_type,
                        lineage.transformation_version,
                        _dumps(lineage.lineage_payload),
                        _dumps(lineage.metadata),
                        _fmt_dt(lineage.created_at),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Evidence lineage is append-only and already exists: {lineage.id}") from exc
        return lineage

    def list_evidence_lineage(self, evidence_record_id: str) -> list[EvidenceLineageRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM content_research_evidence_lineage WHERE evidence_record_id = ? ORDER BY created_at ASC",
                (evidence_record_id,),
            ).fetchall()
        return [self._row_to_evidence_lineage(row) for row in rows]

    def save_result_snapshot(self, snapshot: ResearchResultSnapshotRecord) -> ResearchResultSnapshotRecord:
        if not snapshot.schema_version:
            raise ValueError("ResearchResultSnapshot must include schema_version")
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO content_research_result_snapshots
                         (id, workflow_run_id, research_brief_id, research_plan_id,
                          schema_version, snapshot_version, result_type, status,
                          title, executive_summary, findings_json, recommendations_json,
                          claim_count, supported_claim_count,
                          unsupported_claim_count, citation_coverage_score, faithfulness_score,
                          answer_relevancy_score, derivation_completeness_score,
                          evidence_boundary_calibration_score, decision_summary_json, decision_cards_json,
                          priority_summary_json, evidence_boundary_summary_json, limitations_json,
                          abstentions_json, metadata_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        snapshot.id,
                        snapshot.workflow_run_id,
                        snapshot.research_brief_id,
                        snapshot.research_plan_id,
                        snapshot.schema_version,
                        snapshot.snapshot_version,
                        snapshot.result_type,
                        snapshot.status,
                        snapshot.title,
                        snapshot.executive_summary,
                        _dumps_list(snapshot.findings),
                        _dumps_list(snapshot.recommendations),
                        snapshot.claim_count,
                        snapshot.supported_claim_count,
                        snapshot.unsupported_claim_count,
                        snapshot.citation_coverage_score,
                        snapshot.faithfulness_score,
                        snapshot.answer_relevancy_score,
                        snapshot.derivation_completeness_score,
                        snapshot.evidence_boundary_calibration_score,
                        _dumps(snapshot.decision_summary),
                        _dumps_list(snapshot.decision_cards),
                        _dumps(snapshot.priority_summary),
                        _dumps(snapshot.evidence_boundary_summary),
                        _dumps_list(snapshot.limitations),
                        _dumps_list(snapshot.abstentions),
                        _dumps(snapshot.metadata),
                        _fmt_dt(snapshot.created_at),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Research result snapshot is immutable and already exists: {snapshot.id}") from exc
        return snapshot

    def get_result_snapshot(self, snapshot_id: str) -> ResearchResultSnapshotRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM content_research_result_snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
        return self._row_to_result_snapshot(row) if row else None

    def list_result_snapshots_for_workflow(self, workflow_run_id: str) -> list[ResearchResultSnapshotRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM content_research_result_snapshots WHERE workflow_run_id = ? ORDER BY created_at ASC",
                (workflow_run_id,),
            ).fetchall()
        return [self._row_to_result_snapshot(row) for row in rows]

    def append_human_decision(self, decision: HumanDecisionRecord) -> HumanDecisionRecord:
        if not decision.schema_version:
            raise ValueError("HumanDecision must include schema_version")
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO content_research_human_decisions
                         (id, workflow_run_id, thread_id, schema_version, target_type,
                          target_id, decision_request_id, decision_status,
                          decision_payload_json, rationale, created_by_type, created_by_id,
                          research_brief_id, research_plan_id, research_result_snapshot_id,
                          metadata_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        decision.id,
                        decision.workflow_run_id,
                        decision.thread_id,
                        decision.schema_version,
                        decision.target_type,
                        decision.target_id,
                        decision.decision_request_id,
                        decision.decision_status,
                        _dumps(decision.decision_payload),
                        decision.rationale,
                        decision.created_by_type,
                        decision.created_by_id,
                        decision.research_brief_id,
                        decision.research_plan_id,
                        decision.research_result_snapshot_id,
                        _dumps(decision.metadata),
                        _fmt_dt(decision.created_at),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Human decision is append-only and already exists: {decision.id}") from exc
        return decision

    def get_human_decision_by_request(
        self,
        *,
        workflow_run_id: str,
        target_type: str,
        target_id: str,
        decision_request_id: str,
    ) -> HumanDecisionRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT * FROM content_research_human_decisions
                   WHERE workflow_run_id = ? AND target_type = ? AND target_id = ? AND decision_request_id = ?
                   LIMIT 1""",
                (workflow_run_id, target_type, target_id, decision_request_id),
            ).fetchone()
        return self._row_to_human_decision(row) if row else None

    def list_human_decisions_for_workflow(self, workflow_run_id: str) -> list[HumanDecisionRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM content_research_human_decisions WHERE workflow_run_id = ? ORDER BY created_at ASC, rowid ASC",
                (workflow_run_id,),
            ).fetchall()
        return [self._row_to_human_decision(row) for row in rows]

    def list_current_human_decisions_for_workflow(self, workflow_run_id: str) -> list[HumanDecisionRecord]:
        current: dict[tuple[str, str], HumanDecisionRecord] = {}
        for decision in self.list_human_decisions_for_workflow(workflow_run_id):
            current[(decision.target_type, decision.target_id)] = decision
        return list(current.values())

    def save_run_policy_snapshot(self, snapshot: RunPolicySnapshot) -> RunPolicySnapshot:
        with self._connect() as conn:
            existing = conn.execute("SELECT effective_policy_hash FROM content_research_run_policy_snapshots WHERE id = ?", (snapshot.id,)).fetchone()
            if existing is not None:
                if existing[0] != snapshot.effective_policy_hash:
                    raise ValueError(f"RunPolicySnapshot is append-only: {snapshot.id}")
                return snapshot
            conn.execute("INSERT INTO content_research_run_policy_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (snapshot.id, snapshot.workflow_run_id, snapshot.research_brief_id, snapshot.research_plan_id, snapshot.schema_version, _dumps(snapshot.effective_policy), snapshot.effective_policy_hash, _fmt_dt(snapshot.run_as_of_at), _dumps(snapshot.base_policy_ids_and_versions), _dumps(snapshot.requested_overrides), _dumps(snapshot.validation_result), _fmt_dt(snapshot.created_at), _dumps(snapshot.metadata)))
        return snapshot

    def get_run_policy_snapshot(self, snapshot_id: str) -> RunPolicySnapshot | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM content_research_run_policy_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
        return self._row_to_snapshot(row) if row else None

    def get_run_policy_snapshot_for_workflow(self, workflow_run_id: str) -> RunPolicySnapshot | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM content_research_run_policy_snapshots WHERE workflow_run_id = ?", (workflow_run_id,)).fetchone()
        return self._row_to_snapshot(row) if row else None

    def save_sample_policy(self, policy: SamplePolicy) -> SamplePolicy:
        with self._connect() as conn:
            conn.execute("INSERT INTO content_research_sample_policies VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING", (policy.id, policy.schema_version, policy.direction_id, policy.minimum_samples, policy.minimum_independent_authors, policy.author_cap, _dumps(policy.metadata)))
        return policy

    def get_sample_policy(self, sample_policy_id: str) -> SamplePolicy | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM content_research_sample_policies WHERE id = ?", (sample_policy_id,)).fetchone()
        return SamplePolicy(id=row["id"], schema_version=row["schema_version"], direction_id=row["direction_id"], minimum_samples=row["minimum_samples"], minimum_independent_authors=row["minimum_independent_authors"], author_cap=row["author_cap"], metadata=_loads(row["metadata_json"])) if row else None

    def save_direction_contract(self, contract: DirectionContract) -> DirectionContract:
        with self._connect() as conn:
            conn.execute("INSERT INTO content_research_direction_contracts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING", (contract.id, contract.snapshot_id, contract.direction_id, contract.schema_version, contract.sample_policy_id, _dumps_any_list(list(contract.required_note_fields)), _dumps_any_list(list(contract.optional_note_fields)), _dumps_any_list(list(contract.required_comment_fields)), _dumps_any_list(list(contract.claim_rules)), contract.analysis_schema_version, contract.resume_contract_version, _dumps(contract.metadata)))
        return contract

    def list_direction_contracts(self, snapshot_id: str) -> list[DirectionContract]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM content_research_direction_contracts WHERE snapshot_id = ? ORDER BY direction_id", (snapshot_id,)).fetchall()
        return [DirectionContract(id=row["id"], snapshot_id=row["snapshot_id"], direction_id=row["direction_id"], schema_version=row["schema_version"], sample_policy_id=row["sample_policy_id"], required_note_fields=tuple(_loads_any_list(row["required_note_fields_json"])), optional_note_fields=tuple(_loads_any_list(row["optional_note_fields_json"])), required_comment_fields=tuple(_loads_any_list(row["required_comment_fields_json"])), claim_rules=tuple(_loads_any_list(row["claim_rules_json"])), analysis_schema_version=row["analysis_schema_version"], resume_contract_version=row["resume_contract_version"], metadata=_loads(row["metadata_json"])) for row in rows]

    def _save_typed_record(
        self,
        table: str,
        record: TypedPersistenceRecord,
        values: dict[str, Any],
    ) -> TypedPersistenceRecord:
        if not isinstance(record, TypedPersistenceRecord):
            raise TypeError("new persistence APIs require typed records")
        columns = ("id", "schema_version", *values, "payload_json", "metadata_json", "created_at")
        placeholders = ", ".join("?" for _ in columns)
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                (
                    record.id,
                    record.schema_version,
                    *values.values(),
                    _dumps(record.payload),
                    _dumps(record.metadata),
                    _fmt_dt(record.created_at),
                ),
            )
        return record

    def _require_typed_parent(self, table: str, record_id: str, relation: str) -> None:
        with self._connect() as conn:
            row = conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (record_id,)).fetchone()
        if row is None:
            raise ValueError(f"missing {relation}: {record_id}")

    def _require_result_snapshot(
        self,
        snapshot_id: str,
        workflow_run_id: str,
        research_plan_id: str,
        snapshot_version: str,
    ) -> None:
        snapshot = self.get_result_snapshot(snapshot_id)
        if snapshot is None:
            raise ValueError(f"missing governed snapshot: {snapshot_id}")
        if (
            snapshot.workflow_run_id != workflow_run_id
            or snapshot.research_plan_id != research_plan_id
            or snapshot.snapshot_version != snapshot_version
        ):
            raise ValueError("governed snapshot identity does not match report record")

    def _get_typed_record(
        self,
        table: str,
        record_id: str,
        record_type: type[TypedPersistenceRecord],
        fields: tuple[str, ...],
    ) -> TypedPersistenceRecord | None:
        with self._connect() as conn:
            row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
        return self._row_to_typed_record(row, record_type, fields) if row else None

    def _list_typed_records(
        self,
        table: str,
        record_type: type[TypedPersistenceRecord],
        fields: tuple[str, ...],
    ) -> list[TypedPersistenceRecord]:
        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM {table} ORDER BY created_at ASC, id ASC").fetchall()
        return [self._row_to_typed_record(row, record_type, fields) for row in rows]

    def get_typed_record(
        self,
        record_type: type[_TypedRecordT],
        record_id: str,
    ) -> _TypedRecordT | None:
        try:
            table, fields = _TYPED_RECORD_TABLES[record_type]
        except KeyError as exc:
            raise TypeError("unsupported typed persistence record") from exc
        return self._get_typed_record(table, record_id, record_type, fields)  # type: ignore[return-value]

    def list_typed_records(self, record_type: type[_TypedRecordT]) -> list[_TypedRecordT]:
        try:
            table, fields = _TYPED_RECORD_TABLES[record_type]
        except KeyError as exc:
            raise TypeError("unsupported typed persistence record") from exc
        return self._list_typed_records(table, record_type, fields)  # type: ignore[return-value]

    @staticmethod
    def _row_to_typed_record(
        row: sqlite3.Row,
        record_type: type[TypedPersistenceRecord],
        fields: tuple[str, ...],
    ) -> TypedPersistenceRecord:
        values = {
            field: _parse_dt(row[field]) if field in {"started_at", "finished_at"} and row[field] else row[field]
            for field in fields
        }
        return record_type(
            id=row["id"], schema_version=row["schema_version"], payload=_loads(row["payload_json"]),
            metadata=_loads(row["metadata_json"]), created_at=_parse_dt(row["created_at"]),
            **values,
        )

    def save_canonical_source(self, source: CanonicalSourceRecord) -> CanonicalSourceRecord:
        return self._save_typed_record("content_research_canonical_sources", source, {"platform": source.platform, "platform_source_kind": source.platform_source_kind, "platform_source_id": source.platform_source_id, "canonical_url": source.canonical_url})  # type: ignore[return-value]

    def resolve_canonical_source(self, source: CanonicalSourceRecord) -> CanonicalSourceRecord:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO content_research_canonical_sources (id, schema_version, platform, platform_source_kind, platform_source_id, canonical_url, payload_json, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(platform, platform_source_kind, platform_source_id) DO NOTHING",
                (source.id, source.schema_version, source.platform, source.platform_source_kind, source.platform_source_id, source.canonical_url, _dumps(source.payload), _dumps(source.metadata), _fmt_dt(source.created_at)),
            )
            row = conn.execute("SELECT * FROM content_research_canonical_sources WHERE platform = ? AND platform_source_kind = ? AND platform_source_id = ?", (source.platform, source.platform_source_kind, source.platform_source_id)).fetchone()
        return self._row_to_typed_record(row, CanonicalSourceRecord, ("platform", "platform_source_kind", "platform_source_id", "canonical_url"))  # type: ignore[return-value]

    def get_canonical_source(self, source_id: str) -> CanonicalSourceRecord | None:
        return self._get_typed_record("content_research_canonical_sources", source_id, CanonicalSourceRecord, ("platform", "platform_source_kind", "platform_source_id", "canonical_url"))  # type: ignore[return-value]

    def save_direction_source_projection(self, record: DirectionSourceProjectionRecord) -> DirectionSourceProjectionRecord:
        self._require_typed_parent("content_research_canonical_sources", record.canonical_source_id, "canonical source")
        self._require_typed_parent("content_research_directional_evidence_packets", record.evidence_packet_id, "evidence packet")
        return self._save_typed_record("content_research_direction_source_projections", record, {"workflow_run_id": record.workflow_run_id, "research_direction_id": record.research_direction_id, "canonical_source_id": record.canonical_source_id, "evidence_packet_id": record.evidence_packet_id})  # type: ignore[return-value]

    def save_directional_evidence_packet(self, record: DirectionalEvidencePacketRecord) -> DirectionalEvidencePacketRecord:
        self._require_typed_parent("content_research_canonical_sources", record.canonical_source_id, "canonical source")
        return self._save_typed_record("content_research_directional_evidence_packets", record, {"workflow_run_id": record.workflow_run_id, "research_direction_id": record.research_direction_id, "canonical_source_id": record.canonical_source_id, "field_projection_hash": record.field_projection_hash})  # type: ignore[return-value]

    def list_direction_source_projections(self, workflow_run_id: str, research_direction_id: str, *, offset: int = 0, limit: int = 50) -> list[DirectionSourceProjectionRecord]:
        if offset < 0 or limit < 1:
            raise ValueError("offset must be non-negative and limit must be positive")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM content_research_direction_source_projections WHERE workflow_run_id = ? AND research_direction_id = ? ORDER BY created_at ASC, id ASC LIMIT ? OFFSET ?",
                (workflow_run_id, research_direction_id, limit, offset),
            ).fetchall()
        return [self._row_to_typed_record(row, DirectionSourceProjectionRecord, ("workflow_run_id", "research_direction_id", "canonical_source_id", "evidence_packet_id")) for row in rows]  # type: ignore[return-value]

    def count_run_independent_sources(self, workflow_run_id: str) -> int:
        """Count the complete canonical union without exposing paginated projections."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT canonical_source_id) FROM content_research_direction_source_projections WHERE workflow_run_id = ?",
                (workflow_run_id,),
            ).fetchone()
        return int(row[0] if row else 0)

    def list_directional_evidence_packets(self, workflow_run_id: str, research_direction_id: str, *, offset: int = 0, limit: int = 50) -> list[DirectionalEvidencePacketRecord]:
        if offset < 0 or limit < 1:
            raise ValueError("offset must be non-negative and limit must be positive")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM content_research_directional_evidence_packets WHERE workflow_run_id = ? AND research_direction_id = ? ORDER BY created_at ASC, id ASC LIMIT ? OFFSET ?",
                (workflow_run_id, research_direction_id, limit, offset),
            ).fetchall()
        return [self._row_to_typed_record(row, DirectionalEvidencePacketRecord, ("workflow_run_id", "research_direction_id", "canonical_source_id", "field_projection_hash")) for row in rows]  # type: ignore[return-value]

    def save_claim_candidate(self, record: ClaimCandidateRecord) -> ClaimCandidateRecord:
        self._require_typed_parent("content_research_directional_evidence_packets", record.evidence_packet_id, "evidence packet")
        packet = self.get_typed_record(DirectionalEvidencePacketRecord, record.evidence_packet_id)
        assert packet is not None
        from app.content_research.admission.candidates import validate_candidate_packet
        validate_candidate_packet(record, packet)
        return self._save_typed_record("content_research_claim_candidates", record, {"workflow_run_id": record.workflow_run_id, "research_direction_id": record.research_direction_id, "evidence_packet_id": record.evidence_packet_id, "statement": record.statement, "intent_id": record.intent_id, "claim_type": record.claim_type, "requested_state": record.requested_state})  # type: ignore[return-value]

    def list_claim_candidates(self, workflow_run_id: str, research_direction_id: str) -> list[ClaimCandidateRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM content_research_claim_candidates WHERE workflow_run_id = ? AND research_direction_id = ? ORDER BY created_at ASC, id ASC", (workflow_run_id, research_direction_id)).fetchall()
        fields = ("workflow_run_id", "research_direction_id", "evidence_packet_id", "statement", "intent_id", "claim_type", "requested_state")
        return [self._row_to_typed_record(row, ClaimCandidateRecord, fields) for row in rows]  # type: ignore[return-value]

    def save_claim_admission_decision(self, record: ClaimAdmissionDecisionRecord) -> ClaimAdmissionDecisionRecord:
        self._require_typed_parent("content_research_claim_candidates", record.claim_candidate_id, "claim candidate")
        self._require_typed_parent("content_research_run_policy_snapshots", record.policy_snapshot_id, "policy snapshot")
        return self._save_typed_record("content_research_claim_admission_decisions", record, {"research_direction_id": record.research_direction_id, "claim_candidate_id": record.claim_candidate_id, "decision": record.decision, "policy_snapshot_id": record.policy_snapshot_id})  # type: ignore[return-value]

    def save_direction_result_decision(self, record: DirectionResultDecisionRecord) -> DirectionResultDecisionRecord:
        self._require_typed_parent("content_research_run_policy_snapshots", record.policy_snapshot_id, "policy snapshot")
        return self._save_typed_record("content_research_direction_result_decisions", record, {"research_direction_id": record.research_direction_id, "policy_snapshot_id": record.policy_snapshot_id})  # type: ignore[return-value]

    def save_weak_signal(self, record: WeakSignalRecord) -> WeakSignalRecord:
        self._require_typed_parent("content_research_claim_admission_decisions", record.admission_decision_id, "admission decision")
        return self._save_typed_record("content_research_weak_signals", record, {"admission_decision_id": record.admission_decision_id})  # type: ignore[return-value]

    def save_cross_direction_record(self, record: CrossDirectionRecord) -> CrossDirectionRecord:
        return self._save_typed_record("content_research_cross_direction_records", record, {"research_plan_id": record.research_plan_id, "record_type": record.record_type})  # type: ignore[return-value]

    def save_aggregate_claim(self, record: AggregateClaimRecord) -> AggregateClaimRecord:
        return self._save_typed_record("content_research_aggregate_claims", record, {"research_plan_id": record.research_plan_id, "aggregate_type": record.aggregate_type})  # type: ignore[return-value]

    def save_marketing_conclusion_candidate(self, record: MarketingConclusionCandidateRecord) -> MarketingConclusionCandidateRecord:
        return self._save_typed_record(
            "content_research_marketing_conclusion_candidates",
            record,
            {"workflow_run_id": record.workflow_run_id, "research_plan_id": record.research_plan_id, "track": record.track},
        )  # type: ignore[return-value]

    def list_marketing_conclusion_candidates(self, workflow_run_id: str, research_plan_id: str) -> list[MarketingConclusionCandidateRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM content_research_marketing_conclusion_candidates WHERE workflow_run_id = ? AND research_plan_id = ? ORDER BY created_at ASC, id ASC",
                (workflow_run_id, research_plan_id),
            ).fetchall()
        return [
            self._row_to_typed_record(row, MarketingConclusionCandidateRecord, ("workflow_run_id", "research_plan_id", "track"))
            for row in rows
        ]  # type: ignore[return-value]

    def save_marketing_conclusion_decision(self, record: MarketingConclusionDecisionRecord) -> MarketingConclusionDecisionRecord:
        return self._save_typed_record(
            "content_research_marketing_conclusion_decisions",
            record,
            {"workflow_run_id": record.workflow_run_id, "research_plan_id": record.research_plan_id, "candidate_id": record.candidate_id, "track": record.track, "state": record.state},
        )  # type: ignore[return-value]

    def list_marketing_conclusion_decisions(self, workflow_run_id: str, research_plan_id: str) -> list[MarketingConclusionDecisionRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM content_research_marketing_conclusion_decisions WHERE workflow_run_id = ? AND research_plan_id = ? ORDER BY created_at ASC, id ASC",
                (workflow_run_id, research_plan_id),
            ).fetchall()
        return [
            self._row_to_typed_record(row, MarketingConclusionDecisionRecord, ("workflow_run_id", "research_plan_id", "candidate_id", "track", "state"))
            for row in rows
        ]  # type: ignore[return-value]

    def save_stage_checkpoint(self, record: StageCheckpointRecord) -> StageCheckpointRecord:
        values = {
            "workflow_run_id": record.workflow_run_id,
            "subagent_task_id": record.subagent_task_id,
            "stage_name": record.stage_name,
            "input_fingerprint": record.input_fingerprint,
            "status": record.status,
            "retry_count": record.retry_count,
            "started_at": _fmt_dt(record.started_at) if record.started_at else None,
            "finished_at": _fmt_dt(record.finished_at) if record.finished_at else None,
        }
        columns = ("id", "schema_version", *values, "payload_json", "metadata_json", "created_at")
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(f"{column} = excluded.{column}" for column in columns if column != "id")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO content_research_stage_checkpoints "
                f"({', '.join(columns)}) VALUES ({placeholders}) "
                f"ON CONFLICT(id) DO UPDATE SET {updates}",
                (
                    record.id,
                    record.schema_version,
                    *values.values(),
                    _dumps(record.payload),
                    _dumps(record.metadata),
                    _fmt_dt(record.created_at),
                ),
            )
        return record

    def save_budget_ledger_entry(self, record: BudgetLedgerEntryRecord) -> BudgetLedgerEntryRecord:
        if record.stage_checkpoint_id:
            self._require_typed_parent("content_research_stage_checkpoints", record.stage_checkpoint_id, "stage checkpoint")
        return self._save_typed_record("content_research_budget_ledger_entries", record, {"research_plan_id": record.research_plan_id, "research_direction_id": record.research_direction_id, "idempotency_key": record.idempotency_key, "reservation_status": record.reservation_status, "reserved_amount": record.reserved_amount, "consumed_amount": record.consumed_amount, "stage_checkpoint_id": record.stage_checkpoint_id})  # type: ignore[return-value]

    def save_report_draft(self, record: ReportDraftRecord) -> ReportDraftRecord:
        self._require_result_snapshot(record.governed_snapshot_id, record.workflow_run_id, record.research_plan_id, record.governed_snapshot_version)
        return self._save_typed_record(
            "content_research_report_drafts",
            record,
            {"workflow_run_id": record.workflow_run_id, "research_plan_id": record.research_plan_id, "governed_snapshot_id": record.governed_snapshot_id, "governed_snapshot_version": record.governed_snapshot_version, "input_fingerprint": record.input_fingerprint, "policy_version": record.policy_version, "algorithm_version": record.algorithm_version, "previous_version_id": record.previous_version_id},
        )  # type: ignore[return-value]

    def save_report_faithfulness_decision(self, record: ReportFaithfulnessDecisionRecord) -> ReportFaithfulnessDecisionRecord:
        self._require_result_snapshot(record.governed_snapshot_id, record.workflow_run_id, record.research_plan_id, record.governed_snapshot_version)
        self._require_typed_parent("content_research_report_drafts", record.report_draft_id, "report draft")
        return self._save_typed_record(
            "content_research_report_faithfulness_decisions",
            record,
            {"workflow_run_id": record.workflow_run_id, "research_plan_id": record.research_plan_id, "governed_snapshot_id": record.governed_snapshot_id, "governed_snapshot_version": record.governed_snapshot_version, "input_fingerprint": record.input_fingerprint, "policy_version": record.policy_version, "algorithm_version": record.algorithm_version, "report_draft_id": record.report_draft_id, "previous_version_id": record.previous_version_id},
        )  # type: ignore[return-value]

    def save_report_publication(self, record: ReportPublicationRecord) -> ReportPublicationRecord:
        self._require_result_snapshot(record.governed_snapshot_id, record.workflow_run_id, record.research_plan_id, record.governed_snapshot_version)
        self._require_typed_parent("content_research_report_drafts", record.report_draft_id, "report draft")
        self._require_typed_parent("content_research_report_faithfulness_decisions", record.faithfulness_decision_id, "report faithfulness decision")
        return self._save_typed_record(
            "content_research_report_publications",
            record,
            {"workflow_run_id": record.workflow_run_id, "research_plan_id": record.research_plan_id, "governed_snapshot_id": record.governed_snapshot_id, "governed_snapshot_version": record.governed_snapshot_version, "input_fingerprint": record.input_fingerprint, "policy_version": record.policy_version, "algorithm_version": record.algorithm_version, "report_draft_id": record.report_draft_id, "faithfulness_decision_id": record.faithfulness_decision_id, "publication_state": record.publication_state, "previous_version_id": record.previous_version_id},
        )  # type: ignore[return-value]

    @staticmethod
    def _row_to_snapshot(row: sqlite3.Row) -> RunPolicySnapshot:
        return RunPolicySnapshot(id=row["id"], workflow_run_id=row["workflow_run_id"], research_brief_id=row["research_brief_id"], research_plan_id=row["research_plan_id"], schema_version=row["schema_version"], effective_policy=_loads(row["effective_policy_json"]), effective_policy_hash=row["effective_policy_hash"], run_as_of_at=_parse_dt(row["run_as_of_at"]), base_policy_ids_and_versions=_loads(row["base_policy_json"]), requested_overrides=_loads(row["requested_overrides_json"]), validation_result=_loads(row["validation_result_json"]), created_at=_parse_dt(row["created_at"]), metadata=_loads(row["metadata_json"]))

    @staticmethod
    def _row_to_brief(row: sqlite3.Row) -> ResearchBriefRecord:
        return ResearchBriefRecord(
            id=row["id"],
            workflow_run_id=row["workflow_run_id"],
            thread_id=row["thread_id"],
            schema_version=row["schema_version"],
            status=row["status"],
            payload=_loads(row["payload_json"]),
            metadata=_loads(row["metadata_json"]),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    @staticmethod
    def _row_to_plan(row: sqlite3.Row) -> ResearchPlanRecord:
        return ResearchPlanRecord(
            id=row["id"],
            brief_id=row["brief_id"],
            workflow_run_id=row["workflow_run_id"],
            thread_id=row["thread_id"],
            schema_version=row["schema_version"],
            status=row["status"],
            payload=_loads(row["payload_json"]),
            metadata=_loads(row["metadata_json"]),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    @staticmethod
    def _row_to_direction(row: sqlite3.Row) -> ResearchDirectionRecord:
        return ResearchDirectionRecord(
            id=row["id"],
            plan_id=row["plan_id"],
            workflow_run_id=row["workflow_run_id"],
            thread_id=row["thread_id"],
            schema_version=row["schema_version"],
            status=row["status"],
            priority=row["priority"],
            payload=_loads(row["payload_json"]),
            metadata=_loads(row["metadata_json"]),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> SubagentTaskRecord:
        return SubagentTaskRecord(
            id=row["id"],
            workflow_run_id=row["workflow_run_id"],
            thread_id=row["thread_id"],
            schema_version=row["schema_version"],
            status=row["status"],
            plan_id=row["plan_id"],
            direction_id=row["direction_id"],
            payload=_loads(row["payload_json"]),
            metadata=_loads(row["metadata_json"]),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    @staticmethod
    def _row_to_trace(row: sqlite3.Row) -> TraceRecord:
        return TraceRecord(
            id=row["id"],
            workflow_run_id=row["workflow_run_id"],
            thread_id=row["thread_id"],
            schema_version=row["schema_version"],
            status=row["status"],
            started_at=_parse_dt(row["started_at"]),
            payload=_loads(row["payload_json"]),
            metadata=_loads(row["metadata_json"]),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    @staticmethod
    def _row_to_observation_event(row: sqlite3.Row) -> ObservationEventRecord:
        return ObservationEventRecord(
            id=row["id"],
            trace_id=row["trace_id"],
            workflow_run_id=row["workflow_run_id"],
            thread_id=row["thread_id"],
            schema_version=row["schema_version"],
            status=row["status"],
            sequence_no=row["sequence_no"],
            event_type=row["event_type"],
            event_name=row["event_name"],
            timestamp=_parse_dt(row["timestamp"]),
            payload=_loads(row["payload_json"]),
            metadata=_loads(row["metadata_json"]),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    @staticmethod
    def _row_to_scope_contract(row: sqlite3.Row) -> ResearchScopeContract:
        constraints = tuple(
            ScopeConstraint(
                id=str(item["id"]),
                label=str(item["label"]),
                value=str(item["value"]),
                retrieval_priority=str(item["retrieval_priority"]),
                evidence_gate=str(item["evidence_gate"]),
                allowed_aliases=tuple(str(alias) for alias in item.get("allowed_aliases") or ()),
            )
            for item in _loads_any_list(row["constraints_json"])
        )
        groups = tuple(
            ScopeQueryGroup(
                id=str(item["id"]),
                suggested_query=str(item["suggested_query"]),
                final_query=str(item["final_query"]),
                origin=str(item["origin"]),
                execution_role=str(item["execution_role"]),
            )
            for item in _loads_any_list(row["query_groups_json"])
        )
        return ResearchScopeContract(
            id=row["id"],
            workflow_run_id=row["workflow_run_id"],
            research_plan_id=row["research_plan_id"],
            version=row["version"],
            schema_version=row["schema_version"],
            constraints=constraints,
            query_groups=groups,
            created_at=_parse_dt(row["created_at"]),
        )

    @staticmethod
    def _row_to_scope_audit_event(row: sqlite3.Row) -> ScopeAuditEvent:
        return ScopeAuditEvent(
            id=row["id"],
            workflow_run_id=row["workflow_run_id"],
            scope_contract_id=row["scope_contract_id"],
            scope_contract_version=row["scope_contract_version"],
            event_name=row["event_name"],
            payload=_loads(row["payload_json"]),
            metadata=_loads(row["metadata_json"]),
            created_at=_parse_dt(row["created_at"]),
        )

    @staticmethod
    def _row_to_coverage_snapshot(row: sqlite3.Row) -> CoverageSnapshot:
        return CoverageSnapshot(
            id=row["id"],
            workflow_run_id=row["workflow_run_id"],
            scope_contract_id=row["scope_contract_id"],
            scope_contract_version=row["scope_contract_version"],
            state=row["state"],
            constraint_counts=_loads(row["constraint_counts_json"]),
            unmet_constraint_ids=tuple(
                str(item) for item in _loads_any_list(row["unmet_constraint_ids_json"])
            ),
            created_at=_parse_dt(row["created_at"]),
        )

    @staticmethod
    def _row_to_evidence_record(row: sqlite3.Row) -> EvidenceRecord:
        return EvidenceRecord(
            id=row["id"],
            workflow_run_id=row["workflow_run_id"],
            research_brief_id=row["research_brief_id"],
            research_plan_id=row["research_plan_id"],
            research_direction_id=row["research_direction_id"],
            subagent_task_id=row["subagent_task_id"],
            trace_id=row["trace_id"],
            schema_version=row["schema_version"],
            status=row["status"],
            source_type=row["source_type"],
            source_platform=row["source_platform"],
            source_url=row["source_url"],
            source_id=row["source_id"],
            source_author_id=row["source_author_id"],
            source_author_name=row["source_author_name"],
            source_published_at=_parse_dt(row["source_published_at"]) if row["source_published_at"] else None,
            collected_at=_parse_dt(row["collected_at"]),
            title=row["title"],
            text_excerpt=row["text_excerpt"],
            raw_content_ref=row["raw_content_ref"],
            evidence_type=row["evidence_type"],
            claim=row["claim"],
            metrics=_loads(row["metrics_json"]),
            language=row["language"],
            content_hash=row["content_hash"],
            dedupe_key=row["dedupe_key"],
            retrieval_query=row["retrieval_query"],
            retrieval_rank=row["retrieval_rank"],
            retrieval_score=row["retrieval_score"],
            normalized_payload=_loads(row["normalized_payload_json"]),
            metadata=_loads(row["metadata_json"]),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    @staticmethod
    def _row_to_evidence_lineage(row: sqlite3.Row) -> EvidenceLineageRecord:
        return EvidenceLineageRecord(
            id=row["id"],
            workflow_run_id=row["workflow_run_id"],
            evidence_record_id=row["evidence_record_id"],
            research_brief_id=row["research_brief_id"],
            research_plan_id=row["research_plan_id"],
            research_direction_id=row["research_direction_id"],
            subagent_task_id=row["subagent_task_id"],
            trace_id=row["trace_id"],
            parent_evidence_record_id=row["parent_evidence_record_id"],
            schema_version=row["schema_version"],
            transformation_type=row["transformation_type"],
            transformation_version=row["transformation_version"],
            lineage_payload=_loads(row["lineage_payload_json"]),
            metadata=_loads(row["metadata_json"]),
            created_at=_parse_dt(row["created_at"]),
        )

    @staticmethod
    def _row_to_result_snapshot(row: sqlite3.Row) -> ResearchResultSnapshotRecord:
        return ResearchResultSnapshotRecord(
            id=row["id"],
            workflow_run_id=row["workflow_run_id"],
            research_brief_id=row["research_brief_id"],
            research_plan_id=row["research_plan_id"],
            schema_version=row["schema_version"],
            snapshot_version=row["snapshot_version"],
            result_type=row["result_type"],
            status=row["status"],
            title=row["title"],
            executive_summary=row["executive_summary"],
            findings=_loads_list(row["findings_json"]),
            recommendations=_loads_list(row["recommendations_json"]),
            claim_count=row["claim_count"],
            supported_claim_count=row["supported_claim_count"],
            unsupported_claim_count=row["unsupported_claim_count"],
            citation_coverage_score=row["citation_coverage_score"],
            faithfulness_score=row["faithfulness_score"],
            answer_relevancy_score=row["answer_relevancy_score"],
            derivation_completeness_score=row["derivation_completeness_score"],
            evidence_boundary_calibration_score=row["evidence_boundary_calibration_score"],
            decision_summary=_loads(row["decision_summary_json"]),
            decision_cards=_loads_list(row["decision_cards_json"]),
            priority_summary=_loads(row["priority_summary_json"]),
            evidence_boundary_summary=_loads(row["evidence_boundary_summary_json"]),
            limitations=_loads_list(row["limitations_json"]),
            abstentions=_loads_list(row["abstentions_json"]),
            metadata=_loads(row["metadata_json"]),
            created_at=_parse_dt(row["created_at"]),
        )

    @staticmethod
    def _row_to_human_decision(row: sqlite3.Row) -> HumanDecisionRecord:
        return HumanDecisionRecord(
            id=row["id"],
            workflow_run_id=row["workflow_run_id"],
            thread_id=row["thread_id"],
            schema_version=row["schema_version"],
            target_type=row["target_type"],
            target_id=row["target_id"],
            decision_request_id=row["decision_request_id"],
            decision_status=row["decision_status"],
            decision_payload=_loads(row["decision_payload_json"]),
            rationale=row["rationale"],
            created_by_type=row["created_by_type"],
            created_by_id=row["created_by_id"],
            research_brief_id=row["research_brief_id"],
            research_plan_id=row["research_plan_id"],
            research_result_snapshot_id=row["research_result_snapshot_id"],
            metadata=_loads(row["metadata_json"]),
            created_at=_parse_dt(row["created_at"]),
        )
