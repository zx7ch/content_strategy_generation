"""API schemas for Content Research workflows."""

from __future__ import annotations

from pydantic import BaseModel, Field

CONTENT_RESEARCH_API_SCHEMA_VERSION = "content_research_api_v1"
WORKFLOW_ACTION_REQUEST_SCHEMA_VERSION = "content_research_workflow_action_request_v1"
WORKFLOW_ACTION_RESPONSE_SCHEMA_VERSION = "content_research_workflow_action_response_v1"
P0_WORKFLOW_ACTIONS = (
    "confirm_brief",
    "start_formal_research",
    "retry_formal_research",
    "pause_formal_research",
    "resume_formal_research",
    "end_content_research",
)


class ContentResearchPresearchRequest(BaseModel):
    seed_text: str = Field(min_length=1)
    user_note: str | None = None
    thread_id: str = Field(min_length=1)


class ContentResearchChecklistResponse(BaseModel):
    subject_confirmation: str
    competitor_tags: list[str]
    research_directions: list[str]
    custom_research_question: str = ""
    custom_competitor_input: str = ""


class ContentResearchPresearchResponse(BaseModel):
    schema_version: str = CONTENT_RESEARCH_API_SCHEMA_VERSION
    attempt_id: str
    workflow_run_id: str
    brief_id: str
    status: str
    subject_confirmation: str
    competitor_tags: list[str]
    research_directions: list[str]
    custom_research_question: str
    custom_competitor_input: str = ""
    timeout_status: str
    fallback_used: bool
    execution_mode: str = "local"
    remote_run_id: str | None = None
    local_cache_id: str | None = None
    sync_status: str = "local_only"


class ContentResearchBriefConfirmRequest(BaseModel):
    confirmed_subject: str = Field(min_length=1)
    subject_type: str = "unknown"
    selected_competitors: list[str] = Field(default_factory=list)
    custom_competitors: list[str] = Field(default_factory=list)
    selected_directions: list[str] = Field(min_length=1)
    custom_research_question: str = ""


class ContentResearchDirectionResponse(BaseModel):
    id: str
    name: str
    direction_type: str
    priority: int
    status: str
    payload: dict


class ContentResearchSubagentTaskResponse(BaseModel):
    id: str
    plan_id: str | None
    direction_id: str | None
    status: str
    payload: dict


class ContentResearchPlanResponse(BaseModel):
    id: str
    brief_id: str
    workflow_run_id: str
    status: str
    payload: dict


class ContentResearchBriefResponse(BaseModel):
    id: str
    workflow_run_id: str
    thread_id: str
    status: str
    payload: dict


class ContentResearchWorkflowSummaryResponse(BaseModel):
    schema_version: str = CONTENT_RESEARCH_API_SCHEMA_VERSION
    workflow_run_id: str
    brief: ContentResearchBriefResponse
    plan: ContentResearchPlanResponse | None = None
    directions: list[ContentResearchDirectionResponse] = Field(default_factory=list)
    subagent_tasks: list[ContentResearchSubagentTaskResponse] = Field(default_factory=list)
    runtime_run: dict | None = None
    runtime_steps: list[dict] = Field(default_factory=list)
    runtime_child_tasks: list[dict] = Field(default_factory=list)
    execution_mode: str = "local"
    remote_run_id: str | None = None
    local_cache_id: str | None = None
    sync_status: str = "local_only"


class ContentResearchWorkflowEventsResponse(BaseModel):
    schema_version: str = CONTENT_RESEARCH_API_SCHEMA_VERSION
    workflow_run_id: str
    events: list[dict] = Field(default_factory=list)


class ContentResearchTraceResponse(BaseModel):
    schema_version: str = CONTENT_RESEARCH_API_SCHEMA_VERSION
    workflow_run_id: str
    thread_id: str | None = None
    current_stage: str | None = None
    run_status: str | None = None
    recoverable: bool = True
    duration_ms: int = 0
    error_count: int = 0
    retry_count: int = 0
    traces: list[dict] = Field(default_factory=list)
    observation_events: list[dict] = Field(default_factory=list)
    workflow_events: list[dict] = Field(default_factory=list)
    runtime_steps: list[dict] = Field(default_factory=list)
    runtime_child_tasks: list[dict] = Field(default_factory=list)
    usage_summary: dict = Field(default_factory=dict)
    external_api_summary: dict = Field(default_factory=dict)
    usage_steps: list[dict] = Field(default_factory=list)
    usage_events: list[dict] = Field(default_factory=list)


class ContentResearchSourceCollectionRequest(BaseModel):
    query: str | None = None
    source_kind: str = "search_result"
    # The server owns collection breadth. Clients may request a lower bounded
    # value for API use, but ordinary Creator runs use the full safe ceiling.
    limit: int = Field(default=50, ge=1, le=50)
    sort: str = "likes"
    provider: str = "xiaohongshu"


class ContentResearchSourceCollectionResponse(BaseModel):
    schema_version: str = CONTENT_RESEARCH_API_SCHEMA_VERSION
    workflow_run_id: str
    provider: str
    source_kind: str
    status: str
    failure_reason: str | None = None
    cookie_status: str = "unknown"
    items: list[dict] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class ContentResearchFormalResearchResponse(BaseModel):
    """Result of dispatching the independent specialist research tasks.

    This deliberately contains no shared source items: every specialist owns
    its query and acquisition result.
    """

    schema_version: str = CONTENT_RESEARCH_API_SCHEMA_VERSION
    workflow_run_id: str
    status: str
    task_count: int
    completed_task_count: int
    partial_completed_task_count: int
    failed_tasks: list[dict] = Field(default_factory=list)
    provider: str
    source_kind: str
    limit_per_specialist: int


