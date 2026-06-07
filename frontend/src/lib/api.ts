import type {
  Brand,
  BrandChannelOption,
  DataImportPreviewState,
  DecisionItem,
  EvaluationSlice,
  ExtensionCaptureSessionState,
  PerformanceMetric,
  PublishRecord,
  Topic,
} from "./types";

export const RUNTIME_BASE_URL =
  process.env.NEXT_PUBLIC_XHS_API_BASE_URL?.trim() || "http://127.0.0.1:8000";

let workspaceContext = { workspaceId: "", userId: "" };

export interface BrandsPageData {
  brands: Brand[];
  stats: { activeBrands: number; connectedAccounts: number };
  source: "live";
}

export interface BrandDetailPageData {
  brand: Omit<Brand, "targetAudience" | "brandExpression" | "businessGoals"> & {
    targetAudience: Record<string, unknown>;
    brandExpression: Record<string, unknown>;
    businessGoals: Record<string, unknown>;
  };
  channels: BrandChannelOption[];
  latestExtensionCaptureSession?: ExtensionCaptureSessionState;
  latestDataImportPreview?: DataImportPreviewState;
  source: "live";
}

export interface TopicPoolPageData {
  brand: Pick<Brand, "id" | "name" | "stage" | "targetAudience">;
  topics: Topic[];
  stats: { totalCandidates: number; bestScore: number; lastRefreshAt: string | null };
  source: "live";
}

export interface DecisionsPageData {
  items: DecisionItem[];
  stats: { expectedReward: number; selectedCount: number; explorationProbability: number };
  batchId?: string;
  source: "live";
}

export interface PublishPageData {
  records: PublishRecord[];
  source: "live";
}

export interface PerformancePageData {
  metrics: PerformanceMetric[];
  stats: { averageEngagementRate: number; compositeReward168h: number };
  source: "live";
}

export interface EvaluationPageData {
  slices: EvaluationSlice[];
  summary: {
    comparisonLabel: string;
    sampleSize: number;
    coverage: number;
    essRatio: number;
    uplift: number;
    note: string;
  };
  source: "live";
}

export interface DataProcessingPageData {
  latestExtensionCaptureSession?: ExtensionCaptureSessionState;
  latestDataImportPreview?: DataImportPreviewState;
  recentIngestionRuns: Array<{
    id: string;
    type: string;
    status: string;
    sourceLabel: string;
    createdAt: string;
    importedCount: number;
    dedupedCount: number;
  }>;
  source: "live";
}

export interface DataSourcesPageData {
  brand: Pick<Brand, "id" | "name">;
  channels: BrandChannelOption[];
  latestExtensionCaptureSession?: ExtensionCaptureSessionState;
  latestDataImportPreview?: DataImportPreviewState;
  recentIngestionRuns: DataProcessingPageData["recentIngestionRuns"];
  discoveryTasks: Array<{ id: string; status: string; query: string; createdAt: string }>;
  source: "live";
}

export interface DiscoveryWorkspaceData {
  taskId: string;
  expandedQueries: Array<{ id: string; text: string; category: string }>;
  hotspots: Array<{
    metric: "likes" | "collections" | "comments" | string;
    items: Array<{
      title: string;
      sourceUrl: string;
      author?: string;
      likes: number;
      collections: number;
      comments: number;
    }>;
  }>;
}

export interface V2BrandApiResponse {
  id: string;
  workspace_id?: string;
  name: string;
  category?: string | null;
  stage: string;
  target_audience: Record<string, unknown>;
  brand_voice: Record<string, unknown>;
  goals: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
}

export interface V2BrandListResponse {
  items: V2BrandApiResponse[];
}

export interface V2BrandChannelListResponse {
  items: Array<{ id: string; platform: string; account_name?: string | null; profile_url?: string | null }>;
}

export type PublishCandidate = {
  candidate_id: string;
  title: string;
  content: string;
  tags: string[];
  created_at: string;
};

