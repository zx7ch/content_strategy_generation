from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Generic, List, Optional, TypeVar, Literal
from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Generic Result wrapper
# ---------------------------------------------------------------------------

T = TypeVar("T")

DEFAULT_OUTPUT_LANGUAGE = "zh-CN"


def _normalize_output_language(value: Any) -> str:
    if value is None:
        return DEFAULT_OUTPUT_LANGUAGE
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or DEFAULT_OUTPUT_LANGUAGE
    return value


class Result(BaseModel, Generic[T]):
    code: int
    msg: str
    data: Optional[T] = None


# ---------------------------------------------------------------------------
# XHS note — maps 1:1 to the raw API response
# ---------------------------------------------------------------------------

class XHSNote(BaseModel):
    # author
    author_user_id: str
    author_nick_name: str
    author_avatar: Optional[str] = ""
    author_home_page_url: Optional[str] = ""

    # identity
    note_id: str
    note_url: str
    note_xsec_token: Optional[str] = ""
    note_card_type: Optional[str] = ""
    note_model_type: Optional[str] = ""

    # content
    note_display_title: str
    note_desc: Optional[str] = ""
    note_tags: List[str] = Field(default_factory=list)
    note_image_list: List[str] = Field(default_factory=list)
    note_ip_location: Optional[str] = ""

    # video fields
    video_id: Optional[str] = ""
    video_h264_url: Optional[str] = ""
    video_h265_url: Optional[str] = ""
    video_h266_url: Optional[str] = ""
    video_a1_url: Optional[str] = ""
    note_duration: Optional[str] = ""
    livePhoto: Optional[bool] = False

    # engagement metrics
    note_liked_count: int = 0
    collected_count: int = 0
    comment_count: int = 0
    share_count: int = 0

    # user interaction state
    note_liked: bool = False
    collected: bool = False

    # timestamps
    note_create_time: Optional[str] = ""
    note_last_update_time: Optional[str] = ""

    # monetisation
    pgy_url: Optional[str] = ""

    @field_validator(
        "note_liked_count", "collected_count", "comment_count", "share_count",
        mode="before",
    )
    @classmethod
    def parse_count(cls, v):
        return int(v) if isinstance(v, str) else v


# ---------------------------------------------------------------------------
# Web search
# ---------------------------------------------------------------------------

class WebSearchResult(BaseModel):
    platform: str
    url: str
    title: str
    snippet: str
    query_used: str


class XHSSearchResult(BaseModel):
    platform: str = "xiaohongshu"
    url: str
    title: str
    snippet: str
    query_used: str
    note: XHSNote


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

class RankedNote(BaseModel):
    note: XHSSearchResult
    engagement_score: float
    rank: int


# ---------------------------------------------------------------------------
# DB write
# ---------------------------------------------------------------------------

class WriteToDBResponse(BaseModel):
    code: int
    msg: str
    inserted_count: int = 0
    failed_ids: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Market gap analysis
# ---------------------------------------------------------------------------

class MarketGapReport(BaseModel):
    report: str
    insights: List[str]
    dominant_themes: List[str]
    whitespace: List[str]
    top_performing_styles: List[str]


class AnalysisResponse(BaseModel):
    code: int
    msg: str
    data: Optional[MarketGapReport] = None


# ---------------------------------------------------------------------------
# Agent request / response
# ---------------------------------------------------------------------------

class ContentStrategyRequest(BaseModel):
    user_query: str
    platforms: List[str]
    user_id: Optional[str] = None
    top_k: int = 50
    max_iterations: int = 3


class ContentStrategyResponse(BaseModel):
    llm_response: str
    tool_calls: List[dict] = Field(default_factory=list)
    tool_results: List[dict] = Field(default_factory=list)
    citations: List[str] = Field(default_factory=list)
    debug: Optional[dict] = None


class TopicSuggestion(BaseModel):
    topic: str
    reason: str
    angle: str


class ContentGeneratorRequest(BaseModel):
    user_profile: Optional[Dict[str, Any]] = None
    content_type: Optional[str] = None
    topic: Optional[str] = None
    platform: str = "xiaohongshu"
    output_language: str = DEFAULT_OUTPUT_LANGUAGE
    requirements: Optional[Dict[str, Any]] = None
    brand_guidelines: Optional[Dict[str, Any]] = None
    constraints: Optional[Dict[str, Any]] = None
    brand_preference: Optional[str] = None

    @field_validator("output_language", mode="before")
    @classmethod
    def normalize_output_language(cls, value: Any) -> str:
        return _normalize_output_language(value)


