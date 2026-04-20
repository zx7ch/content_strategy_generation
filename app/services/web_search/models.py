"""Core models for pluggable web search."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


CapabilityKind = Literal["discover", "fetch", "capture", "browser_action"]
CapabilityStatus = Literal["success", "empty", "transient_error", "permanent_error", "unsupported"]
FailureReason = Literal[
    "empty_result",
    "transient_error",
    "permanent_error",
    "auth_required",
    "rate_limited",
    "unsupported_capability",
]
EvidenceSourceKind = Literal[
    "spider_note",
    "browser_capture",
    "manual_text",
    "manual_url",
]


class SearchIntent(BaseModel):
    query: str = ""
    platform: str = "xiaohongshu"
    goal: str = "general"
    known_urls: List[str] = Field(default_factory=list)
    session_id: Optional[str] = None
    workflow_stage: Optional[str] = None
    constraints: Dict[str, Any] = Field(default_factory=dict)


class SearchTraceEntry(BaseModel):
    provider: str
    capability: CapabilityKind
    status: str
    latency_ms: int = 0
    item_count: int = 0
    failure_reason: Optional[str] = None


class Evidence(BaseModel):
    evidence_id: str = ""
    session_id: Optional[str] = None
    platform: str = "xiaohongshu"
    source_kind: EvidenceSourceKind = "spider_note"
    source_provider: str
    source_url: str = ""
    canonical_id: Optional[str] = None
    title: str = ""
    content_text: str = ""
    author: str = ""
    tags: List[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    media: List[str] = Field(default_factory=list)
    query_used: str = ""
    captured_at: datetime = Field(default_factory=datetime.utcnow)
    raw_payload: Dict[str, Any] = Field(default_factory=dict)

    def stable_merge_key(self) -> str:
        if self.canonical_id:
            return f"id:{self.canonical_id}"
        if self.source_url:
            return f"url:{self.source_url.strip()}"
        fingerprint = hashlib.sha1(
            "|".join(
                [
                    self.title.strip(),
                    self.author.strip(),
                    self.query_used.strip(),
                ]
            ).encode("utf-8")
        ).hexdigest()
        return f"hash:{fingerprint}"

    def provider_dedupe_key(self) -> str:
        if self.canonical_id:
            return f"id:{self.canonical_id}"
        if self.source_url:
            return f"url:{self.source_url.strip()}"
        return self.stable_merge_key()


class CapabilityRequest(BaseModel):
    capability: CapabilityKind
    intent: SearchIntent
    limit: int = 50
    cursor: Optional[str] = None
    provider_hint: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class CapabilityResult(BaseModel):
    capability: CapabilityKind
    provider: str
    status: CapabilityStatus
    items: List[Evidence] = Field(default_factory=list)
    failure_reason: Optional[FailureReason] = None
    trace: List[SearchTraceEntry] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ProviderDescriptor(BaseModel):
    provider_name: str
    supported_capabilities: List[CapabilityKind] = Field(default_factory=list)
    platforms: List[str] = Field(default_factory=lambda: ["xiaohongshu"])
    priority: int = 100
    enabled: bool = True


class EvidenceBatch(BaseModel):
    items: List[Evidence] = Field(default_factory=list)
    trace: List[SearchTraceEntry] = Field(default_factory=list)
    status: str = "empty"
    failure_reason: Optional[str] = None