class ContentResearchWorkflowActionRequest(BaseModel):
    schema_version: str = WORKFLOW_ACTION_REQUEST_SCHEMA_VERSION
    action: str = Field(min_length=1)
    payload: dict = Field(default_factory=dict)


class ContentResearchWorkflowActionResponse(BaseModel):
    schema_version: str = WORKFLOW_ACTION_RESPONSE_SCHEMA_VERSION
    workflow_run_id: str
    action: str
    status: str
    result: dict
    execution_mode: str = "local"
    remote_run_id: str | None = None
    local_cache_id: str | None = None
    sync_status: str = "local_only"


class ResultItem(BaseModel):
    result_item_id: str
    claim: str
    summary: str
    evidence_bundle_id: str
    evidence_bundle_ids: list[str] = Field(default_factory=list)
    support_level: str
    claim_status: str
    priority: dict = Field(default_factory=dict)
    priority_label: str = "do_not_prioritize"
    evidence_state: str = "signal"
    evidence_grade: str = "C"
    claim_scope: dict = Field(default_factory=dict)
    next_action: dict = Field(default_factory=dict)
    decision_card: dict = Field(default_factory=dict)
    risk_flags: list[str] = Field(default_factory=list)
    missing_evidence: list[dict] = Field(default_factory=list)
    analysis_trace: dict = Field(default_factory=dict)
    source_count: int = 0


class SnapshotResponse(BaseModel):
    schema_version: str = CONTENT_RESEARCH_API_SCHEMA_VERSION
    snapshot_id: str
    workflow_run_id: str
    research_brief_id: str | None = None
    research_plan_id: str | None = None
    snapshot_version: str
    result_type: str
    status: str
    title: str
    executive_summary: str
    items: list[ResultItem] = Field(default_factory=list)
    findings: list[dict] = Field(default_factory=list)
    recommendations: list[dict] = Field(default_factory=list)
    evidence_bundle_ids: list[str] = Field(default_factory=list)
    claim_count: int = 0
    supported_claim_count: int = 0
    unsupported_claim_count: int = 0
    citation_coverage_score: float | None = None
    faithfulness_score: float | None = None
    answer_relevancy_score: float | None = None
    derivation_completeness_score: float | None = None
    evidence_boundary_calibration_score: float | None = None
    decision_summary: dict = Field(default_factory=dict)
    decision_cards: list[dict] = Field(default_factory=list)
    priority_summary: dict = Field(default_factory=dict)
    evidence_boundary_summary: dict = Field(default_factory=dict)
    limitations: list[dict] = Field(default_factory=list)
    abstentions: list[dict] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    created_at: str


class EvidenceBundleView(BaseModel):
    schema_version: str = CONTENT_RESEARCH_API_SCHEMA_VERSION
    bundle_id: str
    workflow_run_id: str
    research_brief_id: str | None = None
    research_plan_id: str | None = None
    research_direction_id: str | None = None
    status: str
    bundle_type: str
    bundle_version: str
    summary: str
    coverage: dict = Field(default_factory=dict)
    retrieval_metrics: dict = Field(default_factory=dict)
    faithfulness_metrics: dict = Field(default_factory=dict)
    cross_source_metrics: dict = Field(default_factory=dict)
    contradiction_summary: dict = Field(default_factory=dict)
    citation_coverage: dict = Field(default_factory=dict)
    unsupported_claim_count: int = 0
    missing_evidence: list[dict] = Field(default_factory=list)
    priority_policy_id: str | None = None
    evidence_boundary_policy_id: str | None = None
    decision_card: dict = Field(default_factory=dict)
    priority: dict = Field(default_factory=dict)
    evidence_state: str = "signal"
    evidence_grade: str = "C"
    claim_scope: dict = Field(default_factory=dict)
    next_action: dict = Field(default_factory=dict)
    items: list[dict] = Field(default_factory=list)
    evidence_by_role: dict[str, list[dict]] = Field(default_factory=dict)
    lineage_by_evidence_id: dict[str, list[dict]] = Field(default_factory=dict)
    source_links: list[dict] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    created_at: str
    updated_at: str


class HumanDecisionRequest(BaseModel):
    target_id: str = Field(min_length=1)
    decision_request_id: str = Field(min_length=1)
    decision_status: str = Field(min_length=1)
    decision_payload: dict = Field(default_factory=dict)
    rationale: str = ""
    created_by_type: str = "user"
    created_by_id: str | None = None
    research_result_snapshot_id: str | None = None
    metadata: dict = Field(default_factory=dict)


class HumanDecisionResponse(BaseModel):
    schema_version: str = CONTENT_RESEARCH_API_SCHEMA_VERSION
    decision_id: str
    workflow_run_id: str
    target_type: str
    target_id: str
    decision_request_id: str
    decision_status: str
    decision_payload: dict = Field(default_factory=dict)
    rationale: str = ""
    created_by_type: str = "user"
    created_by_id: str | None = None
    research_brief_id: str | None = None
    research_plan_id: str | None = None
    research_result_snapshot_id: str | None = None
    metadata: dict = Field(default_factory=dict)
    advancement: dict = Field(default_factory=dict)
    is_current: bool
    idempotent_replay: bool = False
    history_count: int = 0
    created_at: str


class HumanDecisionsResponse(BaseModel):
    schema_version: str = CONTENT_RESEARCH_API_SCHEMA_VERSION
    workflow_run_id: str
    decisions: list[HumanDecisionResponse] = Field(default_factory=list)
    current_decisions: list[HumanDecisionResponse] = Field(default_factory=list)
