export interface NavigationItem {
  label: string;
  href: string;
  icon?: string;
  badge?: string;
}

export interface Brand {
  id: string;
  name: string;
  category?: string;
  stage: "Seed" | "Growth" | "Mature" | "seed" | "growth" | "mature";
  targetAudience: string;
  status?: "Active" | "Paused";
  accounts?: number;
  createdAt?: string;
  updatedAt?: string;
  brandExpression?: Record<string, unknown>;
  businessGoals?: Record<string, unknown>;
}

export interface BrandChannelOption {
  id: string;
  platform: string;
  accountName?: string;
  profileUrl?: string;
}

export interface IngestionAcceptedResult {
  ingestion_run_id: string;
  status: string;
  imported_item_count?: number;
  accepted_row_count?: number;
  deduped_item_count?: number;
}

export interface ExtensionCaptureSessionState {
  captureSessionId: string;
  captureToken?: string;
  status: "pending_capture" | "captured" | "syncing" | "accepted" | "failed" | "expired";
  expiresAt: string;
  capturedAt?: string;
  previewPayload?: Record<string, unknown>;
  ingestionReceipt?: IngestionAcceptedResult;
  errorSummary?: { type?: string; message: string };
}

export interface DataImportPreviewState {
  previewId: string;
  fileName: string;
  status: "uploaded" | "parsed" | "syncing" | "accepted" | "failed";
  uploadedAt: string;
  parsedRowCount: number;
  previewPayload?: Record<string, unknown>;
  ingestionReceipt?: IngestionAcceptedResult;
  fieldErrors?: Array<Record<string, unknown>>;
  errorSummary?: { type?: string; message: string };
}

export interface BrandSourceSyncPayload {
  capture_payload: Record<string, unknown>;
}

export interface Topic {
  id: string;
  title: string;
  angle?: string;
  type: "Problem" | "Scenario" | "Audience" | "Competitor" | "Trend" | "Core";
  source: "Gap" | "Trend" | "OwnedPerformance" | "Engagement";
  status?: string;
  score: number;
  hypothesis?: string;
  evidenceCount?: number;
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
    originalTitle?: string;
    contributionWeight: number;
    signalScore: number;
    likes: number;
    comments: number;
    collects: number;
    shares: number;
    signalType: string;
  }>;
}

export interface DecisionItem {
  slotIndex: number;
  title: string;
  angle?: string;
  hypothesis?: string;
  strategyScore: number;
  mode: "Exploitation" | "Exploration";
  reviewStatus?: string;
  reviewNotes?: string;
}

export interface PublishRecord {
  id: string;
  title: string;
  channel: string;
  publishedAt: string;
  decisionSource: string;
  status: "Published" | string;
}

export interface PerformanceMetric {
  id: string;
  topicTitle: string;
  impressions: number;
  clicks: number;
  conversionProxyLabel: string;
  rewardScore: number;
}

export interface EvaluationSlice {
  slice: string;
  issue: string;
  action: string;
}
