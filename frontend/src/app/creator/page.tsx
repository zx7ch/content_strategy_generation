"use client";

import { useEffect, useRef, useState } from "react";
import {
  appendThreadMessage,
  cancelJob,
  completeThread,
  createThread,
  deleteThread,
  getJobStatus,
  getThread,
  getThreadResult,
  listThreads,
  renameThread,
  resumeJob,
  startThreadWorkflow,
  subscribeThreadEvents,
  type CreatorThreadSummary,
  type GeneratedNoteItem,
} from "@/lib/api";

type TaskStatus = "running" | "paused" | "cancelled" | "completed";
type MessageRole = "assistant" | "user" | "system";

interface ChatMessage {
  id: string;
  role: MessageRole;
  text: string;
}

interface WorkflowTask {
  stage: "strategy" | "generation" | "completed";
  status: TaskStatus;
  progress: number;
  sessionId: string;
  jobId: string;
}

const WELCOME_MESSAGE: ChatMessage = {
  id: "msg-welcome",
  role: "assistant",
  text: "你好，我是品牌内容增长助手。描述你想生成的内容，直接发送就能开始。",
};

function createId(prefix: string) {
  return `${prefix}-${Math.random().toString(16).slice(2, 10)}`;
}

function inferTaskIntent(text: string) {
  return /生成|选题|策略|文案|笔记|脚本|内容/.test(text);
}

// Map raw backend event messages → user-readable Chinese.
// Catches any messages that weren't translated on the backend side.
function translateStatusMsg(msg: string): string | null {
  const lower = msg.toLowerCase();
  // Drop raw internal identifiers that should never surface to users
  if (lower.includes("job_id") || lower.includes("session_id") || lower.includes("enqueued")) return null;
  // Drop any remaining legacy English-only event names
  if (/^(strategy|generate) job (已入队|queued|running|completed)$/.test(lower)) return null;
  if (lower === "session created") return null;

  // Pass through — backend now emits user-friendly Chinese messages directly
  return msg;
}

function stageLabel(stage: WorkflowTask["stage"]) {
  if (stage === "strategy") return "策略生成";
  if (stage === "generation") return "笔记生成";
  return "已完成";
}

function statusLabel(status: TaskStatus) {
  if (status === "running") return "进行中";
  if (status === "paused") return "已暂停";
  if (status === "cancelled") return "已中断";
  return "完成";
}

