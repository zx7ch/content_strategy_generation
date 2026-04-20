"use client";

import { useState } from "react";

import { Card } from "@/components/ui/Card";
import { DataTable } from "@/components/dashboard/DataTable";
import { StatCard } from "@/components/dashboard/StatCard";
import { Button } from "@/components/ui/Button";
import { useBrandContext } from "@/components/providers/BrandProvider";
import { usePageData } from "@/hooks/usePageData";
import {
  getPerformancePageData,
  getRuntimeApiErrorMessage,
  importPerformanceSnapshot
} from "@/lib/api";
import type { PerformanceMetric } from "@/lib/types";

export default function PerformancePage() {
  const { selectedBrandId, selectedBrandName, loadError, retryBrands } = useBrandContext();
  const { data, error, isLoading, mutate } = usePageData(
    selectedBrandId ? `performance:${selectedBrandId}` : null,
    () => getPerformancePageData(selectedBrandId ?? "")
  );
  const [actionError, setActionError] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const stats = data?.stats ?? { averageEngagementRate: 0, compositeReward168h: 0 };
  const metrics = data?.metrics ?? [];
  const source = data?.source ?? "live";

  async function handleImport() {
    if (!selectedBrandId) {
      return;
    }
    setActionError(null);
    setImporting(true);
    try {
      await importPerformanceSnapshot(selectedBrandId);
      await mutate();
    } catch (importError) {
      setActionError(importError instanceof Error ? importError.message : "导入绩效失败");
    } finally {
      setImporting(false);
    }
  }

  return (
    <div className="space-y-5">
      <section className="flex flex-col gap-4 rounded-panel border border-white/70 bg-white/85 p-6 shadow-panel backdrop-blur sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm text-quiet">Performance</p>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight text-ink">绩效与反馈</h1>
          <p className="mt-2 text-sm text-quiet">
            当前品牌: {selectedBrandName ?? "未选择"} · 数据源: {source === "live" ? "Live API" : "Live API"}
          </p>
          <p className="mt-1 text-sm text-quiet">
            这里用于导入内容发布后的表现数据，不负责抓取平台实时数据。
          </p>
        </div>
        <Button variant="primary" onClick={handleImport} disabled={!selectedBrandId || importing}>
          {importing ? "导入中..." : "导入绩效快照"}
        </Button>
      </section>

      <Card className="space-y-2">
        <h2 className="text-base font-semibold text-ink">这页是做什么的</h2>
        <p className="text-sm text-quiet">
          当某条内容已经发布并拿到曝光、点击、互动等结果后，在这里导入一份绩效快照，让系统知道“这次决策后来表现如何”。
        </p>
      </Card>

      <section className="grid gap-4 md:grid-cols-2">
        <StatCard value={`${(stats.averageEngagementRate * 100).toFixed(1)}%`} label="平均互动率" />
        <StatCard value={stats.compositeReward168h.toFixed(3)} label="复合奖励分 (168h)" />
      </section>

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
          <p className="text-sm text-quiet">正在加载绩效快照...</p>
        </Card>
      ) : null}

      {error ? (
        <Card>
          <p className="text-sm text-rose-600">
            读取绩效数据失败：{getRuntimeApiErrorMessage(error)}
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

      <DataTable<PerformanceMetric>
        columns={[
          {
            key: "topic",
            header: "关联选题",
            render: (metric) => metric.topicTitle
          },
          {
            key: "exposure",
            header: "曝光/点击",
            render: (metric) =>
              `${metric.impressions.toLocaleString()} / ${metric.clicks.toLocaleString()}`
          },
          {
            key: "conversion",
            header: "转化代理",
            render: (metric) => metric.conversionProxyLabel
          },
          {
            key: "reward",
            header: "奖励分",
            render: (metric) => `${metric.rewardScore.toFixed(2)} (短期)`
          }
        ]}
        rows={metrics}
        emptyLabel={selectedBrandId ? "当前品牌还没有绩效快照。" : "请先选择一个品牌。"}
      />
    </div>
  );
}