export type CreatorThreadSummary = {
  thread_id: string;
  title: string;
  status: "active" | "accepted" | "archived";
  active_job_id: string | null;
  active_workflow_session_id?: string | null;
  created_at: string;
  updated_at: string;
};

export type CreatorMessageRecord = {
  message_id: string;
  role: "assistant" | "user" | "system";
  text: string;
};

export type GeneratedNoteItem = {
  note_id: string;
  title: string;
  content: string;
  tags: string[];
  cover_design_prompt?: string | null;
};

export type SessionUsageSummary = {
  session_id: string | null;
  job_id: string | null;
  total_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  total_cost: number;
  currency: string;
  latency_ms: number;
};

export type SessionUsageStepSummary = {
  step_id: string | null;
  step_name: string | null;
  agent_name: string | null;
  total_calls: number;
  failed_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  total_cost: number;
  currency: string;
  latency_ms: number;
};

export type SessionUsageEvent = {
  id: string;
  session_id: string | null;
  job_id: string | null;
  step_id: string | null;
  step_name: string | null;
  agent_name: string | null;
  provider: string;
  model: string;
  model_policy: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  total_cost: number;
  currency: string;
  latency_ms: number | null;
  status: string;
  error_message: string | null;
  created_at: string;
};

export class RuntimeApiError extends Error {
  readonly status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.name = "RuntimeApiError";
    this.status = status;
  }
}

function pathUrl(path: string) {
  return `${RUNTIME_BASE_URL}${path}`;
}

function authHeaders() {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (workspaceContext.workspaceId) headers.set("X-Workspace-Id", workspaceContext.workspaceId);
  if (workspaceContext.userId) headers.set("X-User-Id", workspaceContext.userId);
  return headers;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(pathUrl(path), {
    ...init,
    headers: authHeaders(),
  });
  if (!response.ok) {
    throw new RuntimeApiError(`request failed: ${response.status}`, response.status);
  }
  return (await response.json()) as T;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function mapStage(stage?: string): Brand["stage"] {
  if (stage === "growth") return "Growth";
  if (stage === "mature" || stage === "scaled") return "Mature";
  return "Seed";
}

function mapChannel(raw: { id: string; platform: string; account_name?: string | null; profile_url?: string | null }): BrandChannelOption {
  return {
    id: raw.id,
    platform: raw.platform,
    accountName: raw.account_name ?? undefined,
    profileUrl: raw.profile_url ?? undefined,
  };
}

function mapBrand(raw: V2BrandApiResponse): Brand {
  const audience = raw.target_audience ?? {};
  return {
    id: raw.id,
    name: raw.name,
    category: raw.category ?? undefined,
    stage: mapStage(raw.stage),
    targetAudience: String(audience.summary ?? "待补充"),
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
  };
}

function emptyBrand(brandId: string): Pick<Brand, "id" | "name" | "stage" | "targetAudience"> {
  return { id: brandId, name: "当前品牌", stage: "Seed", targetAudience: "待补充" };
}

export function setWorkspaceContext(workspaceId: string, userId: string) {
  workspaceContext = { workspaceId, userId };
}

export function getWorkspaceContext() {
  return workspaceContext;
}

export async function initializeWorkspaceContext() {
  const health = await fetch(pathUrl("/health"));
  if (!health.ok) {
    throw new RuntimeApiError("Agent Runtime 未启动或不可达", health.status);
  }
  const identity = await requestJson<{ workspace_id: string; user_id: string }>("/workspaces/default");
  setWorkspaceContext(identity.workspace_id, identity.user_id);
  return identity;
}

export function getRuntimeApiErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : "unknown error";
}

export async function getBrandOptions() {
  const data = await requestJson<V2BrandListResponse>("/brands");
  return data.items.map((brand) => ({ id: brand.id, name: brand.name }));
}

export async function getBrandsPageData(): Promise<BrandsPageData> {
  const data = await requestJson<V2BrandListResponse>("/brands");
  const brands = data.items.map(mapBrand);
  return {
    brands,
    stats: { activeBrands: brands.length, connectedAccounts: 0 },
    source: "live",
  };
}

