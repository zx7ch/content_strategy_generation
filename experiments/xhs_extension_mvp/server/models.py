from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from experiments.xhs_extension_mvp.server.scraper_models import ScrapePhase


PageType = Literal["search_result", "note_detail", "manual"]
QueryCategory = Literal["core", "crowd", "scenario", "problem", "compare", "decision", "custom"]
HotspotMetric = Literal["likes", "comments", "collections"]
HotspotStatus = Literal["empty", "ready", "error"]
ActiveTaskStatus = Literal["active", "missing", "expired"]
ExtensionCaptureStatus = Literal["accepted", "duplicate_only", "failed"]


class ExpandedQuery(BaseModel):
    query_id: str
    category: QueryCategory
    query_text: str
    order: int


class EvidenceRef(BaseModel):
    note_id: Optional[str] = None
    title: str
    source_url: str
    raw_href: str = ""
    xsec_token: str = ""
    xsec_source: str = ""
    debug_url_source: str = ""
    query_text: str = ""
    author: str = ""
    likes: int = 0
    comments: int = 0
    collections: int = 0


class Candidate(BaseModel):
    candidate_id: str
    title: str
    why_now: str
    angle: str
    score: float
    supporting_note_count: int = 0
    query_coverage_count: int = 0
    score_explanation: str = ""
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class CollectionSummary(BaseModel):
    capture_batch_count: int = 0
    deduped_item_count: int = 0
    manual_seed_count: int = 0


class RecommendedNote(BaseModel):
    note_id: Optional[str] = None
    title: str
    source_url: str
    author: str = ""
    excerpt: str = ""
    score: float
    score_reason: str = ""
    why_recommended: str = ""
    likes: int = 0
    comments: int = 0
    collections: int = 0
    query_coverage_count: int = 0


class RecommendedNotesFilterReason(BaseModel):
    code: str
    label: str
    count: int = 0


class RecommendedNotesDiagnostics(BaseModel):
    total_note_count: int = 0
    hard_filter_pass_count: int = 0
    llm_recommended_count: int = 0
    llm_excluded_count: int = 0
    analysis_source: Literal["llm", "fallback_rule"] = "fallback_rule"
    analysis_notice: Optional[str] = None
    hard_filter_reasons: list[RecommendedNotesFilterReason] = Field(default_factory=list)


class HotspotItem(BaseModel):
    note_id: Optional[str] = None
    title: str
    source_url: str
    author: str = ""
    excerpt: str = ""
    likes: int = 0
    comments: int = 0
    collections: int = 0
    query_sources: list[str] = Field(default_factory=list)


class HotspotList(BaseModel):
    metric: HotspotMetric
    items: list[HotspotItem] = Field(default_factory=list)


class HotspotSnapshotResponse(BaseModel):
    task_id: str
    status: HotspotStatus = "empty"
    generated_at: Optional[datetime] = None
    stale_seconds: int = 0
    error_message: str = ""
    lists: list[HotspotList] = Field(default_factory=list)


class CaptureItemIn(BaseModel):
    source_url: str
    raw_href: str = ""
    xsec_token: str = ""
    xsec_source: str = ""
    debug_url_source: str = ""
    page_type: PageType
    query_text: str = ""
    note_id: str = ""
    title: str
    author: str = ""
    visible_text_excerpt: str = ""
    tags: list[str] = Field(default_factory=list)
    likes: int = 0
    comments: int = 0
    collections: int = 0
    cover_image_url: str = ""


class CreateTaskRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=120)


class CreateTaskResponse(BaseModel):
    task_id: str
    topic: str
    expanded_queries: list[ExpandedQuery]
    query_generation_source: Literal["llm", "fallback_rule"] = "fallback_rule"
    query_generation_notice: Optional[str] = None


class ErrorSummary(BaseModel):
    code: str
    message: str


class ActiveTask(BaseModel):
    task_id: str
    capture_token: str
    topic: str
    created_at: datetime
    activated_at: datetime
    status: ActiveTaskStatus = "active"
    snapshot_version: int = 0
    capture_count: int = 0
    candidate_count: int = 0


class ActiveSearchContext(BaseModel):
    task_id: str
    query: str
    source: str = "expanded_query"
    opened_at: datetime


class ActiveTaskResponse(BaseModel):
    active_task: Optional[ActiveTask] = None
    active_search_context: Optional[ActiveSearchContext] = None
    error_summary: Optional[ErrorSummary] = None


class ActiveSearchContextRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    source: str = Field(default="expanded_query", max_length=80)
    opened_at: Optional[datetime] = None


class ExtensionHealthResponse(BaseModel):
    status: str
    server_time: datetime
    active_task_available: bool
    version: str = "xhs-extension-mvp"


class ExtensionCaptureRequest(BaseModel):
    task_id: str
    request_id: str
    tab_id: Optional[int] = None
    page_url: str = ""
    page_type: PageType
    query_text: str = ""
    visible_items: list[CaptureItemIn] = Field(default_factory=list)


class ExtensionCaptureResponse(BaseModel):
    task_id: str
    request_id: str
    ingestion_run_id: str
    snapshot_version: int
    captured_count: int
    new_count: int
    duplicate_count: int
    status: ExtensionCaptureStatus
    error_summary: Optional[ErrorSummary] = None


class ManualSeedRequest(BaseModel):
    text: str = Field(min_length=1)


class ManualSeedResponse(BaseModel):
    task_id: str
    imported_count: int


class CustomQueryRequest(BaseModel):
    text: str = Field(min_length=1)


class CustomQueryResponse(BaseModel):
    task_id: str
    created_count: int
    skipped_count: int


class DeleteCustomQueryResponse(BaseModel):
    task_id: str
    deleted: bool


class TaskSnapshotResponse(BaseModel):
    task_id: str
    topic: str
    created_at: datetime
    updated_at: datetime
    query_generation_source: Literal["llm", "fallback_rule"] = "fallback_rule"
    query_generation_notice: Optional[str] = None
    snapshot_version: int = 0
    candidate_count: int = 0
    capture_count: int = 0
    expanded_queries: list[ExpandedQuery]
    imported_page_count: int = 0
    imported_item_count: int = 0
    collection_summary: CollectionSummary = Field(default_factory=CollectionSummary)
    recommended_notes: list[RecommendedNote] = Field(default_factory=list)
    recommended_notes_diagnostics: RecommendedNotesDiagnostics = Field(default_factory=RecommendedNotesDiagnostics)
    candidates: list[Candidate] = Field(default_factory=list)


class TaskSnapshotVersionResponse(BaseModel):
    task_id: str
    snapshot_version: int
    updated_at: datetime
    candidate_count: int
    capture_count: int


class AutoScrapeRequest(BaseModel):
    keyword: str = Field(min_length=1, max_length=200)
    scroll_count: int = Field(default=5, ge=1, le=10)


class AutoScrapeResponse(BaseModel):
    task_id: str
    accepted: bool
    started_at: datetime


class ScrapeStatusResponse(BaseModel):
    task_id: str
    keyword: str
    phase: ScrapePhase
    scroll_index: int
    scroll_total: int
    items_count: int
    error_message: str = ""
    started_at: datetime
    finished_at: Optional[datetime] = None


class ScraperReadinessResponse(BaseModel):
    profile_exists: bool
    logged_in: bool
    last_checked_at: datetime
    detail: str = ""
