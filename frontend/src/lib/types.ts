export interface NavigationItem {
  label: string;
  href: string;
  icon: string;
  active?: boolean;
}

export interface Brand {
  id: string;
  name: string;
  stage: "Seed" | "Growth" | "Mature";
  targetAudience: string;
  status: "Active";
  accounts: number;
}

export interface BrandChannelOption {
  id: string;
  platform: string;
  accountName?: string;
  profileUrl?: string;
}

export interface IngestionAcceptedResult {
  ingestion_run_id: string;
  entry_type: "source_sync" | "data_import";
  status: string;
  accepted_row_count?: number;
  imported_item_count?: number;
  deduped_item_count?: number;
}

export interface BrandSourceSyncItemPayload {
  platform_content_id?: string;
  note_id?: string;
  page_type?: string;
  query_text?: string;
  source_url?: string;
  raw_href?: string;
  title?: string;
  body_text?: string;
  visible_text_excerpt?: string;
  author_id?: string;
  author_handle?: string;
  author_name?: string;
  author_profile_url?: string;
  likes?: number;
  comments?: number;
  collects?: number;
  shares?: number;
  views?: number;
  follows_gained?: number;
  tags?: string[];
  published_at?: string;
  normalized_source_type?: string;
}

export interface BrandSourceSyncPayload {
  source_type: string;
  source_adapter?: string;
  channel_id?: string | null;
  capture_payload: {
    page_type: string;
    captured_at: string;
    items: BrandSourceSyncItemPayload[];
  };
}

export interface HistoricalImportRow {
  published_at: string;
  title: string;
  body_text: string;
  likes: number | string;
  collects: number | string;
  comments: number | string;
  platform_content_id?: string;
  source_url?: string;
  author_handle?: string;
  author_name?: string;
  shares?: number | string;
  tags?: string[] | string;
}

export interface BrandDataImportPayload {
  import_type: string;
  platform: string;
  rows: HistoricalImportRow[];
}

export interface ExtensionCaptureSessionState {
  captureSessionId: string;
  captureToken?: string;
  status: "pending_capture" | "syncing" | "accepted" | "failed" | "expired" | "captured";
  expiresAt: string;
  capturedAt?: string;
  previewPayload?: Record<string, unknown>;
  ingestionReceipt?: IngestionAcceptedResult;
  errorSummary?: {
    type?: string;
    message: string;
  };
}

export interface DataImportPreviewState {
  previewId: string;
  fileName: string;
  status: "uploaded" | "parsed" | "syncing" | "accepted" | "failed";
  uploadedAt: string;
  parsedRowCount: number;
  previewPayload?: Record<string, unknown>;
  ingestionReceipt?: IngestionAcceptedResult;
  fieldErrors: Array<Record<string, unknown>>;
  errorSummary?: {
    type?: string;
    message: string;
  };
}

export interface Topic {
  id: string;
  title: string;
  type: "Core" | "Scenario" | "Problem" | "Audience" | "Competitor" | "Trend";
  hypothesis: string;
  score: number;
  source: "Engagement" | "Gap" | "Trend" | "OwnedPerformance";
  angle?: string;
  evidenceCount?: number;
  updatedAt?: string;
  status?: string;
  evidenceSummary?: {
    sourceCount?: number;
    dominantSignalType?: string;
  };
  scoreBreakdown?: {
    noveltyScore: number;
    fitScore: number;
    trendScore: number;
    historicalRewardScore: number;
    policyScore: number;
    finalScore: number;
    sourceCount?: number;
    brandFitCheck?: boolean;
    brandFitViolations?: string[];
  };
  evidenceProvenance?: Array<{
    itemId: string;
    sourceUrl?: string;
    originalTitle: string;
    signalType: string;
    contributionWeight: number;
    signalScore: number;
    likes: number;
    comments: number;
    collects: number;
    shares: number;
  }>;
}

export interface TopicPoolRefreshResult {
  refresh_run_id: string;
  status: string;
  generated_item_count: number;
  archived_item_count: number;
  total_candidate_count: number;
  refreshed_at: string;
}

export interface DecisionItem {
  slotIndex: number;
  topicId: string;
  decisionEventId?: string;
  title: string;
  angle?: string;
  hypothesis?: string;
  topicType: Topic["type"];
  strategyScore: number;
  mode: "Exploitation" | "Exploration";
  expectedReward: number;
  reviewStatus: string;
  reviewNotes?: string;
  actionLabel: string;
}

export interface PublishRecord {
  id: string;
  title: string;
  channel: string;
  publishedAt: string;
  decisionSource: string;
  status: "Published" | "Pending" | "Failed";
}

export interface PerformanceMetric {
  topicTitle: string;
  impressions: number;
  clicks: number;
  conversionProxyLabel: string;
  engagementRate: number;
  rewardScore: number;
  publishTime: string;
}

export interface EvaluationSlice {
  slice: string;
  issue: string;
  action: string;
}
