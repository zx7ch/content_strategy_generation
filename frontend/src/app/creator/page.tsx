"use client";

import { useEffect, useRef, useState } from "react";
import {
  appendThreadMessage,
  completeThread,
  createThread,
  deleteThread,
  getThreadTimeline,
  getThreadResult,
  getWorkflowRunSnapshot,
  listThreads,
  renameThread,
  subscribeWorkflowRunEvents,
  type CreatorMessage,
  type CreatorThreadSummary,
  type GeneratedNoteItem,
  type WorkflowArtifactRef,
  type WorkflowRunEventData,
  type WorkflowRunSnapshot,
} from "@/lib/api";
import { useBrandContext } from "@/components/providers/BrandProvider";

type TaskStatus = "running" | "paused" | "failed" | "cancelled" | "completed";
type MessageRole = "assistant" | "user" | "system";

interface ChatMessage {
  id: string;
  role: MessageRole;
  text: string;
  messageType?: string;
  artifactRefs?: WorkflowArtifactRef[];
  runId?: string | null;
  actionUrl?: string;
  actionLabel?: string;
}

interface WorkflowTask {
  stage: string;
  status: TaskStatus;
  progress: number;
  runId: string;
  completedSteps: number;
  totalSteps: number;
  currentStepLabel: string;
}

const WELCOME_MESSAGE: ChatMessage = {
  id: "msg-welcome",
  role: "assistant",
  text: "你好，我是品牌内容增长助手。描述你想生成的内容，直接发送就能开始。",
};

function createId(prefix: string) {
  return `${prefix}-${Math.random().toString(16).slice(2, 10)}`;
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
  if (stage === "intake") return "需求理解";
  if (stage === "context") return "上下文构建";
  if (stage === "discovery") return "素材发现";
  if (stage === "retrieval") return "资料召回";
  if (stage === "strategy") return "策略生成";
  if (stage === "generation") return "笔记生成";
  if (stage === "finalization") return "结果整理";
  if (stage === "review") return "等待确认";
  return "已完成";
}

function stepLabel(stepName: string | null | undefined, phase?: string | null) {
  if (!stepName && phase) return stageLabel(phase);
  if (!stepName) return "准备任务";
  const labels: Record<string, string> = {
    "intake.capture_request": "理解创作需求",
    "context.build_context": "构建创作上下文",
    "context.load_constraints": "读取补充要求",
    "context.load_previous_artifacts": "读取历史结果",
    "discovery.plan_queries": "规划真实搜索关键词",
    "discovery.spider_search": "正在搜索小红书真实内容",
    "discovery.assess_source_quality": "评估真实素材质量",
    "discovery.expand_queries": "扩展搜索方向",
    "discovery.persist_sources": "保存真实素材快照",
    "retrieval.rag_index": "建立资料索引",
    "retrieval.rag_retrieve": "召回相关资料",
    "strategy.prepare_prompt": "准备策略提示词",
    "strategy.llm_synthesize": "生成内容策略",
    "strategy.validate_strategy": "校验内容策略",
    "strategy.persist_strategy": "保存内容策略",
    "generation.plan_proposals": "规划笔记选题",
    "generation.select_proposals": "筛选笔记方案",
    "generation.generate_notes_parallel": "生成小红书笔记",
    "generation.similarity_check": "检查内容相似度",
    "generation.rewrite_or_reselect": "优化笔记内容",
    "generation.aggregate_notes": "整理生成笔记",
    "finalization.persist_artifacts": "整理创作结果",
    "finalization.emit_result_ready": "准备结果展示",
    "review.await_user_acceptance": "等待确认",
    "review.publish_candidates": "整理发布候选",
  };
  return labels[stepName] ?? stageLabel(phase ?? stepName.split(".")[0]);
}

function statusLabel(status: TaskStatus) {
  if (status === "running") return "进行中";
  if (status === "paused") return "已暂停";
  if (status === "failed") return "执行失败";
  if (status === "cancelled") return "已中断";
  return "完成";
}

