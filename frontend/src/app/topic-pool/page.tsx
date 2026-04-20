"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { Card } from "@/components/ui/Card";
import { DataTable } from "@/components/dashboard/DataTable";
import { StatCard } from "@/components/dashboard/StatCard";
import { Button } from "@/components/ui/Button";
import { InfoTooltip } from "@/components/ui/InfoTooltip";
import { useBrandContext } from "@/components/providers/BrandProvider";
import { usePageData } from "@/hooks/usePageData";
import {
  getRuntimeApiErrorMessage,
  getTopicPoolPageData,
  runDecisionBatch,
  triggerTopicPoolRefresh
} from "@/lib/api";
import type { Topic } from "@/lib/types";

function getTopicTypeLabel(type: Topic["type"]) {
  switch (type) {
    case "Problem":
      return "问题痛点";
    case "Scenario":
      return "场景需求";
    case "Audience":
      return "人群洞察";
    case "Competitor":
      return "竞品机会";
    case "Trend":
      return "趋势话题";
    default:
      return "核心主题";
  }
}

function getTopicSourceLabel(source: Topic["source"]) {
  switch (source) {
    case "Gap":
      return "内容缺口";
    case "Trend":
      return "趋势信号";
    case "OwnedPerformance":
      return "自有内容反馈";
    default:
      return "互动信号";
  }
}

function getSignalLabel(signalType: string) {
  switch (signalType) {
    case "gap":
      return "内容缺口";
    case "trend":
      return "趋势信号";
    case "owned_performance":
      return "自有内容反馈";
    default:
      return "互动信号";
  }
}

