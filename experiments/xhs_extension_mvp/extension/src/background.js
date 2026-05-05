const DEFAULT_SERVER_URL = "http://127.0.0.1:8010";

const runtimeState = {
  serverUrl: DEFAULT_SERVER_URL,
  activeTask: null,
  activeTaskFetchedAt: 0,
  activeCapturesByTab: new Map(),
  lastCaptureResultByTab: new Map(),
  lastHealthCheckAt: 0,
  connectionState: "unknown",
};

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === "complete" && isSupportedXhsUrl(tab.url)) {
    void ensureContentScript(tabId);
  }
});

chrome.tabs.onActivated.addListener(({ tabId }) => {
  chrome.tabs.get(tabId)
    .then((tab) => {
      if (isSupportedXhsUrl(tab.url)) {
        void ensureContentScript(tabId);
      }
    })
    .catch(() => {
      // Tab may have closed before Chrome returns it.
    });
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  handleMessage(message, sender)
    .then((payload) => sendResponse(payload))
    .catch((error) => {
      console.error("[XHS MVP] Background message failed", error);
      sendResponse({
        ok: false,
        code: error?.code || "runtime_error",
        error: error?.message || String(error),
      });
    });
  return true;
});

async function handleMessage(message, sender) {
  switch (message?.type) {
    case "HEALTH_CHECK":
      return healthCheck(message.serverUrl);
    case "ACTIVE_TASK_REQUEST":
    case "ACTIVE_TASK_RESYNC":
      return syncActiveTask(message.serverUrl);
    case "PAGE_STATUS_REQUEST":
      return getPageStatus(sender.tab?.id);
    case "CAPTURE_VISIBLE_PAGE":
      return captureVisiblePage(message.serverUrl, sender.tab?.id, message.payload);
    case "CONTENT_SCRIPT_ENSURE":
      return ensureContentScript(message.tabId || sender.tab?.id);
    default:
      return { ok: false, code: "runtime_error", error: `Unsupported message type: ${message?.type || "unknown"}` };
  }
}

async function healthCheck(serverUrl) {
  const candidates = await getServerUrlCandidates(serverUrl);
  let lastFailure = "Network error";
  for (const baseUrl of candidates) {
    const response = await fetch(`${baseUrl}/api/extension/health`).catch((error) => {
      lastFailure = `${baseUrl}: ${error?.message || "Network error"}`;
      return null;
    });
    if (!response?.ok) {
      lastFailure = response ? `${baseUrl}: HTTP ${response.status}` : lastFailure;
      continue;
    }
    const payload = await response.json();
    runtimeState.serverUrl = baseUrl;
    runtimeState.connectionState = "connected";
    runtimeState.lastHealthCheckAt = Date.now();
    await chrome.storage.local.set({ serverUrl: baseUrl });
    return { ok: true, health: payload, runtime: snapshotRuntimeState() };
  }
  runtimeState.connectionState = "disconnected";
  throw createRuntimeError(
    "server_unavailable",
    `${lastFailure}. Please start the MVP server first. Tried: ${candidates.join(", ")}.`
  );
}

async function syncActiveTask(serverUrl) {
  const health = await healthCheck(serverUrl);
  const baseUrl = health.runtime?.serverUrl || DEFAULT_SERVER_URL;
  const response = await fetch(`${baseUrl}/api/extension/active-task`);
  if (!response.ok) {
    throw createRuntimeError("server_unavailable", `Active task sync failed: HTTP ${response.status}`);
  }
  const payload = await response.json();
  runtimeState.activeTask = payload.active_task || null;
  runtimeState.activeTaskFetchedAt = runtimeState.activeTask ? Date.now() : 0;
  await chrome.storage.local.set({
    serverUrl: baseUrl,
    activeTask: runtimeState.activeTask,
    activeTaskFetchedAt: runtimeState.activeTaskFetchedAt,
  });
  return { ok: true, ...payload, runtime: snapshotRuntimeState() };
}