class GenerateRequest(BaseModel):
    text: str
    mode: Optional[str] = None
    brand_preference: Optional[str] = None
    constraints: Optional[Dict[str, Any]] = None
    user_profile: Optional[Dict[str, Any]] = None
    content_type: Optional[str] = None
    topic: Optional[str] = None
    platform: str = "xiaohongshu"
    output_language: str = DEFAULT_OUTPUT_LANGUAGE
    requirements: Optional[Dict[str, Any]] = None
    brand_guidelines: Optional[Dict[str, Any]] = None

    @field_validator("output_language", mode="before")
    @classmethod
    def normalize_output_language(cls, value: Any) -> str:
        return _normalize_output_language(value)


class GenerateResponse(BaseModel):
    mode: str
    suggestions: List[TopicSuggestion] = Field(default_factory=list)
    follow_up_question: Optional[str] = None
    brand_preference: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    cover_design_prompt: Optional[str] = None
    designed_update_time: Optional[str] = None
    generated_at: Optional[str] = None


class InitSessionRequest(BaseModel):
    user_id: str
    user_query: str
    platform: str = "xiaohongshu"
    mode: str = "editing"


class CreateSessionResponse(BaseModel):
    session_id: str
    user_id: str
    user_query: str
    platform: str
    mode: str
    stage: str
    lifecycle_state: str
    alive_until: Optional[str] = None
    purge_after: Optional[str] = None
    created_at: str
    updated_at: str


class EnqueueResponse(BaseModel):
    session_id: str
    stage: str
    job_id: str
    job_status: str = Field(default="queued")


class ResumeSessionResponse(BaseModel):
    session_id: str
    lifecycle_state: str
    resumed_jobs: int
    alive_until: Optional[str]
    purge_after: Optional[str]


class JobStatusResponse(BaseModel):
    job_id: str
    session_id: str
    job_type: str
    status: str
    attempts: int
    max_attempts: int
    last_error_code: Optional[str] = None
    last_error_message: Optional[str] = None
    cancel_reason: Optional[str] = None


class SessionStatusResponse(BaseModel):
    session_id: str
    user_id: str
    stage: str
    lifecycle_state: str
    alive_until: Optional[str] = None
    spider_cooldown_until: Optional[str] = None
    purge_after: Optional[str] = None
    job_status: Optional[str] = None
    current_job_id: Optional[str] = None
    token_used: int = 0
    token_budget: int = 0
    budget_remaining: int = 0
    budget_degraded: bool = False
    reindex_state: str = "ok"
    reindex_attempts: int = 0
    created_at: str
    updated_at: str
    error: Optional[str] = None


class ErrorResponse(BaseModel):
    error_code: str
    error_message: str
    error_details: Optional[Dict[str, Any]] = None
    retryable: bool = False
    suggested_action: Optional[str] = None


class SessionEventPayload(BaseModel):
    message: str
    progress: Optional[float] = None
    error_code: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class SessionEvent(BaseModel):
    event_id: int
    event_name: Literal[
        "stage_changed",
        "task_progress",
        "task_failed",
        "task_completed",
        "session_frozen",
        "session_resumed",
        "session_purged",
        "heartbeat",
    ]
    session_id: str
    job_id: Optional[str] = None
    stage: Optional[str] = None
    timestamp: datetime
    payload: SessionEventPayload

# ---------------------------------------------------------------------------
# V2 foundation API
# ---------------------------------------------------------------------------

class V2WorkspaceCreateRequest(BaseModel):
    name: str
    slug: str
    timezone: str = "Asia/Shanghai"


class V2WorkspaceResponse(BaseModel):
    id: str
    name: str
    slug: str
    timezone: str
    status: str
    created_at: str
    updated_at: str


class V2DefaultWorkspaceResponse(BaseModel):
    """Returned by GET /workspaces/default so the frontend can self-configure."""
    workspace_id: str
    user_id: str


class V2BrandCreateRequest(BaseModel):
    name: str
    category: Optional[str] = None
    stage: str
    target_audience: Dict[str, Any] = Field(default_factory=dict)
    brand_voice: Dict[str, Any] = Field(default_factory=dict)
    goals: Dict[str, Any] = Field(default_factory=dict)