function taskFromSnapshot(snapshot: WorkflowRunSnapshot): WorkflowTask | null {
  const status = snapshot.run.status;
  const totalSteps = snapshot.steps.length;
  const completedSteps = snapshot.steps.filter((step) =>
    ["succeeded", "skipped", "cancelled", "failed"].includes(step.status)
  ).length;
  const progress = totalSteps > 0 ? Math.round((completedSteps / totalSteps) * 100) : 0;
  const currentStep = snapshot.steps.find((step) => step.status === "running") ??
    snapshot.steps.find((step) => step.step_name === snapshot.run.current_step) ??
    snapshot.steps.find((step) => ["pending", "retrying"].includes(step.status));
  const currentStepLabel = stepLabel(currentStep?.step_name ?? snapshot.run.current_step, snapshot.run.phase);
  if (status === "succeeded") {
    return {
      stage: "completed",
      status: "completed",
      progress: 100,
      runId: snapshot.run.run_id,
      completedSteps: totalSteps,
      totalSteps,
      currentStepLabel: "任务已完成",
    };
  }
  if (status === "failed") {
    return {
      stage: snapshot.run.phase,
      status: "failed",
      progress,
      runId: snapshot.run.run_id,
      completedSteps,
      totalSteps,
      currentStepLabel,
    };
  }
  if (status === "cancelled" || status === "cancelling") {
    return {
      stage: snapshot.run.phase,
      status: "cancelled",
      progress,
      runId: snapshot.run.run_id,
      completedSteps,
      totalSteps,
      currentStepLabel,
    };
  }
  if (status === "paused" || status === "pausing") {
    return {
      stage: snapshot.run.phase,
      status: "paused",
      progress,
      runId: snapshot.run.run_id,
      completedSteps,
      totalSteps,
      currentStepLabel,
    };
  }
  if (status === "created" || status === "running" || status === "waiting_user") {
    return {
      stage: snapshot.run.phase,
      status: "running",
      progress,
      runId: snapshot.run.run_id,
      completedSteps,
      totalSteps,
      currentStepLabel,
    };
  }
  return null;
}

function workflowEventLine(data: WorkflowRunEventData): string | null {
  const raw = data.payload?.message;
  if (raw && typeof raw === "string") return translateStatusMsg(raw);
  const stepName = typeof data.payload?.step_name === "string" ? data.payload.step_name : undefined;
  if (data.event_type === "run_started") return "任务已创建";
  if (data.event_type === "steps_initialized") return "已拆解创作步骤";
  if (data.event_type === "embedding_initializing") return "正在初始化本地向量模型（首次较慢）";
  if (data.event_type === "run_advanced") return `准备执行：${stepLabel(stepName)}`;
  if (data.event_type === "step_started") return `正在执行：${stepLabel(stepName)}`;
  if (data.event_type === "step_completed") return `已完成：${stepLabel(stepName)}`;
  if (data.event_type === "artifact_attached") return "已保存阶段结果";
  if (data.event_type === "constraint_added") return "已记录补充要求";
  if (data.event_type === "run_pause_requested") return "已请求暂停";
  if (data.event_type === "run_resumed") return "已恢复任务";
  if (data.event_type === "run_cancel_requested") return "已请求取消";
  if (data.event_type === "run_succeeded" || data.event_type === "run_completed") return "任务已完成";
  if (data.event_type === "run_failed" || data.event_type === "step_failed") return "任务执行失败";
  return null;
}

function chatMessageFromRecord(message: CreatorMessage): ChatMessage {
  return {
    id: message.message_id,
    role: message.role,
    text: message.text,
    messageType: message.message_type,
    artifactRefs: message.artifact_refs,
    runId: message.run_id,
  };
}

function payloadFromArtifactRef(ref: WorkflowArtifactRef): Record<string, unknown> | null {
  return ref.artifact?.materialized_payload_json ?? ref.artifact?.payload_json ?? null;
}

