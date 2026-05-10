const state = {
  taskId: null,
  task: {
    snapshot: null,
    snapshotVersion: null,
    pollTimerId: null,
    isPolling: false,
  },
  queries: {
    status: "idle",
  },
  collection: {
    status: "idle",
  },
  recommendations: {
    status: "idle",
  },
  hotspots: {
    snapshot: null,
    status: "idle",
    isLoading: false,
    timerId: null,
  },
  scraper: {
    readiness: null,
    activeQueryId: null,
    activeScrapeStatus: null,
    pollTimerId: null,
  },
};

const QUERY_CATEGORY_LABELS = {
  crowd: "人群",
  scenario: "场景",
  problem: "问题",
  compare: "对比",
  decision: "决策",
  custom: "自定义",
};

const HOTSPOT_TITLES = {
  likes: "最高点赞",
  collections: "最多收藏",
  comments: "最多评论",
};

const topicInput = document.getElementById("topic-input");
const customQueryInput = document.getElementById("custom-query-input");
const manualInput = document.getElementById("manual-input");
const topicSearchLink = document.getElementById("topic-search-link");

const topicQuickCard = document.getElementById("topic-quick-card");
const taskStatus = document.getElementById("task-status");
const taskSnapshotStatus = document.getElementById("task-snapshot-status");
const queryStatus = document.getElementById("query-status");
const queryAutoList = document.getElementById("query-auto-list");
const queryCustomList = document.getElementById("query-custom-list");
const collectionSummary = document.getElementById("collection-summary");
const collectionStatus = document.getElementById("collection-status");
const recommendationStatus = document.getElementById("recommendation-status");
const recommendationList = document.getElementById("recommendation-list");
const hotspotStatus = document.getElementById("hotspot-status");
const hotspotUpdatedAt = document.getElementById("hotspot-updated-at");
const hotspotLists = document.getElementById("hotspot-lists");
const scraperBanner = document.getElementById("scraper-banner");

document.getElementById("create-task-button").addEventListener("click", createTask);
document.getElementById("add-custom-query-button").addEventListener("click", addCustomQueries);
document.getElementById("manual-import-button").addEventListener("click", importManualSeeds);
document.getElementById("hotspot-refresh-button").addEventListener("click", () => refreshHotspots({ force: true }));
document.addEventListener("visibilitychange", handleVisibilityChange);
topicInput.addEventListener("input", renderTaskModule);

renderTaskModule();
renderQueryModule();
renderCollectionModule();
renderRecommendationModule();
renderHotspotModule();
void initScraperReadiness();

async function createTask() {
  const topic = topicInput.value.trim();
  if (!topic) {
    setTaskStatus("请先输入一个搜索主题。");
    return;
  }

  setTaskStatus("正在创建任务...");
  const response = await fetch("/mvp/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ topic }),
  });
  if (!response.ok) {
    setTaskStatus("创建任务失败。");
    return;
  }

  const data = await response.json();
  state.taskId = data.task_id;
  await refreshTaskSnapshot({ silent: true });
  startTaskSnapshotPolling();
  setTaskStatus(data.query_generation_notice
    ? `任务已创建，插件会自动识别当前任务；采集后工作台会自动刷新。${data.query_generation_notice}`
    : "任务已创建，插件会自动识别当前任务；采集后工作台会自动刷新。");
  state.hotspots.snapshot = null;
  state.hotspots.status = "idle";
  renderHotspotModule();
  void refreshHotspots({ force: true, silent: true });
}

async function refreshTaskSnapshot({ silent = false, reason = "manual" } = {}) {
  if (!state.taskId) {
    setTaskStatus("请先创建任务。");
    return;
  }

  if (!silent) {
    setTaskStatus("正在同步任务状态...");
  }
  const response = await fetch(`/mvp/tasks/${state.taskId}`);
  if (!response.ok) {
    setTaskStatus("同步任务状态失败。");
    return;
  }

  applyTaskSnapshot(await response.json());
  renderTaskModule();
  renderQueryModule();
  renderCollectionModule();
  renderRecommendationModule();
  startTaskSnapshotPolling();
  if (!silent) {
    setTaskStatus(reason === "auto" ? "检测到新采集，候选方向已自动更新。" : "任务快照已更新。");
  }
}