export async function createBrand(payload: { name: string; category?: string; stage?: string; audienceSummary?: string }) {
  const brand = await requestJson<V2BrandApiResponse>("/brands", {
    method: "POST",
    body: JSON.stringify({
      name: payload.name,
      category: payload.category,
      stage: payload.stage ?? "seed",
      target_audience: { summary: payload.audienceSummary ?? "" },
    }),
  });
  return { id: brand.id, name: brand.name };
}

export async function getBrandDetailPageData(brandId: string): Promise<BrandDetailPageData> {
  const workspace = await requestJson<{
    brand: V2BrandApiResponse;
    channels: V2BrandChannelListResponse["items"];
    latest_extension_capture_session?: unknown;
    latest_data_import_preview?: unknown;
  }>(`/brands/${brandId}/workspace`);
  return {
    brand: {
      ...mapBrand(workspace.brand),
      stage: workspace.brand.stage === "growth" ? "growth" : workspace.brand.stage === "mature" ? "mature" : "seed",
      targetAudience: workspace.brand.target_audience ?? {},
      brandExpression: workspace.brand.brand_voice ?? {},
      businessGoals: workspace.brand.goals ?? {},
    },
    channels: workspace.channels.map(mapChannel),
    latestExtensionCaptureSession: mapExtensionSession(workspace.latest_extension_capture_session),
    latestDataImportPreview: mapDataPreview(workspace.latest_data_import_preview),
    source: "live",
  };
}

export async function updateBrand(brandId: string, payload: Record<string, unknown>) {
  return requestJson(`/brands/${brandId}`, { method: "PATCH", body: JSON.stringify(toBrandApiPayload(payload)) });
}

export async function createBrandChannel(brandId: string, payload: { platform: string; accountName?: string; profileUrl?: string }) {
  const raw = await requestJson<V2BrandChannelListResponse["items"][number]>(`/brands/${brandId}/channels`, {
    method: "POST",
    body: JSON.stringify({ platform: payload.platform, account_name: payload.accountName, profile_url: payload.profileUrl }),
  });
  return mapChannel(raw);
}

export async function updateBrandChannel(brandId: string, channelId: string, payload: { platform: string; accountName?: string; profileUrl?: string }) {
  const raw = await requestJson<V2BrandChannelListResponse["items"][number]>(`/brands/${brandId}/channels/${channelId}`, {
    method: "PATCH",
    body: JSON.stringify({ platform: payload.platform, account_name: payload.accountName, profile_url: payload.profileUrl }),
  });
  return mapChannel(raw);
}

function toBrandApiPayload(payload: Record<string, unknown>) {
  return {
    name: payload.name,
    category: payload.category,
    stage: payload.stage,
    target_audience: payload.targetAudience,
    brand_voice: payload.brandExpression,
    goals: payload.businessGoals,
  };
}

export async function getTopicPoolPageData(brandId: string): Promise<TopicPoolPageData> {
  const data = await requestJson<{ items?: unknown[]; stats?: Record<string, unknown>; brand?: V2BrandApiResponse }>(`/brands/${brandId}/topic-pool`);
  const topics = (data.items ?? []).map((item) => mapTopic(asRecord(item)));
  return {
    brand: data.brand ? mapBrand(data.brand) : emptyBrand(brandId),
    topics,
    stats: {
      totalCandidates: Number(data.stats?.total_candidates ?? topics.length),
      bestScore: Number(data.stats?.best_score ?? Math.max(0, ...topics.map((topic) => topic.score))),
      lastRefreshAt: typeof data.stats?.last_refresh_at === "string" ? data.stats.last_refresh_at : null,
    },
    source: "live",
  };
}

