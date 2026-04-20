"use client";

import { useEffect, useMemo, useState } from "react";

import { BrandIngestionPanel } from "@/components/brand/BrandIngestionPanel";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { useBrandContext } from "@/components/providers/BrandProvider";
import { usePageData } from "@/hooks/usePageData";
import {
  addDiscoveryQuery,
  createDiscoveryTask,
  deleteDiscoveryQuery,
  getDataSourcesPageData,
  getRuntimeApiErrorMessage,
  refreshDiscoveryHotspots,
  type DiscoveryWorkspaceData
} from "@/lib/api";

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

function getHotspotMetricLabel(metric: string) {
  if (metric === "likes") {
    return "点赞最高";
  }
  if (metric === "collections") {
    return "收藏最高";
  }
  if (metric === "comments") {
    return "评论最高";
  }
  return metric;
}

const HOTSPOT_ORDER = ["likes", "collections", "comments"] as const;

export default function DataSourcesPage() {
  const { selectedBrandId, selectedBrandName } = useBrandContext();
  const { data, error, isLoading, mutate } = usePageData(
    selectedBrandId ? `data-sources:${selectedBrandId}` : null,
    () => getDataSourcesPageData(selectedBrandId ?? "")
  );
  const [topicInput, setTopicInput] = useState("");
  const [customQueryInput, setCustomQueryInput] = useState("");
  const [discovery, setDiscovery] = useState<DiscoveryWorkspaceData | null>(null);
  const [discoveryError, setDiscoveryError] = useState<string | null>(null);
  const [discoveryLoading, setDiscoveryLoading] = useState(false);

  useEffect(() => {
    setDiscovery(null);
    setDiscoveryError(null);
  }, [selectedBrandId]);

  useEffect(() => {
    if (!topicInput.trim() && data?.brand.name) {
      setTopicInput(data.brand.name);
    }
  }, [data?.brand.name, topicInput]);

  const brandHomepage = data?.channels[0]?.profileUrl;
  const recentRuns = data?.recentIngestionRuns ?? [];

  async function handleCreateTask() {
    const topic = topicInput.trim();
    if (!topic || !selectedBrandId) {
      setDiscoveryError("请先填写一个搜索主题。");
      return;
    }
    setDiscoveryLoading(true);
    setDiscoveryError(null);
    try {
      const next = await createDiscoveryTask(selectedBrandId, topic);
      setDiscovery(next);
    } catch (taskError) {
      setDiscoveryError(getRuntimeApiErrorMessage(taskError));
    } finally {
      setDiscoveryLoading(false);
    }
  }

  async function handleRefreshHotspots() {
    if (!discovery?.taskId) {
      setDiscoveryError("请先创建搜索观察任务。");
      return;
    }
    setDiscoveryLoading(true);
    setDiscoveryError(null);
    try {
      setDiscovery(await refreshDiscoveryHotspots(selectedBrandId ?? "", discovery.taskId));
    } catch (taskError) {
      setDiscoveryError(getRuntimeApiErrorMessage(taskError));
    } finally {
      setDiscoveryLoading(false);
    }
  }

  async function handleAddCustomQuery() {
    if (!discovery?.taskId) {
      setDiscoveryError("请先创建搜索观察任务。");
      return;
    }
    if (!customQueryInput.trim()) {
      setDiscoveryError("请先填写一条拓展搜索词。");
      return;
    }
    setDiscoveryLoading(true);
    setDiscoveryError(null);
    try {
      setDiscovery(await addDiscoveryQuery(selectedBrandId ?? "", discovery.taskId, customQueryInput.trim()));
      setCustomQueryInput("");
    } catch (taskError) {
      setDiscoveryError(getRuntimeApiErrorMessage(taskError));
    } finally {
      setDiscoveryLoading(false);
    }
  }

  async function handleDeleteQuery(queryId: string) {
    if (!discovery?.taskId) {
      return;
    }
    setDiscoveryLoading(true);
    setDiscoveryError(null);
    try {
      setDiscovery(await deleteDiscoveryQuery(selectedBrandId ?? "", discovery.taskId, queryId));
    } catch (taskError) {
      setDiscoveryError(getRuntimeApiErrorMessage(taskError));
    } finally {
      setDiscoveryLoading(false);
    }
  }

  const summaryCards = useMemo(
    () => [
      {
        label: "品牌主页",
        value: brandHomepage ? "已配置" : "未配置"
      },
      {
        label: "最近一次采集",
        value: getStatusLabel(data?.latestExtensionCaptureSession?.status)
      },
      {
        label: "最近一次上传",
        value: getStatusLabel(data?.latestDataImportPreview?.status)
      }
    ],
    [brandHomepage, data?.latestDataImportPreview?.status, data?.latestExtensionCaptureSession?.status]
  );

  return (
    <div className="space-y-5">
      <section className="rounded-panel border border-white/70 bg-white/85 p-6 shadow-panel backdrop-blur">
        <p className="text-sm text-quiet">Data Sources</p>
        <h1 className="mt-1 text-3xl font-semibold tracking-tight text-ink">数据源</h1>
        <p className="mt-2 text-sm text-quiet">
          当前品牌：{selectedBrandName ?? "未选择"}。在这里连接品牌主页、发起浏览器采集、上传历史数据，并保留拓展搜索与热榜观察能力。
        </p>
      </section>

      <section className="grid gap-4 md:grid-cols-3">
        {summaryCards.map((item) => (
          <Card key={item.label}>
            <div className="text-sm text-quiet">{item.label}</div>
            <div className="mt-2 text-2xl font-semibold text-ink">{item.value}</div>
          </Card>
        ))}
      </section>

      {isLoading ? (
        <Card>
          <p className="text-sm text-quiet">正在加载当前品牌的数据源工作台...</p>
        </Card>
      ) : null}

      {error ? (
        <Card>
          <p className="text-sm text-rose-600">读取数据源页面失败：{getRuntimeApiErrorMessage(error)}</p>
          <div className="mt-3">
            <Button variant="outline" onClick={() => void mutate()}>
              重试读取
            </Button>
          </div>
        </Card>
      ) : null}

      <section className="space-y-4">
        <Card className="space-y-4">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h2 className="text-lg font-semibold text-ink">搜索观察台</h2>
              <p className="mt-1 text-sm text-quiet">
                先观察搜索方向，再决定发起浏览器采集。这里保留拓展搜索和热榜能力，帮助你快速判断当前内容机会。
              </p>
            </div>
            <div className="flex gap-2">
              <Button variant="primary" onClick={handleCreateTask} disabled={discoveryLoading}>
                {discoveryLoading ? "创建中..." : "创建搜索任务"}
              </Button>
            </div>
          </div>

          <label className="block text-sm text-ink">
            搜索主题
            <input
              className="mt-2 w-full rounded-2xl border border-line bg-white px-4 py-3 text-sm text-slate-700 outline-none focus:border-ink"
              value={topicInput}
              onChange={(event) => setTopicInput(event.target.value)}
              placeholder="例如：轻量户外、敏感肌修护、办公室微运动"
            />
          </label>

          {discoveryError ? <p className="text-sm text-rose-600">{discoveryError}</p> : null}

          <div className="grid gap-4 lg:grid-cols-2">
            <div className="space-y-3 rounded-3xl border border-line bg-slate-50 px-4 py-4">
              <div className="text-sm font-semibold text-ink">拓展搜索</div>
              <div className="space-y-2">
                {discovery?.expandedQueries?.length ? (
                  discovery.expandedQueries.map((query) => (
                    <div
                      key={query.id}
                      className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-white bg-white px-3 py-3 text-sm text-slate-700"
                    >
                      <div>
                        <div className="font-medium text-ink">{query.text}</div>
                        <div className="mt-1 text-xs text-quiet">{query.category}</div>
                      </div>
                      <div className="flex gap-2">
                        <a
                          href={`https://www.xiaohongshu.com/search_result?keyword=${encodeURIComponent(query.text)}`}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center justify-center rounded-full border border-line bg-white px-3 py-2 text-xs font-medium text-ink transition hover:bg-slate-50"
                        >
                          打开搜索
                        </a>
                        {query.category === "custom" ? (
                          <button
                            type="button"
                            className="inline-flex items-center justify-center rounded-full border border-line bg-white px-3 py-2 text-xs font-medium text-slate-600 transition hover:bg-slate-50"
                            onClick={() => void handleDeleteQuery(query.id)}
                          >
                            删除
                          </button>
                        ) : null}
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="rounded-2xl border border-dashed border-line bg-white px-4 py-4 text-sm text-quiet">
                    当前还没有搜索任务。点击上方“创建搜索任务”后，这里会展示系统生成的拓展搜索词。
                  </div>
                )}
              </div>
              <div className="flex gap-2">
                <input
                  className="flex-1 rounded-2xl border border-line bg-white px-4 py-3 text-sm text-slate-700 outline-none focus:border-ink"
                  value={customQueryInput}
                  onChange={(event) => setCustomQueryInput(event.target.value)}
                  placeholder="补充一条你更熟悉的搜索词"
                />
                <Button variant="outline" onClick={handleAddCustomQuery} disabled={discoveryLoading}>
                  添加
                </Button>
              </div>
            </div>

            <div className="space-y-3 rounded-3xl border border-line bg-slate-50 px-4 py-4">
              <div className="text-sm font-semibold text-ink">热榜</div>
              <div className="flex justify-end">
                <Button variant="outline" onClick={handleRefreshHotspots} disabled={discoveryLoading}>
                  {discoveryLoading ? "刷新中..." : "更新热榜"}
                </Button>
              </div>
              {discovery?.hotspots?.length ? (
                <div className="grid gap-3 xl:grid-cols-3">
                  {HOTSPOT_ORDER.map((metric) => {
                    const list = discovery.hotspots.find((item) => item.metric === metric);
                    return (
                      <div key={metric} className="space-y-2">
                        <div className="text-xs font-semibold uppercase tracking-[0.14em] text-quiet">
                          {getHotspotMetricLabel(metric)}
                        </div>
                        {list?.items.length ? (
                          list.items.slice(0, 4).map((item, index) => (
                            <a
                              key={`${metric}-${index}`}
                              href={item.sourceUrl}
                              target="_blank"
                              rel="noreferrer"
                              className="block rounded-2xl border border-white bg-white px-3 py-3 text-sm text-slate-700"
                            >
                              <div className="line-clamp-2 font-medium text-ink">{item.title}</div>
                              <div className="mt-1 text-xs text-quiet">
                                {item.author || "未知作者"} · 赞 {item.likes} · 藏 {item.collections} · 评 {item.comments}
                              </div>
                            </a>
                          ))
                        ) : (
                          <div className="rounded-2xl border border-dashed border-white bg-white px-3 py-3 text-sm text-quiet">
                            当前还没有热榜结果。
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="rounded-2xl border border-dashed border-white bg-white px-4 py-4 text-sm text-quiet">
                  创建搜索任务后，这里会展示点赞、评论、收藏维度的热榜结果。
                </div>
              )}
            </div>
          </div>
        </Card>

        <div className="space-y-2">
          <div className="px-1">
            <h2 className="text-lg font-semibold text-ink">数据入口工作区</h2>
            <p className="mt-1 text-sm text-quiet">确认搜索方向后，在这里正式发起浏览器采集或上传历史表格。</p>
          </div>
          <BrandIngestionPanel
            brandId={data?.brand.id ?? selectedBrandId ?? ""}
            channels={data?.channels ?? []}
            initialSourceSyncSession={data?.latestExtensionCaptureSession}
            initialHistoricalPreview={data?.latestDataImportPreview}
          />
        </div>
      </section>

      <Card className="space-y-4">
        <div>
          <h2 className="text-lg font-semibold text-ink">最近进入系统的数据</h2>
          <p className="mt-1 text-sm text-quiet">浏览器采集和历史数据上传的最近处理记录都会显示在这里。</p>
        </div>
        {recentRuns.length ? (
          <div className="space-y-3">
            {recentRuns.map((run) => (
              <div
                key={run.id}
                className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-line bg-slate-50 px-4 py-4 text-sm text-slate-700"
              >
                <div>
                  <div className="font-medium text-ink">{run.type}</div>
                  <div className="mt-1 text-xs text-quiet">
                    {run.sourceLabel} · {formatTimestamp(run.createdAt)}
                  </div>
                </div>
                <div className="text-xs text-slate-600">
                  状态：{getStatusLabel(run.status)} · 新增 {run.importedCount} · 去重 {run.dedupedCount}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="rounded-2xl border border-dashed border-line bg-slate-50 px-4 py-5 text-sm text-quiet">
            还没有任何数据源记录。先创建一次浏览器采集，或上传历史表格。
          </div>
        )}
      </Card>
    </div>
  );
}