function noteFromArtifactRef(ref: WorkflowArtifactRef): GeneratedNoteItem | null {
  const payload = payloadFromArtifactRef(ref);
  if (!payload) return null;
  const nested = typeof payload.note === "object" && payload.note !== null
    ? payload.note as Record<string, unknown>
    : payload;
  const title = nested.title ?? nested.hook ?? nested.summary;
  const content = nested.content ?? nested.body ?? nested.outline;
  if (!title && !content) return null;
  const tags = Array.isArray(nested.tags)
    ? nested.tags
    : Array.isArray(nested.suggested_tags)
      ? nested.suggested_tags
      : [];
  return {
    note_id: String(nested.note_id ?? nested.id ?? ref.artifact_id),
    title: String(title ?? "未命名笔记"),
    content: String(content ?? ""),
    tags: tags.map(String),
  };
}

function collectArtifactRefs(messages: ChatMessage[]): WorkflowArtifactRef[] {
  const byId = new Map<string, WorkflowArtifactRef>();
  for (const message of messages) {
    for (const ref of message.artifactRefs ?? []) {
      if (!ref.artifact_id) continue;
      byId.set(ref.artifact_id, { ...byId.get(ref.artifact_id), ...ref });
    }
  }
  return [...byId.values()];
}

function shortArtifactId(id: string | null | undefined): string {
  if (!id) return "";
  return id.length > 14 ? `${id.slice(0, 10)}...` : id;
}

function artifactStatusLabel(ref: WorkflowArtifactRef): string {
  const status = ref.artifact?.status ?? "created";
  if (ref.artifact_type === "final_result" || ref.artifact?.artifact_type === "final_result") return "final";
  if (status === "accepted") return "accepted";
  if (status === "active") return "active";
  if (status === "superseded") return "superseded";
  return status;
}

function versionLabel(ref: WorkflowArtifactRef): string {
  const version = ref.artifact_version ?? ref.artifact?.artifact_version ?? 1;
  return `v${version}`;
}

function findArtifactRef(refs: WorkflowArtifactRef[], artifactId: string | null | undefined): WorkflowArtifactRef | null {
  if (!artifactId) return null;
  return refs.find((ref) => ref.artifact_id === artifactId) ?? null;
}

function versionChainFor(ref: WorkflowArtifactRef, allRefs: WorkflowArtifactRef[]): WorkflowArtifactRef[] {
  const chain: WorkflowArtifactRef[] = [];
  const seen = new Set<string>();
  let cursor: WorkflowArtifactRef | null = ref;
  while (cursor && !seen.has(cursor.artifact_id)) {
    seen.add(cursor.artifact_id);
    chain.unshift(cursor);
    cursor = findArtifactRef(allRefs, cursor.parent_artifact_id ?? cursor.artifact?.parent_artifact_id);
  }
  return chain;
}

function ArtifactVersionBadges({ ref, current = false }: { ref: WorkflowArtifactRef; current?: boolean }) {
  const mode = ref.artifact?.payload_mode ?? "snapshot";
  const parentId = ref.parent_artifact_id ?? ref.artifact?.parent_artifact_id;
  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px] font-medium uppercase tracking-wide">
      {current && <span className="rounded-full bg-ink px-2 py-0.5 text-white">current</span>}
      <span className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-600">{versionLabel(ref)}</span>
      <span className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-600">{artifactStatusLabel(ref)}</span>
      <span className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-600">{mode}</span>
      {parentId && (
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-600">
          parent {shortArtifactId(parentId)}
        </span>
      )}
    </div>
  );
}

