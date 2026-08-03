import { getWorkspaceContext, RUNTIME_BASE_URL } from "./api.ts";

type JsonObject = Record<string, unknown>;

export class ContentResearchApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ContentResearchApiError";
    this.status = status;
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
  seed_text: string;
  user_note?: string | null;
  thread_id: string;
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
  custom_research_question: string;
  custom_competitor_input?: string;
  timeout_status: string;
  fallback_used: boolean;
  error_code?: string | null;
  error_message?: string | null;
  recoverable?: boolean;
  configuration_source?: string | null;
  model?: string | null;
}

export interface LLMConfigurationInput { base_url: string; model: string; api_key?: string | null; }
export interface LLMConfiguration { source: string; status: string; base_url: string; model: string; api_key_configured: boolean; api_key_suffix?: string | null; validated_at?: string | null; error_code?: string | null; }

export interface ContentResearchBriefConfirmRequest {
  confirmed_subject: string;
  subject_type: string;
  selected_competitors: string[];
  custom_competitors: string[];
  selected_directions: string[];
  custom_research_question: string;
}

export interface ContentResearchWorkflowSummary {
  workflow_run_id: string;
  brief: {
    id: string;
    workflow_run_id: string;
    thread_id: string;
    status: string;
    payload: JsonObject;
  };
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

export interface ContentResearchTrace {
  schema_version: string;
  workflow_run_id: string;
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
  usage_steps: JsonObject[];
  usage_events: JsonObject[];
}

export interface ContentResearchSourceCollectionRequest {
  query?: string | null;
  source_kind?: string;
  limit?: number;
  sort?: string;
  provider?: string;
}

export interface ContentResearchSourceCollectionResponse {
  workflow_run_id: string;
  provider: string;
  source_kind: string;
  status: string;
  failure_reason?: string | null;
  cookie_status: string;
  items: JsonObject[];
  metadata: JsonObject;
}

export interface ContentResearchFormalResearchResponse {
  workflow_run_id: string;
  status: string;
  task_count: number;
  completed_task_count: number;
  partial_completed_task_count: number;
  failed_tasks: Array<{ task_id: string; agent_name?: string | null; error?: string | null }>;
  provider: string;
  source_kind: string;
  limit_per_specialist: number;
}

export interface ContentResearchWorkflowActionRequest {
  schema_version?: string;
  action: "confirm_brief" | "start_formal_research" | "retry_formal_research" | "resume_formal_research" | "end_content_research" | "retry_presearch";
  payload?: JsonObject;
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

export interface XHSQRLoginResponse {
  attempt_id: string;
  status: "pending" | "authenticated" | "expired" | "failed";
  qr_image_data_url?: string | null;
  failure_code?: string | null;
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
  };
  status_strip: JsonObject;
  citations: JsonObject[];
  run_direction_states: JsonObject[];
  recovery_projection?: JsonObject | null;
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

export async function retryContentResearchPresearch(workflowRunId: string): Promise<ContentResearchPresearchResponse> {
  const response = await runContentResearchWorkflowAction<ContentResearchPresearchResponse>(workflowRunId, {
    action: "retry_presearch", payload: {},
  });
  return response.result;
}

export async function getContentResearchPresearch(attemptId: string): Promise<ContentResearchPresearchResponse> {
  return contentResearchFetch(`/content-research/presearch/${encodeURIComponent(attemptId)}`);
}

export async function confirmContentResearchBrief(
  workflowRunId: string,
  payload: ContentResearchBriefConfirmRequest
): Promise<ContentResearchWorkflowSummary> {
  const response = await runContentResearchWorkflowAction<ContentResearchWorkflowSummary>(workflowRunId, {
    action: "confirm_brief",
    payload: payload as unknown as JsonObject,
  });
  return response.result;
}

export async function confirmContentResearchBriefLegacy(
  briefId: string,
  payload: ContentResearchBriefConfirmRequest
): Promise<ContentResearchWorkflowSummary> {
  return contentResearchFetch(`/content-research/briefs/${encodeURIComponent(briefId)}/confirm`, {
    method: "POST",
    body: payload,
  });
}

export async function getContentResearchWorkflow(workflowRunId: string): Promise<ContentResearchWorkflowSummary> {
  return contentResearchFetch(`/content-research/workflows/${encodeURIComponent(workflowRunId)}`);
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

export async function startContentResearchFormalResearch(
  workflowRunId: string,
  payload: ContentResearchSourceCollectionRequest
): Promise<ContentResearchFormalResearchResponse> {
  const response = await runContentResearchWorkflowAction<ContentResearchFormalResearchResponse>(workflowRunId, {
    action: "start_formal_research",
    payload: payload as unknown as JsonObject,
  });
  return response.result;
}

export async function retryContentResearchFormalResearch(
  workflowRunId: string,
  payload: ContentResearchSourceCollectionRequest
): Promise<ContentResearchFormalResearchResponse> {
  const response = await runContentResearchWorkflowAction<ContentResearchFormalResearchResponse>(workflowRunId, {
    action: "retry_formal_research",
    payload: payload as unknown as JsonObject,
  });
  return response.result;
}

export async function resumeContentResearchFormalResearch(
  workflowRunId: string
): Promise<ContentResearchWorkflowActionResponse<{
  workflow_run_id: string;
  status: string;
  recoverable: boolean;
}>> {
  return runContentResearchWorkflowAction(workflowRunId, {
    action: "resume_formal_research",
    payload: {},
  });
}

export async function endContentResearchWorkflow(workflowRunId: string): Promise<ContentResearchWorkflowActionResponse> {
  return runContentResearchWorkflowAction(workflowRunId, {
    action: "end_content_research",
    payload: {},
  });
}

export async function collectContentResearchSourcesLegacy(
  workflowRunId: string,
  payload: ContentResearchSourceCollectionRequest
): Promise<ContentResearchSourceCollectionResponse> {
  return contentResearchFetch(`/content-research/workflows/${encodeURIComponent(workflowRunId)}/source-collections`, {
    method: "POST",
    body: payload,
  });
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
    throw new ContentResearchApiError(
      await contentResearchErrorText(response, `${options?.method ?? "GET"} ${path} failed`),
      response.status
    );
  }
  return response.json() as Promise<T>;
}

async function contentResearchErrorText(response: Response, fallbackPrefix: string): Promise<string> {
  try {
    const payload = (await response.json()) as {
      error_message?: string;
      suggested_action?: string;
    };
    if (payload.error_message) {
      return payload.suggested_action
        ? `${payload.error_message}。${payload.suggested_action}`
        : payload.error_message;
    }
  } catch {
    // Keep the status fallback readable.
  }
  return `${fallbackPrefix}: ${response.status}`;
}