export default function TopicPoolPage() {
  const router = useRouter();
  const { selectedBrandId, selectedBrandName, loadError, retryBrands } = useBrandContext();
  const { data, error, isLoading, mutate } = usePageData(
    selectedBrandId ? `topic-pool:${selectedBrandId}` : null,
    () => getTopicPoolPageData(selectedBrandId ?? "")
  );
  const [refreshing, setRefreshing] = useState(false);
  const [runningDecision, setRunningDecision] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [expandedTopicIds, setExpandedTopicIds] = useState<Record<string, boolean>>({});

  const topics = data?.topics ?? [];
  const stats = data?.stats ?? { totalCandidates: 0, bestScore: 0, lastRefreshAt: null };
  const source = data?.source ?? "live";
  const brand = data?.brand ?? {
    id: selectedBrandId ?? "unselected",
    name: selectedBrandName ?? "未选择",
    stage: "Seed" as const,
    targetAudience: "待补充"
  };

  async function handleRefresh() {
    if (!selectedBrandId) {
      return;
    }
    setActionError(null);
    setRefreshing(true);
    try {
      await triggerTopicPoolRefresh(selectedBrandId);
      await mutate();
    } catch (refreshError) {
      setActionError(refreshError instanceof Error ? refreshError.message : "刷新选题失败");
    } finally {
      setRefreshing(false);
    }
  }

  async function handleRunDecision() {
    if (!selectedBrandId) {
      return;
    }
    setActionError(null);
    setRunningDecision(true);
    try {
      const batch = await runDecisionBatch(selectedBrandId);
      router.push(`/decisions?batch_id=${batch.batch_id}`);
    } catch (decisionError) {
      setActionError(decisionError instanceof Error ? decisionError.message : "执行决策失败");
    } finally {
      setRunningDecision(false);
    }
  }

  function toggleExplainability(topicId: string) {
    setExpandedTopicIds((current) => ({
      ...current,
      [topicId]: !current[topicId]
    }));
  }

  return (
    <div className="space-y-5">
      <section className="flex flex-col gap-4 rounded-panel border border-white/70 bg-white/85 p-6 shadow-panel backdrop-blur sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm text-quiet">Topic Pool</p>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight text-ink">选题候选库</h1>
          <p className="mt-2 text-sm text-quiet">
            当前品牌: {brand.name} · 数据源: {source === "live" ? "Live API" : "Live API"}
          </p>
          <p className="mt-1 text-sm text-quiet">
            品牌阶段: {brand.stage} · 目标受众: {brand.targetAudience}
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <Button
            variant="outline"
            disabled={!selectedBrandId || runningDecision || topics.length === 0}
            onClick={handleRunDecision}
          >
            {runningDecision ? "执行中..." : "执行决策"}
          </Button>
          <Button
            variant="primary"
            disabled={!selectedBrandId || refreshing}
            onClick={handleRefresh}
          >
            {refreshing ? "刷新中..." : "刷新选题"}
          </Button>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-3">
        <StatCard value={String(stats.totalCandidates)} label="总候选数" />
        <StatCard value={stats.bestScore.toFixed(2)} label="最高得分" />
        <StatCard value={stats.lastRefreshAt ? stats.lastRefreshAt.slice(11, 16) : "--:--"} label="最近刷新" />
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
          <p className="text-sm text-quiet">正在加载当前品牌的 Topic Pool...</p>
        </Card>
      ) : null}

      {error ? (
        <Card>
          <p className="text-sm text-rose-600">
            读取 Topic Pool 失败：{getRuntimeApiErrorMessage(error)}
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

      <Card className="space-y-2">
        <h2 className="text-base font-semibold text-ink">这页是做什么的</h2>
        <p className="text-sm text-quiet">
          这里是候选选题池。先点击“刷新选题”生成候选，再展开“查看依据”核对来源笔记，确认没问题后再去执行决策。
        </p>
      </Card>

      <DataTable<Topic>
        columns={[
          {
            key: "title",
            header: "选题标题",
            render: (topic) => (
              <div>
                <div className="font-medium text-ink">{topic.title}</div>
                <div className="mt-1 text-xs text-quiet">{topic.angle ?? "待补充 angle"}</div>
                <div className="mt-3">
                  <button
                    type="button"
                    className="text-xs font-medium text-slate-600 underline decoration-dotted underline-offset-4"
                    onClick={() => toggleExplainability(topic.id)}
                  >
                    {expandedTopicIds[topic.id] ? "收起依据" : "展开依据"}
                  </button>
                </div>
                {expandedTopicIds[topic.id] ? (
                  <div className="mt-3 space-y-3 rounded-2xl border border-slate-200 bg-slate-50/80 p-3">
                    <div>
                      <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-quiet">
                        Score Breakdown
                      </div>
                      {topic.scoreBreakdown ? (
                        <div className="mt-2 grid gap-2 text-xs text-slate-700 sm:grid-cols-2">
                          <div>Novelty: {topic.scoreBreakdown.noveltyScore.toFixed(2)}</div>
                          <div>Brand fit: {topic.scoreBreakdown.fitScore.toFixed(2)}</div>
                          <div>Trend: {topic.scoreBreakdown.trendScore.toFixed(2)}</div>
                          <div>Historical reward: {topic.scoreBreakdown.historicalRewardScore.toFixed(2)}</div>
                          <div>Policy: {topic.scoreBreakdown.policyScore.toFixed(2)}</div>
                          <div>Final: {topic.scoreBreakdown.finalScore.toFixed(2)}</div>
                          {typeof topic.scoreBreakdown.sourceCount === "number" ? (
                            <div>Evidence count: {topic.scoreBreakdown.sourceCount}</div>
                          ) : null}
                          {typeof topic.scoreBreakdown.brandFitCheck === "boolean" ? (
                            <div>Brand fit check: {topic.scoreBreakdown.brandFitCheck ? "通过" : "未通过"}</div>
                          ) : null}
                        </div>
                      ) : (
                        <p className="mt-2 text-xs text-quiet">暂无打分拆解。</p>
                      )}
                      {topic.scoreBreakdown?.brandFitViolations?.length ? (
                        <div className="mt-2 text-xs text-amber-700">
                          Violations: {topic.scoreBreakdown.brandFitViolations.join(", ")}
                        </div>
                      ) : null}
                    </div>
                    <div>
                      <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-quiet">
                        Evidence Provenance
                      </div>
                      {topic.evidenceProvenance && topic.evidenceProvenance.length > 0 ? (
                        <div className="mt-2 space-y-2">
                          {topic.evidenceProvenance.map((entry) => (
                            <div
                              key={`${topic.id}-${entry.itemId}`}
                              className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700"
                            >
                              <div className="font-medium text-slate-900">
                                {entry.sourceUrl ? (
                                  <div className="flex flex-wrap items-center gap-2">
                                    <a
                                      href={entry.sourceUrl}
                                      target="_blank"
                                      rel="noreferrer"
                                      className="underline decoration-dotted underline-offset-4"
                                    >
                                      {entry.originalTitle}
                                    </a>
                                    <a
                                      href={entry.sourceUrl}
                                      target="_blank"
                                      rel="noreferrer"
                                      className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] font-medium text-slate-600 transition hover:bg-slate-100"
                                    >
                                      打开小红书原帖
                                    </a>
                                  </div>
                                ) : (
                                  entry.originalTitle
                                )}
                              </div>
                              <div className="mt-1 text-quiet">
                                signal={getSignalLabel(entry.signalType)} · contribution={entry.contributionWeight.toFixed(2)} ·
                                score={entry.signalScore.toFixed(2)}
                              </div>
                              <div className="mt-1 text-quiet">
                                likes={entry.likes} · comments={entry.comments} · collects={entry.collects} · shares={entry.shares}
                              </div>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <p className="mt-2 text-xs text-quiet">暂无证据明细。</p>
                      )}
                    </div>
                  </div>
                ) : null}
              </div>
            )
          },
          {
            key: "type",
            header: (
              <span className="inline-flex items-center gap-2">
                类型
                <InfoTooltip
                  label="查看选题类型说明"
                  content="类型表示这个选题在回答什么问题，例如“问题痛点”聚焦用户卡点，“场景需求”聚焦使用场景。"
                />
              </span>
            ),
            render: (topic) => (
              <div>
                <div>{getTopicTypeLabel(topic.type)}</div>
                <div className="mt-1 text-xs text-quiet">{topic.type}</div>
              </div>
            )
          },
          { key: "hypothesis", header: "核心假设", render: (topic) => topic.hypothesis },
          {
            key: "score",
            header: "综合得分",
            render: (topic) => (
              <div className="flex items-center gap-2">
                <span>{topic.score.toFixed(2)}</span>
                <span className="text-xs text-quiet">
                  {topic.score >= 0.85 ? "(高)" : topic.score >= 0.7 ? "(中)" : "(观察)"}
                </span>
              </div>
            )
          },
          {
            key: "source",
            header: (
              <span className="inline-flex items-center gap-2">
                来源
                <InfoTooltip
                  label="查看来源说明"
                  content="来源表示这个选题主要由哪类信号触发，例如“内容缺口”说明用户在找答案但现有内容覆盖不足。"
                />
              </span>
            ),
            render: (topic) => (
              <div>
                <div>{getTopicSourceLabel(topic.source)}</div>
                <div className="mt-1 text-xs text-quiet">{topic.source}</div>
                <div className="mt-1 text-xs text-quiet">
                  evidence={topic.evidenceCount ?? 0} · {topic.status ?? "candidate"}
                </div>
              </div>
            )
          }
        ]}
        rows={topics}
        emptyLabel={
          selectedBrandId
            ? "当前品牌还没有候选选题，先触发 source sync / data import，再点击刷新选题。"
            : "请先选择一个品牌。"
        }
      />
    </div>
  );
}