export default function CreatorPage() {
  const [threads, setThreads] = useState<CreatorThreadSummary[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([WELCOME_MESSAGE]);
  const [input, setInput] = useState("");
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [editingText, setEditingText] = useState("");
  const [activeMenuId, setActiveMenuId] = useState<string | null>(null);
  const [task, setTask] = useState<WorkflowTask | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  // Live status feed driven by SSE payload.message
  const [statusLog, setStatusLog] = useState<string[]>([]);
  // Generated result rendered as a chat bubble (not a bottom panel)
  const [generatedResult, setGeneratedResult] = useState<{
    strategy: { positioning: string } | null;
    notes: GeneratedNoteItem[];
  } | null>(null);
  const [isAccepted, setIsAccepted] = useState(false);
  // Set when switching to a thread that has a paused job — prompts user to resume/cancel
  const [pendingResume, setPendingResume] = useState<{ jobId: string; sessionId: string } | null>(null);

  const taskRef = useRef<WorkflowTask | null>(null);
  const activeThreadIdRef = useRef<string | null>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // Tracks the most recently *requested* thread load to discard stale responses
  const loadingThreadRef = useRef<string | null>(null);

  const activeThread = threads.find((t) => t.thread_id === activeThreadId) ?? null;
  const isTaskRunning = task?.status === "running" || task?.status === "paused";

  useEffect(() => { taskRef.current = task; }, [task]);
  useEffect(() => { activeThreadIdRef.current = activeThreadId; }, [activeThreadId]);

  // Auto-scroll to bottom whenever messages or live feed updates
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, statusLog, generatedResult]);

  // Load thread list on mount; auto-select most recent
  useEffect(() => {
    listThreads()
      .then((items) => {
        setThreads(items);
        if (items.length > 0) void selectThread(items[0].thread_id);
      })
      .catch(() => {});
  }, []);

  // SSE subscription — active only while task is running
  useEffect(() => {
    if (!activeThreadId || !task || task.status !== "running") return;
    const subscribedThreadId = activeThreadId;
    const subscribedSessionId = task.sessionId;
    const isStaleEvent = (data: { thread_id: string; session_id: string }) =>
      data.thread_id !== subscribedThreadId ||
      data.session_id !== subscribedSessionId ||
      activeThreadIdRef.current !== subscribedThreadId;

    const es = subscribeThreadEvents(activeThreadId, {
      onProgress: (data) => {
        if (isStaleEvent(data)) return;
        // Update real progress from SSE payload
        const progress = typeof data.payload?.progress === "number" ? data.payload.progress : undefined;
        if (progress !== undefined) {
          setTask((t) => t ? { ...t, progress } : t);
        }
        // Append live status message (translated to user-friendly text)
        const raw = data.payload?.message;
        if (raw && typeof raw === "string") {
          const translated = translateStatusMsg(raw);
          if (translated) setStatusLog((log) => [...log, translated]);
        }
      },
      onStageChanged: (data) => {
        if (isStaleEvent(data)) return;
        if (data.stage === "generate" && data.job_id) {
          setTask((t) =>
            t ? { ...t, stage: "generation", jobId: data.job_id!, progress: 0 } : t
          );
        }
        const raw = data.payload?.message;
        if (raw && typeof raw === "string") {
          const translated = translateStatusMsg(raw);
          if (translated) setStatusLog((log) => [...log, translated]);
        }
      },
      onCompleted: (data) => {
        if (isStaleEvent(data)) return;
        const current = taskRef.current;
        if (!current) return;
        if (data.stage === "strategy") {
          // generate job is auto-enqueued by the backend worker; just update local stage
          setTask((t) => t ? { ...t, stage: "generation", progress: 0 } : t);
        } else if (data.stage === "generate") {
          es.close();
          setTask((t) => t ? { ...t, status: "completed", stage: "completed", progress: 100 } : t);
          setIsAccepted(false);
          setStatusLog([]);
          if (subscribedThreadId) {
            getThreadResult(subscribedThreadId)
              .then((result) => {
                if (activeThreadIdRef.current !== subscribedThreadId) return;
                setGeneratedResult({
                  strategy: result.strategy as { positioning: string } | null,
                  notes: result.notes,
                });
              })
              .catch(() => {
                appendMessage({ role: "assistant", text: "笔记生成完毕，但读取结果失败，请刷新页面重试。" });
              });
          }
        }
      },
      onFailed: () => {
        if (activeThreadIdRef.current !== subscribedThreadId) return;
        es.close();
        setTask((t) => t ? { ...t, status: "cancelled" } : t);
        setStatusLog([]);
        appendMessage({ role: "system", text: "任务执行失败，请重试。" });
      },
      onCancelled: () => {
        if (activeThreadIdRef.current !== subscribedThreadId) return;
        es.close();
        setTask((t) => t ? { ...t, status: "cancelled" } : t);
        setStatusLog([]);
        appendMessage({ role: "system", text: "任务已取消。" });
      },
    });

    return () => es.close();
  }, [activeThreadId, task?.status, task?.sessionId]);

  function appendMessage(message: Omit<ChatMessage, "id">) {
    setMessages((current) => [...current, { ...message, id: createId("msg") }]);
  }

  function resetConversation() {
    setMessages([WELCOME_MESSAGE]);
    setTask(null);
    setStatusLog([]);
    setGeneratedResult(null);
    setIsAccepted(false);
    setPendingResume(null);
  }

  async function selectThread(threadId: string) {
    setActiveThreadId(threadId);
    setActiveMenuId(null);
    resetConversation();
    loadingThreadRef.current = threadId;
    try {
      const { thread, messages: history } = await getThread(threadId);
      // Discard stale response if user already switched to another thread
      if (loadingThreadRef.current !== threadId) return;
      if (history.length > 0) {
        setMessages(history.map((m) => ({ id: m.message_id, role: m.role, text: m.text })));
      }
      if (thread.status === "accepted") {
        setIsAccepted(true);
        getThreadResult(threadId)
          .then((result) => {
            if (loadingThreadRef.current !== threadId) return;
            setGeneratedResult({
              strategy: result.strategy as { positioning: string } | null,
              notes: result.notes,
            });
          })
          .catch(() => {});
      }

      // Restore task strip from the active workflow. SSE replay then catches up
      // stage/progress/result for the specific thread + session.
      if (thread.active_job_id && thread.active_workflow_session_id && thread.status !== "accepted") {
        let restoredStage: WorkflowTask["stage"] = "strategy";
        let restoredStatus: TaskStatus = "running";
        try {
          const job = await getJobStatus(thread.active_job_id);
          if (loadingThreadRef.current !== threadId) return;
          restoredStage = job.job_type === "generate" ? "generation" : "strategy";
          if (job.status === "paused") restoredStatus = "paused";
          if (job.status === "cancelled" || job.status === "failed") restoredStatus = "cancelled";
        } catch {
          // Keep a running placeholder; SSE replay can still restore the task.
        }
        setTask({
          stage: restoredStage,
          status: restoredStatus,
          progress: 0,
          sessionId: thread.active_workflow_session_id,
          jobId: thread.active_job_id,
        });
        if (restoredStatus === "paused") {
          setPendingResume({
            jobId: thread.active_job_id,
            sessionId: thread.active_workflow_session_id,
          });
        }
      }
    } catch {
      // silently keep welcome message on load failure
    }
  }

  async function handleNewThread() {
    try {
      const created = await createThread();
      addThreadToState(created.thread_id, created.title);
      setActiveThreadId(created.thread_id);
      resetConversation();
    } catch {
      appendMessage({ role: "system", text: "创建对话失败，请检查 runtime 是否在线。" });
    }
  }

  function addThreadToState(thread_id: string, title: string) {
    const summary: CreatorThreadSummary = {
      thread_id,
      title,
      status: "active",
      active_job_id: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    setThreads((current) => [summary, ...current]);
  }

  // Auto-create a thread on first message; title = truncated message text
  async function ensureThread(firstMessage: string): Promise<string | null> {
    if (activeThread) return activeThread.thread_id;
    try {
      const title = firstMessage.length > 20
        ? firstMessage.slice(0, 20) + "…"
        : firstMessage;
      const created = await createThread(title);
      addThreadToState(created.thread_id, created.title);
      setActiveThreadId(created.thread_id);
      return created.thread_id;
    } catch {
      appendMessage({ role: "system", text: "创建对话失败，请检查 runtime 是否在线。" });
      return null;
    }
  }

  async function sendMessage(textOverride?: string) {
    const text = (textOverride ?? input).trim();
    if (!text || isLoading) return;
    setInput("");
    // Reset textarea height after clearing
    if (inputRef.current) {
      inputRef.current.style.height = "auto";
    }
    appendMessage({ role: "user", text });

    // Ensure we have an active thread (auto-create if none)
    const threadId = await ensureThread(text);
    if (!threadId) return;

    let intent = "free_chat";
    try {
      const result = await appendThreadMessage(threadId, text);
      intent = result.intent;
      if (result.updated_title) {
        setThreads((current) =>
          current.map((t) =>
            t.thread_id === threadId ? { ...t, title: result.updated_title! } : t
          )
        );
      }
      if (result.assistant_reply) {
        appendMessage({ role: "assistant", text: result.assistant_reply });
      }
      if (intent === "pause_job") {
        setTask((t) => t ? { ...t, status: "paused" } : t);
        inputRef.current?.focus();
        return;
      } else if (intent === "resume_job") {
        setTask((t) => t ? { ...t, status: "running" } : t);
        setPendingResume(null);
        inputRef.current?.focus();
        return;
      } else if (intent === "cancel_job") {
        setTask((t) => t ? { ...t, status: "cancelled" } : t);
        setPendingResume(null);
        inputRef.current?.focus();
        return;
      } else if (intent === "ask_status") {
        inputRef.current?.focus();
        return;
      }
    } catch {
      // fall through to local intent inference
    }

    if (intent === "add_constraint") {
      inputRef.current?.focus();
      return;
    }

    if (inferTaskIntent(text) && (!task || task.status !== "running")) {
      await startWorkflow(text, threadId);
      inputRef.current?.focus();
      return;
    }

    appendMessage({ role: "assistant", text: "已收到。如需生成内容，请描述你的具体需求。" });
    inputRef.current?.focus();
  }

  async function startWorkflow(text: string, threadId: string) {
    setIsLoading(true);
    setStatusLog([]);
    setGeneratedResult(null);
    try {
      const result = await startThreadWorkflow(threadId, text);
      setTask({
        stage: "strategy",
        status: "running",
        progress: 0,
        sessionId: result.session_id,
        jobId: result.job_id,
      });
      setThreads((current) =>
        current.map((t) =>
          t.thread_id === threadId ? { ...t, active_job_id: result.job_id } : t
        )
      );
      // status log is driven by real SSE events, no hardcoded initial message
    } catch {
      appendMessage({ role: "system", text: "启动工作流失败，请检查 runtime 是否在线。" });
    } finally {
      setIsLoading(false);
    }
  }

  async function handleComplete() {
    if (!activeThread || isAccepted) return;
    try {
      await completeThread(activeThread.thread_id);
      setIsAccepted(true);
    } catch {
      appendMessage({ role: "system", text: "提交失败，请检查 runtime 是否在线。" });
    }
  }

  async function handleStopTask() {
    if (!task || task.status !== "running") return;
    try {
      await cancelJob(task.jobId);
      setTask((t) => t ? { ...t, status: "cancelled" } : t);
      setPendingResume(null);
      setStatusLog([]);
      appendMessage({ role: "assistant", text: "已停止当前任务。" });
    } catch {
      if (activeThreadId) {
        try {
          await appendThreadMessage(activeThreadId, "取消当前任务");
          setTask((t) => t ? { ...t, status: "cancelled" } : t);
          setPendingResume(null);
          setStatusLog([]);
          appendMessage({ role: "assistant", text: "已停止当前任务。" });
          return;
        } catch {
          // fall through to error message
        }
      }
      appendMessage({ role: "system", text: "停止任务失败，请稍后重试。" });
    }
  }

  async function handleRenameThread(thread: CreatorThreadSummary) {
    const nextTitle = window.prompt("重命名对话", thread.title)?.trim();
    if (!nextTitle || nextTitle === thread.title) {
      setActiveMenuId(null);
      return;
    }
    try {
      const updated = await renameThread(thread.thread_id, nextTitle);
      setThreads((current) =>
        current.map((item) =>
          item.thread_id === thread.thread_id
            ? { ...item, title: updated.title, updated_at: new Date().toISOString() }
            : item
        )
      );
    } catch {
      appendMessage({ role: "system", text: "重命名失败，请检查 runtime 是否在线。" });
    } finally {
      setActiveMenuId(null);
    }
  }

  async function handleDeleteThread(thread: CreatorThreadSummary) {
    const confirmed = window.confirm(`删除「${thread.title}」？运行中的任务也会被停止。`);
    if (!confirmed) {
      setActiveMenuId(null);
      return;
    }
    try {
      await deleteThread(thread.thread_id);
      const remaining = threads.filter((item) => item.thread_id !== thread.thread_id);
      setThreads(remaining);
      if (activeThreadId === thread.thread_id) {
        const next = remaining[0] ?? null;
        if (next) {
          await selectThread(next.thread_id);
        } else {
          setActiveThreadId(null);
          resetConversation();
        }
      }
    } catch {
      appendMessage({ role: "system", text: "删除失败，请检查 runtime 是否在线。" });
    } finally {
      setActiveMenuId(null);
    }
  }

  function beginEdit(message: ChatMessage) {
    setEditingMessageId(message.id);
    setEditingText(message.text);
  }

  function cancelEdit() {
    setEditingMessageId(null);
    setEditingText("");
  }

  function resendEdited(messageId: string) {
    const idx = messages.findIndex((m) => m.id === messageId);
    if (idx < 0) return;
    setMessages((current) => current.slice(0, idx));
    setEditingMessageId(null);
    sendMessage(editingText);
    setEditingText("");
  }

  return (
    <div className="flex h-[calc(100vh-4rem)] min-h-[640px] overflow-hidden rounded-none border-x border-line bg-white">

      {/* ── Left sidebar: thread history ── */}
      <aside className="hidden w-[256px] shrink-0 flex-col border-r border-line bg-slate-50 md:flex">
        <div className="flex-1 overflow-y-auto p-3">
          <button
            type="button"
            onClick={handleNewThread}
            className="mb-3 flex w-full items-center gap-2 rounded-xl border border-line bg-white px-3 py-2 text-sm font-medium text-ink transition hover:bg-slate-100"
          >
            <span className="text-base leading-none">+</span> 新建对话
          </button>

          {threads.length === 0 ? (
            <p className="px-3 py-2 text-xs text-quiet">
              发送第一条消息后，对话会自动出现在这里
            </p>
          ) : (
            <div className="space-y-0.5">
              {threads.map((thread) => (
                <div
                  key={thread.thread_id}
                  className={[
                    "group relative flex cursor-pointer items-center gap-2 rounded-xl px-3 py-2 text-sm",
                    thread.thread_id === activeThreadId
                      ? "bg-white font-medium text-ink shadow-sm"
                      : "text-slate-500 hover:bg-white/70 hover:text-ink",
                  ].join(" ")}
                  onClick={() => selectThread(thread.thread_id)}
                >
                  <span className="min-w-0 flex-1 truncate">{thread.title}</span>
                  <button
                    type="button"
                    aria-label="对话操作"
                    onClick={(e) => {
                      e.stopPropagation();
                      setActiveMenuId(activeMenuId === thread.thread_id ? null : thread.thread_id);
                    }}
                    className="hidden h-6 w-6 shrink-0 items-center justify-center rounded text-slate-400 hover:bg-slate-200 hover:text-ink group-hover:flex"
                  >
                    ···
                  </button>
                  {activeMenuId === thread.thread_id && (
                    <div className="absolute right-2 top-9 z-10 w-32 rounded-xl border border-line bg-white py-1 text-sm shadow-panel">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleRenameThread(thread);
                        }}
                        className="block w-full px-3 py-1.5 text-left hover:bg-slate-50"
                      >
                        重命名
                      </button>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleDeleteThread(thread);
                        }}
                        className="block w-full px-3 py-1.5 text-left text-danger hover:bg-dangerBg"
                      >
                        删除
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </aside>

      {/* ── Main area ── */}
      <section className="flex min-w-0 flex-1 flex-col">

        {/* Chat message feed */}
        <div className="flex-1 overflow-y-auto px-4 py-6 md:px-6">
          <div className="mx-auto flex max-w-3xl flex-col gap-5">

            {messages.map((message) => {
              const isUser = message.role === "user";
              const isSystem = message.role === "system";
              const editing = editingMessageId === message.id;

              return (
                <div
                  key={message.id}
                  className={["group flex", isUser ? "justify-end" : "justify-start"].join(" ")}
                >
                  <div
                    className={[
                      "relative max-w-[80%] rounded-2xl px-4 py-3 text-sm leading-6",
                      isUser ? "bg-ink text-white" : "",
                      message.role === "assistant" ? "bg-[#f0f2f5] text-ink" : "",
                      isSystem ? "border border-line bg-white text-xs text-quiet" : "",
                    ].join(" ")}
                  >
                    {editing ? (
                      <div className="w-[min(600px,70vw)]">
                        <textarea
                          value={editingText}
                          onChange={(e) => setEditingText(e.target.value)}
                          className="min-h-20 w-full resize-y rounded-xl border border-line bg-white p-3 text-sm text-ink outline-none focus:border-ink"
                        />
                        <div className="mt-2 flex justify-end gap-2">
                          <button type="button" onClick={cancelEdit} className="rounded-lg px-3 py-1.5 text-xs hover:bg-black/10">
                            取消
                          </button>
                          <button
                            type="button"
                            onClick={() => resendEdited(message.id)}
                            className="rounded-lg bg-white/20 px-3 py-1.5 text-xs"
                          >
                            重发
                          </button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <div className="whitespace-pre-wrap">{message.text}</div>
                        {isUser && (
                          <button
                            type="button"
                            onClick={() => beginEdit(message)}
                            className="absolute -bottom-7 right-0 hidden rounded border border-line bg-white px-2 py-0.5 text-xs text-slate-500 shadow-sm group-hover:block"
                          >
                            编辑
                          </button>
                        )}
                      </>
                    )}
                  </div>
                </div>
              );
            })}

            {/* ── Live status feed — shows real-time SSE messages while task runs ── */}
            {isTaskRunning && statusLog.length > 0 && (
              <div className="flex justify-start">
                <div className="max-w-[80%] rounded-2xl bg-[#f0f2f5] px-4 py-3 text-sm text-ink">
                  {/* Stage label with pulsing indicator */}
                  <div className="mb-2.5 flex items-center gap-2">
                    <span className="h-2 w-2 animate-pulse rounded-full bg-emerald-500" />
                    <span className="text-xs font-medium uppercase tracking-wide text-quiet">
                      {stageLabel(task?.stage ?? "strategy")}
                    </span>
                  </div>
                  {/* Log lines */}
                  <div className="space-y-1.5">
                    {statusLog.map((line, i) => {
                      const isLatest = i === statusLog.length - 1;
                      return (
                        <div
                          key={i}
                          className={["flex items-start gap-2 text-xs", isLatest ? "text-ink" : "text-slate-400"].join(" ")}
                        >
                          <span className="mt-px shrink-0 text-[10px]">
                            {isLatest ? "⋯" : "✓"}
                          </span>
                          <span>{line}</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
            )}

            {/* ── Paused job resume prompt ── */}
            {pendingResume && (
              <div className="flex justify-start">
                <div className="max-w-[80%] rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-ink">
                  <p className="mb-3 leading-6">上次有一个任务被暂停了，是否继续执行？</p>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={async () => {
                        try {
                          await resumeJob(pendingResume.jobId);
                        } catch {
                          // The next status refresh/SSE replay will correct local state.
                        }
                        setTask((t) => t ? { ...t, status: "running" } : t);
                        setPendingResume(null);
                      }}
                      className="rounded-lg bg-ink px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800"
                    >
                      继续
                    </button>
                    <button
                      type="button"
                      onClick={async () => {
                        try {
                          await cancelJob(pendingResume.jobId);
                        } catch {
                          // The next status refresh/SSE replay will correct local state.
                        }
                        setTask((t) => t ? { ...t, status: "cancelled" } : t);
                        setPendingResume(null);
                      }}
                      className="rounded-lg border border-line px-3 py-1.5 text-xs text-slate-500 hover:bg-slate-100"
                    >
                      放弃
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* ── Generated result bubble — notes + complete button in chat flow ── */}
            {generatedResult && (
              <div className="flex justify-start">
                <div className="w-full max-w-[92%] rounded-2xl bg-[#f0f2f5] px-4 py-4 text-sm text-ink">

                  {/* Strategy positioning */}
                  {generatedResult.strategy && (
                    <div className="mb-4 rounded-xl border border-line/60 bg-white px-4 py-3">
                      <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-quiet">
                        内容策略定位
                      </p>
                      <p className="leading-6 text-ink">{generatedResult.strategy.positioning}</p>
                    </div>
                  )}

                  {/* Note cards */}
                  {generatedResult.notes.length > 0 && (
                    <div className="space-y-2.5">
                      <p className="text-[10px] font-semibold uppercase tracking-wider text-quiet">
                        生成笔记（{generatedResult.notes.length} 篇）
                      </p>
                      {generatedResult.notes.map((note) => (
                        <div
                          key={note.note_id}
                          className="rounded-xl border border-line/60 bg-white px-4 py-3"
                        >
                          <p className="font-medium text-ink">{note.title}</p>
                          <p className="mt-1 line-clamp-4 text-xs leading-5 text-quiet">
                            {note.content}
                          </p>
                          {note.tags.filter(Boolean).length > 0 && (
                            <div className="mt-2 flex flex-wrap gap-1">
                              {note.tags.filter(Boolean).map((tag) => (
                                <span
                                  key={tag}
                                  className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-quiet"
                                >
                                  #{tag}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Accept / complete action */}
                  <div className="mt-4 flex items-center justify-between gap-3">
                    <p className="text-xs text-quiet">
                      {isAccepted
                        ? "笔记已加入发布候选列表，可在「发布记录」页查看。"
                        : "确认结果后点击完成，笔记将进入发布候选。"}
                    </p>
                    <button
                      type="button"
                      onClick={handleComplete}
                      disabled={isAccepted}
                      className="shrink-0 rounded-xl bg-ink px-4 py-2 text-sm font-medium text-white transition hover:bg-slate-800 disabled:opacity-40"
                    >
                      {isAccepted ? "✓ 已完成" : "完成"}
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* Scroll anchor */}
            <div ref={chatEndRef} />
          </div>
        </div>

        {/* ── Input area ── */}
        <div className="border-t border-line bg-white px-4 py-4 md:px-6">
          <div className="mx-auto flex max-w-3xl items-end gap-3">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                // Auto-grow: reset then expand to scrollHeight
                e.target.style.height = "auto";
                e.target.style.height = `${e.target.scrollHeight}px`;
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void sendMessage();
                }
              }}
              disabled={isLoading}
              rows={1}
              className="max-h-36 min-h-[44px] flex-1 resize-none overflow-y-auto rounded-2xl border border-line bg-slate-50 px-4 py-3 text-sm leading-6 text-ink outline-none transition focus:border-slate-400 focus:bg-white disabled:opacity-50"
              placeholder={
                isTaskRunning
                  ? "任务进行中，可继续补充要求..."
                  : "描述你想生成的内容，Enter 发送 · Shift+Enter 换行"
              }
            />
            <button
              type="button"
              onClick={() => void sendMessage()}
              disabled={isLoading || !input.trim()}
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-ink text-white transition hover:bg-slate-800 disabled:opacity-30"
              aria-label="发送"
            >
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <line x1="22" y1="2" x2="11" y2="13" />
                <polygon points="22 2 15 22 11 13 2 9 22 2" />
              </svg>
            </button>
            {task?.status === "running" && (
              <button
                type="button"
                onClick={() => void handleStopTask()}
                className="flex h-11 shrink-0 items-center gap-2 rounded-xl border border-danger/30 bg-dangerBg px-3 text-sm font-medium text-danger transition hover:border-danger/50 hover:bg-red-100"
                aria-label="停止任务"
                title="停止当前任务"
              >
                <span className="h-2.5 w-2.5 rounded-sm bg-current" />
                停止
              </button>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
