"""Domain records for Content Research evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.content_research.models import utcnow


@dataclass(frozen=True)
class EvidenceRecord:
    id: str
    workflow_run_id: str
    schema_version: str
    status: str
    source_type: str
    source_platform: str
    source_url: str
    source_id: str
    evidence_type: str
    normalized_payload: dict[str, Any]
    research_brief_id: str | None = None
    research_plan_id: str | None = None
    research_direction_id: str | None = None
    subagent_task_id: str | None = None
    trace_id: str | None = None
    source_author_id: str | None = None
    source_author_name: str | None = None
    source_published_at: datetime | None = None
    collected_at: datetime = field(default_factory=utcnow)
    title: str = ""
    text_excerpt: str = ""
    raw_content_ref: str = ""
    claim: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    language: str = "unknown"
    content_hash: str = ""
    dedupe_key: str = ""
    retrieval_query: str = ""
    retrieval_rank: int | None = None
    retrieval_score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class EvidenceLineageRecord:
    id: str
    evidence_record_id: str
    schema_version: str
    transformation_type: str
    transformation_version: str
    lineage_payload: dict[str, Any]
    workflow_run_id: str = ""
    research_brief_id: str | None = None
    research_plan_id: str | None = None
    research_direction_id: str | None = None
    subagent_task_id: str | None = None
    trace_id: str | None = None
    parent_evidence_record_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class EvidenceBundleRecord:
    id: str
    workflow_run_id: str
    schema_version: str
    status: str
    bundle_type: str
    bundle_version: str
    research_brief_id: str | None = None
    research_plan_id: str | None = None
    research_direction_id: str | None = None
    summary: str = ""
    coverage: dict[str, Any] = field(default_factory=dict)
    retrieval_metrics: dict[str, Any] = field(default_factory=dict)
    faithfulness_metrics: dict[str, Any] = field(default_factory=dict)
    cross_source_metrics: dict[str, Any] = field(default_factory=dict)
    contradiction_summary: dict[str, Any] = field(default_factory=dict)
    citation_coverage: dict[str, Any] = field(default_factory=dict)
    unsupported_claim_count: int = 0
    missing_evidence: list[dict[str, Any]] = field(default_factory=list)
    priority_policy_id: str | None = None
    evidence_boundary_policy_id: str | None = None
    decision_card: dict[str, Any] = field(default_factory=dict)
    priority: dict[str, Any] = field(default_factory=dict)
    evidence_state: str = "signal"
    evidence_grade: str = "C"
    claim_scope: dict[str, Any] = field(default_factory=dict)
    next_action: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class EvidenceBundleItemRecord:
    id: str
    bundle_id: str
    role: str
    sort_order: int
    schema_version: str
    evidence_record_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class ExpandedEvidenceBundle:
    bundle: EvidenceBundleRecord
    items: list[EvidenceBundleItemRecord]
    evidence_by_role: dict[str, list[EvidenceRecord]]
    lineage_by_evidence_id: dict[str, list[EvidenceLineageRecord]]
    source_links: list[dict[str, str]]
    missing_evidence: list[dict[str, Any]]