function VersionChainView({ current, allRefs }: { current: WorkflowArtifactRef; allRefs: WorkflowArtifactRef[] }) {
  const chain = versionChainFor(current, allRefs);
  const parentId = current.parent_artifact_id ?? current.artifact?.parent_artifact_id;
  if (!parentId && chain.length <= 1) return null;

  return (
    <details className="mt-3 rounded-lg border border-line/60 bg-slate-50 px-3 py-2">
      <summary className="cursor-pointer text-xs font-medium text-slate-600">
        版本链（{chain.length} 个版本）
      </summary>
      <div className="mt-2 space-y-2">
        {chain.map((item) => {
          const note = noteFromArtifactRef(item);
          const isCurrent = item.artifact_id === current.artifact_id;
          return (
            <div key={item.artifact_id} className="rounded-lg bg-white px-3 py-2">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-xs font-medium text-ink">
                    {note?.title ?? item.artifact?.summary_text ?? shortArtifactId(item.artifact_id)}
                  </p>
                  {note?.content && (
                    <p className="mt-1 line-clamp-2 text-[11px] leading-4 text-quiet">{note.content}</p>
                  )}
                </div>
                <span className="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600">
                  {isCurrent ? "当前" : "旧版"}
                </span>
              </div>
              <ArtifactVersionBadges ref={item} current={isCurrent} />
            </div>
          );
        })}
        {parentId && chain.length === 1 && (
          <p className="text-[11px] text-quiet">父版本 {shortArtifactId(parentId)} 尚未出现在当前时间线引用中。</p>
        )}
      </div>
    </details>
  );
}

