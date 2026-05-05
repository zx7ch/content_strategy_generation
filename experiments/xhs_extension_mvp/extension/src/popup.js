const serverUrlInput = document.getElementById("server-url");
const statusEl = document.getElementById("status");
const resyncButton = document.getElementById("resync-button");
const openWorkbenchButton = document.getElementById("open-workbench-button");
const connectionStateEl = document.getElementById("connection-state");
const activeTaskEl = document.getElementById("active-task");
const pageStateEl = document.getElementById("page-state");
const visibleCountEl = document.getElementById("visible-count");

bootstrap();
resyncButton.addEventListener("click", syncActiveTask);
openWorkbenchButton.addEventListener("click", openWorkbench);
serverUrlInput.addEventListener("change", syncActiveTask);

async function bootstrap() {
  const stored = await chrome.storage.local.get(["serverUrl", "activeTask"]);
  if (stored.serverUrl) {
    serverUrlInput.value = stored.serverUrl;
  }
  if (stored.activeTask) {
    renderActiveTask(stored.activeTask);
  }
  await syncActiveTask();
  await syncPageStatus({ silent: true });
}

function setStatus(message) {
  statusEl.textContent = message;
}

async function syncActiveTask({ silent = false } = {}) {
  const serverUrl = serverUrlInput.value.trim().replace(/\/$/, "");
  if (!serverUrl) {
    setStatus("Server URL is required.");
    return;
  }
  if (!silent) {
    setStatus("Syncing active task...");
  }
  const response = await sendBackgroundMessage({ type: "ACTIVE_TASK_RESYNC", serverUrl });
  if (!response?.ok) {
    connectionStateEl.textContent = "disconnected";
    activeTaskEl.textContent = "unavailable";
    setStatus(toUserMessage(response?.error, response?.code || "server_unavailable"));
    return;
  }
  renderConnection(response.runtime?.connectionState || "connected");
  renderActiveTask(response.active_task);
  if (!silent) {
    setStatus(response.active_task ? "Active task synced." : toUserMessage(response.error_summary?.message, "no_active_task"));
  }
}

function renderConnection(value) {
  connectionStateEl.textContent = value || "unknown";
}

function renderActiveTask(activeTask) {
  activeTaskEl.textContent = activeTask?.topic
    ? `${activeTask.topic} (${activeTask.status})`
    : "No active task";
}

async function sendBackgroundMessage(message) {
  return chrome.runtime.sendMessage(message).catch((error) => {
    return { ok: false, error: error?.message || "Could not contact extension background runtime." };
  });
}

async function syncPageStatus({ silent = false } = {}) {
  const response = await sendBackgroundMessage({ type: "PAGE_STATUS_REQUEST" });
  if (!response?.ok) {
    pageStateEl.textContent = response?.pageStatus === "content_script_unavailable"
      ? "helper not ready"
      : "unsupported";
    visibleCountEl.textContent = "0";
    if (!silent) {
      setStatus(toUserMessage(response?.error, response?.code || response?.pageStatus));
    }
    return;
  }
  pageStateEl.textContent = formatPageState(response.pageStatus);
  visibleCountEl.textContent = String(response.visibleCount || 0);
}

async function openWorkbench() {
  const serverUrl = serverUrlInput.value.trim().replace(/\/$/, "") || "http://127.0.0.1:8010";
  await chrome.storage.local.set({ serverUrl });
  await chrome.tabs.create({ url: `${serverUrl}/` });
}

function formatPageState(pageStatus) {
  if (pageStatus === "search_result") {
    return "XHS search page";
  }
  if (pageStatus === "note_detail") {
    return "XHS note page";
  }
  return pageStatus || "unknown";
}

function toUserMessage(message, code) {
  if (code === "server_unavailable") {
    return "Not connected to local service. Please start the MVP server first, then retry connection.";
  }
  if (code === "no_active_task") {
    return "No active task detected. Please open the workbench and create a task first.";
  }
  if (code === "unsupported" || code === "unsupported_page") {
    return "This page is not supported for capture. Please open a Xiaohongshu search result page or note detail page.";
  }
  if (code === "content_script_unavailable") {
    return "Page helper is not ready. The extension tried to recover automatically; if this tab was open before extension reload, refresh the Xiaohongshu page.";
  }
  if (code === "capture_already_running") {
    return "Capture is already running for this tab. Please wait for the current capture to finish.";
  }
  if (code === "capture_token_invalid") {
    return "Capture authorization expired. Resync the active task and try again.";
  }
  return message || "Runtime status unavailable.";
}