async function captureVisiblePage(serverUrl, senderTabId, suppliedPagePayload) {
  const activeTaskPayload = await syncActiveTask(serverUrl);
  const baseUrl = activeTaskPayload.runtime?.serverUrl || runtimeState.serverUrl || DEFAULT_SERVER_URL;
  const activeTask = activeTaskPayload.active_task;
  if (!activeTask?.capture_token) {
    return {
      ok: false,
      error: activeTaskPayload.error_summary?.message || "No active task detected. Please create a task first.",
      code: activeTaskPayload.error_summary?.code || "no_active_task",
    };
  }

  const tabId = senderTabId || (await getActiveTabId());
  if (!tabId) {
    return { ok: false, code: "no_active_tab", error: "No active tab found." };
  }
  if (runtimeState.activeCapturesByTab.has(tabId)) {
    return { ok: false, error: "Capture is already running for this tab.", code: "capture_already_running" };
  }

  const requestId = createRequestId();
  runtimeState.activeCapturesByTab.set(tabId, { requestId, taskId: activeTask.task_id, status: "running" });
  try {
    const extraction = suppliedPagePayload
      ? { ok: true, payload: suppliedPagePayload }
      : await extractPageWithRecovery(tabId);
    if (!extraction?.ok) {
      return {
        ok: false,
        code: extraction?.code || "unsupported_page",
        error: extraction?.error || "This page is not supported for capture.",
      };
    }

    const pagePayload = extraction.payload;
    const response = await fetch(`${baseUrl}/api/extension/capture`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Capture-Token": activeTask.capture_token,
      },
      body: JSON.stringify({
        task_id: activeTask.task_id,
        request_id: requestId,
        tab_id: tabId,
        page_url: pagePayload.source_url || "",
        page_type: pagePayload.page_type,
        query_text: pagePayload.query_text || "",
        visible_items: pagePayload.items || [],
      }),
    });
    if (!response.ok) {
      if (response.status === 401) {
        runtimeState.activeTask = null;
        runtimeState.activeTaskFetchedAt = 0;
        await chrome.storage.local.set({ activeTask: null, activeTaskFetchedAt: 0 });
        return {
          ok: false,
          code: "capture_token_invalid",
          error: "Capture authorization expired. Please resync the active task and try again.",
        };
      }
      if (response.status === 403) {
        return {
          ok: false,
          code: "capture_task_mismatch",
          error: "Capture target does not match the active task. Please resync the active task.",
        };
      }
      return {
        ok: false,
        code: "capture_submit_failed",
        error: `Capture submit failed: HTTP ${response.status}`,
      };
    }
    const result = await response.json();
    runtimeState.lastCaptureResultByTab.set(tabId, result);
    return { ok: true, result };
  } finally {
    runtimeState.activeCapturesByTab.delete(tabId);
  }
}

async function getActiveTabId() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab?.id || null;
}

async function sendExtractMessage(tabId) {
  return chrome.tabs.sendMessage(tabId, { type: "extract-page" }).catch((error) => {
    return { ok: false, error: error?.message || "Could not contact content script." };
  });
}

async function getPageStatus(senderTabId) {
  const tabId = senderTabId || (await getActiveTabId());
  if (!tabId) {
    return { ok: false, code: "no_active_tab", pageStatus: "unknown", visibleCount: 0, error: "No active tab found." };
  }
  const extraction = await extractPageWithRecovery(tabId);
  if (!extraction?.ok && extraction.code === "content_script_unavailable") {
    return {
      ok: false,
      code: "content_script_unavailable",
      pageStatus: "content_script_unavailable",
      visibleCount: 0,
      error: "Page helper is not ready yet. The extension tried to recover automatically.",
    };
  }
  if (!extraction?.ok) {
    return {
      ok: false,
      code: extraction.code || "unsupported_page",
      pageStatus: "unsupported_page",
      visibleCount: 0,
      error: extraction?.error || "This page is not supported for capture.",
    };
  }
  return {
    ok: true,
    pageStatus: extraction.payload?.page_type || "supported",
    visibleCount: extraction.payload?.items?.length || 0,
    queryText: extraction.payload?.query_text || "",
  };
}

