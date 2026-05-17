"use client";

import { useEffect, useState } from "react";

import { Card } from "@/components/ui/Card";
import { DataTable } from "@/components/dashboard/DataTable";
import { Button } from "@/components/ui/Button";
import { useBrandContext } from "@/components/providers/BrandProvider";
import { usePageData } from "@/hooks/usePageData";
import { createPublishRecord, getPublishCandidates, getPublishPageData, getRuntimeApiErrorMessage } from "@/lib/api";
import type { PublishCandidate } from "@/lib/api";
import type { PublishRecord } from "@/lib/types";

export default function PublishPage() {
  const { selectedBrandId, selectedBrandName, loadError, retryBrands } = useBrandContext();
  const { data, error, isLoading, mutate } = usePageData(
    selectedBrandId ? `publish:${selectedBrandId}` : null,
    () => getPublishPageData(selectedBrandId ?? "")
  );
  const [actionError, setActionError] = useState<string | null>(null);
  const [submittingMode, setSubmittingMode] = useState<"manual" | "decision" | null>(null);
  const [candidates, setCandidates] = useState<PublishCandidate[]>([]);

  useEffect(() => {
    getPublishCandidates()
      .then((data) => setCandidates(data.items))
      .catch(() => {});
  }, []);
  const records = data?.records ?? [];
  const source = data?.source ?? "live";

  async function handleCreate(mode: "manual" | "decision") {
    if (!selectedBrandId) {
      return;
    }
    setActionError(null);
    setSubmittingMode(mode);
    try {
      await createPublishRecord(selectedBrandId, { mode });
      await mutate();
    } catch (submitError) {
      setActionError(submitError instanceof Error ? submitError.message : "创建发布记录失败");
    } finally {
      setSubmittingMode(null);
    }
  }

  return (
    <div className="space-y-5">
      <section className="flex flex-col gap-4 rounded-panel border border-white/70 bg-white/85 p-6 shadow-panel backdrop-blur sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm text-quiet">Publish Records</p>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight text-ink">发布记录</h1>
          <p className="mt-2 text-sm text-quiet">
            当前品牌: {selectedBrandName ?? "未选择"} · 数据源: {source === "live" ? "Live API" : "Live API"}
          </p>
          <p className="mt-1 text-sm text-quiet">
            这里只做“已发布内容登记”，不会自动调用小红书发笔记。
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <Button
            variant="outline"
            onClick={() => handleCreate("decision")}
            disabled={!selectedBrandId || submittingMode !== null}
          >
            {submittingMode === "decision" ? "登记中..." : "登记已采纳决策"}
          </Button>
          <Button
            variant="primary"
            onClick={() => handleCreate("manual")}
            disabled={!selectedBrandId || submittingMode !== null}
          >
            {submittingMode === "manual" ? "登记中..." : "手动登记发布记录"}
          </Button>
        </div>
      </section>

      <Card className="space-y-2">
        <h2 className="text-base font-semibold text-ink">这页是做什么的</h2>
        <p className="text-sm text-quiet">
          当你已经在小红书真实发出内容后，在这里登记一条发布记录，给后面的绩效导入和离线评估补齐 lineage。
        </p>
        <p className="text-sm text-quiet">
          “登记已采纳决策”适合把已接受的决策结果记成已发布内容；“手动登记发布记录”适合录入非系统决策产生的内容。
        </p>
      </Card>

      {loadError ? (
        <Card>
          <p className="text-sm text-rose-600">
            读取品牌列表失败：{getRuntimeApiErrorMessage(loadError)}
          </p>
          <div className="mt-3">
            <Button variant="outline" onClick={retryBrands}>
              重试品牌加载
            </Button>
          </div>
        </Card>
      ) : null}

      {isLoading ? (
        <Card>
          <p className="text-sm text-quiet">正在加载发布记录...</p>
        </Card>
      ) : null}

      {error ? (
        <Card>
          <p className="text-sm text-rose-600">
            读取发布记录失败：{getRuntimeApiErrorMessage(error)}
          </p>
          <div className="mt-3">
            <Button variant="outline" onClick={() => void mutate()}>
              重试读取
            </Button>
          </div>
        </Card>
      ) : null}

      {actionError ? (
        <Card>
          <p className="text-sm text-rose-600">{actionError}</p>
        </Card>
      ) : null}

      <DataTable<PublishRecord>
        columns={[
          { key: "title", header: "内容标题", render: (record) => record.title },
          { key: "channel", header: "渠道", render: (record) => record.channel },
          { key: "publishedAt", header: "发布时间", render: (record) => record.publishedAt },
          { key: "decisionSource", header: "决策来源", render: (record) => record.decisionSource },
          {
            key: "status",
            header: "状态",
            render: (record) => (
              <span className="rounded-full bg-successBg px-2.5 py-1 text-xs font-medium text-success">
                {record.status === "Published" ? "已发布" : record.status}
              </span>
            )
          }
        ]}
        rows={records}
        emptyLabel={selectedBrandId ? "当前品牌还没有发布记录。" : "请先选择一个品牌。"}
      />

      <section className="rounded-panel border border-white/70 bg-white/85 p-6 shadow-panel backdrop-blur">
        <h2 className="mb-1 text-base font-semibold text-ink">Creator 发布候选</h2>
        <p className="mb-4 text-sm text-quiet">
          来自创作台「完成」操作的生成笔记候选，尚未正式登记为发布记录。
        </p>
        {candidates.length === 0 ? (
          <p className="text-sm text-quiet">暂无 Creator 候选，在创作台完成任务后会出现在这里。</p>
        ) : (
          <div className="space-y-3">
            {candidates.map((c) => (
              <div key={c.candidate_id} className="rounded-lg border border-line bg-white px-4 py-3 text-sm">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0 flex-1">
                    <p className="font-medium text-ink">{c.title}</p>
                    <p className="mt-1 line-clamp-2 text-quiet">{c.content}</p>
                    {c.tags.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {c.tags.filter(Boolean).map((tag) => (
                          <span key={tag} className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-quiet">
                            #{tag}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  <p className="shrink-0 text-xs text-quiet">{c.created_at.slice(0, 10)}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
