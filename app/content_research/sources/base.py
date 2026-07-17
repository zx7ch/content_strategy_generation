"""Source adapter contracts for Content Research."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class SourceCollectionRequest:
    workflow_run_id: str
    query: str
    source_kind: str = "search_result"
    limit: int = 50
    sort: str = "likes"
    context: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SourceCollectionResult:
    provider: str
    source_kind: str
    status: str
    items: list[dict]
    failure_reason: str | None = None
    cookie_status: str = "unknown"
    metadata: dict = field(default_factory=dict)


class SourceAdapter(Protocol):
    async def collect(self, request: SourceCollectionRequest) -> SourceCollectionResult: ...