class V2BrandUpdateRequest(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    stage: Optional[str] = None
    target_audience: Optional[Dict[str, Any]] = None
    brand_voice: Optional[Dict[str, Any]] = None
    goals: Optional[Dict[str, Any]] = None


class V2BrandResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    category: Optional[str] = None
    stage: str
    target_audience: Dict[str, Any] = Field(default_factory=dict)
    brand_voice: Dict[str, Any] = Field(default_factory=dict)
    goals: Dict[str, Any] = Field(default_factory=dict)
    is_demo: bool = False
    created_at: str
    updated_at: str


class V2BrandListResponse(BaseModel):
    items: List[V2BrandResponse] = Field(default_factory=list)


class V2BrandChannelCreateRequest(BaseModel):
    platform: str
    external_account_id: Optional[str] = None
    account_name: Optional[str] = None
    profile_url: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class V2BrandChannelUpdateRequest(BaseModel):
    external_account_id: Optional[str] = None
    account_name: Optional[str] = None
    profile_url: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class V2BrandChannelResponse(BaseModel):
    id: str
    workspace_id: str
    brand_id: str
    platform: str
    external_account_id: Optional[str] = None
    account_name: Optional[str] = None
    profile_url: Optional[str] = None
    status: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class V2BrandChannelListResponse(BaseModel):
    items: List[V2BrandChannelResponse] = Field(default_factory=list)


class V2BrandPolicyConfigUpsertRequest(BaseModel):
    policy_name: str
    policy_version: str
    hard_filter_rules: Dict[str, Any] = Field(default_factory=dict)
    brand_fit_rules: Dict[str, Any] = Field(default_factory=dict)
    exploration_preset_override: Dict[str, Any] = Field(default_factory=dict)
    topic_type_targets: Dict[str, Any] = Field(default_factory=dict)


class V2BrandPolicyConfigResponse(BaseModel):
    id: str
    workspace_id: str
    brand_id: str
    policy_name: str
    policy_version: str
    hard_filter_rules: Dict[str, Any] = Field(default_factory=dict)
    brand_fit_rules: Dict[str, Any] = Field(default_factory=dict)
    exploration_preset_override: Dict[str, Any] = Field(default_factory=dict)
    topic_type_targets: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool
    created_at: str
    updated_at: str


class V2BrandStateSnapshotCreateRequest(BaseModel):
    state_version: str
    stage: str
    state_features: Dict[str, Any] = Field(default_factory=dict)
    source_version: str = "v1"


class V2BrandStateSnapshotResponse(BaseModel):
    id: str
    workspace_id: str
    brand_id: str
    state_version: str
    stage: str
    state_features: Dict[str, Any] = Field(default_factory=dict)
    source_type: str
    source_version: str
    computed_at: str
    valid_from: str
    valid_to: Optional[str] = None
    created_at: str


class V2BrandStateSnapshotListResponse(BaseModel):
    items: List[V2BrandStateSnapshotResponse] = Field(default_factory=list)


class V2SourceSyncCommentPayload(BaseModel):
    platform_comment_id: str
    author_name: Optional[str] = None
    body_text: str
    commented_at: Optional[str] = None
    sentiment_label: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class V2SourceSyncItemPayload(BaseModel):
    platform_content_id: Optional[str] = None
    note_id: Optional[str] = None
    page_type: Optional[str] = None
    query_text: Optional[str] = None
    source_url: Optional[str] = None
    raw_href: Optional[str] = None
    title: Optional[str] = None
    body_text: Optional[str] = None
    visible_text_excerpt: Optional[str] = None
    author_id: Optional[str] = None
    author_handle: Optional[str] = None
    author_name: Optional[str] = None
    author_profile_url: Optional[str] = None
    likes: int = 0
    comments: int = 0
    collects: int = 0
    shares: int = 0
    views: Optional[int] = None
    follows_gained: Optional[int] = None
    tags: List[str] = Field(default_factory=list)
    published_at: Optional[str] = None
    normalized_source_type: Optional[str] = None
    comments_payload: List[V2SourceSyncCommentPayload] = Field(default_factory=list)


class V2SourceSyncCapturePayload(BaseModel):
    page_type: str
    captured_at: str
    items: List[V2SourceSyncItemPayload] = Field(default_factory=list)


class V2BrandSourceSyncRequest(BaseModel):
    source_type: str
    source_adapter: Optional[str] = None
    channel_id: Optional[str] = None
    capture_payload: V2SourceSyncCapturePayload


class V2ExtensionCaptureSessionCreateRequest(BaseModel):
    channel_id: Optional[str] = None


class V2ExtensionCaptureSubmitRequest(BaseModel):
    capture_session_id: str
    capture_token: str
    capture_payload: V2SourceSyncCapturePayload


class V2HistoricalImportRow(BaseModel):
    published_at: str
    title: str
    body_text: str
    likes: int
    collects: int
    comments: int
    platform_content_id: Optional[str] = None
    source_url: Optional[str] = None
    author_handle: Optional[str] = None
    author_name: Optional[str] = None
    shares: int = 0
    tags: List[str] = Field(default_factory=list)


class V2BrandDataImportRequest(BaseModel):
    import_type: str
    platform: str
    rows: List[V2HistoricalImportRow] = Field(default_factory=list)


class V2DataImportPreviewRequest(BaseModel):
    file_name: str = "historical-import.json"
    import_type: str
    platform: str
    rows: List[V2HistoricalImportRow] = Field(default_factory=list)
    file_content_base64: Optional[str] = None
    file_mime_type: Optional[str] = None

    @model_validator(mode="after")
    def validate_preview_input(self) -> "V2DataImportPreviewRequest":
        if self.rows or self.file_content_base64:
            return self
        raise ValueError("either rows or file_content_base64 is required")


class V2IngestionAcceptedResponse(BaseModel):
    ingestion_run_id: str
    entry_type: Literal["source_sync", "data_import"]
    status: str
    accepted_row_count: Optional[int] = None
    imported_item_count: Optional[int] = None
    deduped_item_count: Optional[int] = None


class V2ExtensionCaptureSessionResponse(BaseModel):
    capture_session_id: str
    capture_token: Optional[str] = None
    status: str
    expires_at: str
    captured_at: Optional[str] = None
    preview_payload: Optional[Dict[str, Any]] = None
    ingestion_receipt: Optional[V2IngestionAcceptedResponse] = None
    error_summary: Optional[Dict[str, Any]] = None


class V2DataImportPreviewResponse(BaseModel):
    preview_id: str
    file_name: str
    status: str
    uploaded_at: str
    parsed_row_count: int
    preview_payload: Optional[Dict[str, Any]] = None
    ingestion_receipt: Optional[V2IngestionAcceptedResponse] = None
    field_errors: List[Dict[str, Any]] = Field(default_factory=list)
    error_summary: Optional[Dict[str, Any]] = None


class V2IngestionRunResponse(BaseModel):
    ingestion_run_id: str
    entry_type: str
    source_type: str
    source_adapter: Optional[str] = None
    status: str
    stats: Dict[str, Any] = Field(default_factory=dict)
    error_summary: Dict[str, Any] = Field(default_factory=dict)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    created_at: str


class V2BrandWorkspaceResponse(BaseModel):
    brand: V2BrandResponse
    channels: List[V2BrandChannelResponse] = Field(default_factory=list)
    active_policy: Optional[V2BrandPolicyConfigResponse] = None
    latest_extension_capture_session: Optional[V2ExtensionCaptureSessionResponse] = None
    latest_data_import_preview: Optional[V2DataImportPreviewResponse] = None
    recent_ingestion_runs: List[V2IngestionRunResponse] = Field(default_factory=list)


class V2DiscoveryTaskCreateRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=120)


