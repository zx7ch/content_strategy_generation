import { getRuntimeAuthorizationHeader, getWorkspaceContext, RUNTIME_BASE_URL } from "./api.ts";

type JsonObject = Record<string, unknown>;

export class ContentResearchApiError extends Error {
  readonly status: number;
  readonly code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ContentResearchApiError";
    this.status = status;
    this.code = code;
  }
}

export function isContentResearchReportPending(error: unknown): boolean {
  return error instanceof Error
    && (
      (error instanceof ContentResearchApiError && error.status === 404)
      || /published report artifact is missing|published report not found|\b404\b/i.test(error.message)
    );
}

export interface ContentResearchPresearchRequest {
  command_id: string;
  seed_text: string;
  user_note?: string | null;
  thread_id: string;
}

export type ContentResearchLifecycleState =
  | "presearch_running"
  | "brief_confirmation_required"
  | "scope_confirmation_required"
  | "retrieval_queued"
  | "retrieval_running"
  | "coverage_evaluating"
  | "coverage_decision_required"
  | "report_composing"
  | "report_ready"
  | "recovery_required"
  | "cancelled_or_failed";

export interface ContentResearchRunProjection {
  run_id: string;
  thread_id: string;
  state: ContentResearchLifecycleState;
  state_revision: number;
  entered_at: string;
  allowed_actions: string[];
  reason_code?: string | null;
  error?: JsonObject | null;
  brief_id?: string | null;
  scope_contract_id?: string | null;
  execution_attempt_id?: string | null;
  coverage_snapshot_id?: string | null;
  publication_id?: string | null;
}

export interface ContentResearchPresearchResponse {
  attempt_id: string;
  workflow_run_id: string;
  brief_id: string;
  status: string;
  subject_confirmation: string;
  competitor_tags: string[];
  research_directions: string[];
  direction_catalog: string[];
  custom_competitor_input?: string;
  timeout_status: string;
  fallback_used: boolean;
  error_code?: string | null;
  error_message?: string | null;
  recoverable?: boolean;
  configuration_source?: string | null;
  model?: string | null;
  subject_structure: {
    canonical_subject?: string;
    core_entities?: Array<{ canonical_name?: string; raw_mentions?: string[] }>;
    research_intents?: string[];
    context_modifiers?: string[];
    [key: string]: unknown;
  };
  subject_structure_hash?: string | null;
  subject_structure_analysis_state: string;
  subject_structure_analysis_reason_codes: string[];
  run: ContentResearchRunProjection;
}

export interface LLMConfigurationInput { base_url: string; model: string; api_key?: string | null; }
export interface LLMConfiguration { source: string; status: string; base_url: string; model: string; api_key_configured: boolean; api_key_suffix?: string | null; validated_at?: string | null; error_code?: string | null; }

export interface ContentResearchWorkflowSummary {
  workflow_run_id: string;
  run: ContentResearchRunProjection;
  brief?: {
    id: string;
    workflow_run_id: string;
    thread_id: string;
    status: string;
    payload: JsonObject;
  } | null;
  plan?: {
    id: string;
    brief_id: string;
    workflow_run_id: string;
    status: string;
    payload: JsonObject;
  } | null;
  directions: Array<{
    id: string;
    name: string;
    direction_type: string;
    priority: number;
    status: string;
    payload: JsonObject;
  }>;
  subagent_tasks: Array<{
    id: string;
    plan_id?: string | null;
    direction_id?: string | null;
    status: string;
    payload: JsonObject;
  }>;
  runtime_run?: JsonObject | null;
  runtime_steps: JsonObject[];
  runtime_child_tasks: JsonObject[];
}

