import type {
  Brand,
  BrandChannelOption,
  BrandDataImportPayload,
  BrandSourceSyncPayload,
  DataImportPreviewState,
  DecisionItem,
  ExtensionCaptureSessionState,
  EvaluationSlice,
  IngestionAcceptedResult,
  PerformanceMetric,
  PublishRecord,
  Topic,
  TopicPoolRefreshResult
} from "./types";

// Single source of truth for the local Agent Runtime base URL.
// Override via NEXT_PUBLIC_XHS_API_BASE_URL env var for non-default setups.
export const RUNTIME_BASE_URL: string =
  (typeof process !== "undefined" &&
    (process.env.NEXT_PUBLIC_XHS_API_BASE_URL?.trim() ||
      process.env.XHS_API_BASE_URL?.trim())) ||
  "http://127.0.0.1:8000";
export const MIN_BACKEND_VERSION = "0.1.0";
export const REQUIRED_API_CONTRACT = "local-runtime-v1";

interface RuntimeHealthResponse {
  service: string;
  version: string;
  api_contract: string;
  features?: Record<string, boolean>;
}

export interface V2BrandApiResponse {
  id: string;
  workspace_id: string;
  name: string;
  category?: string | null;
  stage: string;
  target_audience: Record<string, unknown>;
  brand_voice: Record<string, unknown>;
  goals: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface V2BrandListResponse {
  items: V2BrandApiResponse[];
}

export interface V2BrandChannelListResponse {
  items: Array<{
    id: string;
    platform?: string;
    account_name?: string | null;
    profile_url?: string | null;
  }>;
}

export interface V2BrandPolicyConfigResponse {
  id: string;
  policy_name: string;
  policy_version: string;
  topic_type_targets: Record<string, unknown>;
  updated_at: string;
}

interface V2BrandStateSnapshotResponse {
  id: string;
  state_version: string;
  stage: string;
  computed_at: string;
}

export interface V2BrandStateSnapshotListResponse {
  items: V2BrandStateSnapshotResponse[];
}

interface V2BrandWorkspaceApiResponse {
  brand: V2BrandApiResponse;
  channels: Array<{
    id: string;
    platform?: string;
    account_name?: string | null;
    profile_url?: string | null;
  }>;
  active_policy?: V2BrandPolicyConfigResponse | null;
  latest_extension_capture_session?: V2ExtensionCaptureSessionApiResponse | null;
  latest_data_import_preview?: V2DataImportPreviewApiResponse | null;
  recent_ingestion_runs?: Array<{
    ingestion_run_id: string;
    entry_type: string;
    source_type: string;
    source_adapter?: string | null;
    status: string;
    stats: Record<string, unknown>;
    error_summary: Record<string, unknown>;
    started_at?: string | null;
    finished_at?: string | null;
    created_at: string;
  }>;
}

interface V2TopicPoolRefreshApiResponse {
  refresh_run_id: string;
  status: string;
  generated_item_count: number;
  archived_item_count: number;
  total_candidate_count: number;
  refreshed_at: string;
}

interface V2ExtensionCaptureSessionApiResponse {
  capture_session_id: string;
  capture_token?: string | null;
  status: "pending_capture" | "captured" | "syncing" | "accepted" | "failed" | "expired";
  expires_at: string;
  captured_at?: string | null;
  preview_payload?: Record<string, unknown> | null;
  ingestion_receipt?: IngestionAcceptedResult | null;
  error_summary?: {
    type?: string;
    message: string;
  } | null;
}

interface V2DataImportPreviewApiResponse {
  preview_id: string;
  file_name: string;
  status: "uploaded" | "parsed" | "syncing" | "accepted" | "failed";
  uploaded_at: string;
  parsed_row_count: number;
  preview_payload?: Record<string, unknown> | null;
  ingestion_receipt?: IngestionAcceptedResult | null;
  field_errors?: Array<Record<string, unknown>>;
  error_summary?: {
    type?: string;
    message: string;
  } | null;
}

interface V2TopicPoolListApiResponse {
  brand: {
    id: string;
    name: string;
    stage: string;
    target_audience: Record<string, unknown>;
  };
  stats: {
    total_candidate_count: number;
    best_score: number;
    last_refresh_at: string | null;
  };
  items: Array<{
    id: string;
    topic_id: string;
    display_name: string;
    normalized_name: string;
    topic_type: string;
    title: string;
    angle: string;
    hypothesis: string;
    evidence_summary: {
      source_count?: number;
      dominant_signal_type?: string;
    };
    source_agent: string;
    status: string;
    final_score: number;
    score_breakdown?: {
      novelty_score?: number;
      fit_score?: number;
      trend_score?: number;
      historical_reward_score?: number;
      policy_score?: number;
      final_score?: number;
      source_count?: number;
      brand_fit_check?: boolean;
      brand_fit_violations?: string[];
    };
    evidence_provenance?: Array<{
      item_id: string;
      source_url?: string | null;
      original_title: string;
      signal_type: string;
      contribution_weight: number;
      signal_score: number;
      likes: number;
      comments: number;
      collects: number;
      shares: number;
    }>;
    updated_at: string;
  }>;
}

interface V2DecisionBatchItemApiResponse {
  slot_index: number;
  topic_pool_item_id: string;
  decision_event_id?: string | null;
  title: string;
  angle: string;
  hypothesis: string;
  score: number;
  topic_type: string;
  decision_mode?: string | null;
  review_status: string;
  reason_codes: string[];
  review_notes?: string | null;
  reviewed_at?: string | null;
}

interface V2DecisionBatchApiResponse {
  batch_id: string;
  workspace_id: string;
  brand_id: string;
  brand_state_snapshot_id: string;
  brand_policy_config_id: string;
  objective: string;
  exploration_mode: string;
  requested_slot_count: number;
  candidate_count: number;
  chosen_count: number;
  created_at: string;
  items: V2DecisionBatchItemApiResponse[];
}

interface V2PublishRecordApiResponse {
  publish_record_id: string;
  brand_id: string;
  channel_id: string;
  channel_label: string;
  title: string;
  topic_pool_item_id?: string | null;
  decision_event_id?: string | null;
  decision_batch_id?: string | null;
  decision_source: string;
  publish_status: string;
  published_at?: string | null;
  creative_variant?: string | null;
  created_at: string;
}

interface V2PublishRecordListApiResponse {
  items: V2PublishRecordApiResponse[];
}

interface V2PerformanceSnapshotApiResponse {
  performance_snapshot_id: string;
  publish_record_id: string;
  publish_title: string;
  observation_window_hours: number;
  snapshot_at: string;
  reward_version: string;
  impressions: number;
  clicks: number;
  engagement_rate: number;
  conversion_proxy_label: string;
  short_term_reward: number;
  composite_reward: number;
}

interface V2PerformanceSnapshotListApiResponse {
  items: V2PerformanceSnapshotApiResponse[];
}

interface V2EvaluationRunApiResponse {
  evaluation_run_id: string;
  brand_id: string;
  evaluation_type: string;
  policy_name: string;
  policy_version: string;
  status: string;
  sample_count: number;
  summary: Record<string, unknown>;
  slices: Array<{
    slice_key: string;
    slice_value: string;
    sample_count: number;
    metrics: Record<string, unknown>;
  }>;
  created_at: string;
  finished_at?: string | null;
}

interface V2DiscoveryTaskApiResponse {
  task_id: string;
  topic: string;
  query_generation_version: string;
  query_generation_source: string;
  token?: string | null;
  expires_at?: string | null;
  expanded_queries: Array<{
    query_id: string;
    category: string;
    query_text: string;
    order: number;
  }>;
  hotspot_status: string;
  hotspot_generated_at?: string | null;
  hotspot_error_message?: string;
  hotspots: Array<{
    metric: string;
    items: Array<{
      title: string;
      source_url: string;
      author: string;
      excerpt?: string;
      likes: number;
      comments: number;
      collections: number;
      query_sources?: string[];
    }>;
  }>;
}

export interface BrandOption {
  id: string;
  name: string;
}

export interface CreateBrandChannelInput {
  platform: string;
  externalAccountId?: string;
  accountName?: string;
  profileUrl?: string;
  metadata?: Record<string, unknown>;
}

export interface CreateBrandInput {
  name: string;
  category?: string;
  stage: "seed" | "growth" | "mature";
  audienceSummary?: string;
}

export interface UpdateBrandInput {
  name?: string;
  category?: string;
  stage?: "seed" | "growth" | "mature";
  targetAudience?: Record<string, unknown>;
  brandExpression?: Record<string, unknown>;
  businessGoals?: Record<string, unknown>;
}

export interface BrandProfileData {
  id: string;
  name: string;
  category?: string;
  stage: "seed" | "growth" | "mature";
  targetAudience: Record<string, unknown>;
  brandExpression: Record<string, unknown>;
  businessGoals: Record<string, unknown>;
  updatedAt: string;
}

export interface BrandsPageData {
  brands: Brand[];
  stats: {
    activeBrands: number;
    connectedAccounts: number;
  };
  source: "live";
}

export interface BrandDetailPageData {
  brand: BrandProfileData;
  channels: BrandChannelOption[];
  latestExtensionCaptureSession?: ExtensionCaptureSessionState;
  latestDataImportPreview?: DataImportPreviewState;
  source: "live";
}

export interface TopicPoolPageData {
  brand: {
    id: string;
    name: string;
    stage: Brand["stage"];
    targetAudience: string;
  };
  stats: {
    totalCandidates: number;
    bestScore: number;
    lastRefreshAt: string | null;
  };
  topics: Topic[];
  source: "live";
}

export interface DataSourcesPageData {
  brand: BrandProfileData;
  channels: BrandChannelOption[];
  latestExtensionCaptureSession?: ExtensionCaptureSessionState;
  latestDataImportPreview?: DataImportPreviewState;
  recentIngestionRuns: Array<{
    id: string;
    type: string;
    sourceLabel: string;
    status: string;
    createdAt: string;
    importedCount: number;
    dedupedCount: number;
  }>;
  source: "live";
}

export interface DataProcessingPageData extends DataSourcesPageData {}

export interface DiscoveryWorkspaceData {
  taskId?: string;
  topic: string;
  queryGenerationVersion: string;
  queryGenerationSource: string;
  token?: string;
  expandedQueries: Array<{
    id: string;
    text: string;
    category: string;
  }>;
  hotspots: Array<{
    metric: string;
    items: Array<{
      title: string;
      sourceUrl: string;
      author: string;
      likes: number;
      comments: number;
      collections: number;
    }>;
  }>;
  statusLabel: string;
}

export interface DecisionsPageData {
  batchId?: string;
  stats: {
    expectedReward: number;
    selectedCount: number;
    explorationProbability: number;
  };
  items: DecisionItem[];
  source: "live";
}

export interface PublishPageData {
  records: PublishRecord[];
  source: "live";
}

export interface PerformancePageData {
  stats: {
    averageEngagementRate: number;
    compositeReward168h: number;
  };
  metrics: PerformanceMetric[];
  source: "live";
}

export interface EvaluationPageData {
  evaluationRunId?: string;
  summary: {
    comparisonLabel: string;
    sampleSize: number;
    coverage: number;
    essRatio: number;
    uplift: number;
    note: string;
  };
  slices: EvaluationSlice[];
  source: "live";
}

// Workspace identity is resolved at runtime via GET /workspaces/default.
// WorkspaceProvider calls setWorkspaceContext() on mount before any data fetches.
let _workspaceId = "";
let _userId = "";

export function setWorkspaceContext(workspaceId: string, userId: string): void {
  _workspaceId = workspaceId;
  _userId = userId;
}

export function getWorkspaceContext(): { workspaceId: string; userId: string } {
  return {
    workspaceId: _workspaceId,
    userId: _userId
  };
}

export async function getDefaultWorkspace(): Promise<{ workspace_id: string; user_id: string }> {
  const response = await fetch(`${RUNTIME_BASE_URL}/workspaces/default`, { cache: "no-store" });
  if (!response.ok) {
    const error = new Error(`request failed: ${response.status}`) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  return response.json();
}

export async function initializeWorkspaceContext(): Promise<{ workspace_id: string; user_id: string }> {
  // Check runtime health before fetching workspace identity.
  let health: RuntimeHealthResponse;
  try {
    const healthRes = await fetch(`${RUNTIME_BASE_URL}/health`, { cache: "no-store" });
    if (!healthRes.ok) {
      throw new Error(`Agent Runtime 返回异常状态: ${healthRes.status}`);
    }
    health = await healthRes.json();
  } catch {
    throw new Error(
      `Agent Runtime 未启动或不可达 (${RUNTIME_BASE_URL})。请先启动本地 runtime。`
    );
  }
  if (health.api_contract !== REQUIRED_API_CONTRACT) {
    throw new Error(
      `Agent Runtime API 契约不匹配：当前 ${health.api_contract}，需要 ${REQUIRED_API_CONTRACT}。请升级本地 runtime。`
    );
  }
  if (compareSemver(health.version, MIN_BACKEND_VERSION) < 0) {
    throw new Error(
      `Agent Runtime 版本过低：当前 ${health.version}，需要 ${MIN_BACKEND_VERSION} 或更高。请升级本地 runtime。`
    );
  }
  void fetch(`${RUNTIME_BASE_URL}/runtime/prewarm`, {
    method: "POST",
    cache: "no-store"
  }).catch(() => {
    // Prewarm failures are surfaced by workflow progress if the first run still needs embedding.
  });

  const workspace = await getDefaultWorkspace();
  setWorkspaceContext(workspace.workspace_id, workspace.user_id);
  return workspace;
}

function compareSemver(current: string, minimum: string): number {
  const currentParts = current.split(".").map((part) => Number.parseInt(part, 10) || 0);
  const minimumParts = minimum.split(".").map((part) => Number.parseInt(part, 10) || 0);
  for (let index = 0; index < Math.max(currentParts.length, minimumParts.length); index += 1) {
    const diff = (currentParts[index] ?? 0) - (minimumParts[index] ?? 0);
    if (diff !== 0) return diff > 0 ? 1 : -1;
  }
  return 0;
}

export function getRuntimeApiErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return "unknown error";
}

export function getRuntimeApiErrorKind(error: unknown): "offline" | "version" | "contract" | "api" {
  const message = getRuntimeApiErrorMessage(error);
  if (message.includes("未启动") || message.includes("不可达") || message.includes("Failed to fetch")) {
    return "offline";
  }
  if (message.includes("版本过低")) {
    return "version";
  }
  if (message.includes("API 契约不匹配")) {
    return "contract";
  }
  return "api";
}

function getApiConfig() {
  return {
    baseUrl: RUNTIME_BASE_URL,
    workspaceId: _workspaceId,
    userId: _userId,
    authToken:
      process.env.NEXT_PUBLIC_XHS_AUTH_TOKEN?.trim() ||
      process.env.XHS_AUTH_TOKEN?.trim()
  };
}

function summarizeTargetAudience(value: Record<string, unknown>) {
  const ageRanges = Array.isArray(value.age_ranges)
    ? value.age_ranges.filter((item): item is string => typeof item === "string")
    : [];
  const genderSkew = typeof value.gender_skew === "string" ? value.gender_skew : "";
  const summary = typeof value.summary === "string" ? value.summary.trim() : "";
  const genderLabel =
    genderSkew === "female" ? "女性" : genderSkew === "male" ? "男性" : genderSkew ? "泛人群" : "";
  return [ageRanges.join("/"), genderLabel, summary].filter(Boolean).join(" ") || "待补充";
}

function mapStage(stage: string): Brand["stage"] {
  if (stage === "growth" || stage === "Growth") {
    return "Growth";
  }
  if (stage === "scaled" || stage === "mature" || stage === "Mature") {
    return "Mature";
  }
  return "Seed";
}

function normalizeBrandStage(stage: string): BrandProfileData["stage"] {
  if (stage === "growth" || stage === "Growth") {
    return "growth";
  }
  if (stage === "scaled" || stage === "mature" || stage === "Mature") {
    return "mature";
  }
  return "seed";
}

function mapTopicType(topicType: string): Topic["type"] {
  if (topicType === "scenario") {
    return "Scenario";
  }
  if (topicType === "problem") {
    return "Problem";
  }
  if (topicType === "audience") {
    return "Audience";
  }
  if (topicType === "competitor") {
    return "Competitor";
  }
  if (topicType === "trend") {
    return "Trend";
  }
  return "Core";
}

function mapSignalType(signalType: string | undefined): Topic["source"] {
  if (signalType === "gap") {
    return "Gap";
  }
  if (signalType === "trend") {
    return "Trend";
  }
  if (signalType === "owned_performance") {
    return "OwnedPerformance";
  }
  return "Engagement";
}

async function fetchJson<T>(path: string) {
  return requestJson<T>(path);
}

async function requestJson<T>(
  path: string,
  options?: {
    method?: string;
    body?: unknown;
  }
) {
  const config = getApiConfig();
  if (!config.workspaceId) {
    throw new Error("Workspace not initialized. Ensure WorkspaceProvider has mounted.");
  }
  const headers = new Headers({
    "Content-Type": "application/json",
    "X-Workspace-Id": config.workspaceId,
    "X-User-Id": config.userId
  });
  if (config.authToken) {
    headers.set("Authorization", `Bearer ${config.authToken}`);
  }

  const response = await fetch(`${config.baseUrl}${path}`, {
    method: options?.method ?? "GET",
    headers,
    body: options?.body === undefined ? undefined : JSON.stringify(options.body),
    cache: "no-store"
  });
  if (!response.ok) {
    const fallback = new Error(`request failed: ${response.status}`) as Error & { status?: number };
    fallback.status = response.status;
    try {
      const payload = (await response.json()) as { error_message?: string };
      if (typeof payload.error_message === "string" && payload.error_message.trim()) {
        const error = new Error(payload.error_message) as Error & { status?: number };
        error.status = response.status;
        throw error;
      }
    } catch (error) {
      if (error instanceof Error && error.message !== `request failed: ${response.status}`) {
        throw error;
      }
    }
    throw fallback;
  }
  return (await response.json()) as T;
}

function mapDiscoveryTaskResponse(response: V2DiscoveryTaskApiResponse): DiscoveryWorkspaceData {
  const hotspotStatusMap: Record<string, string> = {
    empty: "等待更新热榜",
    ready: "热榜已更新",
    error: response.hotspot_error_message?.trim() || "热榜更新失败"
  };
  return {
    taskId: response.task_id,
    topic: response.topic,
    queryGenerationVersion: response.query_generation_version,
    queryGenerationSource: response.query_generation_source,
    token: response.token ?? undefined,
    expandedQueries: response.expanded_queries.map((item) => ({
      id: item.query_id,
      text: item.query_text,
      category: item.category
    })),
    hotspots: response.hotspots.map((list) => ({
      metric: list.metric,
      items: list.items.map((item) => ({
        title: item.title,
        sourceUrl: item.source_url,
        author: item.author,
        likes: item.likes,
        comments: item.comments,
        collections: item.collections
      }))
    })),
    statusLabel: hotspotStatusMap[response.hotspot_status] ?? "搜索观察任务已更新"
  };
}

function mapDecisionMode(mode: string | undefined | null): DecisionItem["mode"] {
  return mode === "Exploration" ? "Exploration" : "Exploitation";
}

function mapDecisionResponseToPageData(response: V2DecisionBatchApiResponse): DecisionsPageData {
  const items = response.items.map((item) => ({
    slotIndex: item.slot_index,
    topicId: item.topic_pool_item_id,
    decisionEventId: item.decision_event_id ?? undefined,
    title: item.title,
    angle: item.angle,
    hypothesis: item.hypothesis,
    topicType: mapTopicType(item.topic_type),
    strategyScore: item.score,
    mode: mapDecisionMode(item.decision_mode),
    expectedReward: item.score,
    reviewStatus: item.review_status,
    reviewNotes: item.review_notes ?? undefined,
    actionLabel:
      item.review_status === "accept"
        ? "已接受"
        : item.review_status === "reject"
          ? "已拒绝"
          : item.review_status === "edit_and_accept"
            ? "已编辑接受"
            : "待处理"
  }));
  const explorationCount = items.filter((item) => item.mode === "Exploration").length;
  return {
    batchId: response.batch_id,
    stats: {
      expectedReward: items.reduce((sum, item) => sum + item.expectedReward, 0) / Math.max(items.length, 1),
      selectedCount: response.chosen_count,
      explorationProbability: explorationCount / Math.max(items.length, 1)
    },
    items,
    source: "live"
  };
}

function normalizePublishStatus(status: string): PublishRecord["status"] {
  if (status === "published") {
    return "Published";
  }
  if (status === "failed") {
    return "Failed";
  }
  return "Pending";
}

export async function getBrandsPageData(): Promise<BrandsPageData> {
  // P1-1 live path:
  // foundation/master-data endpoints are available in the current phase.
  const brandResponse = await fetchJson<V2BrandListResponse>("/brands");
  const brands = await Promise.all(
    brandResponse.items.map(async (brand) => {
      const channels = await fetchJson<V2BrandChannelListResponse>(`/brands/${brand.id}/channels`);
      return {
        id: brand.id,
        name: brand.name,
        stage: mapStage(brand.stage),
        targetAudience: summarizeTargetAudience(brand.target_audience),
        status: "Active" as const,
        accounts: channels.items.length
      };
    })
  );

  return {
    brands,
    stats: {
      activeBrands: brands.filter((brand) => brand.status === "Active").length,
      connectedAccounts: brands.reduce((sum, brand) => sum + brand.accounts, 0)
    },
    source: "live"
  };
}

export async function createBrand(input: CreateBrandInput): Promise<BrandOption> {
  const response = await requestJson<V2BrandApiResponse>("/brands", {
    method: "POST",
    body: {
      name: input.name,
      category: input.category,
      stage: input.stage,
      target_audience: input.audienceSummary ? { summary: input.audienceSummary } : {},
      brand_voice: {},
      goals: {}
    }
  });

  return {
    id: response.id,
    name: response.name
  };
}

export async function createBrandChannel(
  brandId: string,
  input: CreateBrandChannelInput
): Promise<BrandChannelOption> {
  const response = await requestJson<{
    id: string;
    platform: string;
    account_name?: string | null;
    profile_url?: string | null;
  }>(`/brands/${brandId}/channels`, {
    method: "POST",
    body: {
      platform: input.platform,
      external_account_id: input.externalAccountId,
      account_name: input.accountName,
      profile_url: input.profileUrl,
      metadata: input.metadata ?? {}
    }
  });

  return {
    id: response.id,
    platform: response.platform ?? "xiaohongshu",
    accountName: response.account_name ?? undefined,
    profileUrl: response.profile_url ?? undefined
  };
}

export async function updateBrand(
  brandId: string,
  input: UpdateBrandInput
): Promise<BrandProfileData> {
  const response = await requestJson<V2BrandApiResponse>(`/brands/${brandId}`, {
    method: "PATCH",
    body: {
      name: input.name,
      category: input.category,
      stage: input.stage,
      target_audience: input.targetAudience,
      brand_voice: input.brandExpression,
      goals: input.businessGoals
    }
  });

  return {
    id: response.id,
    name: response.name,
    category: response.category ?? undefined,
    stage: normalizeBrandStage(response.stage),
    targetAudience: response.target_audience,
    brandExpression: response.brand_voice,
    businessGoals: response.goals,
    updatedAt: response.updated_at
  };
}

export async function updateBrandChannel(
  brandId: string,
  channelId: string,
  input: CreateBrandChannelInput
): Promise<BrandChannelOption> {
  const response = await requestJson<{
    id: string;
    platform: string;
    account_name?: string | null;
    profile_url?: string | null;
  }>(`/brands/${brandId}/channels/${channelId}`, {
    method: "PATCH",
    body: {
      external_account_id: input.externalAccountId,
      account_name: input.accountName,
      profile_url: input.profileUrl,
      metadata: input.metadata ?? {}
    }
  });

  return {
    id: response.id,
    platform: response.platform ?? "xiaohongshu",
    accountName: response.account_name ?? undefined,
    profileUrl: response.profile_url ?? undefined
  };
}

export async function getBrandOptions(): Promise<BrandOption[]> {
  const data = await getBrandsPageData();
  return data.brands.map((brand) => ({ id: brand.id, name: brand.name }));
}

export async function getBrandDetailPageData(brandId: string): Promise<BrandDetailPageData> {
  const workspace = await fetchJson<V2BrandWorkspaceApiResponse>(`/brands/${brandId}/workspace`);

  return {
    brand: {
      id: workspace.brand.id,
      name: workspace.brand.name,
      category: workspace.brand.category ?? undefined,
      stage: normalizeBrandStage(workspace.brand.stage),
      targetAudience: workspace.brand.target_audience,
      brandExpression: workspace.brand.brand_voice,
      businessGoals: workspace.brand.goals,
      updatedAt: workspace.brand.updated_at
    },
    channels: workspace.channels.map((channel) => ({
      id: channel.id,
      platform: channel.platform ?? "xiaohongshu",
      accountName: channel.account_name ?? undefined,
      profileUrl: channel.profile_url ?? undefined
    })),
    latestExtensionCaptureSession: workspace.latest_extension_capture_session
      ? mapExtensionCaptureSession(workspace.latest_extension_capture_session)
      : undefined,
    latestDataImportPreview: workspace.latest_data_import_preview
      ? mapDataImportPreview(workspace.latest_data_import_preview)
      : undefined,
    source: "live"
  };
}

function mapBrandWorkspace(workspace: V2BrandWorkspaceApiResponse): DataSourcesPageData {
  return {
    brand: {
      id: workspace.brand.id,
      name: workspace.brand.name,
      category: workspace.brand.category ?? undefined,
      stage: normalizeBrandStage(workspace.brand.stage),
      targetAudience: workspace.brand.target_audience,
      brandExpression: workspace.brand.brand_voice,
      businessGoals: workspace.brand.goals,
      updatedAt: workspace.brand.updated_at
    },
    channels: workspace.channels.map((channel) => ({
      id: channel.id,
      platform: channel.platform ?? "xiaohongshu",
      accountName: channel.account_name ?? undefined,
      profileUrl: channel.profile_url ?? undefined
    })),
    latestExtensionCaptureSession: workspace.latest_extension_capture_session
      ? mapExtensionCaptureSession(workspace.latest_extension_capture_session)
      : undefined,
    latestDataImportPreview: workspace.latest_data_import_preview
      ? mapDataImportPreview(workspace.latest_data_import_preview)
      : undefined,
    recentIngestionRuns: (workspace.recent_ingestion_runs ?? []).map((run) => ({
      id: run.ingestion_run_id,
      type: run.entry_type === "source_sync" ? "浏览器采集" : "历史数据上传",
      sourceLabel: run.source_adapter ?? run.source_type,
      status: run.status,
      createdAt: run.created_at,
      importedCount: Number(run.stats.imported_item_count ?? 0),
      dedupedCount: Number(run.stats.deduped_item_count ?? 0)
    })),
    source: "live"
  };
}

export async function getDataSourcesPageData(brandId: string): Promise<DataSourcesPageData> {
  const workspace = await fetchJson<V2BrandWorkspaceApiResponse>(`/brands/${brandId}/workspace`);
  return mapBrandWorkspace(workspace);
}

export async function getDataProcessingPageData(brandId: string): Promise<DataProcessingPageData> {
  const workspace = await fetchJson<V2BrandWorkspaceApiResponse>(`/brands/${brandId}/workspace`);
  return mapBrandWorkspace(workspace);
}

export async function createDiscoveryTask(brandId: string, topic: string): Promise<DiscoveryWorkspaceData> {
  const created = await requestJson<V2DiscoveryTaskApiResponse>(`/brands/${brandId}/discovery/tasks`, {
    method: "POST",
    body: { topic }
  });
  return mapDiscoveryTaskResponse(created);
}

export async function getDiscoveryTaskSnapshot(brandId: string, taskId: string): Promise<DiscoveryWorkspaceData> {
  const snapshot = await requestJson<V2DiscoveryTaskApiResponse>(
    `/brands/${brandId}/discovery/tasks/${taskId}`
  );
  return mapDiscoveryTaskResponse(snapshot);
}

export async function refreshDiscoveryHotspots(brandId: string, taskId: string): Promise<DiscoveryWorkspaceData> {
  const response = await requestJson<V2DiscoveryTaskApiResponse>(
    `/brands/${brandId}/discovery/tasks/${taskId}/hotspots/refresh`,
    {
      method: "POST"
    }
  );
  return mapDiscoveryTaskResponse(response);
}

export async function addDiscoveryQuery(
  brandId: string,
  taskId: string,
  text: string
): Promise<DiscoveryWorkspaceData> {
  const response = await requestJson<V2DiscoveryTaskApiResponse>(
    `/brands/${brandId}/discovery/tasks/${taskId}/queries`,
    {
      method: "POST",
      body: { text }
    }
  );
  return mapDiscoveryTaskResponse(response);
}

export async function deleteDiscoveryQuery(
  brandId: string,
  taskId: string,
  queryId: string
): Promise<DiscoveryWorkspaceData> {
  const response = await requestJson<V2DiscoveryTaskApiResponse>(
    `/brands/${brandId}/discovery/tasks/${taskId}/queries/${encodeURIComponent(queryId)}`,
    {
      method: "DELETE"
    }
  );
  return mapDiscoveryTaskResponse(response);
}

export async function triggerBrandSourceSync(
  brandId: string,
  payload: BrandSourceSyncPayload
): Promise<IngestionAcceptedResult> {
  return requestJson<IngestionAcceptedResult>(`/brands/${brandId}/source-syncs`, {
    method: "POST",
    body: payload
  });
}

export async function triggerBrandDataImport(
  brandId: string,
  payload: BrandDataImportPayload
): Promise<IngestionAcceptedResult> {
  return requestJson<IngestionAcceptedResult>(`/brands/${brandId}/data-imports`, {
    method: "POST",
    body: payload
  });
}

function mapExtensionCaptureSession(
  response: V2ExtensionCaptureSessionApiResponse
): ExtensionCaptureSessionState {
  return {
    captureSessionId: response.capture_session_id,
    captureToken: response.capture_token ?? undefined,
    status: response.status,
    expiresAt: response.expires_at,
    capturedAt: response.captured_at ?? undefined,
    previewPayload: response.preview_payload ?? undefined,
    ingestionReceipt: response.ingestion_receipt ?? undefined,
    errorSummary: response.error_summary ?? undefined
  };
}

function mapDataImportPreview(response: V2DataImportPreviewApiResponse): DataImportPreviewState {
  return {
    previewId: response.preview_id,
    fileName: response.file_name,
    status: response.status,
    uploadedAt: response.uploaded_at,
    parsedRowCount: response.parsed_row_count,
    previewPayload: response.preview_payload ?? undefined,
    ingestionReceipt: response.ingestion_receipt ?? undefined,
    fieldErrors: response.field_errors ?? [],
    errorSummary: response.error_summary ?? undefined
  };
}

export async function createExtensionCaptureSession(
  brandId: string,
  channelId?: string
): Promise<ExtensionCaptureSessionState> {
  const response = await requestJson<V2ExtensionCaptureSessionApiResponse>(
    `/brands/${brandId}/extension-capture-sessions`,
    {
      method: "POST",
      body: { channel_id: channelId }
    }
  );
  return mapExtensionCaptureSession(response);
}

export async function getExtensionCaptureSession(
  brandId: string,
  captureSessionId: string
): Promise<ExtensionCaptureSessionState> {
  const response = await requestJson<V2ExtensionCaptureSessionApiResponse>(
    `/brands/${brandId}/extension-capture-sessions/${captureSessionId}`
  );
  return mapExtensionCaptureSession(response);
}

export async function submitExtensionCapture(
  captureSessionId: string,
  captureToken: string,
  capturePayload: BrandSourceSyncPayload["capture_payload"]
): Promise<ExtensionCaptureSessionState> {
  const response = await requestJson<V2ExtensionCaptureSessionApiResponse>("/extension-captures", {
    method: "POST",
    body: {
      capture_session_id: captureSessionId,
      capture_token: captureToken,
      capture_payload: capturePayload
    }
  });
  return mapExtensionCaptureSession(response);
}

export async function retryExtensionCaptureSessionSync(
  brandId: string,
  captureSessionId: string
): Promise<ExtensionCaptureSessionState> {
  const response = await requestJson<V2ExtensionCaptureSessionApiResponse>(
    `/brands/${brandId}/extension-capture-sessions/${captureSessionId}/retry-sync`,
    {
      method: "POST"
    }
  );
  return mapExtensionCaptureSession(response);
}

export async function createDataImportPreview(
  brandId: string,
  fileName: string,
  payload: BrandDataImportPayload & {
    fileContentBase64?: string;
    fileMimeType?: string;
  }
): Promise<DataImportPreviewState> {
  const response = await requestJson<V2DataImportPreviewApiResponse>(
    `/brands/${brandId}/data-import-previews`,
    {
      method: "POST",
      body: {
        file_name: fileName,
        import_type: payload.import_type,
        platform: payload.platform,
        rows: payload.rows,
        file_content_base64: payload.fileContentBase64,
        file_mime_type: payload.fileMimeType
      }
    }
  );
  return mapDataImportPreview(response);
}

export async function getDataImportPreview(
  brandId: string,
  previewId: string
): Promise<DataImportPreviewState> {
  const response = await requestJson<V2DataImportPreviewApiResponse>(
    `/brands/${brandId}/data-import-previews/${previewId}`
  );
  return mapDataImportPreview(response);
}

export async function retryDataImportPreviewSync(
  brandId: string,
  previewId: string
): Promise<DataImportPreviewState> {
  const response = await requestJson<V2DataImportPreviewApiResponse>(
    `/brands/${brandId}/data-import-previews/${previewId}/retry-sync`,
    {
      method: "POST"
    }
  );
  return mapDataImportPreview(response);
}

export async function getTopicPoolPageData(brandId: string): Promise<TopicPoolPageData> {
  const response = await fetchJson<V2TopicPoolListApiResponse>(`/brands/${brandId}/topic-pool`);
  return {
    brand: {
      id: response.brand.id,
      name: response.brand.name,
      stage: mapStage(response.brand.stage),
      targetAudience: summarizeTargetAudience(response.brand.target_audience)
    },
    stats: {
      totalCandidates: response.stats.total_candidate_count,
      bestScore: response.stats.best_score,
      lastRefreshAt: response.stats.last_refresh_at
    },
    topics: response.items.map((item) => ({
      id: item.id,
      title: item.title,
      type: mapTopicType(item.topic_type),
      hypothesis: item.hypothesis,
      score: item.final_score,
      source: mapSignalType(item.evidence_summary?.dominant_signal_type),
      angle: item.angle,
      evidenceCount: item.evidence_summary?.source_count ?? 0,
      updatedAt: item.updated_at,
      status: item.status,
      scoreBreakdown: item.score_breakdown
        ? {
            noveltyScore: item.score_breakdown.novelty_score ?? 0,
            fitScore: item.score_breakdown.fit_score ?? 0,
            trendScore: item.score_breakdown.trend_score ?? 0,
            historicalRewardScore: item.score_breakdown.historical_reward_score ?? 0,
            policyScore: item.score_breakdown.policy_score ?? 0,
            finalScore: item.score_breakdown.final_score ?? item.final_score,
            sourceCount: item.score_breakdown.source_count ?? item.evidence_summary?.source_count ?? 0,
            brandFitCheck: item.score_breakdown.brand_fit_check ?? undefined,
            brandFitViolations: item.score_breakdown.brand_fit_violations ?? []
          }
        : undefined,
      evidenceProvenance: item.evidence_provenance?.map((entry) => ({
        itemId: entry.item_id,
        sourceUrl: entry.source_url ?? undefined,
        originalTitle: entry.original_title,
        signalType: entry.signal_type,
        contributionWeight: entry.contribution_weight,
        signalScore: entry.signal_score,
        likes: entry.likes,
        comments: entry.comments,
        collects: entry.collects,
        shares: entry.shares
      }))
    })),
    source: "live"
  };
}

export async function triggerTopicPoolRefresh(brandId: string): Promise<TopicPoolRefreshResult> {
  return requestJson<V2TopicPoolRefreshApiResponse>(
    `/brands/${brandId}/topic-pool/refresh`,
    {
      method: "POST",
      body: { archive_threshold_days: 60 }
    }
  );
}

export async function runDecisionBatch(brandId: string): Promise<V2DecisionBatchApiResponse> {
  return requestJson<V2DecisionBatchApiResponse>(`/brands/${brandId}/decisions/run`, {
    method: "POST",
    body: { requested_slot_count: 3, objective: "topic_recommendation", exploration_mode: "balanced" }
  });
}

export async function getDecisionBatch(batchId: string): Promise<V2DecisionBatchApiResponse> {
  return fetchJson<V2DecisionBatchApiResponse>(`/decision-batches/${batchId}`);
}

export async function getLatestDecisionBatch(brandId: string): Promise<V2DecisionBatchApiResponse> {
  return fetchJson<V2DecisionBatchApiResponse>(`/brands/${brandId}/decision-batches/latest`);
}

export async function reviewDecisionBatchItem(
  batchId: string,
  slotIndex: number,
  payload: {
    review_action: "accept" | "reject" | "edit_and_accept";
    edited_title?: string;
    edited_angle?: string;
    edited_hypothesis?: string;
    review_notes?: string;
  }
) {
  return requestJson(`/decision-batches/${batchId}/items/${slotIndex}`, {
    method: "PATCH",
    body: payload
  });
}

export async function getDecisionsPageData(
  brandId: string,
  options?: { batchId?: string | null }
): Promise<DecisionsPageData> {
  try {
    const response = options?.batchId
      ? await getDecisionBatch(options.batchId)
      : await getLatestDecisionBatch(brandId);
    return mapDecisionResponseToPageData(response);
  } catch (error) {
    const status = (error as Error & { status?: number }).status;
    if (status === 404) {
      return {
        batchId: options?.batchId ?? undefined,
        stats: {
          expectedReward: 0,
          selectedCount: 0,
          explorationProbability: 0
        },
        items: [],
        source: "live"
      };
    }
    throw error;
  }
}

export async function getPublishPageData(_brandId: string): Promise<PublishPageData> {
  const response = await fetchJson<V2PublishRecordListApiResponse>(`/brands/${_brandId}/publish-records`);
  return {
    records: response.items.map((item) => ({
      id: item.publish_record_id,
      title: item.title,
      channel: item.channel_label,
      publishedAt: item.published_at ?? item.created_at,
      decisionSource: item.decision_source,
      decisionEventId: item.decision_event_id ?? undefined,
      decisionBatchId: item.decision_batch_id ?? undefined,
      topicPoolItemId: item.topic_pool_item_id ?? undefined,
      status: normalizePublishStatus(item.publish_status)
    })),
    source: "live"
  };
}

export async function getPerformancePageData(_brandId: string): Promise<PerformancePageData> {
  const response = await fetchJson<V2PerformanceSnapshotListApiResponse>(
    `/brands/${_brandId}/performance-snapshots`
  );
  const metrics = response.items.map((item) => ({
    performanceSnapshotId: item.performance_snapshot_id,
    publishRecordId: item.publish_record_id,
    topicTitle: item.publish_title,
    impressions: item.impressions,
    clicks: item.clicks,
    conversionProxyLabel: item.conversion_proxy_label,
    engagementRate: item.engagement_rate,
    rewardScore: item.composite_reward,
    publishTime: item.snapshot_at
  }));
  return {
    stats: {
      averageEngagementRate:
        metrics.reduce((sum, item) => sum + item.engagementRate, 0) / Math.max(metrics.length, 1),
      compositeReward168h:
        metrics.reduce((sum, item) => sum + item.rewardScore, 0) / Math.max(metrics.length, 1)
    },
    metrics,
    source: "live"
  };
}

export async function getEvaluationPageData(_brandId: string): Promise<EvaluationPageData> {
  try {
    const response = await fetchJson<V2EvaluationRunApiResponse>(`/brands/${_brandId}/evaluation-runs/latest`);
    const summary = response.summary ?? {};
    const candidateQuality =
      typeof summary.candidate_quality === "object" && summary.candidate_quality
        ? (summary.candidate_quality as Record<string, unknown>)
        : {};
    return {
      evaluationRunId: response.evaluation_run_id,
      summary: {
        comparisonLabel: `${response.policy_name} vs ${response.evaluation_type.toUpperCase()}`,
        sampleSize: response.sample_count,
        coverage: Number(summary.coverage_rate ?? 0),
        essRatio: Number(summary.ess_ratio ?? 0),
        uplift: Number(summary.delta_vs_baseline ?? 0),
        note: `Replay=${Number(summary.estimated_policy_value ?? 0).toFixed(3)} · SNIPS=${Number(summary.baseline_policy_value ?? 0).toFixed(3)} · Entropy=${Number(summary.exploration_entropy ?? 0).toFixed(2)} · Candidate pool=${Number(candidateQuality.candidate_pool_size ?? 0).toFixed(2)}`
      },
      slices: response.slices.map((item) => ({
        slice: `${item.slice_key}: ${item.slice_value}`,
        issue: `coverage=${Number(item.metrics.coverage_rate ?? 0).toFixed(2)} · value=${Number(item.metrics.estimated_policy_value ?? 0).toFixed(2)}`,
        action: "关注低覆盖切片并补充探索样本"
      })),
      source: "live"
    };
  } catch (error) {
    const status = (error as Error & { status?: number }).status;
    if (status === 404) {
      return {
        summary: {
          comparisonLabel: "",
          sampleSize: 0,
          coverage: 0,
          essRatio: 0,
          uplift: 0,
          note: "当前品牌还没有 evaluation run。"
        },
        slices: [],
        source: "live"
      };
    }
    throw error;
  }
}

export async function createPublishRecord(
  brandId: string,
  options?: {
    mode?: "manual" | "decision";
    titleHint?: string;
  }
) {
  const channels = await fetchJson<V2BrandChannelListResponse>(`/brands/${brandId}/channels`);
  const channel = channels.items[0];
  if (!channel) {
    throw new Error("当前品牌还没有渠道，请先在品牌配置中创建 channel。");
  }

  if (options?.mode === "decision") {
    const batch = await getLatestDecisionBatch(brandId);
    const item =
      batch.items.find((candidate) => candidate.review_status !== "reject") ??
      batch.items[0];
    if (!item) {
      throw new Error("当前品牌还没有可发布的决策结果。");
    }
    return requestJson<V2PublishRecordApiResponse>("/publish-records", {
      method: "POST",
      body: {
        brand_id: brandId,
        channel_id: channel.id,
        topic_pool_item_id: item.topic_pool_item_id,
        decision_event_id: item.decision_event_id,
        decision_batch_id: batch.batch_id,
        publish_status: "published",
        published_at: new Date().toISOString(),
        creative_variant: "decision_v1"
      }
    });
  }

  return requestJson<V2PublishRecordApiResponse>("/publish-records", {
    method: "POST",
    body: {
      brand_id: brandId,
      channel_id: channel.id,
      publish_status: "published",
      published_at: new Date().toISOString(),
      creative_variant: options?.titleHint ?? "manual_v1"
    }
  });
}

export async function importPerformanceSnapshot(brandId: string) {
  const publishData = await getPublishPageData(brandId);
  const latest = publishData.records[0];
  if (!latest) {
    throw new Error("当前品牌还没有 publish record，无法导入绩效。");
  }
  return requestJson<V2PerformanceSnapshotApiResponse>("/performance/import", {
    method: "POST",
    body: {
      publish_record_id: latest.id,
      observation_window_hours: 168,
      snapshot_at: new Date().toISOString(),
      reward_version: "reward_v1",
      metrics: {
        impressions: 12000,
        clicks: 850,
        likes: 320,
        comments: 28,
        collects: 96,
        shares: 31,
        follows_gained: 12,
        conversion_proxy: {
          value: 0.08,
          type: "store_click_rate",
          source: "manual_import"
        }
      }
    }
  });
}

export async function runEvaluation(brandId: string) {
  return requestJson<V2EvaluationRunApiResponse>("/evaluation-runs", {
    method: "POST",
    body: {
      brand_id: brandId,
      evaluation_type: "replay"
    }
  });
}

// ---------------------------------------------------------------------------
// Creator Thread & Workflow API
// Creator endpoints are workspace-scoped in the browser runtime so local
// artifacts do not leak across brands.
// ---------------------------------------------------------------------------

export interface CreatorThreadSummary {
  thread_id: string;
  workspace_id?: string | null;
  brand_id?: string | null;
  title: string;
  status: string;
  active_job_id: string | null;
  active_run_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkflowStartResult {
  thread_id: string;
  session_id: string;
  job_id: string;
  stage: string;
  run_id?: string | null;
  command_result?: Record<string, unknown> | null;
  active_run_snapshot?: WorkflowRunSnapshot | null;
  compatibility_mode?: "workflow-v2" | string | null;
}

export interface JobStatusResult {
  job_id: string;
  status: string;
  job_type: string;
  session_id: string;
  result: unknown;
}

async function creatorFetch<T>(
  path: string,
  options?: { method?: string; body?: unknown }
): Promise<T> {
  const config = getApiConfig();
  if (!config.workspaceId) {
    throw new Error("Workspace not initialized. Ensure WorkspaceProvider has mounted.");
  }
  const headers = new Headers({
    "X-Workspace-Id": config.workspaceId,
    "X-User-Id": config.userId
  });
  if (options?.body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  if (config.authToken) {
    headers.set("Authorization", `Bearer ${config.authToken}`);
  }
  const res = await fetch(`${RUNTIME_BASE_URL}${path}`, {
    method: options?.method ?? "GET",
    headers,
    body: options?.body !== undefined ? JSON.stringify(options.body) : undefined,
    cache: "no-store",
  });
  if (!res.ok) {
    const err = new Error(await runtimeErrorText(res, `${options?.method ?? "GET"} ${path} failed`)) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return res.json() as Promise<T>;
}

async function runtimeErrorText(response: Response, fallbackPrefix: string): Promise<string> {
  try {
    const payload = (await response.json()) as {
      error_code?: string;
      error_message?: string;
      suggested_action?: string;
    };
    if (payload.error_message) {
      return payload.suggested_action
        ? `${payload.error_message}。${payload.suggested_action}`
        : payload.error_message;
    }
  } catch {
    // Use the structured fallback below.
  }
  return `${fallbackPrefix}: ${response.status}`;
}

export interface CreatorMessage {
  message_id: string;
  thread_id: string;
  role: "user" | "assistant" | "system";
  text: string;
  message_type?: string;
  intent: string | null;
  linked_session_id: string | null;
  linked_job_id: string | null;
  run_id?: string | null;
  artifact_refs?: WorkflowArtifactRef[];
  created_at: string;
}

export interface CreatorThreadDetail {
  thread_id: string;
  workspace_id?: string | null;
  brand_id?: string | null;
  title: string;
  status: string;
  active_workflow_session_id: string | null;
  active_job_id: string | null;
  active_run_id?: string | null;
  accepted_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkflowRunSnapshot {
  run: {
    run_id: string;
    thread_id: string;
    status: string;
    phase: string;
    current_step: string | null;
    active_job_id?: string | null;
    active_job_type?: string | null;
  };
  steps: Array<{
    step_id: string;
    run_id: string;
    step_name: string;
    phase: string;
    status: string;
    checkpoint_json?: Record<string, unknown> | null;
  }>;
  child_tasks: Array<Record<string, unknown>>;
  artifacts: WorkflowArtifact[];
  constraints: Array<Record<string, unknown>>;
  active_job: Record<string, unknown> | null;
}

export interface WorkflowArtifact {
  artifact_id: string;
  artifact_type: string;
  artifact_version?: number | null;
  parent_artifact_id?: string | null;
  payload_mode?: "snapshot" | "patch" | string | null;
  payload_json?: Record<string, unknown> | null;
  materialized_payload_json?: Record<string, unknown> | null;
  summary_text?: string | null;
  status?: string | null;
}

export interface WorkflowArtifactRef {
  artifact_id: string;
  artifact_type?: string | null;
  artifact_version?: number | null;
  parent_artifact_id?: string | null;
  artifact?: WorkflowArtifact | null;
}

export async function listThreads(brandId?: string | null): Promise<CreatorThreadSummary[]> {
  const query = brandId ? `?brand_id=${encodeURIComponent(brandId)}` : "";
  const data = await creatorFetch<{ items: CreatorThreadSummary[] }>(`/threads${query}`);
  return data.items;
}

export async function getThread(threadId: string): Promise<{ thread: CreatorThreadDetail; messages: CreatorMessage[] }> {
  return creatorFetch(`/threads/${threadId}`);
}

export async function getThreadTimeline(threadId: string): Promise<{ thread: CreatorThreadDetail; messages: CreatorMessage[] }> {
  return creatorFetch(`/threads/${threadId}/timeline`);
}

export async function createThread(
  title?: string,
  brandId?: string | null
): Promise<{ thread_id: string; title: string; brand_id?: string | null }> {
  return creatorFetch("/threads", {
    method: "POST",
    body: { ...(title ? { title } : {}), ...(brandId ? { brand_id: brandId } : {}) },
  });
}

export async function renameThread(threadId: string, title: string): Promise<{ thread_id: string; title: string }> {
  return creatorFetch(`/threads/${threadId}`, {
    method: "PATCH",
    body: { title },
  });
}

export async function deleteThread(threadId: string): Promise<{ thread_id: string; deleted: boolean }> {
  return creatorFetch(`/threads/${threadId}`, { method: "DELETE" });
}

export async function appendThreadMessage(
  threadId: string,
  text: string
): Promise<{
  intent: string;
  job_action_result: unknown;
  command_result?: Record<string, unknown> | null;
  active_run_snapshot?: WorkflowRunSnapshot | null;
  updated_title?: string;
  assistant_reply?: string;
}> {
  const res = await creatorFetch<{
    message: unknown;
    intent: string;
    job_action_result: unknown;
    command_result?: Record<string, unknown> | null;
    active_run_snapshot?: WorkflowRunSnapshot | null;
    updated_title?: string;
    assistant_reply?: string;
  }>(`/threads/${threadId}/messages`, { method: "POST", body: { text } });
  return {
    intent: res.intent,
    job_action_result: res.job_action_result,
    command_result: res.command_result,
    active_run_snapshot: res.active_run_snapshot,
    updated_title: res.updated_title,
    assistant_reply: res.assistant_reply,
  };
}

export async function startThreadWorkflow(
  threadId: string,
  userQuery: string
): Promise<WorkflowStartResult> {
  return creatorFetch(`/threads/${threadId}/workflow`, {
    method: "POST",
    body: { user_query: userQuery },
  });
}

export async function getJobStatus(jobId: string): Promise<JobStatusResult> {
  return creatorFetch(`/jobs/${jobId}`);
}

export async function cancelJob(jobId: string): Promise<{ job_id: string; session_id: string; status: string }> {
  return creatorFetch(`/jobs/${jobId}/cancel`, { method: "POST" });
}

export async function resumeJob(jobId: string): Promise<{ job_id: string; session_id: string; status: string }> {
  return creatorFetch(`/jobs/${jobId}/resume`, { method: "POST" });
}

export async function enqueueGenerate(sessionId: string): Promise<{ session_id: string; job_id: string }> {
  return creatorFetch(`/sessions/${sessionId}/generate`, {
    method: "POST",
    body: {},
  });
}

export interface ThreadEventData {
  event_id: number;
  thread_id: string;
  session_id: string;
  job_id: string | null;
  stage: string | null;
  event_name: string;
  payload: { message?: string; progress?: number; error_code?: string | null; details?: unknown };
}

export interface WorkflowRunEventData {
  event_id: number;
  run_id: string;
  thread_id?: string;
  step_id?: string | null;
  child_task_id?: string | null;
  job_id?: string | null;
  event_type: string;
  event_level?: string;
  payload: { message?: string; progress?: number; error_code?: string | null; details?: unknown; [key: string]: unknown };
}

export function subscribeThreadEvents(
  threadId: string,
  handlers: {
    onProgress?: (data: ThreadEventData) => void;
    onCompleted?: (data: ThreadEventData) => void;
    onFailed?: (data: ThreadEventData) => void;
    onCancelled?: (data: ThreadEventData) => void;
    onStageChanged?: (data: ThreadEventData) => void;
  }
): EventSource {
  const es = new EventSource(`${RUNTIME_BASE_URL}/threads/${threadId}/events`);

  const parse = (e: Event): ThreadEventData => JSON.parse((e as MessageEvent).data) as ThreadEventData;

  es.addEventListener("workflow_task_progress", (e) => handlers.onProgress?.(parse(e)));
  es.addEventListener("workflow_task_completed", (e) => handlers.onCompleted?.(parse(e)));
  es.addEventListener("workflow_task_failed", (e) => handlers.onFailed?.(parse(e)));
  es.addEventListener("workflow_cancelled", (e) => handlers.onCancelled?.(parse(e)));
  es.addEventListener("workflow_stage_changed", (e) => handlers.onStageChanged?.(parse(e)));

  return es;
}

export async function getWorkflowRunSnapshot(
  runId: string,
  threadId?: string
): Promise<WorkflowRunSnapshot> {
  const query = threadId ? `?thread_id=${encodeURIComponent(threadId)}` : "";
  return creatorFetch(`/workflow-runs/${runId}/snapshot${query}`);
}

export function subscribeWorkflowRunEvents(
  runId: string,
  handlers: {
    onEvent?: (data: WorkflowRunEventData) => void;
    onCompleted?: (data: WorkflowRunEventData) => void;
    onFailed?: (data: WorkflowRunEventData) => void;
    onCancelled?: (data: WorkflowRunEventData) => void;
  }
): EventSource {
  const es = new EventSource(`${RUNTIME_BASE_URL}/workflow-runs/${runId}/events`);
  const parse = (e: Event): WorkflowRunEventData => JSON.parse((e as MessageEvent).data) as WorkflowRunEventData;
  const handle = (e: Event) => {
    const data = parse(e);
    handlers.onEvent?.(data);
    if (data.event_type === "run_completed" || data.event_type === "run_succeeded") handlers.onCompleted?.(data);
    if (data.event_type === "run_failed") handlers.onFailed?.(data);
    if (data.event_type === "run_cancelled" || data.event_type === "run_cancel_requested") handlers.onCancelled?.(data);
  };
  [
    "run_started",
    "steps_initialized",
    "embedding_initializing",
    "step_started",
    "step_completed",
    "step_retry_scheduled",
    "step_failed",
    "step_cancelled",
    "child_tasks_created",
    "child_task_started",
    "child_task_completed",
    "child_task_retry_scheduled",
    "child_task_failed",
    "artifact_attached",
    "constraint_added",
    "run_pause_requested",
    "run_resumed",
    "run_cancel_requested",
    "run_cancelled",
    "run_completed",
    "run_succeeded",
    "run_failed",
  ].forEach((eventName) => es.addEventListener(eventName, handle));
  es.onmessage = handle;
  return es;
}

export interface PublishCandidate {
  candidate_id: string;
  workspace_id?: string | null;
  brand_id?: string | null;
  thread_id: string;
  session_id: string;
  note_id: string;
  title: string;
  content: string;
  tags: string[];
  topic_type: string;
  core_hypothesis: string;
  score: number;
  score_type: "predicted" | string;
  source: string;
  created_at: string;
}

export interface CompleteThreadResponse {
  thread_id: string;
  status: string;
  publish_candidate_count: number;
}

export interface GeneratedNoteItem {
  note_id: string;
  title: string;
  content: string;
  tags: string[];
}

export interface ThreadResult {
  thread_id: string;
  session_id: string | null;
  strategy: { positioning: string; [key: string]: unknown } | null;
  notes: GeneratedNoteItem[];
}

export async function completeThread(threadId: string): Promise<CompleteThreadResponse> {
  return creatorFetch(`/threads/${threadId}/complete`, { method: "POST" });
}

export async function getPublishCandidates(filters?: {
  brandId?: string | null;
  threadId?: string | null;
  runId?: string | null;
}): Promise<{ items: PublishCandidate[] }> {
  const params = new URLSearchParams();
  if (filters?.brandId) params.set("brand_id", filters.brandId);
  if (filters?.threadId) params.set("thread_id", filters.threadId);
  if (filters?.runId) params.set("run_id", filters.runId);
  const query = params.toString() ? `?${params.toString()}` : "";
  return creatorFetch(`/publish-candidates${query}`);
}

export async function getThreadResult(threadId: string): Promise<ThreadResult> {
  return creatorFetch(`/threads/${threadId}/result`);
}
