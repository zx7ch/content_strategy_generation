import * as React from "react";

import type {
  BrandDetailPageData,
  BrandsPageData,
  V2BrandApiResponse,
  V2BrandChannelListResponse,
  V2BrandListResponse
} from "./api";
import type { Brand, DataImportPreviewState, ExtensionCaptureSessionState } from "./types";

const cacheFn: <T extends (...args: never[]) => unknown>(fn: T) => T =
  typeof React.cache === "function"
    ? (React.cache as <T extends (...args: never[]) => unknown>(fn: T) => T)
    : ((fn) => fn);

type WorkspaceIdentity = {
  workspace_id: string;
  user_id: string;
};

type RequestJsonOptions = {
  method?: string;
  body?: unknown;
};

type V2BrandWorkspaceApiResponse = {
  brand: V2BrandApiResponse;
  channels: Array<{
    id: string;
    platform?: string;
    account_name?: string | null;
    profile_url?: string | null;
  }>;
  active_policy?: Record<string, unknown> | null;
  latest_extension_capture_session?: {
    capture_session_id: string;
    capture_token?: string | null;
    status: "pending_capture" | "captured" | "syncing" | "accepted" | "failed" | "expired";
    expires_at: string;
    captured_at?: string | null;
    preview_payload?: Record<string, unknown> | null;
    ingestion_receipt?: Record<string, unknown> | null;
    error_summary?: {
      type?: string;
      message: string;
    } | null;
  } | null;
  latest_data_import_preview?: {
    preview_id: string;
    file_name: string;
    status: "uploaded" | "parsed" | "syncing" | "accepted" | "failed";
    uploaded_at: string;
    parsed_row_count: number;
    preview_payload?: Record<string, unknown> | null;
    ingestion_receipt?: Record<string, unknown> | null;
    field_errors?: Array<Record<string, unknown>>;
    error_summary?: {
      type?: string;
      message: string;
    } | null;
  } | null;
};

export class LiveApiError extends Error {
  readonly status?: number;
  readonly path: string;

  constructor(message: string, options: { path: string; status?: number; cause?: unknown }) {
    super(message, { cause: options.cause });
    this.name = "LiveApiError";
    this.status = options.status;
    this.path = options.path;
  }
}

function getApiBaseUrl() {
  return (
    process.env.NEXT_PUBLIC_XHS_API_BASE_URL?.trim() ||
    process.env.XHS_API_BASE_URL?.trim() ||
    "http://127.0.0.1:8000"
  );
}

const resolveDefaultWorkspace = cacheFn(async (): Promise<WorkspaceIdentity> => {
  const response = await fetch(`${getApiBaseUrl()}/workspaces/default`, { cache: "no-store" });
  if (!response.ok) {
    throw new LiveApiError("无法解析默认 workspace。", {
      path: "/workspaces/default",
      status: response.status
    });
  }
  return response.json();
});

async function serverRequestJson<T>(path: string, options?: RequestJsonOptions): Promise<T> {
  const workspace = await resolveDefaultWorkspace();
  const headers = new Headers({
    "Content-Type": "application/json",
    "X-Workspace-Id": workspace.workspace_id,
    "X-User-Id": workspace.user_id
  });

  const authToken =
    process.env.NEXT_PUBLIC_XHS_AUTH_TOKEN?.trim() ||
    process.env.XHS_AUTH_TOKEN?.trim();
  if (authToken) {
    headers.set("Authorization", `Bearer ${authToken}`);
  }

  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    method: options?.method ?? "GET",
    headers,
    body: options?.body === undefined ? undefined : JSON.stringify(options.body),
    cache: "no-store"
  });

  if (!response.ok) {
    throw new LiveApiError(`读取 ${path} 失败。`, {
      path,
      status: response.status
    });
  }

  return (await response.json()) as T;
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

export type ServerBrandDetailPageData = BrandDetailPageData;

function mapExtensionCaptureSession(
  response: NonNullable<V2BrandWorkspaceApiResponse["latest_extension_capture_session"]>
): ExtensionCaptureSessionState {
  return {
    captureSessionId: response.capture_session_id,
    captureToken: response.capture_token ?? undefined,
    status: response.status,
    expiresAt: response.expires_at,
    capturedAt: response.captured_at ?? undefined,
    previewPayload: response.preview_payload ?? undefined,
    ingestionReceipt: response.ingestion_receipt as ExtensionCaptureSessionState["ingestionReceipt"],
    errorSummary: response.error_summary ?? undefined
  };
}

function mapDataImportPreview(
  response: NonNullable<V2BrandWorkspaceApiResponse["latest_data_import_preview"]>
): DataImportPreviewState {
  return {
    previewId: response.preview_id,
    fileName: response.file_name,
    status: response.status,
    uploadedAt: response.uploaded_at,
    parsedRowCount: response.parsed_row_count,
    previewPayload: response.preview_payload ?? undefined,
    ingestionReceipt: response.ingestion_receipt as DataImportPreviewState["ingestionReceipt"],
    fieldErrors: response.field_errors ?? [],
    errorSummary: response.error_summary ?? undefined
  };
}

export async function getServerBrandsPageData(): Promise<BrandsPageData> {
  const brandResponse = await serverRequestJson<V2BrandListResponse>("/brands");
  const brands = await Promise.all(
    brandResponse.items.map(async (brand) => {
      const channels = await serverRequestJson<V2BrandChannelListResponse>(`/brands/${brand.id}/channels`);
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

export async function getServerBrandDetailPageData(brandId: string): Promise<ServerBrandDetailPageData> {
  const workspace = await serverRequestJson<V2BrandWorkspaceApiResponse>(`/brands/${brandId}/workspace`);

  return {
    brand: {
      id: workspace.brand.id,
      name: workspace.brand.name,
      category: workspace.brand.category ?? undefined,
      stage:
        workspace.brand.stage === "growth"
          ? "growth"
          : workspace.brand.stage === "mature" || workspace.brand.stage === "scaled"
            ? "mature"
            : "seed",
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

export function getLiveApiErrorMessage(error: unknown) {
  if (error instanceof LiveApiError) {
    const statusLabel = error.status ? `HTTP ${error.status}` : "未返回状态码";
    return `${error.message} 路径: ${error.path} (${statusLabel})`;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "未知错误";
}