async function ensureContentScript(tabId) {
  if (!tabId) {
    return { ok: false, code: "no_active_tab", error: "No active tab found." };
  }
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (!isSupportedXhsUrl(tab?.url)) {
    return {
      ok: false,
      code: "unsupported_page",
      error: "This page is not supported for capture. Please open a Xiaohongshu page.",
    };
  }
  const mounted = await chrome.tabs.sendMessage(tabId, { type: "PING_CAPTURE_PANEL" }).catch(() => null);
  if (mounted?.ok) {
    await chrome.tabs.sendMessage(tabId, { type: "MOUNT_CAPTURE_PANEL" }).catch(() => null);
    return { ok: true, alreadyLoaded: true, panelMounted: Boolean(mounted.panelMounted) };
  }
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["src/content.js"],
    });
    await chrome.tabs.sendMessage(tabId, { type: "MOUNT_CAPTURE_PANEL" }).catch(() => null);
    return { ok: true, injected: true };
  } catch (error) {
    return {
      ok: false,
      code: "content_script_unavailable",
      error: error?.message || "Could not inject content script into current page.",
    };
  }
}

async function extractPageWithRecovery(tabId) {
  let extraction = await sendExtractMessage(tabId);
  if (!extraction?.ok && isMissingContentScriptError(extraction.error)) {
    const injected = await ensureContentScript(tabId);
    if (!injected.ok) {
      return injected;
    }
    extraction = await sendExtractMessage(tabId);
  }
  if (!extraction?.ok && isMissingContentScriptError(extraction?.error)) {
    return {
      ok: false,
      code: "content_script_unavailable",
      error: "Page helper is not ready yet. The extension tried to recover automatically.",
    };
  }
  if (!extraction?.ok) {
    return {
      ok: false,
      code: "unsupported_page",
      error: extraction?.error || "This page is not supported for capture.",
    };
  }
  if (extraction.payload?.page_type === "unknown") {
    return {
      ok: false,
      code: "unsupported_page",
      error: "This page is not supported for capture. Please open a Xiaohongshu search result page or note detail page.",
    };
  }
  return extraction;
}

function isMissingContentScriptError(message) {
  return /Could not establish connection|Receiving end does not exist|Could not contact content script/i.test(message || "");
}

function isSupportedXhsUrl(url) {
  try {
    const parsed = new URL(url || "");
    return parsed.protocol === "https:" && /(^|\.)xiaohongshu\.com$/i.test(parsed.hostname);
  } catch {
    return false;
  }
}

async function resolveServerUrl(serverUrl) {
  const stored = await chrome.storage.local.get(["serverUrl"]);
  const baseUrl = normalizeServerUrl(serverUrl || stored.serverUrl || DEFAULT_SERVER_URL);
  runtimeState.serverUrl = baseUrl;
  await chrome.storage.local.set({ serverUrl: baseUrl });
  return baseUrl;
}

async function getServerUrlCandidates(serverUrl) {
  const stored = await chrome.storage.local.get(["serverUrl"]);
  return uniqueServerUrls([
    serverUrl,
    stored.serverUrl,
    DEFAULT_SERVER_URL,
    "http://localhost:8010",
  ]);
}

function uniqueServerUrls(values) {
  const seen = new Set();
  const urls = [];
  for (const value of values) {
    const normalized = normalizeServerUrl(value);
    if (!normalized || seen.has(normalized)) {
      continue;
    }
    seen.add(normalized);
    urls.push(normalized);
  }
  return urls;
}

function normalizeServerUrl(value) {
  return String(value || "").trim().replace(/\/$/, "");
}

function snapshotRuntimeState() {
  return {
    serverUrl: runtimeState.serverUrl,
    activeTask: runtimeState.activeTask,
    activeTaskFetchedAt: runtimeState.activeTaskFetchedAt,
    lastHealthCheckAt: runtimeState.lastHealthCheckAt,
    connectionState: runtimeState.connectionState,
  };
}

function createRequestId() {
  if (crypto?.randomUUID) {
    return crypto.randomUUID();
  }
  return `req_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function createRuntimeError(code, message) {
  const error = new Error(message);
  error.code = code;
  return error;
}
