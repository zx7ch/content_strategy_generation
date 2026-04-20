"use client";

import { useState } from "react";

import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { DataTable } from "@/components/dashboard/DataTable";
import { useBrandContext } from "@/components/providers/BrandProvider";
import { usePageData } from "@/hooks/usePageData";
import { getEvaluationPageData, getRuntimeApiErrorMessage, runEvaluation } from "@/lib/api";
import type { EvaluationSlice } from "@/lib/types";

export default function EvaluationPage() {
  const { selectedBrandId, selectedBrandName, loadError, retryBrands } = useBrandContext();
  const { data, error, isLoading, mutate } = usePageData(
    selectedBrandId ? `evaluation:${selectedBrandId}` : null,
    () => getEvaluationPageData(selectedBrandId ?? "")
  );
  const [actionError, setActionError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const summary = data?.summary ?? {
    comparisonLabel: "",
    sampleSize: 0,
    coverage: 0,
    essRatio: 0,
    uplift: 0,
    note: ""
  };
  const slices = data?.slices ?? [];
  const source = data?.source ?? "live";

  async function handleRun() {
    if (!selectedBrandId) {
      return;
    }
    setActionError(null);
    setRunning(true);
    try {
      await runEvaluation(selectedBrandId);
      await mutate();
    } catch (runError) {
      setActionError(runError instanceof Error ? runError.message : "运行评估失败");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="space-y-5">
      <section className="flex flex-col gap-4 rounded-panel border border-white/70 bg-white/85 p-6 shadow-panel backdrop-blur sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm text-quiet">Evaluation</p>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight text-ink">离线评估</h1>
          <p className="mt-2 text-sm text-quiet">
            当前品牌: {selectedBrandName ?? "未选择"} · 数据源: {source === "live" ? "Live API" : "Live API"}
          </p>
          <p className="mt-1 text-sm text-quiet">
            这里只做离线 replay / coverage 分析，不会回写到小红书，也不会自动替你发布新内容。
          </p>
        </div>
        <Button variant="primary" onClick={handleRun} disabled={!selectedBrandId || running}>
          {running ? "运行中..." : "运行评估"}
        </Button>
      </section>

      <Card className="space-y-2">
        <h2 className="text-base font-semibold text-ink">这页是做什么的</h2>
        <p className="text-sm text-quiet">
          当前品牌已经积累了“决策记录 + 发布记录 + 绩效反馈”之后，可以在这里做离线评估，看当前策略在历史样本上是否比基线更好。
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
          <p className="text-sm text-quiet">正在加载评估结果...</p>
        </Card>
      ) : null}

      {error ? (
        <Card>
          <p className="text-sm text-rose-600">
            读取评估结果失败：{getRuntimeApiErrorMessage(error)}
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

      <section className="grid gap-4 lg:grid-cols-2">
        <Card>
          <h2 className="text-lg font-semibold text-ink">策略对比</h2>
          <p className="mt-2 text-sm text-slate-700">{summary.comparisonLabel}</p>
          <ul className="mt-4 space-y-2 text-sm text-quiet">
            <li>样本量: {summary.sampleSize.toLocaleString()}</li>
            <li>覆盖率: {(summary.coverage * 100).toFixed(0)}%</li>
            <li>ESS Ratio: {summary.essRatio.toFixed(2)}</li>
          </ul>
        </Card>
        <Card>
          <h2 className="text-lg font-semibold text-ink">评估结果</h2>
          <p className="mt-2 text-sm text-slate-700">
            新策略提升:{" "}
            <span className="font-semibold text-success">+{(summary.uplift * 100).toFixed(1)}%</span>
          </p>
          <p className="mt-3 text-sm text-quiet">{summary.note}</p>
        </Card>
      </section>

      <div className="text-sm font-medium text-ink">失败案例分析</div>

      <DataTable<EvaluationSlice>
        columns={[
          { key: "slice", header: "切片维度", render: (row) => row.slice },
          { key: "issue", header: "问题描述", render: (row) => row.issue },
          { key: "action", header: "建议措施", render: (row) => row.action }
        ]}
        rows={slices}
        emptyLabel={selectedBrandId ? "当前品牌还没有离线评估结果。" : "请先选择一个品牌。"}
      />
    </div>
  );
}
