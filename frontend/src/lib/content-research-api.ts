import { RUNTIME_BASE_URL } from "./api.ts";

type JsonObject = Record<string, unknown>;

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
  custom_research_question: string;
  custom_competitor_input?: string;
  timeout_status: string;
  fallback_used: boolean;
}

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
  usage_summary: {
    total_calls?: number;
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    total_cost?: number;
    currency?: string;
    latency_ms?: number;
    [key: string]: unknown;
  };
  usage_steps: JsonObject[];
  usage_events: JsonObject[];
  provider_operations: Array<{
    operation_fingerprint: string;
    operation?: string | null;
    status: string;
    started_at?: string | null;
    finished_at?: string | null;
    failure_code?: string | null;
    failure_reason?: string | null;
    retryable: boolean;
    recovery_action?: string | null;
  }>;
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
  action: "confirm_brief" | "start_formal_research" | "retry_formal_research" | "end_content_research";
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

/** R4's only formal report contract; UI rendering is owned by U1. */
export interface ContentResearchPublishedReportResponse {
  schema_version: string;
  workflow_run_id: string;
  workflow_terminal_state: string;
  publication_state: "complete_verified_report" | "partial_verified_report" | "evidence_only_report";
  artifact: JsonObject;
  publication: JsonObject;
  sections: JsonObject[];
  citation_groups: JsonObject[];
  citation_total: number;
  citation_offset: number;
  citation_limit: number;
  claim_cards: JsonObject[];
  weak_signals: JsonObject[];
  cross_direction_records: JsonObject[];
  aggregate_claims: JsonObject[];
  limitations_recovery: JsonObject[];
  trace: JsonObject;
}

export interface ContentResearchEvidenceBundleView {
  schema_version: string;
  bundle_id: string;
  workflow_run_id: string;
  research_brief_id?: string | null;
  research_plan_id?: string | null;
  research_direction_id?: string | null;
  status: string;
  bundle_type: string;
  bundle_version: string;
  summary: string;
  coverage: JsonObject;
  retrieval_metrics: JsonObject;
  faithfulness_metrics: JsonObject;
  cross_source_metrics: JsonObject;
  contradiction_summary: JsonObject;
  citation_coverage: JsonObject;
  unsupported_claim_count: number;
  missing_evidence: JsonObject[];
  priority_policy_id?: string | null;
  evidence_boundary_policy_id?: string | null;
  decision_card: JsonObject;
  priority: JsonObject;
  evidence_state: string;
  evidence_grade: string;
  claim_scope: JsonObject;
  next_action: JsonObject;
  items: JsonObject[];
  evidence_by_role: Record<string, JsonObject[]>;
  lineage_by_evidence_id: Record<string, JsonObject[]>;
  source_links: JsonObject[];
  metadata: JsonObject;
  created_at: string;
  updated_at: string;
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

export async function getContentResearchPublishedReport(
  workflowRunId: string,
  options: { researchPlanId?: string; publicationId?: string; citationOffset?: number; citationLimit?: number } = {}
): Promise<ContentResearchPublishedReportResponse> {
  const query = new URLSearchParams();
  if (options.researchPlanId) query.set("research_plan_id", options.researchPlanId);
  if (options.publicationId) query.set("publication_id", options.publicationId);
  if (options.citationOffset !== undefined) query.set("citation_offset", String(options.citationOffset));
  if (options.citationLimit !== undefined) query.set("citation_limit", String(options.citationLimit));
  const suffix = query.size ? `?${query.toString()}` : "";
  return contentResearchFetch(`/content-research/workflows/${encodeURIComponent(workflowRunId)}/report${suffix}`);
}

export async function getContentResearchEvidenceBundle(bundleId: string): Promise<ContentResearchEvidenceBundleView> {
  return contentResearchFetch(`/content-research/evidence-bundles/${encodeURIComponent(bundleId)}`);
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

export async function restoreContentResearchWorkflow(workflowRunId: string): Promise<{
  workflow: ContentResearchWorkflowSummary;
  trace: ContentResearchTrace;
}> {
  const [workflow, trace] = await Promise.all([
    getContentResearchWorkflow(workflowRunId),
    getContentResearchTrace(workflowRunId),
  ]);
  return { workflow, trace };
}

async function contentResearchFetch<T>(
  path: string,
  options?: {
    method?: string;
    body?: unknown;
  }
): Promise<T> {
  const headers = new Headers();
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
    throw new Error(await contentResearchErrorText(response, `${options?.method ?? "GET"} ${path} failed`));
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