function mapTopic(raw: Record<string, unknown>): Topic {
  return {
    id: String(raw.id ?? raw.topic_id ?? crypto.randomUUID()),
    title: String(raw.title ?? raw.topic ?? "Untitled topic"),
    type: (raw.type as Topic["type"]) ?? "Core",
    source: (raw.source as Topic["source"]) ?? "Engagement",
    status: typeof raw.status === "string" ? raw.status : undefined,
    score: Number(raw.score ?? raw.signal_score ?? 0),
    angle: typeof raw.angle === "string" ? raw.angle : undefined,
    hypothesis: typeof raw.hypothesis === "string" ? raw.hypothesis : undefined,
    evidenceCount: Number(raw.evidence_count ?? 0),
    scoreBreakdown: raw.score_breakdown
      ? mapScoreBreakdown(asRecord(raw.score_breakdown))
      : raw.scoreBreakdown
        ? mapScoreBreakdown(asRecord(raw.scoreBreakdown))
        : undefined,
    evidenceProvenance: Array.isArray(raw.evidence_provenance)
      ? raw.evidence_provenance.map((item) => {
          const entry = asRecord(item);
          return {
            itemId: String(entry.item_id ?? entry.itemId ?? ""),
            sourceUrl: typeof entry.source_url === "string" ? entry.source_url : typeof entry.sourceUrl === "string" ? entry.sourceUrl : undefined,
            originalTitle: typeof entry.original_title === "string" ? entry.original_title : typeof entry.originalTitle === "string" ? entry.originalTitle : undefined,
            contributionWeight: Number(entry.contribution_weight ?? entry.contributionWeight ?? 0),
            signalScore: Number(entry.signal_score ?? entry.signalScore ?? 0),
            likes: Number(entry.likes ?? 0),
            comments: Number(entry.comments ?? 0),
            collects: Number(entry.collects ?? 0),
            shares: Number(entry.shares ?? 0),
            signalType: typeof entry.signal_type === "string" ? entry.signal_type : typeof entry.signalType === "string" ? entry.signalType : "engagement",
          };
        })
      : [],
  };
}

function mapScoreBreakdown(raw: Record<string, unknown>): NonNullable<Topic["scoreBreakdown"]> {
  const violations = raw.brand_fit_violations ?? raw.brandFitViolations;
  return {
    noveltyScore: Number(raw.novelty_score ?? raw.noveltyScore ?? 0),
    fitScore: Number(raw.fit_score ?? raw.fitScore ?? 0),
    trendScore: Number(raw.trend_score ?? raw.trendScore ?? 0),
    historicalRewardScore: Number(raw.historical_reward_score ?? raw.historicalRewardScore ?? 0),
    policyScore: Number(raw.policy_score ?? raw.policyScore ?? 0),
    finalScore: Number(raw.final_score ?? raw.finalScore ?? 0),
    sourceCount: typeof raw.source_count === "number" ? raw.source_count : typeof raw.sourceCount === "number" ? raw.sourceCount : undefined,
    brandFitCheck: typeof raw.brand_fit_check === "boolean" ? raw.brand_fit_check : typeof raw.brandFitCheck === "boolean" ? raw.brandFitCheck : undefined,
    brandFitViolations: Array.isArray(violations) ? violations.filter((item): item is string => typeof item === "string") : [],
  };
}

export async function triggerTopicPoolRefresh(brandId: string) {
  return requestJson(`/brands/${brandId}/topic-pool/refresh`, { method: "POST", body: "{}" });
}

export async function runDecisionBatch(brandId: string) {
  return requestJson<{ batch_id: string }>(`/brands/${brandId}/decision-runs`, { method: "POST", body: "{}" });
}

export async function getDecisionsPageData(brandId: string, options?: { batchId?: string | null }): Promise<DecisionsPageData> {
  const path = options?.batchId ? `/decision-batches/${options.batchId}` : `/brands/${brandId}/decision-batches/latest`;
  try {
    const data = await requestJson<{ batch_id?: string; items?: unknown[]; stats?: Record<string, unknown> }>(path);
    const items = (data.items ?? []).map((item, index) => mapDecisionItem(asRecord(item), index));
    return {
      items,
      batchId: data.batch_id ?? options?.batchId ?? undefined,
      stats: {
        expectedReward: Number(data.stats?.expected_reward ?? 0),
        selectedCount: Number(data.stats?.selected_count ?? items.length),
        explorationProbability: Number(data.stats?.exploration_probability ?? 0),
      },
      source: "live",
    };
  } catch (error) {
    if (error instanceof RuntimeApiError && error.status === 404) {
      return { items: [], stats: { expectedReward: 0, selectedCount: 0, explorationProbability: 0 }, source: "live" };
    }
    throw error;
  }
}

