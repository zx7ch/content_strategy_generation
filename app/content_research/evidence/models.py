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