class V2DiscoveryCustomQueryRequest(BaseModel):
    text: str = Field(min_length=1)


class V2DiscoveryExpandedQueryResponse(BaseModel):
    query_id: str
    category: str
    query_text: str
    order: int


class V2DiscoveryHotspotItemResponse(BaseModel):
    note_id: Optional[str] = None
    title: str
    source_url: str = ""
    author: str = ""
    excerpt: str = ""
    likes: int = 0
    comments: int = 0
    collections: int = 0
    query_sources: List[str] = Field(default_factory=list)


class V2DiscoveryHotspotListResponse(BaseModel):
    metric: str
    items: List[V2DiscoveryHotspotItemResponse] = Field(default_factory=list)


class V2DiscoveryTaskResponse(BaseModel):
    task_id: str
    topic: str
    query_generation_version: str = "legacy"
    query_generation_source: str = "legacy"
    token: Optional[str] = None
    expires_at: Optional[str] = None
    expanded_queries: List[V2DiscoveryExpandedQueryResponse] = Field(default_factory=list)
    hotspot_status: str = "empty"
    hotspot_generated_at: Optional[str] = None
    hotspot_error_message: str = ""
    hotspots: List[V2DiscoveryHotspotListResponse] = Field(default_factory=list)


class V2TopicPoolRefreshRequest(BaseModel):
    archive_threshold_days: int = 60