function mapDecisionItem(raw: Record<string, unknown>, index: number): DecisionItem {
  return {
    slotIndex: Number(raw.slot_index ?? index),
    title: String(raw.title ?? "Untitled decision"),
    angle: typeof raw.angle === "string" ? raw.angle : undefined,
    hypothesis: typeof raw.hypothesis === "string" ? raw.hypothesis : undefined,
    strategyScore: Number(raw.strategy_score ?? raw.score ?? 0),
    mode: raw.mode === "Exploration" ? "Exploration" : "Exploitation",
    reviewStatus: typeof raw.review_status === "string" ? raw.review_status : undefined,
    reviewNotes: typeof raw.review_notes === "string" ? raw.review_notes : undefined,
  };
}

export async function reviewDecisionBatchItem(batchId: string, slotIndex: number, payload: Record<string, unknown>) {
  return requestJson(`/decision-batches/${batchId}/items/${slotIndex}/review`, { method: "POST", body: JSON.stringify(payload) });
}

export async function getPublishPageData(brandId: string): Promise<PublishPageData> {
  const data = await requestJson<{ items?: unknown[] }>(`/brands/${brandId}/publish-records`);
  return { records: (data.items ?? []).map((item) => mapPublishRecord(asRecord(item))), source: "live" };
}

function mapPublishRecord(raw: Record<string, unknown>): PublishRecord {
  return {
    id: String(raw.id ?? crypto.randomUUID()),
    title: String(raw.title ?? "Untitled"),
    channel: String(raw.channel ?? raw.platform ?? "xiaohongshu"),
    publishedAt: String(raw.published_at ?? raw.created_at ?? ""),
    decisionSource: String(raw.decision_source ?? "manual"),
    status: String(raw.status ?? "Published"),
  };
}

export async function createPublishRecord(brandId: string, payload: Record<string, unknown>) {
  return requestJson(`/brands/${brandId}/publish-records`, { method: "POST", body: JSON.stringify(payload) });
}

export async function getPublishCandidates() {
  return requestJson<{ items: PublishCandidate[] }>("/creator/publish-candidates");
}

export async function getPerformancePageData(brandId: string): Promise<PerformancePageData> {
  const data = await requestJson<{ items?: unknown[]; stats?: Record<string, unknown> }>(`/brands/${brandId}/performance-snapshots`);
  return {
    metrics: (data.items ?? []).map((item) => {
      const raw = asRecord(item);
      return {
        id: String(raw.id ?? crypto.randomUUID()),
        topicTitle: String(raw.topic_title ?? raw.title ?? "Untitled"),
        impressions: Number(raw.impressions ?? 0),
        clicks: Number(raw.clicks ?? 0),
        conversionProxyLabel: String(raw.conversion_proxy_label ?? "互动"),
        rewardScore: Number(raw.reward_score ?? 0),
      };
    }),
    stats: {
      averageEngagementRate: Number(data.stats?.average_engagement_rate ?? 0),
      compositeReward168h: Number(data.stats?.composite_reward_168h ?? 0),
    },
    source: "live",
  };
}

export async function importPerformanceSnapshot(brandId: string) {
  return requestJson(`/brands/${brandId}/performance-snapshots/import`, { method: "POST", body: "{}" });
}

