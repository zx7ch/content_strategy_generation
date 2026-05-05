(() => {
if (window.__XHS_MVP_CAPTURE_PANEL_BOOTED__) {
  window.dispatchEvent(new CustomEvent("xhs-mvp-capture-panel:mount"));
  return;
}
window.__XHS_MVP_CAPTURE_PANEL_BOOTED__ = true;

const SEARCH_PAGE_MARKERS = [".note-item", "[data-note-id]", ".search-content", ".feeds-page"];
const DETAIL_PAGE_MARKERS = ["#detail-title", ".note-content", ".author-container", ".interaction-container"];
const PANEL_ID = "xhs-mvp-capture-panel";
const PANEL_STYLE_ID = "xhs-mvp-capture-panel-style";

let activeTaskContext = null;
let lastCapturedVisibleIds = new Set();
let visibleRefreshTimer = null;
let panelObserver = null;
let captureInFlight = false;
let pendingCaptureVisibleIds = new Set();

bootCapturePanel();
window.addEventListener("xhs-mvp-capture-panel:mount", () => {
  mountCapturePanelWhenReady();
  startPanelPersistenceObserver();
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "PING_CAPTURE_PANEL") {
    sendResponse({ ok: true, panelMounted: Boolean(document.getElementById(PANEL_ID)) });
    return;
  }

  if (message?.type === "MOUNT_CAPTURE_PANEL") {
    mountCapturePanelWhenReady();
    startPanelPersistenceObserver();
    sendResponse({ ok: true, panelMounted: true });
    return;
  }

  if (message?.type !== "extract-page") {
    return;
  }

  try {
    console.info("[XHS MVP] Starting page extraction", { url: window.location.href });
    const payload = extractCurrentPage();
    console.info("[XHS MVP] Page extraction completed", {
      pageType: payload.page_type,
      queryText: payload.query_text,
      itemCount: payload.items.length
    });
    sendResponse({ ok: true, payload });
  } catch (error) {
    console.error("[XHS MVP] Page extraction failed", error);
    sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
  return true;
});

function initializeActiveTaskContext() {
  if (!chrome?.runtime?.sendMessage) {
    activeTaskContext = null;
    updatePanelTaskState("runtime_error");
    setPanelFeedback("插件运行时暂不可用，请重新加载扩展后刷新页面。");
    return;
  }
  try {
    chrome.runtime.sendMessage({ type: "ACTIVE_TASK_REQUEST" }, (response) => {
    if (chrome.runtime.lastError) {
      console.warn("[XHS MVP] Active task initialization failed", chrome.runtime.lastError.message);
      activeTaskContext = null;
      updatePanelTaskState("runtime_error");
      setPanelFeedback(toPanelMessage(chrome.runtime.lastError.message, "runtime_error"));
      return;
    }
    if (!response?.ok) {
      activeTaskContext = null;
      updatePanelTaskState(response?.code || "server_unavailable");
      setPanelFeedback(toPanelMessage(response?.error, response?.code || "server_unavailable"));
      return;
    }
    activeTaskContext = response?.active_task || null;
    updatePanelTaskState(response?.error_summary?.code);
    if (!activeTaskContext && response?.error_summary?.code) {
      setPanelFeedback(toPanelMessage(response.error_summary.message, response.error_summary.code));
    }
    console.info("[XHS MVP] Active task initialized", {
      taskId: activeTaskContext?.task_id || null,
      status: activeTaskContext?.status || "missing"
    });
    });
  } catch (error) {
    activeTaskContext = null;
    updatePanelTaskState("runtime_error");
    setPanelFeedback(toPanelMessage(error instanceof Error ? error.message : String(error), "runtime_error"));
  }
}

function bootCapturePanel() {
  mountCapturePanelWhenReady();
  startVisibleCountRefresh();
  startPanelPersistenceObserver();
  initializeActiveTaskContext();
}

function mountCapturePanelWhenReady() {
  if (!document.body) {
    window.setTimeout(mountCapturePanelWhenReady, 250);
    return;
  }
  injectCapturePanel();
}

function injectCapturePanel() {
  if (document.getElementById(PANEL_ID)) {
    return;
  }
  if (!document.body) {
    return;
  }
  injectPanelStyles();
  const panel = document.createElement("div");
  panel.id = PANEL_ID;
  panel.setAttribute("role", "region");
  panel.setAttribute("aria-label", "XHS 采集助手");
  panel.style.cssText = [
    "position: fixed !important",
    "right: 20px !important",
    "bottom: 24px !important",
    "z-index: 2147483647 !important",
    "display: block !important",
    "visibility: visible !important",
    "opacity: 1 !important",
    "pointer-events: auto !important",
    "transform: translateZ(0) !important"
  ].join("; ");
  panel.innerHTML = `
    <div class="xhs-mvp-card">
      <div class="xhs-mvp-head">
        <div>
          <div class="xhs-mvp-kicker">XHS 采集助手</div>
          <strong class="xhs-mvp-title">当前页采集</strong>
        </div>
        <button id="xhs-mvp-hide-btn" class="xhs-mvp-icon-btn" type="button">收起</button>
      </div>
      <div id="xhs-mvp-task" class="xhs-mvp-line">同步任务中...</div>
      <div id="xhs-mvp-count" class="xhs-mvp-line">当前页可见笔记：检测中...</div>
      <div id="xhs-mvp-feedback" class="xhs-mvp-feedback">只采集当前可见内容，不自动滚动、不翻页。</div>
      <button id="xhs-mvp-capture-btn" class="xhs-mvp-primary-btn" type="button">采集当前页</button>
      <div class="xhs-mvp-actions">
        <button id="xhs-mvp-retry-btn" class="xhs-mvp-secondary-btn" type="button">重试连接/同步</button>
      </div>
    </div>
  `;
  document.body.appendChild(panel);

  document.getElementById("xhs-mvp-capture-btn")?.addEventListener("click", handlePanelCaptureClick);
  document.getElementById("xhs-mvp-retry-btn")?.addEventListener("click", handlePanelRetryClick);
  document.getElementById("xhs-mvp-hide-btn")?.addEventListener("click", () => {
    panel.classList.toggle("is-collapsed");
    const collapsed = panel.classList.contains("is-collapsed");
    document.getElementById("xhs-mvp-hide-btn").textContent = collapsed ? "展开" : "收起";
  });

  updatePanelTaskState();
  refreshVisibleCount();
}

function startPanelPersistenceObserver() {
  if (panelObserver) {
    return;
  }
  panelObserver = new MutationObserver(() => {
    if (!document.getElementById(PANEL_ID)) {
      mountCapturePanelWhenReady();
    }
  });
  const target = document.documentElement || document.body;
  if (target) {
    panelObserver.observe(target, { childList: true, subtree: true });
  }
}

function injectPanelStyles() {
  if (document.getElementById(PANEL_STYLE_ID)) {
    return;
  }
  const style = document.createElement("style");
  style.id = PANEL_STYLE_ID;
  style.textContent = `
    #${PANEL_ID} {
      position: fixed;
      right: 20px;
      bottom: 24px;
      z-index: 2147483647;
      width: 286px;
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Noto Sans SC", sans-serif;
      color: #231c17;
      pointer-events: auto;
    }
    #${PANEL_ID} .xhs-mvp-card {
      display: grid;
      gap: 10px;
      padding: 14px;
      border: 1px solid rgba(115, 77, 55, 0.2);
      border-radius: 20px;
      background: rgba(255, 250, 244, 0.96);
      box-shadow: 0 18px 44px rgba(55, 38, 28, 0.18);
      backdrop-filter: blur(12px);
    }
    #${PANEL_ID}.is-collapsed .xhs-mvp-line,
    #${PANEL_ID}.is-collapsed .xhs-mvp-feedback,
    #${PANEL_ID}.is-collapsed .xhs-mvp-primary-btn,
    #${PANEL_ID}.is-collapsed .xhs-mvp-actions {
      display: none;
    }
    #${PANEL_ID} .xhs-mvp-head {
      display: flex;
      align-items: start;
      justify-content: space-between;
      gap: 10px;
    }
    #${PANEL_ID} .xhs-mvp-kicker {
      color: #8a6f5f;
      font-size: 11px;
      letter-spacing: 0.08em;
    }
    #${PANEL_ID} .xhs-mvp-title {
      display: block;
      margin-top: 2px;
      font-size: 16px;
    }
    #${PANEL_ID} .xhs-mvp-line,
    #${PANEL_ID} .xhs-mvp-feedback {
      color: #5f5148;
      font-size: 13px;
      line-height: 1.45;
    }
    #${PANEL_ID} .xhs-mvp-feedback {
      min-height: 18px;
      color: #8a3c29;
    }
    #${PANEL_ID} button {
      border: 0;
      cursor: pointer;
      font: inherit;
    }
    #${PANEL_ID} .xhs-mvp-icon-btn {
      padding: 6px 8px;
      border-radius: 999px;
      background: rgba(138, 60, 41, 0.08);
      color: #8a3c29;
      font-size: 12px;
    }
    #${PANEL_ID} .xhs-mvp-primary-btn {
      width: 100%;
      padding: 10px 12px;
      border-radius: 999px;
      background: #b4472e;
      color: #fffaf4;
    }
    #${PANEL_ID} .xhs-mvp-actions {
      display: grid;
      gap: 8px;
    }
    #${PANEL_ID} .xhs-mvp-secondary-btn {
      padding: 9px 10px;
      border-radius: 999px;
      background: rgba(138, 60, 41, 0.08);
      color: #8a3c29;
      font-size: 12px;
    }
    #${PANEL_ID} .xhs-mvp-primary-btn:disabled {
      cursor: not-allowed;
      opacity: 0.55;
    }
  `;
  document.documentElement.appendChild(style);
}

async function handlePanelCaptureClick() {
  const button = document.getElementById("xhs-mvp-capture-btn");
  captureInFlight = true;
  setCaptureDisabled(true);
  try {
    const beforeCapture = safeExtractCurrentPage();
    if (!beforeCapture.ok) {
      setPanelFeedback(beforeCapture.error);
      return;
    }
    pendingCaptureVisibleIds = collectVisibleItemIds(getDisplayItems(beforeCapture.payload.items));
    setPanelFeedback(`正在提交本次可见 ${pendingCaptureVisibleIds.size} 条；你可以继续滚动，计数会继续更新。`);
    refreshVisibleCount();
    if (!activeTaskContext) {
      setPanelFeedback(`正在同步任务并提交本次可见 ${pendingCaptureVisibleIds.size} 条；你可以继续滚动。`);
    }
    const response = await sendRuntimeMessage({ type: "CAPTURE_VISIBLE_PAGE", payload: beforeCapture.payload });
    if (!response?.ok) {
      setPanelFeedback(toPanelMessage(response?.error, response?.code));
      return;
    }
    const result = response.result;
    lastCapturedVisibleIds = new Set(pendingCaptureVisibleIds);
    setPanelFeedback(`已采集 ${result.captured_count} 条，新增 ${result.new_count} 条，重复 ${result.duplicate_count} 条。`);
    refreshVisibleCount();
  } finally {
    captureInFlight = false;
    pendingCaptureVisibleIds = new Set();
    setCaptureDisabled(false);
    refreshVisibleCount();
  }
}

function handlePanelRetryClick() {
  setPanelFeedback("正在重试连接并同步任务...");
  initializeActiveTaskContext();
  refreshVisibleCount();
}

function startVisibleCountRefresh() {
  if (visibleRefreshTimer) {
    return;
  }
  visibleRefreshTimer = window.setInterval(refreshVisibleCount, 1500);
  window.addEventListener("scroll", debounce(refreshVisibleCount, 300), { passive: true });
}

function refreshVisibleCount() {
  const countEl = document.getElementById("xhs-mvp-count");
  if (!countEl) {
    return;
  }
  const extraction = safeExtractCurrentPage();
  if (!extraction.ok) {
    countEl.textContent = "当前页面不支持采集";
    setPanelFeedback(toPanelMessage(extraction.error, "unsupported_page"));
    setCaptureDisabled(true);
    return;
  }
  const displayItems = getDisplayItems(extraction.payload.items);
  const ids = collectVisibleItemIds(displayItems);
  const newCount = Array.from(ids).filter((id) => !lastCapturedVisibleIds.has(id)).length;
  const pendingText = captureInFlight ? `；本次提交中：${pendingCaptureVisibleIds.size} 条` : "";
  countEl.textContent = `当前页可见笔记：${displayItems.length} 条；新增可见：${newCount} 条${pendingText}`;
  setCaptureDisabled(false);
}

function updatePanelTaskState(code) {
  const taskEl = document.getElementById("xhs-mvp-task");
  if (!taskEl) {
    return;
  }
  if (!activeTaskContext) {
    taskEl.textContent = "未检测到任务，请先回工作台创建任务";
    setCaptureDisabled(false);
    return;
  }
  taskEl.textContent = `当前任务：${activeTaskContext.topic}`;
  setCaptureDisabled(false);
}

function setCaptureDisabled(disabled) {
  const button = document.getElementById("xhs-mvp-capture-btn");
  if (button) {
    button.disabled = Boolean(disabled || captureInFlight);
    button.textContent = captureInFlight ? "采集中..." : "采集当前页";
  }
}

function setPanelFeedback(message) {
  const feedback = document.getElementById("xhs-mvp-feedback");
  if (feedback) {
    feedback.textContent = message;
  }
}

function safeExtractCurrentPage() {
  try {
    return { ok: true, payload: extractCurrentPage() };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}

function collectVisibleItemIds(items) {
  return new Set(
    (items || [])
      .map((item) => item.note_id || item.source_url || item.title)
      .filter(Boolean)
  );
}

function getDisplayItems(items) {
  return (items || []).filter((item) => item.debug_url_source !== "search_page_context");
}

function sendRuntimeMessage(message) {
  return chrome.runtime.sendMessage(message).catch((error) => {
    return { ok: false, error: error?.message || "无法连接插件后台运行时。" };
  });
}

function toPanelMessage(message, code) {
  if (code === "server_unavailable") {
    return "未连接本地服务，请先启动 MVP server 后点击重试连接。";
  }
  if (code === "no_active_task") {
    return "未检测到任务，请先回工作台创建任务。";
  }
  if (code === "unsupported_page") {
    return "当前页面不支持采集，请打开小红书搜索页或笔记详情页。";
  }
  if (code === "content_script_unavailable") {
    return "页面采集助手暂未就绪，插件已尝试自动恢复；仍失败时请刷新当前小红书页。";
  }
  if (code === "capture_already_running") {
    return "当前页面正在采集中，请稍等。";
  }
  if (code === "capture_token_invalid") {
    return "采集授权已过期，请点击重试连接/同步后再采集。";
  }
  if (code === "capture_task_mismatch") {
    return "采集目标和当前任务不一致，请回工作台确认任务后重试。";
  }
  if (code === "capture_submit_failed") {
    return "采集结果提交失败，请确认本地服务可用后重试。";
  }
  return message || "采集失败，请稍后重试。";
}

function debounce(fn, waitMs) {
  let timer = null;
  return () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(fn, waitMs);
  };
}

function extractCurrentPage() {
  const url = window.location.href;
  const pageType = detectPageType(url);
  if (pageType === "search_result") {
    const items = extractSearchResultItems();
    if (!items.length) {
      throw new Error("No visible items found on this search result page.");
    }
    return {
      source_url: url,
      page_type: "search_result",
      query_text: extractSearchKeyword(),
      items,
    };
  }

  if (pageType === "note_detail") {
    return {
      source_url: url,
      page_type: "note_detail",
      query_text: "",
      items: [extractNoteDetailItem()],
    };
  }

  throw new Error("Unsupported page. Open a XHS search result page or note detail page.");
}

function detectPageType(url) {
  if (url.includes("/search_result")) {
    return "search_result";
  }
  if (url.includes("/explore/") || url.includes("/note/")) {
    return "note_detail";
  }
  if (SEARCH_PAGE_MARKERS.some((selector) => document.querySelector(selector))) {
    return "search_result";
  }
  if (DETAIL_PAGE_MARKERS.some((selector) => document.querySelector(selector))) {
    return "note_detail";
  }
  return "unknown";
}

function extractSearchKeyword() {
  const params = new URLSearchParams(window.location.search);
  const directKeyword = params.get("keyword");
  if (directKeyword) {
    return directKeyword;
  }
  const input = document.querySelector('input[type="text"], input.search-input');
  return input?.value?.trim() || "";
}

function extractSearchResultItems() {
  let roots = Array.from(
    document.querySelectorAll(
      [
        ".note-item",
        "[data-note-id]",
        "section.note-item",
        "div.note-item"
      ].join(",")
    )
  );
  if (!roots.length) {
    roots = Array.from(
      document.querySelectorAll(
        [
          "a[href*='/explore/']",
          "a[href*='/note/']"
        ].join(",")
      )
    );
  }

  const items = [];
  const seen = new Set();
  for (const root of roots) {
    const item = extractItemFromRoot(root, "search_result");
    if (!item || !item.note_id) {
      continue;
    }
    const dedupeKey = item.note_id || `${item.title}|${item.source_url}`;
    if (seen.has(dedupeKey)) {
      continue;
    }
    seen.add(dedupeKey);
    items.push(item);
  }
  items.push(buildSearchPageContextItem());
  return items;
}

function extractNoteDetailItem() {
  return extractItemFromRoot(document.body, "note_detail");
}

function extractItemFromRoot(root, pageType) {
  const link = root.matches?.("a[href*='/explore/'], a[href*='/note/']")
    ? root
    : root.querySelector?.("a[href*='/explore/'], a[href*='/note/']");
  const rawHref = link?.getAttribute?.("href") || "";
  const href = link?.href || window.location.href;
  const noteId = extractNoteId(root, href);
  const resolvedLink = resolveBestSourceUrl({ root, link, noteId, rawHref, absoluteHref: href, pageType });

  const title = firstText(root, [
    "#detail-title",
    ".title",
    "h1",
    "h2",
    ".note-title",
    "[data-title]"
  ]);
  const excerpt = firstText(root, [
    ".note-content",
    ".desc",
    ".content",
    ".note-desc",
    "[data-content]"
  ]);
  const author = firstText(root, [
    ".author",
    ".author-name",
    ".name",
    ".username",
    "[data-author]"
  ]);

  const tags = Array.from(root.querySelectorAll?.("[class*='tag'], a[href*='hashtag'], .tag" ) || [])
    .map((element) => cleanText(element.textContent))
    .filter(Boolean)
    .slice(0, 8);

  const coverImageUrl =
    root.querySelector?.("img")?.src ||
    document.querySelector?.("meta[property='og:image']")?.content ||
    "";

  const likes = parseMetric(firstText(root, [".like-count", "[data-likes]", ".count", ".like-wrapper"]));
  const comments = parseMetric(firstText(root, [".comment-count", "[data-comments]", ".comment-wrapper"]));
  const collections = parseMetric(firstText(root, [".collect-count", "[data-collects]", ".collect-wrapper"]));

  const finalTitle = cleanText(title) || cleanText(document.title);
  if (!finalTitle) {
    return null;
  }

  return {
    source_url: resolvedLink.sourceUrl,
    raw_href: rawHref,
    xsec_token: resolvedLink.xsecToken,
    xsec_source: resolvedLink.xsecSource,
    debug_url_source: resolvedLink.debugUrlSource,
    page_type: pageType,
    query_text: pageType === "search_result" ? extractSearchKeyword() : "",
    note_id: noteId,
    title: finalTitle,
    author: cleanText(author),
    visible_text_excerpt: cleanText(excerpt),
    tags,
    likes,
    comments,
    collections,
    cover_image_url: coverImageUrl
  };
}

function resolveBestSourceUrl({ root, link, noteId, rawHref, absoluteHref, pageType }) {
  const candidates = [];
  const pushCandidate = (value, label) => {
    if (!value) {
      return;
    }
    candidates.push({ value: String(value), label });
  };

  pushCandidate(absoluteHref, "link.href");
  pushCandidate(rawHref, "link.raw_href");
  if (noteId) {
    const rootedAnchors = root.querySelectorAll?.(`a[href*='${noteId}']`) || [];
    rootedAnchors.forEach((anchor, index) => {
      pushCandidate(anchor.href, `root.anchor.href.${index}`);
      pushCandidate(anchor.getAttribute?.("href"), `root.anchor.raw.${index}`);
    });

    const documentAnchors = document.querySelectorAll(`a[href*='${noteId}']`);
    documentAnchors.forEach((anchor, index) => {
      pushCandidate(anchor.href, `document.anchor.href.${index}`);
      pushCandidate(anchor.getAttribute?.("href"), `document.anchor.raw.${index}`);
    });
  }

  extractAttributeHints(root).forEach((hint, index) => pushCandidate(hint, `root.attr.${index}`));
  if (link) {
    extractAttributeHints(link).forEach((hint, index) => pushCandidate(hint, `link.attr.${index}`));
  }

  if (noteId) {
    extractSignedUrlsFromHtml(root.outerHTML, noteId).forEach((hint, index) => {
      pushCandidate(hint, `root.html.${index}`);
    });
    extractSignedUrlsFromHtml(document.documentElement.outerHTML.slice(0, 500000), noteId).forEach((hint, index) => {
      pushCandidate(hint, `document.html.${index}`);
    });
  }

  let best = normalizeXhsUrl(absoluteHref);
  let bestSource = "fallback.href";
  for (const candidate of candidates) {
    const normalized = normalizeXhsUrl(candidate.value);
    if (!normalized) {
      continue;
    }
    if (normalized.xsecToken) {
      return {
        sourceUrl: normalized.url,
        xsecToken: normalized.xsecToken,
        xsecSource: normalized.xsecSource,
        debugUrlSource: candidate.label
      };
    }
    if (!best) {
      best = normalized;
      bestSource = candidate.label;
    }
  }

  if (best) {
    return {
      sourceUrl: best.url,
      xsecToken: best.xsecToken,
      xsecSource: best.xsecSource,
      debugUrlSource: bestSource
    };
  }

  const fallbackUrl = pageType === "note_detail" ? window.location.href : absoluteHref;
  return {
    sourceUrl: fallbackUrl,
    xsecToken: "",
    xsecSource: "",
    debugUrlSource: "window_or_href_fallback"
  };
}

function extractAttributeHints(node) {
  if (!node?.attributes) {
    return [];
  }
  return Array.from(node.attributes)
    .map((attribute) => attribute.value)
    .filter((value) => /xsec_token|xsecToken|\/explore\/|\/note\//.test(String(value || "")));
}

function extractSignedUrlsFromHtml(html, noteId) {
  if (!html || !noteId) {
    return [];
  }
  const patterns = [
    new RegExp(`https?:\\\\/\\\\/www\\.xiaohongshu\\.com\\\\/(?:explore|note)\\\\/${noteId}[^"'\\s<]*`, "g"),
    new RegExp(`/(?:explore|note)/${noteId}[^"'\\s<]*`, "g")
  ];
  const results = [];
  for (const pattern of patterns) {
    const matches = html.match(pattern) || [];
    results.push(...matches.map((match) => match.replaceAll("\\/", "/")));
  }
  return Array.from(new Set(results));
}

function normalizeXhsUrl(candidate) {
  if (!candidate) {
    return null;
  }
  const trimmed = String(candidate).trim();
  if (!trimmed) {
    return null;
  }
  try {
    const url = new URL(trimmed, window.location.origin);
    if (!/xiaohongshu\.com$/.test(url.hostname)) {
      return null;
    }
    const isDirectNotePath = /(?:\/explore\/|\/note\/)/.test(url.pathname);
    const isSignedSearchResultPath = /^\/search_result\/[a-zA-Z0-9]+$/.test(url.pathname)
      && url.searchParams.has("xsec_token");
    if (!isDirectNotePath && !isSignedSearchResultPath) {
      return null;
    }

    // Search result deep links sometimes carry the only usable xsec_token.
    // Normalize them into the canonical note-detail path for candidate links.
    if (isSignedSearchResultPath) {
      const match = url.pathname.match(/^\/search_result\/([a-zA-Z0-9]+)$/);
      if (match) {
        url.pathname = `/explore/${match[1]}`;
      }
      if (!url.searchParams.get("xsec_source")) {
        url.searchParams.set("xsec_source", "pc_search");
      }
    }

    return {
      url: url.toString(),
      xsecToken: url.searchParams.get("xsec_token") || "",
      xsecSource: url.searchParams.get("xsec_source") || ""
    };
  } catch {
    return null;
  }
}

function extractNoteId(root, href) {
  const direct =
    root.getAttribute?.("data-note-id") ||
    root.getAttribute?.("data-id") ||
    "";
  if (direct) {
    return direct;
  }
  const match = href.match(/\/(?:explore|note)\/([a-zA-Z0-9]+)/);
  return match ? match[1] : "";
}

function buildSearchPageContextItem() {
  return {
    source_url: window.location.href,
    raw_href: "",
    xsec_token: "",
    xsec_source: "",
    debug_url_source: "search_page_context",
    page_type: "search_result",
    query_text: extractSearchKeyword(),
    note_id: "",
    title: cleanText(document.title) || extractSearchKeyword() || "search_result_page",
    author: "",
    visible_text_excerpt: "",
    tags: [],
    likes: 0,
    comments: 0,
    collections: 0,
    cover_image_url: ""
  };
}

function firstText(root, selectors) {
  for (const selector of selectors) {
    const element = root.querySelector?.(selector);
    if (element) {
      const value = cleanText(element.textContent || element.getAttribute?.("content") || "");
      if (value) {
        return value;
      }
    }
  }
  return "";
}

function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function parseMetric(text) {
  if (!text) {
    return 0;
  }
  const cleaned = text.replace(/[^0-9.万亿]/g, "");
  if (!cleaned) {
    return 0;
  }
  if (cleaned.includes("亿")) {
    return Math.round(parseFloat(cleaned) * 100000000);
  }
  if (cleaned.includes("万")) {
    return Math.round(parseFloat(cleaned) * 10000);
  }
  const numeric = parseInt(cleaned, 10);
  return Number.isNaN(numeric) ? 0 : numeric;
}
})();
