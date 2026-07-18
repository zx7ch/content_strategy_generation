"""Typed persistence records for the post-bundle evidence architecture."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.content_research.models import utcnow


def _required(*values: str) -> None:
    if not all(values):
        raise ValueError("required identity or relationship field is missing")


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
    research_direction_id: str = ""
    canonical_source_id: str = ""
    evidence_packet_id: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        _required(self.research_direction_id, self.canonical_source_id, self.evidence_packet_id)


@dataclass(frozen=True)
class DirectionalEvidencePacketRecord(TypedPersistenceRecord):
    research_direction_id: str = ""
    canonical_source_id: str = ""
    field_projection_hash: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        _required(self.research_direction_id, self.canonical_source_id, self.field_projection_hash)


@dataclass(frozen=True)
class ClaimCandidateRecord(TypedPersistenceRecord):
    research_direction_id: str = ""
    evidence_packet_id: str = ""
    statement: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        _required(self.research_direction_id, self.evidence_packet_id, self.statement)


@dataclass(frozen=True)
class ClaimAdmissionDecisionRecord(TypedPersistenceRecord):
    research_direction_id: str = ""
    claim_candidate_id: str = ""
    decision: str = ""
    policy_snapshot_id: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        _required(self.research_direction_id, self.claim_candidate_id, self.decision, self.policy_snapshot_id)
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
        if self.aggregate_type not in {"cross_direction_corroboration", "cross_direction_tension", "action_hypothesis"}:
            raise ValueError("invalid aggregate claim type")


@dataclass(frozen=True)
class StageCheckpointRecord(TypedPersistenceRecord):
    subagent_task_id: str = ""
    stage_name: str = ""
    input_fingerprint: str = ""
    status: str = "pending"
    retry_count: int = 0

    def __post_init__(self) -> None:
        super().__post_init__()
        _required(self.subagent_task_id, self.stage_name, self.input_fingerprint)
        if self.stage_name not in {"collect", "packet", "facts", "admission", "reconcile", "aggregate", "compose", "faithfulness"}:
            raise ValueError("invalid checkpoint stage")
        if self.retry_count < 0:
            raise ValueError("checkpoint retry_count cannot be negative")


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
        if self.reservation_status not in {"reserved", "committed", "released", "expired", "cost_unknown"}:
            raise ValueError("invalid reservation status")
        if self.reserved_amount < 0 or self.consumed_amount < 0:
            raise ValueError("ledger amounts cannot be negative")


@dataclass(frozen=True)
class ReportFaithfulnessDecisionRecord(TypedPersistenceRecord):
    research_plan_id: str = ""
    result_snapshot_id: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        _required(self.research_plan_id, self.result_snapshot_id)