export async function getEvaluationPageData(brandId: string): Promise<EvaluationPageData> {
  try {
    const data = await requestJson<{ slices?: unknown[]; summary?: Record<string, unknown> }>(`/brands/${brandId}/evaluation-runs/latest`);
    return {
      slices: (data.slices ?? []).map((item) => {
        const raw = asRecord(item);
        return { slice: String(raw.slice ?? ""), issue: String(raw.issue ?? ""), action: String(raw.action ?? "") };
      }),
      summary: {
        comparisonLabel: String(data.summary?.comparison_label ?? ""),
        sampleSize: Number(data.summary?.sample_size ?? 0),
        coverage: Number(data.summary?.coverage ?? 0),
        essRatio: Number(data.summary?.ess_ratio ?? 0),
        uplift: Number(data.summary?.uplift ?? 0),
        note: String(data.summary?.note ?? ""),
      },
      source: "live",
    };
  } catch (error) {
    if (error instanceof RuntimeApiError && error.status === 404) {
      return {
        slices: [],
        summary: { comparisonLabel: "", sampleSize: 0, coverage: 0, essRatio: 0, uplift: 0, note: "当前品牌还没有 evaluation run。" },
        source: "live",
      };
    }
    throw error;
  }
}

export async function runEvaluation(brandId: string) {
  return requestJson(`/brands/${brandId}/evaluation-runs`, { method: "POST", body: "{}" });
}

export async function getDataSourcesPageData(brandId: string): Promise<DataSourcesPageData> {
  const detail = await getBrandDetailPageData(brandId);
  return {
    brand: { id: detail.brand.id, name: detail.brand.name },
    channels: detail.channels,
    latestExtensionCaptureSession: detail.latestExtensionCaptureSession,
    latestDataImportPreview: detail.latestDataImportPreview,
    recentIngestionRuns: [],
    discoveryTasks: [],
    source: "live",
  };
}

export async function getDataProcessingPageData(brandId: string): Promise<DataProcessingPageData> {
  const detail = await getBrandDetailPageData(brandId);
  return {
    latestExtensionCaptureSession: detail.latestExtensionCaptureSession,
    latestDataImportPreview: detail.latestDataImportPreview,
    recentIngestionRuns: [],
    source: "live",
  };
}

export async function createDiscoveryTask(brandId: string, payload: string | Record<string, unknown>) {
  const body = typeof payload === "string" ? { topic: payload } : payload;
  return mapDiscoveryWorkspace(await requestJson(`/brands/${brandId}/discovery-tasks`, { method: "POST", body: JSON.stringify(body) }));
}

export async function refreshDiscoveryHotspots(brandId: string, taskId: string) {
  return mapDiscoveryWorkspace(await requestJson(`/brands/${brandId}/discovery-tasks/${taskId}/hotspots/refresh`, { method: "POST", body: "{}" }));
}

export async function addDiscoveryQuery(brandId: string, taskId: string, text: string) {
  return mapDiscoveryWorkspace(await requestJson(`/brands/${brandId}/discovery-tasks/${taskId}/queries`, { method: "POST", body: JSON.stringify({ text }) }));
}

export async function deleteDiscoveryQuery(brandId: string, taskId: string, queryId: string) {
  return mapDiscoveryWorkspace(await requestJson(`/brands/${brandId}/discovery-tasks/${taskId}/queries/${queryId}`, { method: "DELETE" }));
}

function mapDiscoveryWorkspace(value: unknown): DiscoveryWorkspaceData {
  const raw = asRecord(value);
  const queries = Array.isArray(raw.expanded_queries) ? raw.expanded_queries : raw.expandedQueries;
  const hotspots = Array.isArray(raw.hotspots) ? raw.hotspots : [];
  return {
    taskId: String(raw.task_id ?? raw.taskId ?? ""),
    expandedQueries: Array.isArray(queries)
      ? queries.map((item) => {
          const query = asRecord(item);
          return {
            id: String(query.id ?? query.query_id ?? crypto.randomUUID()),
            text: String(query.text ?? query.query ?? ""),
            category: String(query.category ?? "custom"),
          };
        })
      : [],
    hotspots: hotspots.map((item) => {
      const hotspot = asRecord(item);
      const entries = Array.isArray(hotspot.items) ? hotspot.items : [];
      return {
        metric: String(hotspot.metric ?? ""),
        items: entries.map((entry) => {
          const row = asRecord(entry);
          return {
            title: String(row.title ?? ""),
            sourceUrl: String(row.source_url ?? row.sourceUrl ?? "#"),
            author: typeof row.author === "string" ? row.author : undefined,
            likes: Number(row.likes ?? 0),
            collections: Number(row.collections ?? row.collects ?? 0),
            comments: Number(row.comments ?? 0),
          };
        }),
      };
    }),
  };
}

