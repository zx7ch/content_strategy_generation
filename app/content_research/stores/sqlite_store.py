"""SQLite adapter for Content Research P0 records."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, TypeVar

from app.content_research.bootstrap import bootstrap_content_research_schema
from app.content_research.contracts import DirectionContract, RunPolicySnapshot, SamplePolicy
from app.content_research.evidence.models import (
    EvidenceBundleItemRecord,
    EvidenceBundleRecord,
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
    ReportFaithfulnessDecisionRecord,
    StageCheckpointRecord,
    TypedPersistenceRecord,
    WeakSignalRecord,
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
    DirectionSourceProjectionRecord: ("content_research_direction_source_projections", ("research_direction_id", "canonical_source_id", "evidence_packet_id")),
    DirectionalEvidencePacketRecord: ("content_research_directional_evidence_packets", ("research_direction_id", "canonical_source_id", "field_projection_hash")),
    ClaimCandidateRecord: ("content_research_claim_candidates", ("research_direction_id", "evidence_packet_id", "statement")),
    ClaimAdmissionDecisionRecord: ("content_research_claim_admission_decisions", ("research_direction_id", "claim_candidate_id", "decision", "policy_snapshot_id")),
    DirectionResultDecisionRecord: ("content_research_direction_result_decisions", ("research_direction_id", "policy_snapshot_id")),
    WeakSignalRecord: ("content_research_weak_signals", ("admission_decision_id",)),
    CrossDirectionRecord: ("content_research_cross_direction_records", ("research_plan_id", "record_type")),
    AggregateClaimRecord: ("content_research_aggregate_claims", ("research_plan_id", "aggregate_type")),
    StageCheckpointRecord: ("content_research_stage_checkpoints", ("subagent_task_id", "stage_name", "input_fingerprint", "status", "retry_count")),
    BudgetLedgerEntryRecord: ("content_research_budget_ledger_entries", ("research_plan_id", "research_direction_id", "idempotency_key", "reservation_status", "reserved_amount", "consumed_amount", "stage_checkpoint_id")),
    ReportFaithfulnessDecisionRecord: ("content_research_report_faithfulness_decisions", ("research_plan_id", "result_snapshot_id")),
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
        conn.execute("PRAGMA journal_mode=WAL")
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
            bundle_ids = [row[0] for row in conn.execute("SELECT id FROM content_research_evidence_bundles WHERE workflow_run_id = ?", (workflow_run_id,))]
            evidence_ids = [row[0] for row in conn.execute("SELECT id FROM content_research_evidence_records WHERE workflow_run_id = ?", (workflow_run_id,))]
            for bundle_id in bundle_ids:
                conn.execute("DELETE FROM content_research_evidence_bundle_items WHERE bundle_id = ?", (bundle_id,))
            for evidence_id in evidence_ids:
                conn.execute("DELETE FROM content_research_evidence_lineage WHERE evidence_record_id = ?", (evidence_id,))
            for table in ("content_research_evidence_bundles", "content_research_evidence_records", "content_research_human_decisions", "content_research_observation_events", "content_research_traces", "content_research_result_snapshots", "content_research_subagent_tasks", "content_research_directions", "content_research_plans", "content_research_briefs"):
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

    def save_evidence_bundle(self, bundle: EvidenceBundleRecord) -> EvidenceBundleRecord:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO content_research_evidence_bundles
                     (id, workflow_run_id, research_brief_id, research_plan_id,
                      research_direction_id, schema_version, status, bundle_type,
                      bundle_version, summary, coverage_json, retrieval_metrics_json,
                      faithfulness_metrics_json, cross_source_metrics_json,
                      contradiction_summary_json, citation_coverage_json,
                      unsupported_claim_count, missing_evidence_json, priority_policy_id,
                      evidence_boundary_policy_id, decision_card_json, priority_json,
                      evidence_state, evidence_grade, claim_scope_json, next_action_json,
                      metadata_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     workflow_run_id=excluded.workflow_run_id,
                     research_brief_id=excluded.research_brief_id,
                     research_plan_id=excluded.research_plan_id,
                     research_direction_id=excluded.research_direction_id,
                     schema_version=excluded.schema_version,
                     status=excluded.status,
                     bundle_type=excluded.bundle_type,
                     bundle_version=excluded.bundle_version,
                     summary=excluded.summary,
                     coverage_json=excluded.coverage_json,
                     retrieval_metrics_json=excluded.retrieval_metrics_json,
                     faithfulness_metrics_json=excluded.faithfulness_metrics_json,
                     cross_source_metrics_json=excluded.cross_source_metrics_json,
                     contradiction_summary_json=excluded.contradiction_summary_json,
                     citation_coverage_json=excluded.citation_coverage_json,
                     unsupported_claim_count=excluded.unsupported_claim_count,
                     missing_evidence_json=excluded.missing_evidence_json,
                     priority_policy_id=excluded.priority_policy_id,
                     evidence_boundary_policy_id=excluded.evidence_boundary_policy_id,
                     decision_card_json=excluded.decision_card_json,
                     priority_json=excluded.priority_json,
                     evidence_state=excluded.evidence_state,
                     evidence_grade=excluded.evidence_grade,
                     claim_scope_json=excluded.claim_scope_json,
                     next_action_json=excluded.next_action_json,
                     metadata_json=excluded.metadata_json,
                     updated_at=excluded.updated_at""",
                (
                    bundle.id,
                    bundle.workflow_run_id,
                    bundle.research_brief_id,
                    bundle.research_plan_id,
                    bundle.research_direction_id,
                    bundle.schema_version,
                    bundle.status,
                    bundle.bundle_type,
                    bundle.bundle_version,
                    bundle.summary,
                    _dumps(bundle.coverage),
                    _dumps(bundle.retrieval_metrics),
                    _dumps(bundle.faithfulness_metrics),
                    _dumps(bundle.cross_source_metrics),
                    _dumps(bundle.contradiction_summary),
                    _dumps(bundle.citation_coverage),
                    bundle.unsupported_claim_count,
                    _dumps_list(bundle.missing_evidence),
                    bundle.priority_policy_id,
                    bundle.evidence_boundary_policy_id,
                    _dumps(bundle.decision_card),
                    _dumps(bundle.priority),
                    bundle.evidence_state,
                    bundle.evidence_grade,
                    _dumps(bundle.claim_scope),
                    _dumps(bundle.next_action),
                    _dumps(bundle.metadata),
                    _fmt_dt(bundle.created_at),
                    _fmt_dt(bundle.updated_at),
                ),
            )
        return bundle

    def get_evidence_bundle(self, bundle_id: str) -> EvidenceBundleRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM content_research_evidence_bundles WHERE id = ?",
                (bundle_id,),
            ).fetchone()
        return self._row_to_evidence_bundle(row) if row else None

    def list_evidence_bundles_for_workflow(self, workflow_run_id: str) -> list[EvidenceBundleRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM content_research_evidence_bundles WHERE workflow_run_id = ? ORDER BY created_at ASC",
                (workflow_run_id,),
            ).fetchall()
        return [self._row_to_evidence_bundle(row) for row in rows]

    def add_evidence_bundle_item(self, item: EvidenceBundleItemRecord) -> EvidenceBundleItemRecord:
        _validate_payload("EvidenceBundleItem", item.payload)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO content_research_evidence_bundle_items
                     (id, bundle_id, evidence_record_id, role, sort_order, schema_version,
                      payload_json, metadata_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     bundle_id=excluded.bundle_id,
                     evidence_record_id=excluded.evidence_record_id,
                     role=excluded.role,
                     sort_order=excluded.sort_order,
                     schema_version=excluded.schema_version,
                     payload_json=excluded.payload_json,
                     metadata_json=excluded.metadata_json""",
                (
                    item.id,
                    item.bundle_id,
                    item.evidence_record_id,
                    item.role,
                    item.sort_order,
                    item.schema_version,
                    _dumps(item.payload),
                    _dumps(item.metadata),
                    _fmt_dt(item.created_at),
                ),
            )
        return item

    def list_evidence_bundle_items(self, bundle_id: str) -> list[EvidenceBundleItemRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM content_research_evidence_bundle_items WHERE bundle_id = ? ORDER BY sort_order ASC, created_at ASC",
                (bundle_id,),
            ).fetchall()
        return [self._row_to_evidence_bundle_item(row) for row in rows]

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
                          evidence_bundle_ids_json, claim_count, supported_claim_count,
                          unsupported_claim_count, citation_coverage_score, faithfulness_score,
                          answer_relevancy_score, derivation_completeness_score,
                          evidence_boundary_calibration_score, decision_summary_json, decision_cards_json,
                          priority_summary_json, evidence_boundary_summary_json, limitations_json,
                          abstentions_json, metadata_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                        _dumps_any_list(snapshot.evidence_bundle_ids),
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
        return record_type(
            id=row["id"], schema_version=row["schema_version"], payload=_loads(row["payload_json"]),
            metadata=_loads(row["metadata_json"]), created_at=_parse_dt(row["created_at"]),
            **{field: row[field] for field in fields},
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
        return self._save_typed_record("content_research_direction_source_projections", record, {"research_direction_id": record.research_direction_id, "canonical_source_id": record.canonical_source_id, "evidence_packet_id": record.evidence_packet_id})  # type: ignore[return-value]

    def save_directional_evidence_packet(self, record: DirectionalEvidencePacketRecord) -> DirectionalEvidencePacketRecord:
        self._require_typed_parent("content_research_canonical_sources", record.canonical_source_id, "canonical source")
        return self._save_typed_record("content_research_directional_evidence_packets", record, {"research_direction_id": record.research_direction_id, "canonical_source_id": record.canonical_source_id, "field_projection_hash": record.field_projection_hash})  # type: ignore[return-value]

    def save_claim_candidate(self, record: ClaimCandidateRecord) -> ClaimCandidateRecord:
        self._require_typed_parent("content_research_directional_evidence_packets", record.evidence_packet_id, "evidence packet")
        return self._save_typed_record("content_research_claim_candidates", record, {"research_direction_id": record.research_direction_id, "evidence_packet_id": record.evidence_packet_id, "statement": record.statement})  # type: ignore[return-value]

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

    def save_stage_checkpoint(self, record: StageCheckpointRecord) -> StageCheckpointRecord:
        return self._save_typed_record("content_research_stage_checkpoints", record, {"subagent_task_id": record.subagent_task_id, "stage_name": record.stage_name, "input_fingerprint": record.input_fingerprint, "status": record.status, "retry_count": record.retry_count})  # type: ignore[return-value]

    def save_budget_ledger_entry(self, record: BudgetLedgerEntryRecord) -> BudgetLedgerEntryRecord:
        if record.stage_checkpoint_id:
            self._require_typed_parent("content_research_stage_checkpoints", record.stage_checkpoint_id, "stage checkpoint")
        return self._save_typed_record("content_research_budget_ledger_entries", record, {"research_plan_id": record.research_plan_id, "research_direction_id": record.research_direction_id, "idempotency_key": record.idempotency_key, "reservation_status": record.reservation_status, "reserved_amount": record.reserved_amount, "consumed_amount": record.consumed_amount, "stage_checkpoint_id": record.stage_checkpoint_id})  # type: ignore[return-value]

    def save_report_faithfulness_decision(self, record: ReportFaithfulnessDecisionRecord) -> ReportFaithfulnessDecisionRecord:
        return self._save_typed_record(
            "content_research_report_faithfulness_decisions",
            record,
            {"research_plan_id": record.research_plan_id, "result_snapshot_id": record.result_snapshot_id},
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
    def _row_to_evidence_bundle(row: sqlite3.Row) -> EvidenceBundleRecord:
        return EvidenceBundleRecord(
            id=row["id"],
            workflow_run_id=row["workflow_run_id"],
            research_brief_id=row["research_brief_id"],
            research_plan_id=row["research_plan_id"],
            research_direction_id=row["research_direction_id"],
            schema_version=row["schema_version"],
            status=row["status"],
            bundle_type=row["bundle_type"],
            bundle_version=row["bundle_version"],
            summary=row["summary"],
            coverage=_loads(row["coverage_json"]),
            retrieval_metrics=_loads(row["retrieval_metrics_json"]),
            faithfulness_metrics=_loads(row["faithfulness_metrics_json"]),
            cross_source_metrics=_loads(row["cross_source_metrics_json"]),
            contradiction_summary=_loads(row["contradiction_summary_json"]),
            citation_coverage=_loads(row["citation_coverage_json"]),
            unsupported_claim_count=row["unsupported_claim_count"],
            missing_evidence=_loads_list(row["missing_evidence_json"]),
            priority_policy_id=row["priority_policy_id"],
            evidence_boundary_policy_id=row["evidence_boundary_policy_id"],
            decision_card=_loads(row["decision_card_json"]),
            priority=_loads(row["priority_json"]),
            evidence_state=row["evidence_state"],
            evidence_grade=row["evidence_grade"],
            claim_scope=_loads(row["claim_scope_json"]),
            next_action=_loads(row["next_action_json"]),
            metadata=_loads(row["metadata_json"]),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    @staticmethod
    def _row_to_evidence_bundle_item(row: sqlite3.Row) -> EvidenceBundleItemRecord:
        return EvidenceBundleItemRecord(
            id=row["id"],
            bundle_id=row["bundle_id"],
            evidence_record_id=row["evidence_record_id"],
            role=row["role"],
            sort_order=row["sort_order"],
            schema_version=row["schema_version"],
            payload=_loads(row["payload_json"]),
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
            evidence_bundle_ids=[str(item) for item in _loads_any_list(row["evidence_bundle_ids_json"])],
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
