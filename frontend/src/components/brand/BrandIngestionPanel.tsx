"use client";

import { useEffect, useMemo, useState, type ChangeEvent } from "react";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import {
  createDataImportPreview,
  createExtensionCaptureSession,
  getDataImportPreview,
  getExtensionCaptureSession,
  retryDataImportPreviewSync,
  retryExtensionCaptureSessionSync,
  submitExtensionCapture
} from "@/lib/api";
import type {
  BrandChannelOption,
  BrandSourceSyncPayload,
  DataImportPreviewState,
  ExtensionCaptureSessionState,
  IngestionAcceptedResult
} from "@/lib/types";

interface BrandIngestionPanelProps {
  brandId: string;
  channels: BrandChannelOption[];
  initialSourceSyncSession?: ExtensionCaptureSessionState;
  initialHistoricalPreview?: DataImportPreviewState;
}

type UploadDraft = {
  fileName: string;
  fileContentBase64: string;
  fileMimeType?: string;
};

type ExtensionCaptureMessage = {
  type?: string;
  captureSessionId?: string;
  captureToken?: string;
  capturePayload?: BrandSourceSyncPayload["capture_payload"];
  capture_payload?: BrandSourceSyncPayload["capture_payload"];
};

function formatChannelLabel(channel: BrandChannelOption) {
  return channel.accountName ?? channel.profileUrl ?? `${channel.platform} channel`;
}

function formatTimestamp(value?: string) {
  if (!value) {
    return "暂无";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString("zh-CN", { hour12: false });
}

function getSourceSyncPreviewPayload(
  session: ExtensionCaptureSessionState | null,
  selectedChannelId: string
) {
  if (session?.previewPayload) {
    return session.previewPayload;
  }
  return {
    source_type: "xhs_extension_capture",
    source_adapter: "extension_source_sync_adapter_v1",
    channel_id: selectedChannelId || null,
    capture_payload: {
      status: "waiting_for_extension_return_path"
    }
  };
}

async function toBase64(file: File) {
  const buffer = await file.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunkSize = 0x8000;
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
  }
  return window.btoa(binary);
}

function ReceiptCard({ receipt }: { receipt?: IngestionAcceptedResult }) {
  if (!receipt) {
    return <p className="text-sm text-quiet">还没有生成 ingestion receipt。</p>;
  }
  return (
    <div className="rounded-2xl border border-line bg-slate-50 px-4 py-3 text-sm text-slate-700">
      <div className="font-medium text-ink">最近一次回执</div>
      <div className="mt-1 text-quiet">
        状态: {receipt.status} · Run ID: {receipt.ingestion_run_id}
      </div>
      <div className="mt-2 text-quiet">
        导入: {receipt.imported_item_count ?? 0}
        {receipt.accepted_row_count !== undefined ? ` · 解析行数: ${receipt.accepted_row_count}` : ""}
        {receipt.deduped_item_count !== undefined ? ` · 去重: ${receipt.deduped_item_count}` : ""}
      </div>
    </div>
  );
}

function JsonPreview({ payload }: { payload?: unknown }) {
  return (
    <pre className="max-h-80 overflow-auto rounded-3xl border border-line bg-slate-50 px-4 py-3 text-xs text-slate-700">
      {payload ? JSON.stringify(payload, null, 2) : "{\n  \"status\": \"waiting_for_preview\"\n}"}
    </pre>
  );
}

