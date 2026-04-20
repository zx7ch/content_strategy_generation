"use client";

import { useBrandContext } from "@/components/providers/BrandProvider";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { usePageData } from "@/hooks/usePageData";
import { getDataProcessingPageData, getRuntimeApiErrorMessage } from "@/lib/api";

function formatTimestamp(value?: string | null) {
  if (!value) {
    return "暂无";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString("zh-CN", { hour12: false });
}

function getStatusLabel(status?: string) {
  switch (status) {
    case "accepted":
    case "completed":
      return "已完成";
    case "syncing":
      return "处理中";
    case "pending_capture":
    case "uploaded":
    case "parsed":
      return "待处理";
    case "failed":
      return "失败";
    default:
      return "未开始";
  }
}

function JsonPreview({ value }: { value?: unknown }) {
  return (
    <pre className="max-h-80 overflow-auto rounded-3xl border border-line bg-slate-50 px-4 py-4 text-xs text-slate-700">
      {value ? JSON.stringify(value, null, 2) : "{\n  \"status\": \"waiting_for_data\"\n}"}
    </pre>
  );
}

export default function DataProcessingPage() {
  const { selectedBrandId, selectedBrandName } = useBrandContext();
  const { data, error, isLoading, mutate } = usePageData(
    selectedBrandId ? `data-processing:${selectedBrandId}` : null,
    () => getDataProcessingPageData(selectedBrandId ?? "")
  );

  return (
    <div className="space-y-5">
      <section className="rounded-panel border border-white/70 bg-white/85 p-6 shadow-panel backdrop-blur">
        <p className="text-sm text-quiet">Data Processing</p>
        <h1 className="mt-1 text-3xl font-semibold tracking-tight text-ink">数据处理</h1>
        <p className="mt-2 text-sm text-quiet">
          当前品牌：{selectedBrandName ?? "未选择"}。这里查看最近一次浏览器采集和历史上传的预览、校验结果与处理记录。
        </p>
      </section>

      {isLoading ? (
        <Card>
          <p className="text-sm text-quiet">正在加载数据处理页面...</p>
        </Card>
      ) : null}

      {error ? (
        <Card>
          <p className="text-sm text-rose-600">读取数据处理页面失败：{getRuntimeApiErrorMessage(error)}</p>
          <div className="mt-3">
            <Button variant="outline" onClick={() => void mutate()}>
              重试读取
            </Button>
          </div>
        </Card>
      ) : null}

      <section className="grid gap-4 xl:grid-cols-2">
        <Card className="space-y-4">
          <div>
            <h2 className="text-lg font-semibold text-ink">浏览器采集预览</h2>
            <p className="mt-1 text-sm text-quiet">查看最近一次浏览器采集返回的结构化结果与处理状态。</p>
          </div>
          <div className="rounded-2xl border border-line bg-slate-50 px-4 py-3 text-sm text-slate-700">
            状态：{getStatusLabel(data?.latestExtensionCaptureSession?.status)} ·
            创建时间：{formatTimestamp(data?.latestExtensionCaptureSession?.capturedAt)}
          </div>
          {data?.latestExtensionCaptureSession?.errorSummary ? (
            <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
              {data.latestExtensionCaptureSession.errorSummary.message}
            </div>
          ) : null}
          <JsonPreview value={data?.latestExtensionCaptureSession?.previewPayload} />
        </Card>

        <Card className="space-y-4">
          <div>
            <h2 className="text-lg font-semibold text-ink">历史上传预览</h2>
            <p className="mt-1 text-sm text-quiet">查看最近一次历史数据上传的结构化预览、校验结果和错误信息。</p>
          </div>
          <div className="rounded-2xl border border-line bg-slate-50 px-4 py-3 text-sm text-slate-700">
            状态：{getStatusLabel(data?.latestDataImportPreview?.status)} ·
            上传时间：{formatTimestamp(data?.latestDataImportPreview?.uploadedAt)}
          </div>
          {data?.latestDataImportPreview?.errorSummary ? (
            <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
              {data.latestDataImportPreview.errorSummary.message}
            </div>
          ) : null}
          {data?.latestDataImportPreview?.fieldErrors?.length ? (
            <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
              检测到 {data.latestDataImportPreview.fieldErrors.length} 条校验提示，请检查下方预览内容。
            </div>
          ) : null}
          <JsonPreview value={data?.latestDataImportPreview?.previewPayload} />
        </Card>
      </section>

      <Card className="space-y-4">
        <div>
          <h2 className="text-lg font-semibold text-ink">处理历史</h2>
          <p className="mt-1 text-sm text-quiet">最近进入系统的数据处理记录会统一展示在这里，便于排查和回溯。</p>
        </div>
        {data?.recentIngestionRuns?.length ? (
          <div className="space-y-3">
            {data.recentIngestionRuns.map((run) => (
              <div
                key={run.id}
                className="rounded-2xl border border-line bg-slate-50 px-4 py-4 text-sm text-slate-700"
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="font-medium text-ink">{run.type}</div>
                  <div className="text-xs text-slate-600">{getStatusLabel(run.status)}</div>
                </div>
                <div className="mt-2 text-xs text-quiet">
                  {run.sourceLabel} · {formatTimestamp(run.createdAt)}
                </div>
                <div className="mt-2 text-xs text-slate-600">
                  新增 {run.importedCount} · 去重 {run.dedupedCount}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="rounded-2xl border border-dashed border-line bg-slate-50 px-4 py-5 text-sm text-quiet">
            当前没有待处理数据。去数据源页发起采集或上传历史表格。
          </div>
        )}
      </Card>
    </div>
  );
}