function ArtifactRefsView({ refs, allRefs }: { refs: WorkflowArtifactRef[]; allRefs: WorkflowArtifactRef[] }) {
  const notes = refs
    .filter((ref) => ref.artifact_type === "generated_note" || ref.artifact?.artifact_type === "generated_note")
    .map((ref) => ({ ref, note: noteFromArtifactRef(ref) }))
    .filter((item): item is { ref: WorkflowArtifactRef; note: GeneratedNoteItem } => item.note !== null);
  const strategies = refs
    .filter((ref) => ref.artifact_type === "strategy" || ref.artifact?.artifact_type === "strategy")
    .map((ref) => ({ ref, payload: payloadFromArtifactRef(ref) }))
    .filter((item): item is { ref: WorkflowArtifactRef; payload: Record<string, unknown> } => item.payload !== null);

  if (notes.length === 0 && strategies.length === 0) return null;

  return (
    <div className="mt-3 space-y-2.5">
      {strategies.map(({ ref, payload }, index) => (
        <div key={`strategy-${index}`} className="rounded-xl border border-line/60 bg-white px-4 py-3">
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-quiet">
            内容策略定位
          </p>
          <p className="leading-6 text-ink">{String(payload.positioning ?? payload.summary ?? "策略已生成")}</p>
          <ArtifactVersionBadges ref={ref} current />
        </div>
      ))}
      {notes.length > 0 && (
        <div className="space-y-2.5">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-quiet">
            生成笔记（{notes.length} 篇）
          </p>
          {notes.map(({ ref, note }) => (
            <div key={ref.artifact_id} className="rounded-xl border border-line/60 bg-white px-4 py-3">
              <p className="font-medium text-ink">{note.title}</p>
              <p className="mt-1 line-clamp-4 text-xs leading-5 text-quiet">{note.content}</p>
              {note.tags.filter(Boolean).length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {note.tags.filter(Boolean).map((tag) => (
                    <span key={tag} className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-quiet">
                      #{tag}
                    </span>
                  ))}
                </div>
              )}
              <ArtifactVersionBadges ref={ref} current />
              <VersionChainView current={ref} allRefs={allRefs} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function CreatorPage() {
  const { selectedBrandId } = useBrandContext();
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
  const taskRef = useRef<WorkflowTask | null>(null);
  const activeThreadIdRef = useRef<string | null>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const snapshotRefreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const snapshotRefreshInFlightRef = useRef(false);
  // Tracks the most recently *requested* thread load to discard stale responses
  const loadingThreadRef = useRef<string | null>(null);

  const activeThread = threads.find((t) => t.thread_id === activeThreadId) ?? null;
  const activeTopicPoolUrl = activeThread
    ? `/topic-pool?thread_id=${encodeURIComponent(activeThread.thread_id)}${task?.runId ? `&run_id=${encodeURIComponent(task.runId)}` : ""}`
    : null;
  const isTaskRunning = task?.status === "running" || task?.status === "paused";
  const showTaskCard = task?.status === "running" || task?.status === "paused" || task?.status === "failed";
  const allArtifactRefs = collectArtifactRefs(messages);

  useEffect(() => { taskRef.current = task; }, [task]);
  useEffect(() => { activeThreadIdRef.current = activeThreadId; }, [activeThreadId]);

  // Auto-scroll only for chat/result changes. Task progress stays inside the
  // progress card and must not push the conversation downward on every event.
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, generatedResult]);

  // Load thread list on mount; auto-select most recent
  useEffect(() => {
    listThreads(selectedBrandId)
      .then((items) => {
        setThreads(items);
        if (items.length > 0) void selectThread(items[0].thread_id);
      })
      .catch(() => {});
  }, [selectedBrandId]);

  // SSE subscription — active only while task is running
  useEffect(() => {
    if (!activeThreadId || !task || task.status !== "running") return;
    const subscribedThreadId = activeThreadId;
    const subscribedRunId = task.runId;
    const isStale = () => activeThreadIdRef.current !== subscribedThreadId;

    const refreshSnapshot = async () => {
      if (snapshotRefreshInFlightRef.current) return;
      snapshotRefreshInFlightRef.current = true;
      try {
        const snapshot = await getWorkflowRunSnapshot(subscribedRunId, subscribedThreadId);
        if (isStale()) return;
        applySnapshot(snapshot, subscribedThreadId);
      } catch {
        // Snapshot refresh is best-effort; SSE keeps the visible log moving.
      } finally {
        snapshotRefreshInFlightRef.current = false;
      }
    };

    const scheduleRefreshSnapshot = () => {
      if (snapshotRefreshTimerRef.current) return;
      snapshotRefreshTimerRef.current = setTimeout(() => {
        snapshotRefreshTimerRef.current = null;
        void refreshSnapshot();
      }, 500);
    };

    const es = subscribeWorkflowRunEvents(subscribedRunId, {
      onEvent: (data) => {
        if (isStale()) return;
        const progress = typeof data.payload?.progress === "number" ? data.payload.progress : undefined;
        if (progress !== undefined) {
          setTask((t) => t ? { ...t, progress } : t);
        }
        if (data.payload?.phase && typeof data.payload.phase === "string") {
          setTask((t) => t ? { ...t, stage: data.payload.phase as string } : t);
        }
        const line = workflowEventLine(data);
        if (line) setStatusLog((log) => [...log, line].slice(-6));
        scheduleRefreshSnapshot();
      },
      onCompleted: () => {
        if (isStale()) return;
        es.close();
        setTask((t) => t ? { ...t, status: "completed", stage: "completed", progress: 100 } : t);
        setIsAccepted(false);
        setStatusLog((log) => [...log, "任务已完成"].slice(-6));
        void refreshSnapshot();
        getThreadResult(subscribedThreadId)
          .then((result) => {
            if (isStale()) return;
            setGeneratedResult({
              strategy: result.strategy as { positioning: string } | null,
              notes: result.notes,
            });
          })
          .catch(() => {
            appendMessage({ role: "assistant", text: "任务完成，但读取结果失败，请刷新页面重试。" });
          });
      },
      onFailed: () => {
        if (isStale()) return;
        es.close();
        setTask((t) => t ? { ...t, status: "failed" } : t);
        setStatusLog((log) => [...log, "任务执行失败，请重试。"].slice(-6));
      },
      onCancelled: () => {
        if (isStale()) return;
        es.close();
        setTask((t) => t ? { ...t, status: "cancelled" } : t);
        setStatusLog((log) => [...log, "任务已取消。"].slice(-6));
      },
    });

    return () => {
      if (snapshotRefreshTimerRef.current) {
        clearTimeout(snapshotRefreshTimerRef.current);
        snapshotRefreshTimerRef.current = null;
      }
      es.close();
    };
  }, [activeThreadId, task?.status, task?.runId]);

  function appendMessage(message: Omit<ChatMessage, "id">) {
    setMessages((current) => [...current, { ...message, id: createId("msg") }]);
  }

  function applySnapshot(snapshot: WorkflowRunSnapshot | null | undefined, threadId: string) {
    if (!snapshot) return;
    const nextTask = taskFromSnapshot(snapshot);
    setTask(nextTask);
    setThreads((current) =>
      current.map((t) =>
        t.thread_id === threadId
          ? { ...t, active_run_id: snapshot.run.run_id, updated_at: new Date().toISOString() }
          : t
      )
    );
    if (snapshot.artifacts.length === 0) {
      setGeneratedResult(null);
    }
  }

  function resetConversation() {
    setMessages([WELCOME_MESSAGE]);
    setTask(null);
    setStatusLog([]);
    setGeneratedResult(null);
    setIsAccepted(false);
  }

  async function selectThread(threadId: string) {
    setActiveThreadId(threadId);
    setActiveMenuId(null);
    resetConversation();
    loadingThreadRef.current = threadId;
    try {
      const { thread, messages: history } = await getThreadTimeline(threadId);
      // Discard stale response if user already switched to another thread
      if (loadingThreadRef.current !== threadId) return;
      if (history.length > 0) {
        setMessages(history.map(chatMessageFromRecord));
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

      if (thread.active_run_id && thread.status !== "accepted") {
        const snapshot = await getWorkflowRunSnapshot(thread.active_run_id, threadId);
        if (loadingThreadRef.current !== threadId) return;
        applySnapshot(snapshot, threadId);
      }
    } catch {
      // silently keep welcome message on load failure
    }
  }

  async function handleNewThread() {
    try {
      const created = await createThread(undefined, selectedBrandId);
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
      brand_id: selectedBrandId,
      title,
      status: "active",
      active_job_id: null,
      active_run_id: null,
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
      const created = await createThread(title, selectedBrandId);
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
    setIsLoading(true);
    setInput("");
    // Reset textarea height after clearing
    if (inputRef.current) {
      inputRef.current.style.height = "auto";
    }
    appendMessage({ role: "user", text });

    // Ensure we have an active thread (auto-create if none)
    const threadId = await ensureThread(text);
    if (!threadId) {
      setIsLoading(false);
      return;
    }

    try {
      const result = await appendThreadMessage(threadId, text);
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
      if (result.active_run_snapshot) {
        applySnapshot(result.active_run_snapshot, threadId);
      }
      if (
        result.intent === "complete_run" ||
        result.intent === "revise_artifact" ||
        result.intent === "rerun_workflow"
      ) {
        try {
          const timeline = await getThreadTimeline(threadId);
          setMessages(timeline.messages.map(chatMessageFromRecord));
        } catch {
          // The optimistic user/assistant messages above remain valid.
        }
      }
    } catch {
      appendMessage({ role: "system", text: "发送失败，请检查 runtime 是否在线。" });
    } finally {
      setIsLoading(false);
      inputRef.current?.focus();
    }
  }

  async function handleComplete() {
    if (!activeThread || isAccepted) return;
    try {
      const result = await completeThread(activeThread.thread_id);
      setIsAccepted(true);
      appendMessage({
        role: "assistant",
        text: `已加入选题库（${result.publish_candidate_count} 篇）。`,
        actionLabel: "查看选题库",
        actionUrl: activeTopicPoolUrl ?? "/topic-pool",
      });
    } catch {
      appendMessage({ role: "system", text: "提交失败，请检查 runtime 是否在线。" });
    }
  }

  async function handleStopTask() {
    if (!task || !["running", "paused"].includes(task.status)) return;
    try {
      if (!activeThreadId) return;
      const result = await appendThreadMessage(activeThreadId, "取消当前任务");
      if (result.active_run_snapshot) {
        applySnapshot(result.active_run_snapshot, activeThreadId);
      } else {
        setTask((t) => t ? { ...t, status: "cancelled" } : t);
      }
      setTask((t) => t ? { ...t, status: "cancelled" } : t);
      setStatusLog([]);
      if (result.assistant_reply) {
        appendMessage({ role: "assistant", text: result.assistant_reply });
      }
    } catch {
      appendMessage({ role: "system", text: "停止任务失败，请稍后重试。" });
    }
  }

  async function handlePauseOrResumeTask() {
    if (!task || !activeThreadId) return;
    try {
      const result = await appendThreadMessage(
        activeThreadId,
        task.status === "paused" ? "继续" : "暂停一下"
      );
      if (result.active_run_snapshot) {
        applySnapshot(result.active_run_snapshot, activeThreadId);
      }
      if (result.assistant_reply) {
        appendMessage({ role: "assistant", text: result.assistant_reply });
      }
    } catch {
      appendMessage({ role: "system", text: "任务控制失败，请稍后重试。" });
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
                        {message.actionUrl && message.actionLabel ? (
                          <a
                            className="mt-2 inline-flex rounded-lg border border-line bg-white px-3 py-1.5 text-xs font-medium text-ink transition hover:bg-slate-50"
                            href={message.actionUrl}
                          >
                            {message.actionLabel}
                          </a>
                        ) : null}
                        {message.messageType === "artifact_result" && message.artifactRefs && (
                          <ArtifactRefsView refs={message.artifactRefs} allRefs={allArtifactRefs} />
                        )}
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

            {/* Live WorkflowRun progress card */}
            {showTaskCard && task && (
              <div className="flex justify-start">
                <div className="w-full max-w-[80%] rounded-2xl bg-[#f0f2f5] px-4 py-3 text-sm text-ink">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <div className="mb-2 flex items-center gap-2">
                        <span
                          className={[
                            "h-2 w-2 rounded-full",
                            task.status === "failed" ? "bg-danger" : "animate-pulse bg-emerald-500",
                          ].join(" ")}
                        />
                        <span className="font-medium">
                          {task.status === "failed" ? "创作任务执行失败" : "创作任务进行中"}
                        </span>
                      </div>
                      <div className="space-y-1 text-xs leading-5 text-quiet">
                        <p>阶段：{stageLabel(task.stage)}</p>
                        <p>当前：{task.currentStepLabel}</p>
                        <p>
                          进度：{task.completedSteps} / {task.totalSteps || "?"} · {task.progress}%
                        </p>
                      </div>
                    </div>
                    <span className="shrink-0 rounded-full bg-white px-2.5 py-1 text-xs text-quiet">
                      {statusLabel(task.status)}
                    </span>
                  </div>
                  <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white">
                    <div
                      className="h-full rounded-full bg-emerald-500 transition-all"
                      style={{ width: `${Math.max(0, Math.min(100, task.progress))}%` }}
                    />
                  </div>
                  <div className="mt-3">
                    <p className="mb-1.5 text-xs font-medium text-quiet">最近进展：</p>
                    <div className="space-y-1.5">
                      {(statusLog.length ? statusLog : ["任务已创建"]).map((line, i, lines) => {
                        const isLatest = i === lines.length - 1;
                        return (
                          <div
                            key={`${line}-${i}`}
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
                  <div className="mt-3 flex gap-2">
                    {task.status !== "failed" && (
                      <>
                        <button
                          type="button"
                          onClick={() => void handlePauseOrResumeTask()}
                          className="rounded-lg border border-line bg-white px-3 py-1.5 text-xs font-medium text-ink transition hover:bg-slate-50"
                        >
                          {task.status === "paused" ? "继续" : "暂停"}
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleStopTask()}
                          className="rounded-lg border border-danger/30 bg-dangerBg px-3 py-1.5 text-xs font-medium text-danger transition hover:bg-red-100"
                        >
                          取消
                        </button>
                      </>
                    )}
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
                        ? "笔记已加入选题库，可直接查看。"
                        : "确认结果后点击完成，笔记将进入选题库。"}
                      {isAccepted && activeTopicPoolUrl ? (
                        <a
                          className="ml-2 font-medium text-slate-700 underline decoration-dotted underline-offset-4"
                          href={activeTopicPoolUrl}
                        >
                          查看选题库
                        </a>
                      ) : null}
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