export interface ContentResearchHistoricalWorkflowSummary {
  schema_version: string;
  workflow_run_id: string;
  historical_read_only: true;
  historical_run: {
    run_id: string;
    thread_id: string;
    status: string;
    read_only: true;
    mutation_authority?: null;
    [key: string]: unknown;
  };
  brief: NonNullable<ContentResearchWorkflowSummary["brief"]>;
  plan?: ContentResearchWorkflowSummary["plan"];
  directions: ContentResearchWorkflowSummary["directions"];
  subagent_tasks: ContentResearchWorkflowSummary["subagent_tasks"];
  runtime_run?: JsonObject | null;
  runtime_steps: JsonObject[];
  runtime_child_tasks: JsonObject[];
}

export type ContentResearchWorkflowReadResponse =
  | ContentResearchWorkflowSummary
  | ContentResearchHistoricalWorkflowSummary;

export function isHistoricalContentResearchWorkflow(
  workflow: ContentResearchWorkflowReadResponse,
): workflow is ContentResearchHistoricalWorkflowSummary {
  return "historical_read_only" in workflow && workflow.historical_read_only === true;
}

export function contentResearchWorkflowThreadId(
  workflow: ContentResearchWorkflowReadResponse,
): string {
  return isHistoricalContentResearchWorkflow(workflow)
    ? workflow.historical_run.thread_id
    : workflow.run.thread_id;
}

export function requireMutableContentResearchWorkflow(
  workflow: ContentResearchWorkflowReadResponse,
): ContentResearchWorkflowSummary {
  if (isHistoricalContentResearchWorkflow(workflow)) {
    throw new ContentResearchApiError("Historical content research workflow is read-only", 409);
  }
  return workflow;
}

export interface ContentResearchTrace {
  schema_version: string;
  workflow_run_id: string;
  trace_revision: number;
  effective_attempt?: {
    kind: "analysis";
    attempt_no: number;
    state: string;
  } | null;
  state?: ContentResearchLifecycleState | null;
  state_revision?: number | null;
  state_transitions?: JsonObject[];
  thread_id?: string | null;
  current_stage?: string | null;
  run_status?: string | null;
  recoverable: boolean;
  duration_ms: number;
  error_count: number;
  retry_count: number;
  traces: JsonObject[];
  observation_events: JsonObject[];
  workflow_events: JsonObject[];
  runtime_steps: JsonObject[];
  runtime_child_tasks: JsonObject[];
  execution_units: Array<{
    id: string;
    state: string;
    recovery_state: "replayable" | "outcome_unknown" | "manual_recovery_required";
    identity_schema: string;
    identity_state: "canonical" | "legacy_identity_incomplete";
    identity_json: JsonObject;
    facts: Array<{
      attempt_no: number;
      sequence_no: number;
      kind: string;
      payload: JsonObject;
    }>;
  }>;
  usage_summary: JsonObject;
  external_api_summary: JsonObject;
  provider_operations: Array<{
    operation_fingerprint: string;
    operation?: string | null;
    provider?: string | null;
    provider_operation?: string | null;
    source_kind?: string | null;
    result_status?: string | null;
    item_count?: number | null;
    completeness?: string | null;
    status: string;
    started_at?: string | null;
    finished_at?: string | null;
    failure_code?: string | null;
    failure_reason?: string | null;
    retryable: boolean;
    recovery_action?: string | null;
  }>;
  logical_checkpoints: Array<JsonObject | ContentResearchMarketingConclusionTraceCheckpoint>;
  usage_steps: JsonObject[];
  usage_events: JsonObject[];
  llm_recovery?: {
    required?: boolean;
    required_since?: string | null;
    error_code?: string | null;
    configuration_source?: string | null;
    model?: string | null;
  };
}