function StructuredError({
  summary,
  fieldErrors
}: {
  summary?: { type?: string; message: string };
  fieldErrors?: Array<Record<string, unknown>>;
}) {
  if (!summary && (!fieldErrors || fieldErrors.length === 0)) {
    return null;
  }
  return (
    <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
      <div className="font-medium">失败信息</div>
      {summary ? (
        <div className="mt-2">
          {summary.type ? `${summary.type}: ` : ""}
          {summary.message}
        </div>
      ) : null}
      {fieldErrors?.length ? (
        <ul className="mt-2 space-y-1">
          {fieldErrors.map((fieldError, index) => (
            <li key={index}>{JSON.stringify(fieldError)}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function LaneStatusCard({
  title,
  status,
  detail
}: {
  title: string;
  status?: string;
  detail: string;
}) {
  return (
    <div className="rounded-2xl border border-line bg-slate-50 px-4 py-3 text-sm text-slate-700">
      <div className="font-medium text-ink">{title}</div>
      <div className="mt-1 text-quiet">状态: {status ?? "idle"}</div>
      <div className="mt-2 text-quiet">{detail}</div>
    </div>
  );
}

export function BrandIngestionPanel({
  brandId,
  channels,
  initialSourceSyncSession,
  initialHistoricalPreview
}: BrandIngestionPanelProps) {
  const initialChannelId =
    typeof initialSourceSyncSession?.previewPayload?.["channel_id"] === "string"
      ? (initialSourceSyncSession.previewPayload["channel_id"] as string)
      : "";
  const [selectedChannelId, setSelectedChannelId] = useState("");
  const [sourceSyncSession, setSourceSyncSession] = useState<ExtensionCaptureSessionState | null>(
    initialSourceSyncSession ?? null
  );
  const [historicalPreview, setHistoricalPreview] = useState<DataImportPreviewState | null>(
    initialHistoricalPreview ?? null
  );
  const [uploadDraft, setUploadDraft] = useState<UploadDraft | null>(null);
  const [extensionPayloadDraft, setExtensionPayloadDraft] = useState("");
  const [showAdvancedExtensionTools, setShowAdvancedExtensionTools] = useState(false);
  const [submitting, setSubmitting] = useState<"source_sync" | "data_import" | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    if (channels.some((channel) => channel.id === selectedChannelId)) {
      return;
    }
    setSelectedChannelId(initialChannelId || channels[0]?.id || "");
  }, [channels, initialChannelId, selectedChannelId]);

  useEffect(() => {
    setSourceSyncSession(initialSourceSyncSession ?? null);
  }, [initialSourceSyncSession]);

  useEffect(() => {
    setHistoricalPreview(initialHistoricalPreview ?? null);
  }, [initialHistoricalPreview]);

  useEffect(() => {
    if (!sourceSyncSession || !["pending_capture", "syncing"].includes(sourceSyncSession.status)) {
      return;
    }
    const timer = window.setInterval(() => {
      void getExtensionCaptureSession(brandId, sourceSyncSession.captureSessionId)
        .then(setSourceSyncSession)
        .catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [brandId, sourceSyncSession]);

  useEffect(() => {
    if (!historicalPreview || !["parsed", "syncing"].includes(historicalPreview.status)) {
      return;
    }
    const timer = window.setInterval(() => {
      void getDataImportPreview(brandId, historicalPreview.previewId)
        .then(setHistoricalPreview)
        .catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [brandId, historicalPreview]);

  useEffect(() => {
    async function handleExtensionReturn(message: MessageEvent<ExtensionCaptureMessage>) {
      const payload = message.data;
      if (!payload || payload.type !== "xhs-extension-capture-result") {
        return;
      }
      if (!sourceSyncSession || payload.captureSessionId !== sourceSyncSession.captureSessionId) {
        return;
      }
      const capturePayload = payload.capturePayload ?? payload.capture_payload;
      if (!capturePayload || !sourceSyncSession.captureToken) {
        return;
      }

      setErrorMessage(null);
      setSubmitting("source_sync");
      try {
        const submitted = await submitExtensionCapture(
          sourceSyncSession.captureSessionId,
          payload.captureToken ?? sourceSyncSession.captureToken,
          capturePayload
        );
        setSourceSyncSession(await getExtensionCaptureSession(brandId, submitted.captureSessionId));
      } catch (error) {
        setErrorMessage(error instanceof Error ? error.message : "扩展回传提交失败");
      } finally {
        setSubmitting(null);
      }
    }

    window.addEventListener("message", handleExtensionReturn);
    return () => window.removeEventListener("message", handleExtensionReturn);
  }, [brandId, sourceSyncSession]);

  const sourcePreviewPayload = useMemo(
    () => getSourceSyncPreviewPayload(sourceSyncSession, selectedChannelId),
    [selectedChannelId, sourceSyncSession]
  );

  const historicalPreviewPayload = useMemo(() => {
    if (historicalPreview?.previewPayload) {
      return historicalPreview.previewPayload;
    }
    if (!uploadDraft) {
      return { status: "waiting_for_file_upload" };
    }
    return {
      file_name: uploadDraft.fileName,
      import_type: "historical_note_import_v1",
      platform: "xiaohongshu",
      status: "ready_for_server_preview"
    };
  }, [historicalPreview?.previewPayload, uploadDraft]);

  async function handleStartExtension() {
    if (!selectedChannelId) {
      setErrorMessage("请先选择一个接入账号。");
      return;
    }
    setErrorMessage(null);
    setSubmitting("source_sync");
    try {
      const session = await createExtensionCaptureSession(brandId, selectedChannelId);
      setSourceSyncSession(session);
      setExtensionPayloadDraft("");
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "启动扩展采集失败");
    } finally {
      setSubmitting(null);
    }
  }

  async function handleManualExtensionSubmit() {
    if (!sourceSyncSession?.captureSessionId || !sourceSyncSession.captureToken) {
      setErrorMessage("请先启动扩展采集会话。");
      return;
    }
    setErrorMessage(null);
    setSubmitting("source_sync");
    try {
      const parsed = JSON.parse(extensionPayloadDraft) as BrandSourceSyncPayload["capture_payload"];
      const submitted = await submitExtensionCapture(
        sourceSyncSession.captureSessionId,
        sourceSyncSession.captureToken,
        parsed
      );
      setSourceSyncSession(await getExtensionCaptureSession(brandId, submitted.captureSessionId));
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "扩展结果提交失败");
    } finally {
      setSubmitting(null);
    }
  }

  async function handleRetrySourceSync() {
    if (!sourceSyncSession?.captureSessionId) {
      return;
    }
    setErrorMessage(null);
    setSubmitting("source_sync");
    try {
      const retried = await retryExtensionCaptureSessionSync(brandId, sourceSyncSession.captureSessionId);
      setSourceSyncSession(await getExtensionCaptureSession(brandId, retried.captureSessionId));
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "重试 source sync 失败");
    } finally {
      setSubmitting(null);
    }
  }

  async function handleUploadFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      setUploadDraft(null);
      return;
    }
    setErrorMessage(null);
    try {
      const fileContentBase64 = await toBase64(file);
      setUploadDraft({
        fileName: file.name,
        fileContentBase64,
        fileMimeType: file.type || undefined
      });
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "读取文件失败");
      setUploadDraft(null);
    }
  }

  async function handleCreateHistoricalPreview() {
    if (!uploadDraft) {
      setErrorMessage("请先选择一个历史导入文件。");
      return;
    }
    setErrorMessage(null);
    setSubmitting("data_import");
    try {
      const preview = await createDataImportPreview(brandId, uploadDraft.fileName, {
        import_type: "historical_note_import_v1",
        platform: "xiaohongshu",
        rows: [],
        fileContentBase64: uploadDraft.fileContentBase64,
        fileMimeType: uploadDraft.fileMimeType
      });
      setHistoricalPreview(await getDataImportPreview(brandId, preview.previewId));
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "创建历史导入预览失败");
    } finally {
      setSubmitting(null);
    }
  }

  async function handleRetryHistoricalImport() {
    if (!historicalPreview?.previewId) {
      return;
    }
    setErrorMessage(null);
    setSubmitting("data_import");
    try {
      const retried = await retryDataImportPreviewSync(brandId, historicalPreview.previewId);
      setHistoricalPreview(await getDataImportPreview(brandId, retried.previewId));
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "重试历史导入失败");
    } finally {
      setSubmitting(null);
    }
  }

  return (
    <section className="space-y-4">
      <Card>
        <h2 className="text-lg font-semibold text-ink">数据入口工作区</h2>
        <p className="mt-2 text-sm text-quiet">
          在这里发起浏览器采集或上传历史数据。先把数据送进系统，再去数据处理页查看校验结果和处理回执。
        </p>
        {errorMessage ? <p className="mt-3 text-sm text-rose-600">{errorMessage}</p> : null}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <div className="flex items-start justify-between gap-4">
            <div>
              <h3 className="text-lg font-semibold text-ink">浏览器采集</h3>
              <p className="mt-1 text-sm text-quiet">
                选择品牌主页后创建采集会话。会话创建成功后，页面会进入等待状态，浏览器扩展完成回传后会自动继续提交。
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                disabled={!sourceSyncSession?.previewPayload || submitting !== null}
                onClick={handleRetrySourceSync}
              >
                {submitting === "source_sync" ? "处理中..." : "按当前预览重试"}
              </Button>
              <Button
                variant="primary"
                disabled={submitting !== null || channels.length === 0 || !selectedChannelId}
                onClick={handleStartExtension}
              >
                {submitting === "source_sync" ? "启动中..." : channels.length === 0 ? "先接入账号" : "创建采集会话"}
              </Button>
            </div>
          </div>

          <label className="mt-4 block text-sm text-quiet">
            品牌主页
            <select
              className="mt-2 w-full rounded-2xl border border-line bg-slate-50 px-4 py-3 text-sm text-slate-700 outline-none focus:border-ink disabled:cursor-not-allowed disabled:opacity-60"
              value={selectedChannelId}
              onChange={(event) => setSelectedChannelId(event.target.value)}
              disabled={channels.length === 0 || submitting !== null}
            >
              {channels.length === 0 ? <option value="">暂无可用账号</option> : null}
              {channels.map((channel) => (
                <option key={channel.id} value={channel.id}>
                  {formatChannelLabel(channel)}
                </option>
              ))}
            </select>
          </label>
          {channels.length === 0 ? (
            <p className="mt-2 text-sm text-amber-700">当前还没有可用接入账号。请先回到品牌配置页补充品牌主页信息。</p>
          ) : null}

          <div className="mt-4 space-y-3">
            <LaneStatusCard
              title="当前状态"
              status={sourceSyncSession?.status}
              detail={
                sourceSyncSession
                  ? `会话 ID: ${sourceSyncSession.captureSessionId} · 过期时间: ${formatTimestamp(sourceSyncSession.expiresAt)}`
                  : "尚未启动采集会话。"
              }
            />
            <StructuredError summary={sourceSyncSession?.errorSummary} />
            <div>
              <div className="mb-2 text-sm font-medium text-ink">只读预览</div>
              <JsonPreview payload={sourcePreviewPayload} />
            </div>
            <ReceiptCard receipt={sourceSyncSession?.ingestionReceipt} />
            <div className="rounded-2xl border border-dashed border-line bg-slate-50 px-4 py-3">
              <button
                type="button"
                className="w-full text-left text-sm font-medium text-ink"
                onClick={() => setShowAdvancedExtensionTools((value) => !value)}
              >
                {showAdvancedExtensionTools ? "收起高级操作" : "打开高级操作"}
              </button>
              {showAdvancedExtensionTools ? (
                <div className="mt-3 space-y-3 text-sm text-slate-700">
                  <div className="text-quiet">调试时可以把扩展返回的采集 JSON 粘贴到这里，再手动提交到当前采集会话。</div>
                  <textarea
                    className="min-h-28 w-full rounded-2xl border border-line bg-white px-4 py-3 text-xs text-slate-700 outline-none focus:border-ink"
                    placeholder="把扩展返回的采集 JSON 粘贴到这里。"
                    value={extensionPayloadDraft}
                    onChange={(event) => setExtensionPayloadDraft(event.target.value)}
                  />
                  <div className="flex justify-end">
                    <Button
                      variant="ghost"
                      disabled={!extensionPayloadDraft.trim() || !sourceSyncSession || submitting !== null}
                      onClick={handleManualExtensionSubmit}
                    >
                      {submitting === "source_sync" ? "提交中..." : "提交当前 JSON"}
                    </Button>
                  </div>
                </div>
              ) : null}
            </div>
          </div>
        </Card>

        <Card>
          <div className="flex items-start justify-between gap-4">
            <div>
              <h3 className="text-lg font-semibold text-ink">历史数据上传</h3>
              <p className="mt-1 text-sm text-quiet">
                上传历史表格后，系统会先生成预览并继续处理，适合补充已有内容数据。
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                disabled={!historicalPreview?.previewPayload || submitting !== null}
                onClick={handleRetryHistoricalImport}
              >
                {submitting === "data_import" ? "处理中..." : "按当前预览重试"}
              </Button>
              <Button
                variant="primary"
                disabled={submitting !== null || !uploadDraft}
                onClick={handleCreateHistoricalPreview}
              >
                {submitting === "data_import" ? "上传中..." : "生成预览并导入"}
              </Button>
            </div>
          </div>

          <label className="mt-4 block text-sm text-quiet">
            历史导入文件
            <input
              className="mt-2 block w-full rounded-2xl border border-line bg-slate-50 px-4 py-3 text-sm text-slate-700 file:mr-3 file:rounded-full file:border-0 file:bg-white file:px-3 file:py-2 file:text-sm file:font-medium file:text-ink"
              type="file"
              accept=".json,.csv,.tsv,.xlsx"
              onChange={(event) => void handleUploadFile(event)}
              disabled={submitting !== null}
            />
          </label>

          <div className="mt-4 space-y-3">
            <LaneStatusCard
              title="当前状态"
              status={historicalPreview?.status}
              detail={
                historicalPreview
                  ? `文件: ${historicalPreview.fileName} · 上传时间: ${formatTimestamp(historicalPreview.uploadedAt)} · 解析行数: ${historicalPreview.parsedRowCount}`
                  : uploadDraft
                    ? `待上传文件: ${uploadDraft.fileName}`
                    : "尚未选择历史导入文件。"
              }
            />
            <StructuredError
              summary={historicalPreview?.errorSummary}
              fieldErrors={historicalPreview?.fieldErrors}
            />
            <div>
              <div className="mb-2 text-sm font-medium text-ink">只读预览</div>
              <JsonPreview payload={historicalPreviewPayload} />
            </div>
            <ReceiptCard receipt={historicalPreview?.ingestionReceipt} />
          </div>
        </Card>
      </div>
    </section>
  );
}
