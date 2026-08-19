"""API schemas for Content Research workflows."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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
    "retry_presearch",
    "clarify_subject",
    "confirm_subject_structure",
    "repair_from_persisted_packets",
    "prepare_scope",
    "confirm_scope",
    "resolve_coverage",
)


class ContentResearchPresearchRequest(BaseModel):
    seed_text: str = Field(min_length=1)
    user_note: str | None = None
    thread_id: str = Field(min_length=1)


class ContentResearchLLMConfigurationRequest(BaseModel):
    base_url: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key: str | None = None


class ContentResearchLLMConfigurationResponse(BaseModel):
    source: str
    status: str
    base_url: str
    model: str
    api_key_configured: bool
    api_key_suffix: str | None = None
    validated_at: datetime | None = None
    error_code: str | None = None


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
    direction_catalog: list[str]
    custom_research_question: str
    custom_competitor_input: str = ""
    timeout_status: str
    fallback_used: bool
    execution_mode: str = "local"
    remote_run_id: str | None = None
    local_cache_id: str | None = None
    sync_status: str = "local_only"
    error_code: str | None = None
    error_message: str | None = None
    recoverable: bool = False
    configuration_source: str | None = None
    model: str | None = None
    subject_structure: dict = Field(default_factory=dict)
    subject_structure_hash: str | None = None
    subject_structure_state: str = "needs_confirmation"
    subject_structure_reason_codes: list[str] = Field(default_factory=list)
    subject_structure_user_confirmed_fields: list[str] = Field(default_factory=list)


class ContentResearchSubjectStructureConfirmationInput(BaseModel):
    core_object: str = Field(min_length=1, max_length=200)
    research_intent: str = Field(min_length=1, max_length=200)
    context_modifiers: str | list[str] = Field(default_factory=list)


class ContentResearchBriefConfirmRequest(BaseModel):
    confirmed_subject: str = Field(min_length=1)
    subject_structure_hash: str | None = Field(default=None, min_length=1)
    subject_type: str = "unknown"
    selected_competitors: list[str] = Field(default_factory=list)
    custom_competitors: list[str] = Field(default_factory=list)
    selected_directions: list[str] = Field(
        min_length=1,
        description="Non-empty requested subset of the Lite direction catalog.",
    )
    custom_research_question: str = ""
    primary_marketing_goal: str = Field(min_length=1)
    subject_structure_confirmation: ContentResearchSubjectStructureConfirmationInput | None = None


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


class ContentResearchScopeProjectionResponse(BaseModel):
    """Read-only durable projection of a workflow's Scope authority."""

    schema_version: str = CONTENT_RESEARCH_API_SCHEMA_VERSION
    workflow_run_id: str
    state: Literal["awaiting_confirmation", "confirmed", "superseded"]
    draft: dict
    scope_contract: dict | None = None
    audit_events: list[dict] = Field(default_factory=list)
    allowed_actions: list[dict] = Field(default_factory=list)
    coverage_snapshot: dict | None = None
    allowed_resolutions: list[dict] = Field(default_factory=list)


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
    provider_operations: list[dict] = Field(default_factory=list)
    logical_checkpoints: list[dict] = Field(default_factory=list)
    usage_steps: list[dict] = Field(default_factory=list)
    usage_events: list[dict] = Field(default_factory=list)
    llm_recovery: dict = Field(default_factory=dict)


class ContentResearchSourceCollectionRequest(BaseModel):
    operation: str = "discover_candidates"
    query: str | None = None
    note_id: str | None = None
    note_url: str | None = None
    cursor: str | None = None
    top_level_only: bool = True
    required_fields: list[str] = Field(default_factory=list)
    source_kind: str = "search_result"
    # The server owns collection breadth. Clients may request a lower bounded
    # value for API use, but ordinary Creator runs use the full safe ceiling.
    limit: int = Field(default=50, ge=1, le=50)
    sort: str = "likes"
    provider: str = "xiaohongshu"


class ContentResearchSourceCollectionResponse(BaseModel):
    schema_version: str = CONTENT_RESEARCH_API_SCHEMA_VERSION
    execution_authority: Literal["diagnostic_only"] = "diagnostic_only"
    workflow_run_id: str
    provider: str
    source_kind: str
    operation: str = "discover_candidates"
    status: str
    failure_reason: str | None = None
    cookie_status: str = "unknown"
    items: list[dict] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    next_cursor: str | None = None
    completeness: str = "complete"
    field_availability: dict[str, str] = Field(default_factory=dict)
    retryable: bool = False


class XHSQRLoginResponse(BaseModel):
    attempt_id: str
    status: str
    qr_image_data_url: str | None = None
    failure_code: str | None = None