export async function createExtensionCaptureSession(brandId: string, channelId: string) {
  return mapExtensionSession(await requestJson(`/brands/${brandId}/extension-capture-sessions`, { method: "POST", body: JSON.stringify({ channel_id: channelId }) }))!;
}

export async function getExtensionCaptureSession(brandId: string, captureSessionId: string) {
  return mapExtensionSession(await requestJson(`/brands/${brandId}/extension-capture-sessions/${captureSessionId}`))!;
}

export async function submitExtensionCapture(captureSessionId: string, captureToken: string, capturePayload: Record<string, unknown>) {
  const submitted = await requestJson<{ capture_session_id: string }>(`/extension-capture-sessions/${captureSessionId}/submit`, {
    method: "POST",
    body: JSON.stringify({ capture_token: captureToken, capture_payload: capturePayload }),
  });
  return { captureSessionId: submitted.capture_session_id };
}

export async function retryExtensionCaptureSessionSync(brandId: string, captureSessionId: string) {
  return mapExtensionSession(await requestJson(`/brands/${brandId}/extension-capture-sessions/${captureSessionId}/retry`, { method: "POST", body: "{}" }))!;
}

export async function createDataImportPreview(
  brandId: string,
  fileNameOrPayload: string | Record<string, unknown>,
  payload?: Record<string, unknown>
) {
  const body =
    typeof fileNameOrPayload === "string"
      ? { file_name: fileNameOrPayload, preview_payload: payload ?? {} }
      : fileNameOrPayload;
  return mapDataPreview(await requestJson(`/brands/${brandId}/data-import-previews`, { method: "POST", body: JSON.stringify(body) }))!;
}

export async function getDataImportPreview(brandId: string, previewId: string) {
  return mapDataPreview(await requestJson(`/brands/${brandId}/data-import-previews/${previewId}`))!;
}

export async function retryDataImportPreviewSync(brandId: string, previewId: string) {
  return mapDataPreview(await requestJson(`/brands/${brandId}/data-import-previews/${previewId}/retry`, { method: "POST", body: "{}" }))!;
}

function mapExtensionSession(value: unknown): ExtensionCaptureSessionState | undefined {
  if (!value) return undefined;
  const raw = asRecord(value);
  return {
    captureSessionId: String(raw.capture_session_id ?? raw.captureSessionId ?? ""),
    captureToken: typeof raw.capture_token === "string" ? raw.capture_token : undefined,
    status: (raw.status as ExtensionCaptureSessionState["status"]) ?? "pending_capture",
    expiresAt: String(raw.expires_at ?? raw.expiresAt ?? ""),
    capturedAt: typeof raw.captured_at === "string" ? raw.captured_at : undefined,
    previewPayload: asRecord(raw.preview_payload),
    ingestionReceipt: raw.ingestion_receipt as ExtensionCaptureSessionState["ingestionReceipt"],
    errorSummary: raw.error_summary as ExtensionCaptureSessionState["errorSummary"],
  };
}

function mapDataPreview(value: unknown): DataImportPreviewState | undefined {
  if (!value) return undefined;
  const raw = asRecord(value);
  return {
    previewId: String(raw.preview_id ?? raw.previewId ?? ""),
    fileName: String(raw.file_name ?? raw.fileName ?? ""),
    status: (raw.status as DataImportPreviewState["status"]) ?? "uploaded",
    uploadedAt: String(raw.uploaded_at ?? raw.uploadedAt ?? ""),
    parsedRowCount: Number(raw.parsed_row_count ?? raw.parsedRowCount ?? 0),
    previewPayload: asRecord(raw.preview_payload),
    ingestionReceipt: raw.ingestion_receipt as DataImportPreviewState["ingestionReceipt"],
    fieldErrors: Array.isArray(raw.field_errors) ? (raw.field_errors as Array<Record<string, unknown>>) : [],
    errorSummary: raw.error_summary as DataImportPreviewState["errorSummary"],
  };
}

