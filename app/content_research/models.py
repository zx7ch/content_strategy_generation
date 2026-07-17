"""Domain records for the Content Research P0 store."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ResearchBriefRecord:
    id: str
    workflow_run_id: str
    thread_id: str
    schema_version: str
    status: str
    payload: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class ResearchPlanRecord:
    id: str
    brief_id: str
    workflow_run_id: str
    thread_id: str
    schema_version: str
    status: str
    payload: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class ResearchDirectionRecord:
    id: str
    plan_id: str
    workflow_run_id: str
    thread_id: str
    schema_version: str
    status: str
    priority: int
    payload: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class SubagentTaskRecord:
    id: str
    workflow_run_id: str
    thread_id: str
    schema_version: str
    status: str
    plan_id: str | None
    direction_id: str | None
    payload: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class TraceRecord:
    id: str
    workflow_run_id: str
    thread_id: str
    schema_version: str
    status: str
    started_at: datetime
    payload: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class ObservationEventRecord:
    id: str
    trace_id: str
    workflow_run_id: str
    thread_id: str
    schema_version: str
    status: str
    sequence_no: int
    event_type: str
    event_name: str
    timestamp: datetime
    payload: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class ResearchResultSnapshotRecord:
    id: str
    workflow_run_id: str
    schema_version: str
    snapshot_version: str
    result_type: str
    status: str
    title: str
    executive_summary: str
    research_brief_id: str | None = None
    research_plan_id: str | None = None
    findings: list[dict[str, Any]] = field(default_factory=list)
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    evidence_bundle_ids: list[str] = field(default_factory=list)
    claim_count: int = 0
    supported_claim_count: int = 0
    unsupported_claim_count: int = 0
    citation_coverage_score: float | None = None
    faithfulness_score: float | None = None
    answer_relevancy_score: float | None = None
    derivation_completeness_score: float | None = None
    evidence_boundary_calibration_score: float | None = None
    decision_summary: dict[str, Any] = field(default_factory=dict)
    decision_cards: list[dict[str, Any]] = field(default_factory=list)
    priority_summary: dict[str, Any] = field(default_factory=dict)
    evidence_boundary_summary: dict[str, Any] = field(default_factory=dict)
    limitations: list[dict[str, Any]] = field(default_factory=list)
    abstentions: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class HumanDecisionRecord:
    id: str
    workflow_run_id: str
    thread_id: str
    schema_version: str
    target_type: str
    target_id: str
    decision_request_id: str
    decision_status: str
    decision_payload: dict[str, Any]
    rationale: str = ""
    created_by_type: str = "user"
    created_by_id: str | None = None
    research_brief_id: str | None = None
    research_plan_id: str | None = None
    research_result_snapshot_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
