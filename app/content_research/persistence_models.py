"""Typed persistence records for the post-bundle evidence architecture."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.content_research.models import utcnow


def _required(*values: str) -> None:
    if not all(values):
        raise ValueError("required identity or relationship field is missing")


def _forbid_legacy_report_payload(value: Any) -> None:
    if isinstance(value, dict):
        forbidden = {
            "evidence_bundle_id",
            "evidence_bundle_ids",
            "items",
            "recommendations",
        }
        if forbidden & set(value):
            raise ValueError("report payload cannot contain legacy bundle/result fields")
        for nested in value.values():
            _forbid_legacy_report_payload(nested)
    elif isinstance(value, list | tuple):
        for nested in value:
            _forbid_legacy_report_payload(nested)


@dataclass(frozen=True)
class TypedPersistenceRecord:
    id: str
    schema_version: str
    payload: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        _required(self.id, self.schema_version)


@dataclass(frozen=True)
class CanonicalSourceRecord(TypedPersistenceRecord):
    platform: str = ""
    platform_source_kind: str = ""
    platform_source_id: str = ""
    canonical_url: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        _required(self.platform, self.platform_source_kind, self.platform_source_id)


@dataclass(frozen=True)
class DirectionSourceProjectionRecord(TypedPersistenceRecord):
    workflow_run_id: str = ""
    research_direction_id: str = ""
    canonical_source_id: str = ""
    evidence_packet_id: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        _required(
            self.workflow_run_id,
            self.research_direction_id,
            self.canonical_source_id,
            self.evidence_packet_id,
        )


@dataclass(frozen=True)
class DirectionalEvidencePacketRecord(TypedPersistenceRecord):
    workflow_run_id: str = ""
    research_direction_id: str = ""
    canonical_source_id: str = ""
    field_projection_hash: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        _required(
            self.workflow_run_id,
            self.research_direction_id,
            self.canonical_source_id,
            self.field_projection_hash,
        )


@dataclass(frozen=True)
class ClaimCandidateRecord(TypedPersistenceRecord):
    workflow_run_id: str = ""
    research_direction_id: str = ""
    evidence_packet_id: str = ""
    statement: str = ""
    intent_id: str = ""
    claim_type: str = ""
    requested_state: str = "pending_admission"

    def __post_init__(self) -> None:
        super().__post_init__()
        _required(
            self.workflow_run_id,
            self.research_direction_id,
            self.evidence_packet_id,
            self.statement,
            self.intent_id,
            self.claim_type,
            self.requested_state,
        )


@dataclass(frozen=True)
class ClaimAdmissionDecisionRecord(TypedPersistenceRecord):
    research_direction_id: str = ""
    claim_candidate_id: str = ""
    decision: str = ""
    policy_snapshot_id: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        _required(
            self.research_direction_id,
            self.claim_candidate_id,
            self.decision,
            self.policy_snapshot_id,
        )
        if self.decision not in {"admitted", "downgraded", "rejected"}:
            raise ValueError("invalid admission decision")


@dataclass(frozen=True)
class DirectionResultDecisionRecord(TypedPersistenceRecord):
    research_direction_id: str = ""
    policy_snapshot_id: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        _required(self.research_direction_id, self.policy_snapshot_id)


@dataclass(frozen=True)
class WeakSignalRecord(TypedPersistenceRecord):
    admission_decision_id: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        _required(self.admission_decision_id)


@dataclass(frozen=True)
class CrossDirectionRecord(TypedPersistenceRecord):
    research_plan_id: str = ""
    record_type: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        _required(self.research_plan_id, self.record_type)
        if self.record_type not in {"contradiction", "overlap"}:
            raise ValueError("invalid cross-direction record type")


@dataclass(frozen=True)
class AggregateClaimRecord(TypedPersistenceRecord):
    research_plan_id: str = ""
    aggregate_type: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        _required(self.research_plan_id, self.aggregate_type)
        if self.aggregate_type not in {
            "cross_direction_corroboration",
            "cross_direction_tension",
            "action_hypothesis",
        }:
            raise ValueError("invalid aggregate claim type")


@dataclass(frozen=True)
class StageCheckpointRecord(TypedPersistenceRecord):
    workflow_run_id: str = ""
    subagent_task_id: str = ""
    stage_name: str = ""
    input_fingerprint: str = ""
    status: str = "pending"
    retry_count: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _required(
            self.workflow_run_id, self.subagent_task_id, self.stage_name, self.input_fingerprint
        )
        if self.stage_name not in {
            "subject_structure",
            "query_plan",
            "collect",
            "collect_page",
            "operation",
            "selection",
            "selection_revision",
            "coverage_decision",
            "fallback_decision",
            "relevance_revision",
            "detail",
            "comments",
            "comments_page",
            "packet",
            "facts",
            "admission",
            "reconcile",
            "aggregate",
            "compose",
            "faithfulness",
        }:
            raise ValueError("invalid checkpoint stage")
        if self.retry_count < 0:
            raise ValueError("checkpoint retry_count cannot be negative")
        if self.finished_at is not None and self.started_at is None:
            raise ValueError("finished checkpoint requires started_at")
        if (
            self.started_at is not None
            and self.finished_at is not None
            and self.finished_at < self.started_at
        ):
            raise ValueError("checkpoint finished_at cannot precede started_at")


@dataclass(frozen=True)
class BudgetLedgerEntryRecord(TypedPersistenceRecord):
    research_plan_id: str = ""
    research_direction_id: str | None = None
    idempotency_key: str = ""
    reservation_status: str = ""
    reserved_amount: float = 0.0
    consumed_amount: float = 0.0
    stage_checkpoint_id: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _required(self.research_plan_id, self.idempotency_key, self.reservation_status)
        if self.reservation_status not in {
            "reserved",
            "committed",
            "released",
            "expired",
            "cost_unknown",
        }:
            raise ValueError("invalid reservation status")
        if self.reserved_amount < 0 or self.consumed_amount < 0:
            raise ValueError("ledger amounts cannot be negative")


@dataclass(frozen=True)
class ReportDraftRecord(TypedPersistenceRecord):
    workflow_run_id: str = ""
    research_plan_id: str = ""
    governed_snapshot_id: str = ""
    governed_snapshot_version: str = ""
    input_fingerprint: str = ""
    policy_version: str = ""
    algorithm_version: str = ""
    previous_version_id: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _forbid_legacy_report_payload(self.payload)
        _required(
            self.workflow_run_id,
            self.research_plan_id,
            self.governed_snapshot_id,
            self.governed_snapshot_version,
            self.input_fingerprint,
            self.policy_version,
            self.algorithm_version,
        )


@dataclass(frozen=True)
class ReportFaithfulnessDecisionRecord(TypedPersistenceRecord):
    workflow_run_id: str = ""
    research_plan_id: str = ""
    governed_snapshot_id: str = ""
    governed_snapshot_version: str = ""
    input_fingerprint: str = ""
    policy_version: str = ""
    algorithm_version: str = ""
    report_draft_id: str = ""
    previous_version_id: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _forbid_legacy_report_payload(self.payload)
        _required(
            self.workflow_run_id,
            self.research_plan_id,
            self.governed_snapshot_id,
            self.governed_snapshot_version,
            self.input_fingerprint,
            self.policy_version,
            self.algorithm_version,
            self.report_draft_id,
        )


@dataclass(frozen=True)
class ReportPublicationRecord(TypedPersistenceRecord):
    workflow_run_id: str = ""
    research_plan_id: str = ""
    governed_snapshot_id: str = ""
    governed_snapshot_version: str = ""
    input_fingerprint: str = ""
    policy_version: str = ""
    algorithm_version: str = ""
    report_draft_id: str = ""
    faithfulness_decision_id: str = ""
    publication_state: str = ""
    previous_version_id: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _forbid_legacy_report_payload(self.payload)
        _required(
            self.workflow_run_id,
            self.research_plan_id,
            self.governed_snapshot_id,
            self.governed_snapshot_version,
            self.input_fingerprint,
            self.policy_version,
            self.algorithm_version,
            self.report_draft_id,
            self.faithfulness_decision_id,
            self.publication_state,
        )
        if self.publication_state not in {
            "complete_verified_report",
            "partial_verified_report",
            "evidence_only_report",
        }:
            raise ValueError("invalid report publication state")
