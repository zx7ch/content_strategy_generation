"""Shared subagent contracts for Content Research."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.content_research.evidence.models import EvidenceBundleRecord, EvidenceRecord
from app.content_research.models import SubagentTaskRecord
from app.content_research.sources import SourceAdapterRegistry
from app.content_research.sources.base import SourceOperationResult


@dataclass(frozen=True)
class SubagentExecutionContext:
    task: SubagentTaskRecord
    source_registry: SourceAdapterRegistry
    provider: str = "xiaohongshu"
    source_kind: str = "search_result"
    query: str = ""
    limit: int = 10
    source_result: SourceOperationResult | None = None


@dataclass(frozen=True)
class SubagentFinding:
    finding_id: str
    summary: str
    evidence_refs: list[str]
    supporting_fact_ids: list[str]
    missing_evidence: list[dict[str, Any]]
    evidence_boundary_hint: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SubagentExecutionResult:
    status: str
    findings: list[SubagentFinding]
    evidence_records: list[EvidenceRecord]
    missing_evidence: list[dict[str, Any]]
    evidence_bundle: EvidenceBundleRecord | None = None
    failure_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ContentResearchSubagent(Protocol):
    agent_name: str
    agent_version: str

    async def execute(self, context: SubagentExecutionContext) -> SubagentExecutionResult: ...