class V2TopicPoolRefreshResponse(BaseModel):
    refresh_run_id: str
    status: str
    generated_item_count: int
    archived_item_count: int
    total_candidate_count: int
    refreshed_at: str


class V2TopicPoolBrandSummary(BaseModel):
    id: str
    name: str
    stage: str
    target_audience: Dict[str, Any] = Field(default_factory=dict)


class V2TopicPoolStatsResponse(BaseModel):
    total_candidate_count: int
    best_score: float
    last_refresh_at: Optional[str] = None


class V2TopicPoolItemResponse(BaseModel):
    id: str
    topic_id: str
    display_name: str
    normalized_name: str
    topic_type: str
    title: str
    angle: str
    hypothesis: str
    evidence_summary: Dict[str, Any] = Field(default_factory=dict)
    source_agent: str
    status: str
    final_score: float
    score_breakdown: Dict[str, Any] = Field(default_factory=dict)
    evidence_provenance: List[Dict[str, Any]] = Field(default_factory=list)
    is_demo: bool = False
    updated_at: str


class V2TopicPoolListResponse(BaseModel):
    brand: V2TopicPoolBrandSummary
    stats: V2TopicPoolStatsResponse
    items: List[V2TopicPoolItemResponse] = Field(default_factory=list)


class V2DecisionRunRequest(BaseModel):
    requested_slot_count: int = 3
    objective: str = "topic_recommendation"
    exploration_mode: str = "balanced"


class V2DecisionBatchItemResponse(BaseModel):
    slot_index: int
    topic_pool_item_id: str
    decision_event_id: Optional[str] = None
    title: str
    angle: str
    hypothesis: str
    score: float
    topic_type: str
    decision_mode: Optional[str] = None
    review_status: str
    reason_codes: List[str] = Field(default_factory=list)
    review_notes: Optional[str] = None
    reviewed_at: Optional[str] = None
    is_demo: bool = False


class V2DecisionRunResponse(BaseModel):
    batch_id: str
    workspace_id: str
    brand_id: str
    brand_state_snapshot_id: str
    brand_policy_config_id: str
    objective: str
    exploration_mode: str
    requested_slot_count: int
    candidate_count: int
    chosen_count: int
    created_at: str
    items: List[V2DecisionBatchItemResponse] = Field(default_factory=list)


class V2DecisionBatchDetailResponse(V2DecisionRunResponse):
    pass


class V2DecisionBatchItemReviewRequest(BaseModel):
    review_action: Literal["accept", "reject", "edit_and_accept"]
    edited_title: Optional[str] = None
    edited_angle: Optional[str] = None
    edited_hypothesis: Optional[str] = None
    review_notes: Optional[str] = None


class V2DecisionBatchItemReviewResponse(BaseModel):
    batch_id: str
    slot_index: int
    topic_pool_item_id: str
    decision_event_id: Optional[str] = None
    review_status: str
    title: str
    angle: str
    hypothesis: str
    score: float
    reason_codes: List[str] = Field(default_factory=list)
    review_notes: Optional[str] = None
    reviewed_at: Optional[str] = None


class V2PublishRecordCreateRequest(BaseModel):
    brand_id: str
    channel_id: str
    topic_pool_item_id: Optional[str] = None
    decision_event_id: Optional[str] = None
    decision_batch_id: Optional[str] = None
    publish_status: str
    published_at: Optional[datetime] = None
    content_item_id: Optional[str] = None
    creative_variant: Optional[str] = None


class V2PublishRecordResponse(BaseModel):
    publish_record_id: str
    brand_id: str
    channel_id: str
    channel_label: str
    title: str
    topic_pool_item_id: Optional[str] = None
    decision_event_id: Optional[str] = None
    decision_batch_id: Optional[str] = None
    decision_source: str
    publish_status: str
    published_at: Optional[str] = None
    creative_variant: Optional[str] = None
    is_demo: bool = False
    created_at: str


class V2PublishRecordListResponse(BaseModel):
    items: List[V2PublishRecordResponse] = Field(default_factory=list)