export interface ContentResearchMarketingConclusionTraceTrack {
  state: "selected" | "contested" | "directional" | "qualified" | "insufficient_evidence" | "no_single_primary_conclusion" | "analysis_unavailable";
  supporting_note_count?: number;
  independent_author_count?: number;
  counter_note_count?: number;
  counter_author_count?: number;
  verifier_state?: "verified" | "contested" | "rejected";
  reason_codes?: string[];
  execution?: "completed" | "failed";
  decision?: string;
  publication_role?: string;
  publication_disposition?: {
    state: "published" | "withheld_by_faithfulness" | "omitted_by_publication_policy";
    reason_code?: "faithfulness_not_verified";
  };
  failure_code?: string;
  failure_detail?: string;
  recovery_action?: "repair_model_configuration_and_resume";
}

export interface ContentResearchMarketingConclusionTraceCheckpoint {
  stage: "marketing_conclusion";
  status: string;
  tracks?: Partial<Record<"need" | "value" | "message", ContentResearchMarketingConclusionTraceTrack>>;
  reason_codes?: string[];
  recovery_action?: "repair_model_configuration_and_resume";
  replayed_from_persisted_packets?: true;
  provider_operation_count_delta?: number;
  packet_count_delta?: number;
}

export interface ContentResearchWorkflowActionRequest {
  schema_version?: string;
  command_id: string;
  expected_state: ContentResearchLifecycleState;
  expected_revision: number;
  action:
    | "cancel"
    | "retry_presearch"
    | "retry_retrieval"
    | "retry_analysis"
    | "retry_report"
    | "repair_publication"
    | "revise_subject"
    | "confirm_brief"
    | "replace_scope_draft"
    | "confirm_scope";
  payload?: JsonObject;
}

