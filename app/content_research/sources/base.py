"""Source adapter contracts for Content Research."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

FieldAvailability = Literal["present", "missing", "not_requested", "unavailable", "not_applicable"]


@dataclass(frozen=True)
class ProviderCapability:
    operation: str
    status: Literal["supported", "unavailable", "unsupported"]
    fields: tuple[str, ...] = ()
    limits: dict[str, int | bool] = field(default_factory=dict)
    failure_retryability: dict[str, bool] = field(default_factory=dict)


@dataclass(frozen=True)
class DiscoverCandidatesRequest:
    workflow_run_id: str
    query: str
    limit: int = 50
    sort: str = "likes"
    cursor: str | None = None
    context: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CollectNoteDetailRequest:
    workflow_run_id: str
    note_id: str
    note_url: str = ""
    required_fields: tuple[str, ...] = ()
    context: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CollectCommentsRequest:
    workflow_run_id: str
    parent_note_id: str
    note_url: str = ""
    limit: int = 30
    cursor: str | None = None
    top_level_only: bool = True
    reply_depth_limit: int = 0
    context: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SourceOperationResult:
    provider: str
    operation: Literal["discover_candidates", "collect_note_detail", "collect_comments"]
    source_kind: str
    status: Literal["completed", "empty", "failed", "partial_completed"]
    items: list[dict]
    failure_reason: str | None = None
    cookie_status: str = "unknown"
    next_cursor: str | None = None
    completeness: Literal["complete", "partial", "truncated_by_cap", "unavailable"] = "complete"
    field_availability: dict[str, FieldAvailability] = field(default_factory=dict)
    retryable: bool = False
    metadata: dict = field(default_factory=dict)


class SourceAdapter(Protocol):
    async def discover_candidates(self, request: DiscoverCandidatesRequest) -> SourceOperationResult: ...
    async def collect_note_detail(self, request: CollectNoteDetailRequest) -> SourceOperationResult: ...
    async def collect_comments(self, request: CollectCommentsRequest) -> SourceOperationResult: ...
    def capabilities(self) -> tuple[ProviderCapability, ...]: ...