class V2PerformanceImportMetrics(BaseModel):
    impressions: int = 0
    clicks: int = 0
    likes: int = 0
    comments: int = 0
    collects: int = 0
    shares: int = 0
    follows_gained: int = 0
    conversion_proxy: Dict[str, Any] = Field(default_factory=dict)


class V2PerformanceImportRequest(BaseModel):
    publish_record_id: str
    observation_window_hours: int
    snapshot_at: datetime
    reward_version: str
    metrics: V2PerformanceImportMetrics


class V2PerformanceSnapshotResponse(BaseModel):
    performance_snapshot_id: str
    publish_record_id: str
    publish_title: str
    observation_window_hours: int
    snapshot_at: str
    reward_version: str
    impressions: int
    clicks: int
    engagement_rate: float
    conversion_proxy_label: str
    short_term_reward: float
    composite_reward: float
    is_demo: bool = False


class V2PerformanceSnapshotListResponse(BaseModel):
    items: List[V2PerformanceSnapshotResponse] = Field(default_factory=list)


class V2EvaluationRunRequest(BaseModel):
    brand_id: str
    evaluation_type: str = "replay"


class V2EvaluationRunSliceResponse(BaseModel):
    slice_key: str
    slice_value: str
    sample_count: int
    metrics: Dict[str, Any] = Field(default_factory=dict)


class V2EvaluationRunResponse(BaseModel):
    evaluation_run_id: str
    brand_id: str
    evaluation_type: str
    policy_name: str
    policy_version: str
    status: str
    sample_count: int
    summary: Dict[str, Any] = Field(default_factory=dict)
    slices: List[V2EvaluationRunSliceResponse] = Field(default_factory=list)
    created_at: str
    finished_at: Optional[str] = None

# ---------------------------------------------------------------------------
# Platform & consumer analysis
# ---------------------------------------------------------------------------

class UserInsight(BaseModel):
    """
    用户画像核心信号
      audience_fit      — LLM打分 (语义判断受众匹配)
      engagement_intent — 纯数据 (collected / liked 比值归一化)
      purchase_readiness — 纯数据 (pgy_url存在=1.0, 否则=0.0)
    """
    audience_fit: float = 0.0       # LLM  — 受众与平台主流用户的匹配度
    engagement_intent: float = 0.0  # 数据 — collected/liked 比值，反映主动收藏意愿
    purchase_readiness: float = 0.0 # 数据 — 是否蒲公英报备商业笔记 (pgy_url)


class ContentSignal(BaseModel):
    """
    内容偏好核心信号
      format_fit        — 纯数据 (card_type + 图片数量)
      topic_opportunity — 纯数据 (标签平均互动分位估算竞争密度)
      trend_alignment   — LLM打分 (话题是否在上升期)
    """
    format_fit: float = 0.0        # 数据 — 图文适配度 (card_type=normal + 图片数)
    topic_opportunity: float = 0.0 # 数据 — 蓝海程度：标签竞争密度的倒数
    trend_alignment: float = 0.0   # LLM  — 话题与当前上升趋势的关联度


class UserHiddenExpectation(BaseModel):
    """A single inferred hidden expectation from user behaviour patterns."""
    expectation: str              # what the user actually wants (not what they say)
    evidence: str                 # which notes / patterns support this inference
    strategy_implication: str     # how content strategy should respond to this


class PlatformReport(BaseModel):
    user_insight: UserInsight                          # 用户画像核心信号
    content_signal: ContentSignal                      # 内容偏好核心信号
    hidden_expectations: List[UserHiddenExpectation]   # users' real desires beneath surface inputs
    whitespace: List[str]                              # content gaps nobody is covering
    top_performing_styles: List[str]                   # formats / tones driving high engagement
    strategy_objectives: List[str]                     # concrete objectives for content generation
    insights: List[str]                                # actionable differentiated angles
    report: str                                        # full narrative summary


class PlatformAnalysisResponse(BaseModel):
    code: int
    msg: str
    data: Optional[PlatformReport] = None


class RAGDocument(BaseModel):
    doc_id: str
    session_id: str
    note_id: str
    title: str
    content: str
    tags: List[str] = Field(default_factory=list)
    embedding_vector: Optional[List[float]] = None
    engagement_score: float = 0.0


class V2DemoDatasetStateResponse(BaseModel):
    loaded: bool
    brand_id: Optional[str] = None
    loaded_at: Optional[str] = None
    dataset_version: str
    summary_counts: Optional[Dict[str, int]] = None