export function createContentResearchCommandId(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") return globalThis.crypto.randomUUID();
  return `cmd_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

const projectedCommandIds = new Map<string, string>();

export function contentResearchCommand(
  run: ContentResearchRunProjection,
  action: ContentResearchWorkflowActionRequest["action"],
  payload: JsonObject = {},
): ContentResearchWorkflowActionRequest {
  const identity = JSON.stringify([
    run.run_id,
    run.state,
    run.state_revision,
    action,
    payload,
  ]);
  const commandId = projectedCommandIds.get(identity) ?? createContentResearchCommandId();
  if (!projectedCommandIds.has(identity) && projectedCommandIds.size >= 256) {
    const oldest = projectedCommandIds.keys().next().value;
    if (oldest) projectedCommandIds.delete(oldest);
  }
  projectedCommandIds.set(identity, commandId);
  return {
    command_id: commandId,
    expected_state: run.state,
    expected_revision: run.state_revision,
    action,
    payload,
  };
}

export interface ContentResearchWorkflowActionResponse<T = JsonObject> {
  schema_version: string;
  workflow_run_id: string;
  action: string;
  status: string;
  result: T;
  execution_mode: string;
  remote_run_id?: string | null;
  local_cache_id?: string | null;
  sync_status: string;
}

export interface ContentResearchScopeConstraint {
  id: string;
  label: string;
  value: string;
  mode: "required" | "preferred";
  allowed_aliases: string[];
}

export interface ContentResearchScopeDraftQueryGroup {
  suggested_query: string;
  final_query: string;
  targeted_required_terms: string[];
  origin?: "system_suggested" | "user_edited" | null;
}

export interface ContentResearchScopeDraft {
  schema_version: string;
  id: string;
  workflow_run_id: string;
  research_plan_id: string;
  structure_hash: string;
  core_object: string;
  product_experience_aspect?: string | null;
  context_audience_aspect?: string | null;
  constraints: ContentResearchScopeConstraint[];
  query_groups: ContentResearchScopeDraftQueryGroup[];
  created_at: string;
}

export type ContentResearchScopeExecutionRole = "coverage" | "supplementary" | "exploratory";

export interface ContentResearchScopeContractQueryGroup {
  id: string;
  suggested_query: string;
  final_query: string;
  origin: "system_suggested" | "user_edited";
  execution_role: ContentResearchScopeExecutionRole;
}

export interface ContentResearchScopeContract {
  id: string;
  workflow_run_id: string;
  research_plan_id: string;
  version: number;
  schema_version: string;
  constraints: ContentResearchScopeConstraint[];
  query_groups: ContentResearchScopeContractQueryGroup[];
  created_at: string;
}

export interface ContentResearchScopeAuditEvent {
  id: string;
  workflow_run_id: string;
  scope_draft_id?: string;
  scope_contract_id?: string;
  scope_contract_version?: number;
  event_name: string;
  payload: JsonObject;
  created_at: string;
}

export interface ContentResearchScopeProjection {
  schema_version: string;
  workflow_run_id: string;
  state: ContentResearchLifecycleState;
  state_revision: number;
  run: ContentResearchRunProjection;
  draft: ContentResearchScopeDraft;
  scope_contract: ContentResearchScopeContract | null;
  audit_events: ContentResearchScopeAuditEvent[];
  allowed_actions: Array<JsonObject & { action: string; available: boolean }>;
  coverage_snapshot: (JsonObject & {
    id: string;
    state: string;
    unmet_constraint_ids: string[];
    constraint_counts: JsonObject;
  }) | null;
  allowed_resolutions: Array<JsonObject & {
    action: ContentResearchCoverageResolution;
    available: boolean;
    valid_constraint_ids: string[];
    supplementary_queries_required: boolean;
    unavailable_reason?: string | null;
  }>;
  decision_recovery: (JsonObject & {
    state: string;
    message: string;
    required_action: string;
    allowed_resolutions: ContentResearchCoverageResolution[];
  }) | null;
  execution_unit: ContentResearchExecutionUnitProjection | null;
  subject_structure_analysis_state: string;
  subject_structure_analysis_reason_codes: string[];
}

export interface ContentResearchExecutionUnitProjection {
  id: string;
  state: string;
  attempt_no: number;
  recovery_state: "replayable" | "outcome_unknown" | "manual_recovery_required";
  allowed_actions: Array<JsonObject & { action: string; available: boolean }>;
  trace_summary: {
    fact_count: number;
    attempt_count: number;
    last_fact_kind?: string | null;
  };
}

type UnsafeContentResearchExecutionUnitProjection = ContentResearchExecutionUnitProjection & {
  lease_token?: unknown;
  lease_owner?: unknown;
  lease_expires_at?: unknown;
};

function safeContentResearchExecutionUnit(
  executionUnit: UnsafeContentResearchExecutionUnitProjection,
): ContentResearchExecutionUnitProjection {
  const {
    lease_token: _leaseToken,
    lease_owner: _leaseOwner,
    lease_expires_at: _leaseExpiresAt,
    ...safeExecutionUnit
  } = executionUnit;
  return safeExecutionUnit;
}

export interface ContentResearchBriefConfirmationInput {
  brief_id: string;
  selected_competitors: string[];
  custom_competitor_input: string;
  selected_directions: string[];
}

export interface ContentResearchScopeDraftReplacementInput {
  scope_draft_id: string;
  core_object: string;
  product_experience_aspect?: string | null;
  context_audience_aspect?: string | null;
}

export interface ContentResearchScopeDraftActionResult {
  run: ContentResearchRunProjection;
  scope: ContentResearchScopeProjection;
}

export interface ContentResearchScopeConfirmationActionResult {
  run: ContentResearchRunProjection;
  scope: ContentResearchScopeProjection;
}

export type ContentResearchCoverageResolution =
  | "expand_required_constraint"
  | "generate_limited_report"
  | "relax_constraint";

export interface ContentResearchResolveCoverageRequest {
  scope_contract_version: number;
  coverage_snapshot_id: string;
  resolution: ContentResearchCoverageResolution;
  constraint_id?: string;
  supplementary_queries?: string[];
}

export interface ContentResearchConfirmScopeResult {
  scope_contract: ContentResearchScopeContract;
  audit_event: ContentResearchScopeAuditEvent;
}

export interface ContentResearchResolveCoverageResult {
  report_mode: string;
  scope_contract: ContentResearchScopeContract;
  unmet_constraint_ids: string[];
  audit_event: ContentResearchScopeAuditEvent;
  execution_unit: ContentResearchExecutionUnitProjection;
}

export interface XHSQRLoginResponse {
  attempt_id: string;
  status: "pending" | "authenticated" | "expired" | "failed";
  qr_image_data_url?: string | null;
  failure_code?: string | null;
}

export interface XHSLoginStatus {
  authenticated: boolean;
  source?: "qr" | "manual_cookie" | null;
  updated_at?: string | null;
  failure_code?: string | null;
}

export async function getXHSLoginStatus(): Promise<XHSLoginStatus> {
  return contentResearchFetch("/content-research/providers/xiaohongshu/login");
}

export async function saveXHSManualCookie(cookie: string): Promise<XHSLoginStatus> {
  return contentResearchFetch("/content-research/providers/xiaohongshu/login", { method: "PUT", body: { cookie } });
}

export async function clearXHSLogin(): Promise<XHSLoginStatus> {
  return contentResearchFetch("/content-research/providers/xiaohongshu/login", { method: "DELETE" });
}

export async function startXHSQRLogin(): Promise<XHSQRLoginResponse> {
  return contentResearchFetch("/content-research/providers/xiaohongshu/login/qr", { method: "POST" });
}

export async function getCurrentXHSQRLogin(): Promise<XHSQRLoginResponse> {
  return contentResearchFetch("/content-research/providers/xiaohongshu/login/qr");
}

export async function getXHSQRLogin(attemptId: string): Promise<XHSQRLoginResponse> {
  return contentResearchFetch(`/content-research/providers/xiaohongshu/login/qr/${encodeURIComponent(attemptId)}`);
}

/** Narrow public projection from the immutable report publication. */
export type ContentResearchMarketingConclusionTrack =
  | {
      state: "selected";
      conclusion_id: string;
      statement: string;
      citation_group_ids: string[];
      supporting_note_count: number;
      independent_author_count: number;
      additional_qualified_count: number;
    }
  | {
      state: "directional";
      conclusion_id: string;
      statement: string;
      citation_group_ids: string[];
      supporting_note_count: number;
      independent_author_count: number;
      note_gap: number;
      author_gap: number;
      reason_codes: string[];
      verification_direction: string;
    }
  | {
      state: "contested";
      conclusion_id: string;
      statement: string;
      citation_group_ids: string[];
      counter_citation_group_ids: string[];
      supporting_note_count: number;
      independent_author_count: number;
      counter_note_count: number;
      counter_author_count: number;
      additional_qualified_count: number;
      reason_codes: string[];
      verification_direction: string;
    }
  | {
      state: "insufficient_evidence" | "no_single_primary_conclusion" | "analysis_unavailable";
      reason_codes: string[];
      verification_direction: string;
    }
  | {
      state: "withheld_by_faithfulness" | "omitted_by_publication_policy";
      analysis_state: "selected" | "directional";
      reason_codes: string[];
      verification_direction: string;
    };

export interface ContentResearchPriorityAction {
  label: "建议";
  statement: string;
  primary_marketing_goal: string;
  supporting_conclusion_ids: string[];
}

export interface ContentResearchLiteReportResponse {
  schema_version: string;
  workflow_run_id: string;
  workflow_execution_state: string;
  subject?: string | null;
  frozen_scope: JsonObject;
  collected_at?: string | null;
  publication: JsonObject & { state?: string | null };
  sections: {
    main_findings: JsonObject[];
    weak_signals: JsonObject[];
    limitations_scope: JsonObject[];
    marketing_conclusions: Partial<Record<"need" | "value" | "message", ContentResearchMarketingConclusionTrack>>;
    priority_action?: ContentResearchPriorityAction | null;
  };
  status_strip: JsonObject;
  citations: JsonObject[];
  run_direction_states: JsonObject[];
  recovery_projection?: JsonObject | null;
}

export interface ContentResearchDirectionEvidence {
  workflow_run_id: string;
  direction_id: string;
  status?: string;
  candidates: Array<JsonObject & { title?: string; source_url?: string; author?: string; author_id?: string; retrieval_query?: string; detail_attempted?: boolean }>;
  selections: Array<JsonObject & { canonical_source_id?: string; selected?: boolean; reasons?: string[] }>;
  exclusions: Array<JsonObject & { canonical_source_id?: string; reasons?: string[] }>;
  packets: JsonObject[];
}

export interface ContentResearchHumanDecisionRequest {
  target_id: string;
  decision_request_id: string;
  decision_status: "selected" | "watchlist" | "rejected";
  decision_payload?: JsonObject;
  rationale?: string;
  created_by_type?: string;
  created_by_id?: string | null;
  research_result_snapshot_id?: string | null;
  metadata?: JsonObject;
}

export interface ContentResearchHumanDecisionResponse {
  schema_version: string;
  decision_id: string;
  workflow_run_id: string;
  target_type: string;
  target_id: string;
  decision_request_id: string;
  decision_status: string;
  decision_payload: JsonObject;
  rationale: string;
  created_by_type: string;
  created_by_id?: string | null;
  research_brief_id?: string | null;
  research_plan_id?: string | null;
  research_result_snapshot_id?: string | null;
  metadata: JsonObject;
  advancement: JsonObject;
  is_current: boolean;
  idempotent_replay: boolean;
  history_count: number;
  created_at: string;
}

export interface ContentResearchHumanDecisionsResponse {
  schema_version: string;
  workflow_run_id: string;
  decisions: ContentResearchHumanDecisionResponse[];
  current_decisions: ContentResearchHumanDecisionResponse[];
}

export async function createContentResearchPresearch(
  input: ContentResearchPresearchRequest
): Promise<ContentResearchPresearchResponse> {
  return contentResearchFetch("/content-research/presearch", {
    method: "POST",
    body: input,
  });
}

export async function getLLMConfiguration(): Promise<LLMConfiguration> {
  return contentResearchFetch("/content-research/llm-config");
}

export async function validateLLMConfiguration(input: LLMConfigurationInput): Promise<LLMConfiguration> {
  return contentResearchFetch("/content-research/llm-config/validate", { method: "POST", body: input });
}

export async function saveLLMConfiguration(input: LLMConfigurationInput): Promise<LLMConfiguration> {
  return contentResearchFetch("/content-research/llm-config", { method: "PUT", body: input });
}

export async function deleteLLMConfiguration(): Promise<LLMConfiguration> {
  return contentResearchFetch("/content-research/llm-config", { method: "DELETE" });
}

export async function retryContentResearchPresearch(run: ContentResearchRunProjection): Promise<ContentResearchPresearchResponse> {
  const response = await runContentResearchWorkflowAction<ContentResearchPresearchResponse>(run.run_id, contentResearchCommand(run, "retry_presearch"));
  return response.result;
}

export async function retryContentResearchRetrieval(
  run: ContentResearchRunProjection
): Promise<ContentResearchWorkflowActionResponse<JsonObject>> {
  return runContentResearchWorkflowAction<JsonObject>(
    run.run_id,
    contentResearchCommand(run, "retry_retrieval")
  );
}

export async function retryContentResearchAnalysis(
  run: ContentResearchRunProjection
): Promise<ContentResearchWorkflowActionResponse<JsonObject>> {
  return runContentResearchWorkflowAction<JsonObject>(
    run.run_id,
    contentResearchCommand(run, "retry_analysis")
  );
}

export async function retryContentResearchReport(
  run: ContentResearchRunProjection
): Promise<ContentResearchWorkflowActionResponse<JsonObject>> {
  return runContentResearchWorkflowAction<JsonObject>(
    run.run_id,
    contentResearchCommand(run, "retry_report")
  );
}

export async function getContentResearchPresearch(attemptId: string): Promise<ContentResearchPresearchResponse> {
  return contentResearchFetch(`/content-research/presearch/${encodeURIComponent(attemptId)}`);
}

export async function getContentResearchWorkflow(
  workflowRunId: string,
): Promise<ContentResearchWorkflowReadResponse> {
  return contentResearchFetch(`/content-research/workflows/${encodeURIComponent(workflowRunId)}`);
}

export async function getContentResearchScope(
  workflowRunId: string,
  version?: number,
): Promise<ContentResearchScopeProjection> {
  const suffix = version === undefined ? "" : `?version=${encodeURIComponent(String(version))}`;
  const projection = await contentResearchFetch<ContentResearchScopeProjection & {
    execution_unit?: UnsafeContentResearchExecutionUnitProjection | null;
  }>(
    `/content-research/workflows/${encodeURIComponent(workflowRunId)}/scope${suffix}`,
  );
  if (!projection.execution_unit) return { ...projection, execution_unit: null };
  return {
    ...projection,
    execution_unit: safeContentResearchExecutionUnit(projection.execution_unit),
  };
}

export async function getContentResearchTrace(workflowRunId: string): Promise<ContentResearchTrace> {
  return contentResearchFetch(`/content-research/workflows/${encodeURIComponent(workflowRunId)}/trace`);
}

export async function getContentResearchLiteReport(
  workflowRunId: string,
  options: { researchPlanId?: string; publicationId?: string; citationGroupIds?: string[] } = {}
): Promise<ContentResearchLiteReportResponse> {
  const query = new URLSearchParams();
  if (options.researchPlanId) query.set("research_plan_id", options.researchPlanId);
  if (options.publicationId) query.set("publication_id", options.publicationId);
  for (const citationGroupId of options.citationGroupIds ?? []) {
    query.append("citation_group_ids", citationGroupId);
  }
  const suffix = query.size ? `?${query.toString()}` : "";
  return contentResearchFetch(`/content-research/workflows/${encodeURIComponent(workflowRunId)}/lite-report${suffix}`);
}

export async function getContentResearchDirectionEvidence(
  workflowRunId: string,
  directionId: string,
): Promise<ContentResearchDirectionEvidence> {
  return contentResearchFetch(
    `/content-research/workflows/${encodeURIComponent(workflowRunId)}/directions/${encodeURIComponent(directionId)}/evidence?limit=50`,
  );
}

export async function getContentResearchLiteReportWithRetry(
  workflowRunId: string,
  retryDelaysMs: readonly number[] = [500, 1000, 2000],
): Promise<ContentResearchLiteReportResponse> {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await getContentResearchLiteReport(workflowRunId);
    } catch (error) {
      if (!(error instanceof TypeError) || attempt >= retryDelaysMs.length) throw error;
      await new Promise<void>((resolve) => setTimeout(resolve, retryDelaysMs[attempt]));
    }
  }
}

export async function submitContentResearchBrandDecision(
  workflowRunId: string,
  payload: ContentResearchHumanDecisionRequest
): Promise<ContentResearchHumanDecisionResponse> {
  return contentResearchFetch(`/content-research/workflows/${encodeURIComponent(workflowRunId)}/brand-decisions`, {
    method: "POST",
    body: payload,
  });
}

export async function submitContentResearchContentDecision(
  workflowRunId: string,
  payload: ContentResearchHumanDecisionRequest
): Promise<ContentResearchHumanDecisionResponse> {
  return contentResearchFetch(`/content-research/workflows/${encodeURIComponent(workflowRunId)}/content-decisions`, {
    method: "POST",
    body: payload,
  });
}

export async function getContentResearchDecisions(
  workflowRunId: string
): Promise<ContentResearchHumanDecisionsResponse> {
  return contentResearchFetch(`/content-research/workflows/${encodeURIComponent(workflowRunId)}/decisions`);
}

export async function endContentResearchWorkflow(run: ContentResearchRunProjection): Promise<ContentResearchWorkflowActionResponse> {
  return runContentResearchWorkflowAction(run.run_id, contentResearchCommand(run, "cancel"));
}

export async function reviseContentResearchSubject(
  run: ContentResearchRunProjection,
  clarificationText: string
): Promise<ContentResearchWorkflowActionResponse<ContentResearchPresearchResponse>> {
  return runContentResearchWorkflowAction(run.run_id, contentResearchCommand(run, "revise_subject", { clarification_text: clarificationText }));
}

export async function confirmContentResearchBrief(
  run: ContentResearchRunProjection,
  input: ContentResearchBriefConfirmationInput,
): Promise<ContentResearchWorkflowActionResponse<ContentResearchScopeDraftActionResult>> {
  return runContentResearchWorkflowAction(
    run.run_id,
    contentResearchCommand(run, "confirm_brief", { ...input }),
  );
}

export async function replaceContentResearchScopeDraft(
  run: ContentResearchRunProjection,
  input: ContentResearchScopeDraftReplacementInput,
): Promise<ContentResearchWorkflowActionResponse<ContentResearchScopeDraftActionResult>> {
  return runContentResearchWorkflowAction(
    run.run_id,
    contentResearchCommand(run, "replace_scope_draft", { ...input }),
  );
}

export async function confirmContentResearchScope(
  run: ContentResearchRunProjection,
  scopeDraftId: string,
): Promise<ContentResearchWorkflowActionResponse<ContentResearchScopeConfirmationActionResult>> {
  return runContentResearchWorkflowAction(
    run.run_id,
    contentResearchCommand(run, "confirm_scope", { scope_draft_id: scopeDraftId }),
  );
}

export async function runContentResearchWorkflowAction<T = JsonObject>(
  workflowRunId: string,
  payload: ContentResearchWorkflowActionRequest
): Promise<ContentResearchWorkflowActionResponse<T>> {
  return contentResearchFetch(`/content-research/workflows/${encodeURIComponent(workflowRunId)}/actions`, {
    method: "POST",
    body: payload,
  });
}

async function contentResearchFetch<T>(
  path: string,
  options?: {
    method?: string;
    body?: unknown;
  }
): Promise<T> {
  const headers = new Headers();
  const { workspaceId, userId } = getWorkspaceContext();
  if (!workspaceId || !userId) {
    throw new ContentResearchApiError("Workspace context is required", 401);
  }
  headers.set("X-Workspace-Id", workspaceId);
  headers.set("X-User-Id", userId);
  const authorization = getRuntimeAuthorizationHeader();
  if (authorization) {
    headers.set("Authorization", authorization);
  }
  if (options?.body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${RUNTIME_BASE_URL}${path}`, {
    method: options?.method ?? "GET",
    headers,
    body: options?.body !== undefined ? JSON.stringify(options.body) : undefined,
    cache: "no-store",
  });
  if (!response.ok) {
    const error = await contentResearchError(response, `${options?.method ?? "GET"} ${path} failed`);
    throw new ContentResearchApiError(error.message, response.status, error.code);
  }
  return response.json() as Promise<T>;
}

async function contentResearchError(
  response: Response,
  fallbackPrefix: string,
): Promise<{ message: string; code?: string }> {
  try {
    const payload = (await response.json()) as {
      error_code?: string;
      error_message?: string;
      suggested_action?: string;
    };
    if (payload.error_message) {
      return {
        message: payload.suggested_action
          ? `${payload.error_message}。${payload.suggested_action}`
          : payload.error_message,
        code: payload.error_code,
      };
    }
  } catch {
    // Keep the status fallback readable.
  }
  return { message: `${fallbackPrefix}: ${response.status}` };
}