export async function listThreads() {
  const data = await requestJson<{ items: CreatorThreadSummary[] }>("/threads");
  return data.items;
}

export async function createThread(title?: string) {
  return requestJson<CreatorThreadSummary>("/threads", { method: "POST", body: JSON.stringify({ title }) });
}

export async function getThread(threadId: string) {
  return requestJson<{ thread: CreatorThreadSummary; messages: CreatorMessageRecord[] }>(`/threads/${threadId}`);
}

export async function renameThread(threadId: string, title: string) {
  return requestJson<CreatorThreadSummary>(`/threads/${threadId}`, { method: "PATCH", body: JSON.stringify({ title }) });
}

export async function deleteThread(threadId: string) {
  return requestJson(`/threads/${threadId}`, { method: "DELETE" });
}

export async function appendThreadMessage(threadId: string, text: string) {
  return requestJson<{ intent: string; assistant_reply?: string; updated_title?: string }>(`/threads/${threadId}/messages`, {
    method: "POST",
    body: JSON.stringify({ role: "user", text }),
  });
}

export async function startThreadWorkflow(threadId: string, text: string) {
  return requestJson<{ session_id: string; job_id: string }>(`/threads/${threadId}/workflow`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

export async function getThreadResult(threadId: string) {
  return requestJson<{ strategy: unknown; notes: GeneratedNoteItem[] }>(`/threads/${threadId}/result`);
}

export async function completeThread(threadId: string) {
  return requestJson(`/threads/${threadId}/complete`, { method: "POST", body: "{}" });
}

export async function getJobStatus(jobId: string) {
  return requestJson<{ job_id: string; session_id: string; job_type: string; status: string }>(`/jobs/${jobId}`);
}

export async function cancelJob(jobId: string) {
  return requestJson(`/jobs/${jobId}/cancel`, { method: "POST", body: "{}" });
}

export async function resumeJob(jobId: string) {
  return requestJson(`/jobs/${jobId}/resume`, { method: "POST", body: "{}" });
}

export async function getSessionUsage(sessionId: string) {
  return requestJson<SessionUsageSummary>(`/sessions/${sessionId}/usage`);
}

export async function getSessionUsageSteps(sessionId: string) {
  const data = await requestJson<{ steps: SessionUsageStepSummary[] }>(`/sessions/${sessionId}/usage/steps`);
  return data.steps;
}

export async function getSessionUsageEvents(sessionId: string) {
  const data = await requestJson<{ events: SessionUsageEvent[] }>(`/sessions/${sessionId}/usage/events`);
  return data.events;
}

export function subscribeThreadEvents(
  threadId: string,
  handlers: {
    onProgress?: (data: any) => void;
    onStageChanged?: (data: any) => void;
    onCompleted?: (data: any) => void;
    onFailed?: (data: any) => void;
    onCancelled?: (data: any) => void;
  }
) {
  const es = new EventSource(pathUrl(`/threads/${threadId}/events`));
  es.addEventListener("workflow_task_progress", (event) => handlers.onProgress?.(JSON.parse((event as MessageEvent).data)));
  es.addEventListener("workflow_stage_changed", (event) => handlers.onStageChanged?.(JSON.parse((event as MessageEvent).data)));
  es.addEventListener("workflow_task_completed", (event) => handlers.onCompleted?.(JSON.parse((event as MessageEvent).data)));
  es.addEventListener("workflow_task_failed", (event) => handlers.onFailed?.(JSON.parse((event as MessageEvent).data)));
  es.addEventListener("workflow_cancelled", (event) => handlers.onCancelled?.(JSON.parse((event as MessageEvent).data)));
  return es;
}