async function addCustomQueries() {
  if (!state.taskId) {
    setQueryStatus("请先创建任务。");
    return;
  }

  const text = customQueryInput.value.trim();
  if (!text) {
    setQueryStatus("请先输入至少一条自定义拓展词。");
    return;
  }

  setQueryStatus("正在添加自定义拓展词...");
  const response = await fetch(`/mvp/tasks/${state.taskId}/queries`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!response.ok) {
    setQueryStatus("添加自定义拓展词失败。");
    return;
  }

  const data = await response.json();
  customQueryInput.value = "";
  await refreshTaskSnapshot({ silent: true });
  if (data.created_count > 0 && data.skipped_count > 0) {
    setQueryStatus(`已添加 ${data.created_count} 条自定义词，跳过 ${data.skipped_count} 条重复内容。`);
  } else if (data.created_count > 0) {
    setQueryStatus(`已添加 ${data.created_count} 条自定义词。`);
  } else {
    setQueryStatus("输入的自定义拓展词都已存在。");
  }
}

async function deleteCustomQuery(queryId) {
  if (!state.taskId || !queryId) {
    return;
  }

  const response = await fetch(`/mvp/tasks/${state.taskId}/queries/${encodeURIComponent(queryId)}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    setQueryStatus("删除自定义拓展词失败。");
    return;
  }

  await refreshTaskSnapshot({ silent: true });
  setQueryStatus("已删除这条自定义拓展词。");
}

async function importManualSeeds() {
  if (!state.taskId) {
    setCollectionStatus("请先创建任务。");
    return;
  }

  const text = manualInput.value.trim();
  if (!text) {
    setCollectionStatus("请先输入要补充的链接或文本线索。");
    return;
  }

  setCollectionStatus("正在导入手动补充内容...");
  const response = await fetch(`/mvp/tasks/${state.taskId}/manual-seeds`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!response.ok) {
    setCollectionStatus("导入手动补充内容失败。");
    return;
  }

  manualInput.value = "";
  await refreshTaskSnapshot({ silent: true });
  setCollectionStatus("手动补充内容已导入。");
}

async function refreshHotspots({ force = false, silent = false } = {}) {
  if (!state.taskId || state.hotspots.isLoading) {
    return;
  }

  state.hotspots.isLoading = true;
  state.hotspots.status = "loading";
  if (!silent) {
    setHotspotStatus(force ? "正在更新热点..." : "正在加载热点...");
  }
  renderHotspotModule();

  const endpoint = force
    ? `/mvp/tasks/${state.taskId}/hotspots/refresh`
    : `/mvp/tasks/${state.taskId}/hotspots`;
  const response = await fetch(endpoint, { method: force ? "POST" : "GET" }).catch(() => null);

  state.hotspots.isLoading = false;
  if (!response || !response.ok) {
    state.hotspots.status = "error";
    setHotspotStatus("热点模块加载失败。");
    renderHotspotModule();
    return;
  }

  const data = await response.json();
  state.hotspots.snapshot = data;
  state.hotspots.status = data.status || "empty";

  if (data.status === "error") {
    setHotspotStatus(data.error_message || "热点刷新失败，已保留上次结果。");
  } else if (data.status === "ready") {
    setHotspotStatus("热点榜已更新。");
  } else {
    setHotspotStatus("当前还没有热点数据。");
  }
  renderHotspotModule();
}

function renderTaskModule() {
  const snapshot = state.task.snapshot;
  const currentTopic = (snapshot?.topic || topicInput.value || "").trim();
  topicSearchLink.href = buildXhsSearchUrl(currentTopic || "");
  topicSearchLink.setAttribute("aria-disabled", currentTopic ? "false" : "true");
  topicSearchLink.style.pointerEvents = currentTopic ? "auto" : "none";
  topicSearchLink.style.opacity = currentTopic ? "1" : "0.55";

  if (!snapshot) {
    topicQuickCard.innerHTML = `
      <div class="topic-quick-copy">
        <span class="field-label">当前搜索词</span>
        <strong>${escapeHtml(currentTopic || "未创建")}</strong>
      </div>
      <div class="subtle">这里始终保留用户原始输入的搜索入口。</div>
    `;
    renderTaskSnapshotStatus();
    return;
  }

  topicQuickCard.innerHTML = `
    <div class="topic-quick-copy">
      <span class="field-label">当前搜索词</span>
      <strong>${escapeHtml(snapshot.topic)}</strong>
    </div>
    <div class="subtle">当前任务：已采集 ${formatCount(snapshot.capture_count)} 次，候选方向 ${formatCount(snapshot.candidate_count)} 个，最近更新 ${formatDateTime(snapshot.updated_at)}。${formatQueryGenerationStatus(snapshot)}</div>
  `;
  renderTaskSnapshotStatus();
}

function renderQueryModule() {
  const snapshot = state.task.snapshot;
  if (!snapshot) {
    queryStatus.textContent = "默认拓展词和自定义拓展词会分别维护。";
    queryAutoList.innerHTML = `<div class="empty-note">创建任务后，这里会出现默认的 5 条拓展搜索词。</div>`;
    queryCustomList.innerHTML = `<div class="empty-note">你添加的自定义搜索词会显示在这里。</div>`;
    return;
  }

  const autoQueries = getAutomaticQueries(snapshot);
  const customQueries = getCustomQueries(snapshot);

  queryAutoList.innerHTML = autoQueries.length
    ? autoQueries.map((query) => renderQueryCard(query, { deletable: false })).join("")
    : `<div class="empty-note">当前没有默认拓展搜索词。</div>`;

  queryCustomList.innerHTML = customQueries.length
    ? customQueries.map((query) => renderQueryCard(query, { deletable: true })).join("")
    : `<div class="empty-note">还没有自定义拓展词。</div>`;
  queryStatus.textContent = `当前有 ${autoQueries.length} 条默认拓展词，${customQueries.length} 条自定义拓展词。`;

  queryCustomList.querySelectorAll("[data-delete-query-id]").forEach((button) => {
    button.addEventListener("click", () => deleteCustomQuery(button.getAttribute("data-delete-query-id")));
  });
  [queryAutoList, queryCustomList].forEach((list) => {
    list.querySelectorAll("[data-auto-scrape]").forEach((btn) => {
      btn.addEventListener("click", () =>
        void triggerAutoScrape(btn.getAttribute("data-auto-scrape"), btn.getAttribute("data-query-text"))
      );
    });
  });
}

function renderCollectionModule() {
  const snapshot = state.task.snapshot;
  if (!snapshot) {
    collectionStatus.textContent = "采集完成后，这里会实时显示累计结果。";
    collectionSummary.innerHTML = `
      <div class="summary-card"><span>采集次数</span><strong>0</strong></div>
      <div class="summary-card"><span>累计内容</span><strong>0</strong></div>
      <div class="summary-card"><span>手动补充</span><strong>0</strong></div>
    `;
    return;
  }

  const summary = snapshot.collection_summary || {};
  collectionStatus.textContent = `当前累计 ${summary.capture_batch_count || snapshot.imported_page_count || 0} 次采集，保留 ${summary.deduped_item_count || snapshot.imported_item_count || 0} 条内容。`;
  collectionSummary.innerHTML = `
    <div class="summary-card">
      <span>采集次数</span>
      <strong>${escapeHtml(String(summary.capture_batch_count || snapshot.imported_page_count || 0))}</strong>
    </div>
    <div class="summary-card">
      <span>累计内容</span>
      <strong>${escapeHtml(String(summary.deduped_item_count || snapshot.imported_item_count || 0))}</strong>
    </div>
    <div class="summary-card">
      <span>手动补充</span>
      <strong>${escapeHtml(String(summary.manual_seed_count || 0))}</strong>
    </div>
  `;
}

function renderRecommendationModule() {
  const snapshot = state.task.snapshot;
  const notes = snapshot?.recommended_notes || [];
  const diagnostics = snapshot?.recommended_notes_diagnostics || {};
  const totalCount = Number(diagnostics.total_note_count || 0);
  const passCount = Number(diagnostics.hard_filter_pass_count || 0);
  const recommendedCount = Number(diagnostics.llm_recommended_count || notes.length || 0);
  const llmExcludedCount = Number(diagnostics.llm_excluded_count || 0);
  const filterReasons = Array.isArray(diagnostics.hard_filter_reasons) ? diagnostics.hard_filter_reasons : [];
  if (!notes.length) {
    recommendationStatus.textContent = buildRecommendationStatus({
      totalCount,
      passCount,
      recommendedCount,
      llmExcludedCount,
      filterReasons,
      hasNotes: false,
    });
    recommendationList.innerHTML = `<div class="empty-note">当前还没有推荐笔记。先去采集一些结果，或手动补充线索。</div>`;
    return;
  }

  recommendationStatus.textContent = buildRecommendationStatus({
    totalCount,
    passCount,
    recommendedCount,
    llmExcludedCount,
    filterReasons,
    hasNotes: true,
  });
  recommendationList.innerHTML = notes.map((note) => `
    <article class="recommend-card">
      <div class="recommend-head">
        <a class="recommend-title" href="${escapeAttribute(note.source_url)}" target="_blank" rel="noopener">
          ${escapeHtml(note.title)}
        </a>
        <span class="score-badge" title="${escapeAttribute(note.score_reason || "暂无评分理由")}">评分 ${Number(note.score).toFixed(2)}</span>
      </div>
      <div class="metric-row">
        <span class="metric-chip">点赞 ${formatCount(note.likes)}</span>
        <span class="metric-chip">收藏 ${formatCount(note.collections)}</span>
        <span class="metric-chip">评论 ${formatCount(note.comments)}</span>
        <span class="metric-chip">覆盖 ${formatCount(note.query_coverage_count)} 个搜索词</span>
      </div>
      <div class="recommend-block">
        <h3>值得查看的原因</h3>
        <p>${escapeHtml(note.why_recommended || "这条笔记和当前搜索目标匹配度较高。")}</p>
      </div>
      <div class="recommend-block">
        <h3>第一段内容</h3>
        <p>${escapeHtml(note.excerpt || "暂无摘要内容。")}</p>
      </div>
      <div class="subtle">作者：${escapeHtml(note.author || "未知作者")}</div>
    </article>
  `).join("");
}

function buildRecommendationStatus({
  totalCount,
  passCount,
  recommendedCount,
  llmExcludedCount,
  filterReasons,
  hasNotes,
}) {
  if (!totalCount) {
    return hasNotes ? `当前展示 ${recommendedCount} 条推荐笔记。` : "当前还没有推荐笔记。";
  }

  const parts = [
    `共看了 ${totalCount} 条候选`,
    `通过硬筛 ${passCount} 条`,
    `最终推荐 ${recommendedCount} 条`,
  ];
  const hardFilteredCount = Math.max(totalCount - passCount, 0);
  if (hardFilteredCount > 0 && filterReasons.length) {
    const reasonText = filterReasons
      .slice(0, 3)
      .map((reason) => `${reason.label} ${reason.count} 条`)
      .join("，");
    parts.push(`硬筛排除 ${hardFilteredCount} 条：${reasonText}`);
  } else if (llmExcludedCount > 0) {
    parts.push(`LLM 复核后排除 ${llmExcludedCount} 条`);
  }
  return parts.join("；") + "。";
}

function renderHotspotModule() {
  const snapshot = state.hotspots.snapshot;
  hotspotUpdatedAt.textContent = snapshot?.generated_at
    ? `最近更新：${formatDateTime(snapshot.generated_at)}`
    : "最近更新：--";

  if (state.hotspots.isLoading && !snapshot) {
    hotspotLists.innerHTML = `<div class="empty-note">正在加载热点榜...</div>`;
    return;
  }

  if (!snapshot || !snapshot.lists || !snapshot.lists.length) {
    hotspotLists.innerHTML = `<div class="empty-note">当前还没有热点榜数据。</div>`;
    return;
  }

  hotspotLists.innerHTML = getOrderedHotspotLists(snapshot.lists).map((list) => `
    <section class="hotspot-card">
      <h3>${escapeHtml(HOTSPOT_TITLES[list.metric] || list.metric)}</h3>
      <div class="hotspot-list">
        ${list.items.length
          ? list.items.map((item, index) => `
              <article class="hotspot-item">
                <div class="hotspot-metrics">
                  <span class="hotspot-rank">${index + 1}</span>
                  <div class="metric-row">
                    <span class="metric-chip">点赞 ${formatCount(item.likes)}</span>
                    <span class="metric-chip">收藏 ${formatCount(item.collections)}</span>
                    <span class="metric-chip">评论 ${formatCount(item.comments)}</span>
                  </div>
                </div>
                <a class="hotspot-title" href="${escapeAttribute(item.source_url)}" target="_blank" rel="noopener">${escapeHtml(item.title)}</a>
              </article>
            `).join("")
          : `<div class="empty-note">当前这组榜单还没有内容。</div>`}
      </div>
    </section>
  `).join("");
}

async function initScraperReadiness() {
  const resp = await fetch("/api/scraper/readiness").catch(() => null);
  if (!resp?.ok) return;
  state.scraper.readiness = await resp.json();
  renderScraperBanner();
}

function renderScraperBanner() {
  const r = state.scraper.readiness;
  scraperBanner.hidden = !r || r.logged_in;
}

async function triggerAutoScrape(queryId, queryText) {
  if (!state.taskId || state.scraper.activeQueryId) return;
  const resp = await fetch(`/api/tasks/${state.taskId}/auto-scrape`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ keyword: queryText, scroll_count: 5 }),
  }).catch(() => null);
  if (!resp?.ok) { setQueryStatus("自动采集触发失败。"); return; }
  state.scraper.activeQueryId = queryId;
  state.scraper.activeScrapeStatus = null;
  renderQueryModule();
  startScrapeStatusPolling();
}

function startScrapeStatusPolling() {
  stopScrapeStatusPolling();
  state.scraper.pollTimerId = window.setInterval(pollScrapeStatus, 1000);
}

function stopScrapeStatusPolling() {
  if (state.scraper.pollTimerId) {
    window.clearInterval(state.scraper.pollTimerId);
    state.scraper.pollTimerId = null;
  }
}

async function pollScrapeStatus() {
  if (!state.taskId || !state.scraper.activeQueryId) return;
  const resp = await fetch(`/api/tasks/${state.taskId}/scrape-status`).catch(() => null);
  if (!resp?.ok) return;
  const status = await resp.json();
  state.scraper.activeScrapeStatus = status;
  const el = document.querySelector(`[data-scrape-status="${state.scraper.activeQueryId}"]`);
  if (el) { el.hidden = false; el.textContent = formatScrapePhase(status); }
  if (["done", "error", "login_required"].includes(status.phase)) {
    stopScrapeStatusPolling();
    state.scraper.activeQueryId = null;
    renderQueryModule();
    if (status.phase === "done") {
      void refreshTaskFromCandidateDirections();
      setTaskStatus("自动采集完成，候选方向已更新。");
    }
  }
}

function formatScrapePhase(s) {
  if (!s) return "正在启动...";
  if (s.phase === "scrolling") return `滚动 ${s.scroll_index}/${s.scroll_total}，已采集 ${s.items_count} 条`;
  if (s.phase === "done") return `✅ 采集完成，共 ${s.items_count} 条`;
  if (s.phase === "error") return `❌ 失败：${s.error_message}`;
  if (s.phase === "login_required") return "⚠️ 采集器未登录，请完成登录";
  if (s.phase === "navigating") return "正在打开小红书搜索页...";
  return "正在启动采集器...";
}

function renderQueryCard(query, { deletable }) {
  const isBusy = !!state.scraper.activeQueryId;
  const isActive = state.scraper.activeQueryId === query.query_id;
  const statusText = isActive ? escapeHtml(formatScrapePhase(state.scraper.activeScrapeStatus)) : "";
  return `
    <article class="query-card">
      <div class="query-row">
        <div class="query-tags">
          <span class="tag">${escapeHtml(getQueryLabel(query.category))}</span>
        </div>
        <div class="button-row">
          <a class="link-button secondary" href="${buildXhsSearchUrl(query.query_text)}" target="_blank" rel="noopener">打开小红书搜索</a>
          <button class="secondary" type="button" data-auto-scrape="${escapeAttribute(query.query_id)}" data-query-text="${escapeAttribute(query.query_text)}" ${!state.taskId || isBusy ? "disabled" : ""}>🔄 自动采集</button>
          ${deletable ? `<button class="ghost" type="button" data-delete-query-id="${escapeAttribute(query.query_id)}">删除</button>` : ""}
        </div>
      </div>
      <strong>${escapeHtml(query.query_text)}</strong>
      <div class="status--inline" data-scrape-status="${escapeAttribute(query.query_id)}" ${isActive ? "" : "hidden"}>${statusText}</div>
    </article>
  `;
}

function getAutomaticQueries(snapshot) {
  return (snapshot.expanded_queries || [])
    .filter((query) => query.category !== "core" && query.category !== "custom")
    .slice(0, 5);
}

function getCustomQueries(snapshot) {
  return (snapshot.expanded_queries || []).filter((query) => query.category === "custom");
}

function getOrderedHotspotLists(lists) {
  const order = { likes: 0, collections: 1, comments: 2 };
  return [...lists].sort((left, right) => (order[left.metric] ?? 9) - (order[right.metric] ?? 9));
}

function startHotspotPolling() {
    stopHotspotPolling();
    if (!state.taskId) {
      return;
  }
  state.hotspots.timerId = window.setInterval(() => {
    if (document.visibilityState !== "visible") {
      return;
    }
    void refreshHotspots({ force: true, silent: true });
  }, 90000);
}

function stopHotspotPolling() {
  if (state.hotspots.timerId) {
    window.clearInterval(state.hotspots.timerId);
    state.hotspots.timerId = null;
  }
}

function handleVisibilityChange() {
  if (document.visibilityState !== "visible") {
    stopHotspotPolling();
    stopTaskSnapshotPolling();
    return;
  }
  startTaskSnapshotPolling();
  startHotspotPolling();
}

function applyTaskSnapshot(snapshot) {
  state.task.snapshot = snapshot;
  state.task.snapshotVersion = Number(snapshot?.snapshot_version ?? 0);
}

function startTaskSnapshotPolling() {
  stopTaskSnapshotPolling();
  if (!state.taskId || document.visibilityState !== "visible") {
    return;
  }
  state.task.pollTimerId = window.setInterval(pollTaskSnapshotVersion, 2000);
  renderTaskSnapshotStatus();
}

function stopTaskSnapshotPolling() {
  if (state.task.pollTimerId) {
    window.clearInterval(state.task.pollTimerId);
    state.task.pollTimerId = null;
  }
  renderTaskSnapshotStatus();
}

async function pollTaskSnapshotVersion() {
  if (!state.taskId || state.task.isPolling) {
    return;
  }
  state.task.isPolling = true;
  try {
    const response = await fetch(`/api/tasks/${state.taskId}/snapshot`);
    if (!response.ok) {
      return;
    }
    const version = await response.json();
    const nextVersion = Number(version.snapshot_version ?? 0);
    if (state.task.snapshotVersion !== null && nextVersion !== state.task.snapshotVersion) {
      await refreshTaskFromCandidateDirections();
      setTaskStatus("检测到新采集，候选方向已自动更新。");
    }
  } finally {
    state.task.isPolling = false;
  }
}

async function refreshTaskFromCandidateDirections() {
  const response = await fetch(`/api/tasks/${state.taskId}/candidate-directions`);
  if (!response.ok) {
    setTaskStatus("自动刷新候选方向失败，请稍后重试或重新打开工作台。");
    return;
  }
  applyTaskSnapshot(await response.json());
  renderTaskModule();
  renderQueryModule();
  renderCollectionModule();
  renderRecommendationModule();
}

function renderTaskSnapshotStatus() {
  if (!taskSnapshotStatus) {
    return;
  }
  const snapshot = state.task.snapshot;
  if (!state.taskId || !snapshot) {
    taskSnapshotStatus.textContent = "创建任务后会自动监听采集结果变化。";
    return;
  }
  const listening = state.task.pollTimerId ? "自动监听中" : "自动监听暂停";
  taskSnapshotStatus.textContent = `${listening}：当前任务「${snapshot.topic}」，已采集 ${formatCount(snapshot.capture_count)} 次，候选方向 ${formatCount(snapshot.candidate_count)} 个，最近更新 ${formatDateTime(snapshot.updated_at)}。`;
}

function setTaskStatus(message) {
  taskStatus.textContent = message;
  taskStatus.classList.add("is-strong");
}

function setQueryStatus(message) {
  queryStatus.textContent = message;
  queryStatus.classList.add("is-strong");
}

function setCollectionStatus(message) {
  collectionStatus.textContent = message;
  collectionStatus.classList.add("is-strong");
}

function setHotspotStatus(message) {
  hotspotStatus.textContent = message;
  hotspotStatus.classList.add("is-strong");
}

function buildXhsSearchUrl(query) {
  return `https://www.xiaohongshu.com/search_result?keyword=${encodeURIComponent(query)}`;
}

function getQueryLabel(category) {
  return QUERY_CATEGORY_LABELS[category] || "拓展";
}

function formatCount(value) {
  const number = Number(value || 0);
  return Number.isFinite(number) ? String(Math.max(0, number)) : "0";
}

function formatDateTime(value) {
  try {
    return new Date(value).toLocaleString("zh-CN", {
      hour12: false,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "--";
  }
}

function formatQueryGenerationStatus(snapshot) {
  if (!snapshot?.query_generation_source) {
    return "";
  }
  if (snapshot.query_generation_source === "llm") {
    return " 默认拓展词来源：AI 生成。";
  }
  return snapshot.query_generation_notice
    ? ` 默认拓展词来源：规则降级。${snapshot.query_generation_notice}`
    : " 默认拓展词来源：规则降级。";
}

async function buildHttpErrorMessage(response, fallback) {
  const statusText = [response.status, response.statusText].filter(Boolean).join(" ");

  try {
    const payload = await response.clone().json();
    const detail = typeof payload?.detail === "string"
      ? payload.detail.trim()
      : typeof payload?.message === "string"
        ? payload.message.trim()
        : "";
    if (detail) {
      return `${fallback}（${statusText}：${detail}）`;
    }
  } catch {
    // Fall back to plain-text parsing below.
  }

  try {
    const text = (await response.text()).trim();
    if (text) {
      return `${fallback}（${statusText}：${text.slice(0, 120)}）`;
    }
  } catch {
    // Ignore and fall back to the generic status-only message.
  }

  return statusText ? `${fallback}（${statusText}）` : fallback;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function escapeAttribute(value) {
  return escapeHtml(value ?? "");
}