class XHSLoginStatusResponse(BaseModel):
    authenticated: bool
    source: str | None = None
    updated_at: str | None = None
    failure_code: str | None = None


class XHSManualCookieRequest(BaseModel):
    cookie: str = Field(min_length=1, max_length=16384)


class ContentResearchDirectionEvidenceResponse(BaseModel):
    schema_version: str = CONTENT_RESEARCH_API_SCHEMA_VERSION
    workflow_run_id: str
    direction_id: str
    status: str = "not_started"
    counts: dict[str, int] = Field(default_factory=dict)
    query_plan_hash: str | None = None
    candidate_manifest_hash: str | None = None
    query_groups: list[dict] = Field(default_factory=list)
    selection_policy: dict = Field(default_factory=dict)
    coverage_unmet_query_group_ids: list[str] = Field(default_factory=list)
    selection_revisions: list[dict] = Field(default_factory=list)
    comment_collection: dict = Field(default_factory=dict)
    candidates: list[dict] = Field(default_factory=list)
    selections: list[dict] = Field(default_factory=list)
    exclusions: list[dict] = Field(default_factory=list)
    packets: list[dict] = Field(default_factory=list)
    direction_result: dict = Field(default_factory=dict)
    weak_signals: list[dict] = Field(default_factory=list)
    offset: int = 0
    limit: int = 50


class ContentResearchGovernanceResponse(BaseModel):
    schema_version: str = CONTENT_RESEARCH_API_SCHEMA_VERSION
    workflow_run_id: str
    research_plan_id: str
    governed_snapshot_identity: dict = Field(default_factory=dict)
    cross_direction_records: list[dict] = Field(default_factory=list)
    aggregate_claims: list[dict] = Field(default_factory=list)
    cross_direction_total: int = 0
    aggregate_total: int = 0
    offset: int = 0
    limit: int = 50


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


class PrepareScopeRequest(BaseModel):
    direction_id: str = Field(min_length=1)


class ScopeFinalQueryEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    final_query: str = Field(min_length=1)


class ConfirmScopeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    scope_draft_id: str = Field(min_length=1)
    structure_hash: str = Field(min_length=1)
    query_groups: list[ScopeFinalQueryEdit] = Field(min_length=1, max_length=3)


class ResolveCoverageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    scope_contract_version: int = Field(ge=1)
    coverage_snapshot_id: str = Field(min_length=1)
    resolution: Literal[
        "expand_required_constraint",
        "generate_limited_report",
        "relax_constraint",
    ]
    constraint_id: str | None = Field(default=None, min_length=1)
    supplementary_queries: list[str] = Field(default_factory=list, max_length=2)


class ScopeExecutionAuthorizationResponse(BaseModel):
    id: str
    execution_unit_id: str | None = None
    workflow_run_id: str
    scope_contract_id: str
    scope_contract_version: int
    coverage_snapshot_id: str
    resolution: Literal[
        "expand_required_constraint",
        "generate_limited_report",
        "relax_constraint",
    ]
    execution_revision: int
    state: Literal["authorized_collection", "authorized_limited_report"]
    created_at: datetime


class ContentResearchSubjectClarificationRequest(BaseModel):
    clarification_text: str = Field(min_length=1, max_length=2000)


class ContentResearchSubjectStructureConfirmationRequest(BaseModel):
    subject_structure_hash: str = Field(min_length=1)
    core_object: str = Field(min_length=1, max_length=200)
    research_intent: str = Field(min_length=1, max_length=200)
    context_modifiers: str | list[str] = Field(default_factory=list)


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
    limitations: list[dict] = Field(default_factory=list)
    governed_snapshot: dict = Field(default_factory=dict)
    created_at: str


class ContentResearchLiteSectionsResponse(BaseModel):
    main_findings: list[dict] = Field(default_factory=list)
    weak_signals: list[dict] = Field(default_factory=list)
    limitations_scope: list[dict] = Field(default_factory=list)
    marketing_conclusions: dict[str, dict] = Field(default_factory=dict)
    priority_action: dict | None = None


class ContentResearchLiteReportResponse(BaseModel):
    """Stable narrow projection of the formal F003 report contract."""

    schema_version: str = CONTENT_RESEARCH_API_SCHEMA_VERSION
    workflow_run_id: str
    workflow_execution_state: str
    subject: str | None = None
    frozen_scope: dict = Field(default_factory=dict)
    collected_at: str | None = None
    publication: dict = Field(default_factory=dict)
    sections: ContentResearchLiteSectionsResponse = Field(
        default_factory=ContentResearchLiteSectionsResponse
    )
    status_strip: dict = Field(default_factory=dict)
    citations: list[dict] = Field(default_factory=list)
    run_direction_states: list[dict] = Field(default_factory=list)
    recovery_projection: dict | None = None


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
