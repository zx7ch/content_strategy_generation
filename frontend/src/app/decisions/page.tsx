"use client";

import { useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { Card } from "@/components/ui/Card";
import { DataTable } from "@/components/dashboard/DataTable";
import { StatCard } from "@/components/dashboard/StatCard";
import { Button } from "@/components/ui/Button";
import { useBrandContext } from "@/components/providers/BrandProvider";
import { usePageData } from "@/hooks/usePageData";
import {
  getDecisionsPageData,
  getRuntimeApiErrorMessage,
  reviewDecisionBatchItem,
  runDecisionBatch
} from "@/lib/api";
import type { DecisionItem } from "@/lib/types";

export default function DecisionsPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { selectedBrandId, selectedBrandName, loadError, retryBrands } = useBrandContext();
  const batchId = searchParams.get("batch_id");
  const swrKey = useMemo(
    () => (selectedBrandId ? `decisions:${selectedBrandId}:${batchId ?? "latest"}` : null),
    [batchId, selectedBrandId]
  );
  const { data, error, isLoading, mutate } = usePageData(
    swrKey,
    () => getDecisionsPageData(selectedBrandId ?? "", { batchId })
  );
  const [actionError, setActionError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [actingSlot, setActingSlot] = useState<number | null>(null);

  const items = data?.items ?? [];
  const stats = data?.stats ?? { expectedReward: 0, selectedCount: 0, explorationProbability: 0 };
  const source = data?.source ?? "live";
  const activeBatchId = data?.batchId ?? batchId ?? undefined;

  async function handleRunDecision() {
    if (!selectedBrandId) {
      return;
    }
    setActionError(null);
    setRunning(true);
    try {
      const batch = await runDecisionBatch(selectedBrandId);
      router.push(`/decisions?batch_id=${batch.batch_id}`);
      await mutate();
    } catch (runError) {
      setActionError(runError instanceof Error ? runError.message : "执行决策失败");
    } finally {
      setRunning(false);
    }
  }

  async function handleReviewAction(
    item: DecisionItem,
    action: "accept" | "reject" | "edit_and_accept"
  ) {
    if (!activeBatchId) {
      setActionError("当前没有可操作的 decision batch。");
      return;
    }
    setActionError(null);
    setActingSlot(item.slotIndex);
    try {
      if (action === "edit_and_accept") {
        const editedTitle = window.prompt("编辑标题", item.title) ?? item.title;
        const editedAngle = window.prompt("编辑 angle", item.angle ?? "") ?? item.angle ?? "";
        const editedHypothesis = window.prompt("编辑 hypothesis", item.hypothesis ?? "") ?? item.hypothesis ?? "";
        const reviewNotes = window.prompt("补充备注", item.reviewNotes ?? "") ?? item.reviewNotes ?? "";
        await reviewDecisionBatchItem(activeBatchId, item.slotIndex, {
          review_action: action,
          edited_title: editedTitle,
          edited_angle: editedAngle,
          edited_hypothesis: editedHypothesis,
          review_notes: reviewNotes
        });
      } else {
        await reviewDecisionBatchItem(activeBatchId, item.slotIndex, {
          review_action: action
        });
      }
      await mutate();
    } catch (reviewError) {
      setActionError(reviewError instanceof Error ? reviewError.message : "更新 review 状态失败");
    } finally {
      setActingSlot(null);
    }
  }

  return (
    <div className="space-y-5">
      <section className="flex flex-col gap-4 rounded-panel border border-white/70 bg-white/85 p-6 shadow-panel backdrop-blur sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm text-quiet">Decision Batches</p>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight text-ink">决策批次</h1>
          <p className="mt-2 text-sm text-quiet">
            当前品牌: {selectedBrandName ?? "未选择"} · 数据源: {source === "live" ? "Live API" : "Live API"}
          </p>
          <p className="mt-1 text-sm text-quiet">当前批次: {activeBatchId ?? "尚未生成"}</p>
        </div>
        <Button variant="primary" onClick={handleRunDecision} disabled={!selectedBrandId || running}>
          {running ? "执行中..." : "执行决策流"}
        </Button>
      </section>

      <section className="grid gap-4 md:grid-cols-3">
        <StatCard value={stats.expectedReward.toFixed(2)} label="预期奖励分" />
        <StatCard value={String(stats.selectedCount)} label="已选数量" />
        <StatCard value={stats.explorationProbability.toFixed(2)} label="探索概率" />
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
          <p className="text-sm text-quiet">正在加载决策批次...</p>
        </Card>
      ) : null}

      {error ? (
        <Card>
          <p className="text-sm text-rose-600">
            读取决策批次失败：{getRuntimeApiErrorMessage(error)}
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

      <div className="text-sm font-medium text-ink">本次选题结果</div>

      <DataTable<DecisionItem>
        columns={[
          {
            key: "title",
            header: "选题",
            render: (item) => (
              <div>
                <div className="font-medium text-ink">Slot {item.slotIndex + 1} · {item.title}</div>
                <div className="mt-1 text-xs text-quiet">{item.angle ?? "待补充 angle"}</div>
              </div>
            )
          },
          {
            key: "score",
            header: "策略得分",
            render: (item) => (
              <span className="font-medium">
                {item.strategyScore.toFixed(2)} {item.mode === "Exploitation" ? "(Baseline)" : "(Exploration)"}
              </span>
            )
          },
          {
            key: "mode",
            header: "模式 / 状态",
            render: (item) => (
              <div>
                <div>{item.mode === "Exploitation" ? "利用 (Exploitation)" : "探索 (Exploration)"}</div>
                <div className="mt-1 text-xs text-quiet">review={item.reviewStatus ?? "pending"}</div>
              </div>
            )
          },
          {
            key: "action",
            header: "操作",
            render: (item) => (
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="primary"
                  disabled={actingSlot === item.slotIndex}
                  onClick={() => handleReviewAction(item, "accept")}
                >
                  接受
                </Button>
                <Button
                  variant="ghost"
                  disabled={actingSlot === item.slotIndex}
                  onClick={() => handleReviewAction(item, "reject")}
                >
                  拒绝
                </Button>
                <Button
                  variant="outline"
                  disabled={actingSlot === item.slotIndex}
                  onClick={() => handleReviewAction(item, "edit_and_accept")}
                >
                  编辑接受
                </Button>
              </div>
            )
          }
        ]}
        rows={items}
        emptyLabel={
          selectedBrandId
            ? "当前品牌还没有决策批次。请先从 Topic Pool 执行决策，或在本页点击“执行决策流”。"
            : "请先选择一个品牌。"
        }
      />
    </div>
  );
}
