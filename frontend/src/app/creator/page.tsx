"use client";

import React, { useEffect, useRef, useState } from "react";
import type { PointerEvent } from "react";
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
import {
  contentResearchCommand,
  createContentResearchPresearch,
  createContentResearchCommandId,
  endContentResearchWorkflow,
  getContentResearchDirectionEvidence,
  getContentResearchTrace,
  isContentResearchReportPending,
  getContentResearchLiteReport,
  getContentResearchLiteReportWithRetry,
  getContentResearchScope,
  getContentResearchWorkflow,
  reviseContentResearchSubject,
  retryContentResearchPresearch,
  runContentResearchWorkflowAction,
  type ContentResearchDirectionEvidence,
  type ContentResearchLiteReportResponse,
  type ContentResearchPresearchResponse,
  type ContentResearchConfirmScopeRequest,
  type ContentResearchPrepareScopeRequest,
  type ContentResearchResolveCoverageRequest,
  type ContentResearchScopeDraft,
  type ContentResearchScopeProjection,
  type ContentResearchTrace,
  type ContentResearchWorkflowSummary,
} from "@/lib/content-research-api";
import { ModelServiceCard } from "@/components/content-research/ModelServiceCard";
import { XiaohongshuLoginCard } from "@/components/content-research/XiaohongshuLoginCard";
import { traceExecutionDurationText } from "@/lib/content-research-trace";
import { resolveContentResearchModelRecovery } from "@/lib/content-research-recovery";
import { contentResearchErrorFeedback } from "@/lib/content-research-error-feedback";
import {
  ContentResearchRequestEpoch,
  frozenReportScopeQueries,
  projectedModelRecoveryVisible,
} from "./page-state";

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
  report?: ContentResearchLiteReportResponse;
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

interface ContentResearchIntentState {
  seed: string;
  presearch: ContentResearchPresearchResponse;
}

interface ContentResearchRunState {
  workflowRunId: string;
  summary: ContentResearchWorkflowSummary;
  trace: ContentResearchTrace | null;
  formalResearch: ContentResearchFormalResearchReadModel | null;
  formalResearchStatus: "idle" | "collecting" | "completed" | "failed" | "invalid";
  report: ContentResearchLiteReportResponse | null;
  reportStatus: "idle" | "loading" | "ready" | "unavailable" | "failed";
  reportError: string | null;
}
interface ContentResearchFormalResearchReadModel {
  status: string;
  task_count: number;
  failed_tasks: Array<{ task_id: string; agent_name?: string | null; error?: string | null }>;
}
type LitePublicationState = "complete_verified_report" | "partial_verified_report" | "directional_report" | "evidence_only_report";

function litePublicationState(report: ContentResearchLiteReportResponse): LitePublicationState | null {
  const state = stringField(report.publication, "state");
  return state === "complete_verified_report" || state === "partial_verified_report" || state === "directional_report" || state === "evidence_only_report"
    ? state
    : null;
}

function isExpectedLiteReportAbsence(error: unknown) {
  return isContentResearchReportPending(error);
}

const WELCOME_MESSAGE: ChatMessage = {
  id: "msg-welcome",
  role: "assistant",
  text: "你好，我是品牌内容增长助手。描述你想生成的内容，直接发送就能开始。",
};
const CONTENT_RESEARCH_ACTIVE_RUNS_STORAGE_KEY = "xhs-growth-agent:content-research-active-runs-by-thread";


function contentResearchRunsByThread(): Record<string, string> {
  try {
    const stored = window.localStorage.getItem(CONTENT_RESEARCH_ACTIVE_RUNS_STORAGE_KEY);
    const parsed: unknown = stored ? JSON.parse(stored) : {};
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return Object.fromEntries(
      Object.entries(parsed).filter(([threadId, runId]) => typeof threadId === "string" && typeof runId === "string" && runId)
    );
  } catch {
    return {};
  }
}

function contentResearchRunForThread(threadId: string): string | null {
  return contentResearchRunsByThread()[threadId] ?? null;
}

function contentResearchThreadForRun(workflowRunId: string): string | null {
  return Object.entries(contentResearchRunsByThread())
    .find(([, savedRunId]) => savedRunId === workflowRunId)?.[0] ?? null;
}

function saveContentResearchRunForThread(threadId: string, workflowRunId: string) {
  const runs = contentResearchRunsByThread();
  runs[threadId] = workflowRunId;
  window.localStorage.setItem(CONTENT_RESEARCH_ACTIVE_RUNS_STORAGE_KEY, JSON.stringify(runs));
}

function removeContentResearchRunForThread(threadId: string) {
  const runs = contentResearchRunsByThread();
  if (!(threadId in runs)) return;
  delete runs[threadId];
  if (Object.keys(runs).length === 0) {
    window.localStorage.removeItem(CONTENT_RESEARCH_ACTIVE_RUNS_STORAGE_KEY);
  } else {
    window.localStorage.setItem(CONTENT_RESEARCH_ACTIVE_RUNS_STORAGE_KEY, JSON.stringify(runs));
  }
}

function createId(prefix: string) {
  return `${prefix}-${Math.random().toString(16).slice(2, 10)}`;
}

function formalResearchStatus(result: ContentResearchFormalResearchReadModel | null): ContentResearchRunState["formalResearchStatus"] {
  if (!result) return "idle";
  if (result.status === "failed") return "failed";
  return ["completed", "partial_completed", "succeeded"].includes(result.status)
    ? "completed"
    : "collecting";
}

function contentResearchStartFailure(detail: string): {
  status: ContentResearchRunState["formalResearchStatus"];
  message: string;
} {
  if (/Creator thread (is required|no longer exists)/i.test(detail)) {
    return {
      status: "invalid",
      message: "本轮调研所属的 Creator 对话已不存在，无法继续。请在有效对话中重新发起一轮内容调研。",
    };
  }
  return {
    status: "failed",
    message: `专家调研启动失败：${detail}。请检查来源登录态、采集服务和运行配置后继续。`,
  };
}

function isUncertainContentResearchDispatchFailure(detail: string) {
  // Confirmation has already atomically persisted the run and its queued
  // dispatch job. A browser transport failure while asking the API to wake the
  // dispatcher therefore does not establish that the formal run failed to
  // start; the server may have accepted the request (or the recovery scan may
  // claim the queued job) before the response became unavailable.
  return /failed to fetch|networkerror|network request failed/i.test(detail);
}

function contentResearchRunWithReport(
  workflowRunId: string,
  summary: ContentResearchWorkflowSummary,
  trace: ContentResearchTrace | null,
  formalResearch: ContentResearchFormalResearchReadModel | null,
  report: ContentResearchLiteReportResponse | null
): ContentResearchRunState {
  return {
    workflowRunId,
    summary,
    trace,
    formalResearch,
    formalResearchStatus: formalResearchStatus(formalResearch),
    report,
    reportStatus: report ? "ready" : "idle",
    reportError: null,
  };
}

function presearchFromWorkflowSummary(
  summary: ContentResearchWorkflowSummary,
): ContentResearchPresearchResponse {
  if (!summary.brief) throw new Error("当前状态尚未生成 Brief");
  const payload = summary.brief.payload;
  const rawStructure = payload.subject_structure;
  const subjectStructure = rawStructure && typeof rawStructure === "object" && !Array.isArray(rawStructure)
    ? rawStructure as ContentResearchPresearchResponse["subject_structure"]
    : {};
  return {
    attempt_id: stringField(payload, "attempt_id", `restored-${summary.workflow_run_id}`),
    workflow_run_id: summary.workflow_run_id,
    brief_id: summary.brief.id,
    status: stringField(payload, "status", summary.brief.status),
    subject_confirmation: stringField(payload, "subject_confirmation"),
    competitor_tags: arrayField(payload, "competitor_tags"),
    research_directions: arrayField(payload, "research_directions"),
    direction_catalog: arrayField(payload, "direction_catalog"),
    custom_competitor_input: stringField(payload, "custom_competitor_input"),
    timeout_status: stringField(payload, "timeout_status", "none"),
    fallback_used: payload.fallback_used === true,
    error_code: stringField(payload, "error_code") || null,
    error_message: stringField(payload, "error_message") || null,
    recoverable: payload.recoverable === true,
    configuration_source: stringField(payload, "configuration_source") || null,
    model: stringField(payload, "model") || null,
    subject_structure: subjectStructure,
    subject_structure_hash: stringField(payload, "subject_structure_hash") || null,
    run: summary.run,
  };
}

function sourceFailureReasonText(reason: string | null | undefined) {
  if (reason === "auth_required") return "需要登录小红书网页端";
  if (reason === "rate_limited") return "当前访问过于频繁，请稍后再继续";
  if (reason) return "采集服务暂时不可用，请检查服务状态后继续";
  return "无";
}

function workflowStatusLabel(status: string) {
  if (["completed", "succeeded", "success"].includes(status)) return "已完成";
  if (["running", "collecting"].includes(status)) return "进行中";
  if (status === "pending") return "等待开始";
  if (status === "retrying") return "等待恢复";
  if (status === "waiting_user") return "等待恢复";
  if (status === "failed") return "未完成";
  if (["cancelled", "cancelling"].includes(status)) return "已结束";
  return "等待处理";
}

function contentResearchSubject(run: ContentResearchRunState): string {
  const payload = run.summary.brief?.payload ?? {};
  return stringField(payload, "seed_text") ||
    stringField(payload, "confirmed_subject") ||
    stringField(payload, "subject_confirmation") ||
    "本轮调研";
}

function displayChatText(text: string | null | undefined) {
  // Timeline records from an older runtime can lack free text; an unavailable
  // report must still render its explicit error state instead of crashing.
  return (text ?? "").replace(
    /(小红书采集未完成：)([a-z_]+)(。可在「查看调研过程」中重试。)/,
    (_match, prefix, reason) => `${prefix}${sourceFailureReasonText(reason)}。`
  ).replace(
    /返回 (\d+) 条 search_result_minimal 素材/g,
    "已采集 $1 条公开内容"
  );
}

function claimStatusLabel(status: string) {
  if (status === "supported") return "证据支持";
  if (status === "evidence_insufficient") return "证据不足";
  if (status === "unsupported") return "证据不足";
  return status || "未知";
}

function evidenceStateLabel(value: string) {
  if (value === "verified") return "已验证";
  if (value === "partially_supported") return "部分支持";
  if (value === "signal") return "线索";
  if (value === "case_only") return "单案例";
  if (value === "invalid") return "不可用";
  return value || "未知";
}

function priorityLabel(value: string) {
  if (value === "high_priority") return "优先执行";
  if (value === "high_potential_needs_more_evidence") return "高潜力需补证";
  if (value === "evidence_backed_reference") return "证据参考";
  if (value === "useful_but_lower_priority") return "低优先级";
  if (value === "do_not_prioritize") return "暂不推荐";
  return value || "未知";
}

function roleLabel(role: string) {
  if (role === "supporting_fact") return "支持证据";
  if (role === "conflicting_fact") return "冲突证据";
  if (role === "missing_evidence") return "缺失证据";
  return role;
}

function readableClaimScope(value: string) {
  if (/^use as a bounded research signal\.?$/i.test(value.trim())) {
    return "可作为调研线索，需结合更多证据判断。";
  }
  return value;
}

function readableMissingEvidence(item: Record<string, unknown>) {
  const message = stringField(item, "message", stringField(item, "reason", "暂未说明"));
  if (/^need comment evidence before finalizing the claim\.?$/i.test(message.trim())) {
    return "需补充用户评论证据后再确定该结论。";
  }
  return message;
}

function stringField(source: Record<string, unknown> | undefined | null, key: string, fallback = "") {
  const value = source?.[key];
  return typeof value === "string" ? value : fallback;
}

function firstString(source: Record<string, unknown> | undefined | null, keys: string[], fallback = "") {
  return keys.map((key) => stringField(source, key)).find(Boolean) || fallback;
}

function arrayField(source: Record<string, unknown> | undefined | null, key: string): string[] {
  const value = source?.[key];
  return Array.isArray(value) ? value.map(String).filter(Boolean) : [];
}

function decisionStatusLabel(status: string) {
  if (status === "selected") return "已选择";
  if (status === "watchlist") return "观察中";
  if (status === "rejected") return "已拒绝";
  return status || "未决策";
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
  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px] font-medium tracking-wide">
      {current && <span className="rounded-full bg-ink px-2 py-0.5 text-white">当前版本</span>}
      <span className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-600">{versionLabel(ref)}</span>
      <span className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-600">{artifactStatusLabel(ref)}</span>
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
          <p className="text-[11px] text-quiet">上一版本暂未加载。</p>
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

function splitInlineList(value: string) {
  return value
    .split(/[,，、\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

const LITE_DIRECTION_CATALOG = [
  { id: "product_marketing", label: "产品营销" },
  { id: "competitor_discovery", label: "竞品发现" },
  { id: "content_performance", label: "内容表现" },
] as const;

function liteDirectionLabel(value: string) {
  return LITE_DIRECTION_CATALOG.find((item) => item.id === value)?.label ?? value;
}

function ContentResearchIntentCard({
  intent,
}: {
  intent: ContentResearchIntentState;
}) {
  const structure = intent.presearch.subject_structure ?? {};
  const subject = structure.canonical_subject?.trim() || intent.seed || "本轮调研";
  const coreObject = structure.core_entities?.[0]?.canonical_name?.trim() || subject;
  const intents = (structure.research_intents ?? []).filter(Boolean).join("、") || "待确认";
  const contexts = (structure.context_modifiers ?? []).filter(Boolean).join("、") || "无特定场景";
  const presearchConclusion = intent.presearch.subject_confirmation.trim();
  const initialCompetitors = intent.presearch.competitor_tags.length ? intent.presearch.competitor_tags : ["待补充竞品"];
  const visibleDirections = LITE_DIRECTION_CATALOG
    .filter((item) => intent.presearch.direction_catalog.includes(item.id))
    .map((item) => item.id);
  const [subjectConfirmed, setSubjectConfirmed] = useState<"yes" | "mostly" | "no" | null>(null);
  const [selectedCompetitors, setSelectedCompetitors] = useState<string[]>([]);
  const [selectedDirections, setSelectedDirections] = useState<string[]>([]);
  const [extraCompetitors, setExtraCompetitors] = useState(intent.presearch.custom_competitor_input ?? "");

  function toggleValue(
    value: string,
    setSelected: React.Dispatch<React.SetStateAction<string[]>>,
  ) {
    setSelected((selected) => (
      selected.includes(value)
        ? selected.filter((item) => item !== value)
        : [...selected, value]
    ));
  }

  if (intent.presearch.run.state === "recovery_required") {
    return (
      <div className="flex justify-start">
        <div className="w-full max-w-[92%] rounded-2xl border border-amber-200 bg-amber-50 px-5 py-4 text-sm text-amber-900">
          模型服务配置需要更新。请在右侧保存并验证新配置后继续本次预检索。
        </div>
      </div>
    );
  }

  if (intent.presearch.run.state !== "brief_confirmation_required") return null;

  return (
    <div className="flex justify-start">
      <div className="w-full max-w-[92%] space-y-4">
        <div className="flex items-start gap-3">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-[#e9f0eb] text-2xl text-[#516f5f]">
            ⌕
          </div>
          <div>
            <h2 className="text-2xl font-semibold leading-tight text-ink">在开始前，请确认几个关键点</h2>
            <p className="mt-1 text-sm text-quiet">这能帮助专家团队更精准地锁定调研范围</p>
          </div>
        </div>

        <div className="rounded-2xl border border-line bg-white px-5 py-4 shadow-sm">
          <p className="text-sm font-medium text-slate-400">你的需求</p>
          <p className="mt-3 text-lg font-medium text-ink">{intent.seed}</p>
        </div>

        <div className="rounded-2xl border border-line bg-white px-5 py-4 shadow-sm">
          <p className="mb-3 rounded-xl bg-[#f4f7f5] px-3 py-2 text-xs leading-5 text-slate-600">
            核心对象：{coreObject}｜意图：{intents}｜场景：{contexts}
          </p>
          <p className="text-lg font-semibold leading-7 text-ink">
            我们识别到你要调研的是「{subject}」。是否准确？
          </p>
          {presearchConclusion && (
            <p className="mt-2 text-sm leading-6 text-quiet">预检索判断：{presearchConclusion}</p>
          )}
          <p className="mt-2 text-sm text-slate-400">若不准确，请直接在底部对话框补充说明。</p>
          <div className="mt-4 flex flex-wrap gap-2">
            {[
              ["yes", "准确，继续"],
              ["mostly", "大致准确，下面补充"],
              ["no", "不准确，我在下方说明"],
            ].map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => setSubjectConfirmed(value as "yes" | "mostly" | "no")}
                className={[
                  "rounded-full px-4 py-2 text-sm font-medium transition",
                  subjectConfirmed === value ? "bg-[#789180] text-white" : "bg-[#e8efe9] text-[#51665a] hover:bg-[#dfe8e1]",
                ].join(" ")}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="rounded-2xl border border-line bg-white px-5 py-4 shadow-sm">
          <p className="text-lg font-semibold leading-7 text-ink">
            为「{subject}」自动发现了以下候选竞品，请勾选你希望重点对比的对象（可多选）：
          </p>
          <p className="mt-2 text-sm text-slate-400">勾选后会纳入本轮对比；是否能形成结论取决于可获得的公开证据。</p>
          <div className="mt-4 flex flex-wrap gap-2">
            {initialCompetitors.map((competitor) => (
              <button
                key={competitor}
                type="button"
                onClick={() => competitor !== "待补充竞品" && toggleValue(competitor, setSelectedCompetitors)}
                disabled={competitor === "待补充竞品"}
                className={[
                  "rounded-full px-4 py-2 text-sm font-medium transition",
                  selectedCompetitors.includes(competitor) ? "bg-[#789180] text-white" : "bg-[#e8efe9] text-[#51665a] hover:bg-[#dfe8e1]",
                  competitor === "待补充竞品" ? "cursor-not-allowed opacity-50" : "",
                ].join(" ")}
              >
                {competitor}
              </button>
            ))}
          </div>
          <div className="mt-4">
            <input
              value={extraCompetitors}
              onChange={(event) => setExtraCompetitors(event.target.value)}
              className="h-11 min-w-0 flex-1 rounded-xl border border-line bg-white px-4 text-sm outline-none focus:border-[#789180]"
              placeholder="补充其他想调研的竞品，回车添加（可用逗号分隔多个）"
            />
          </div>
        </div>

        <div className="rounded-2xl border border-line bg-white px-5 py-4 shadow-sm">
          <p className="text-lg font-semibold leading-7 text-ink">请选择本轮调研方向（可多选）：</p>
          <div className="mt-4 flex flex-wrap gap-2">
            {visibleDirections.map((direction) => (
              <button
                key={direction}
                type="button"
                onClick={() => toggleValue(direction, setSelectedDirections)}
                className={[
                  "rounded-full px-4 py-2 text-sm font-medium transition",
                  selectedDirections.includes(direction) ? "bg-[#789180] text-white" : "bg-[#e8efe9] text-[#51665a] hover:bg-[#dfe8e1]",
                ].join(" ")}
              >
                {liteDirectionLabel(direction)}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function reportStateLabel(state: LitePublicationState) {
  if (state === "complete_verified_report") return "已完整核验";
  if (state === "partial_verified_report") return "部分内容已核验";
  return "仅展示已验证证据";
}

function recordList(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    : [];
}
function selectRequestedDirectionStates(report: ContentResearchLiteReportResponse) {
  const requestedDirectionIds = new Set(arrayField(report.frozen_scope, "direction_ids"));
  return recordList(report.run_direction_states).filter((item) =>
    requestedDirectionIds.has(stringField(item, "direction"))
  );
}

function numberField(source: Record<string, unknown>, key: string) {
  const value = source[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function liteCollectedDate(value: string | null | undefined) {
  return value ? `截至 ${value.slice(0, 10)}` : "采集时间未公开";
}

function recoveryReasonLabel(reason: string) {
  if (reason === "auth_expired" || reason === "auth_required") return "登录状态已失效";
  if (reason === "rate_limited") return "来源访问频率受限";
  return "调研暂时中断";
}

function coverageReasonLabel(reason: string) {
  if (reason === "minimum_relevant_samples_unmet") return "相关证据样本不足";
  if (reason === "minimum_independent_authors_unmet") return "独立作者数不足";
  if (reason === "direct_core_support_unmet") return "缺少核心对象直接证据";
  if (reason === "explicit_user_focus_unmet") return "用户重点尚未覆盖";
  if (reason === "replacement_capacity_exhausted") return "可替换样本已用尽";
  return reason.replaceAll("_", " ");
}

function marketingConclusionReasonLabel(reason: string) {
  if (reason === "conclusion_no_qualified_candidate") return "现有证据尚未达到主结论门槛。";
  if (reason === "conclusion_note_count_unmet") return "合格笔记数量不足。";
  if (reason === "conclusion_author_count_unmet") return "独立作者数量不足。";
  if (reason === "conclusion_support_tied") return "多个结论支持度相同，暂不指定唯一主结论。";
  if (reason === "first_intent_support_unmet") return "首要营销目标的直接支持不足。";
  if (["marketing_analysis_unavailable", "marketing_conclusion_unavailable", "marketing_conclusion_not_verified"].includes(reason)) return "结论分析当前不可用。";
  return "证据链未通过营销结论核验。";
}

function marketingConclusionStateLabel(state: string) {
  if (state === "selected") return "已选定";
  if (state === "qualified") return "已达到门槛";
  if (state === "insufficient_evidence") return "证据不足";
  if (state === "no_single_primary_conclusion") return "暂无唯一主结论";
  if (state === "analysis_unavailable") return "分析不可用";
  return "等待判定";
}

function marketingConclusionTrackLabel(track: string) {
  if (track === "need") return "需求";
  if (track === "value") return "价值";
  if (track === "message") return "表达";
  return "结论";
}

function ContentResearchReportMessage({
  report,
}: {
  report: ContentResearchLiteReportResponse;
}) {
  const [selectedCitation, setSelectedCitation] = useState<Record<string, unknown> | null>(null);
  const [candidateAudit, setCandidateAudit] = useState<ContentResearchDirectionEvidence | null>(null);
  const [candidateAuditError, setCandidateAuditError] = useState<string | null>(null);
  const publicationState = litePublicationState(report);
  const recovery = report.recovery_projection && typeof report.recovery_projection === "object"
    ? report.recovery_projection
    : null;
  const publicationIsNone = report.publication.state === null;

  useEffect(() => {
    setSelectedCitation(null);
  }, [report.workflow_run_id, report.citations]);

  if (!publicationState) {
    if (!publicationIsNone || !recovery) return null;
    const completedStages = arrayField(recovery, "completed_stages");
    return (
      <section
        className="w-full max-w-[92%] rounded-2xl border border-amber-200 bg-amber-50 px-5 py-4 text-sm text-ink shadow-sm"
        aria-label="Content Research recovery status"
      >
        <p className="text-[10px] font-semibold tracking-wider text-amber-700">研究运行</p>
        <h3 className="mt-1 text-lg font-semibold">调研可继续</h3>
        <p className="mt-2 text-sm text-amber-900">{recoveryReasonLabel(stringField(recovery, "reason_code"))}</p>
        <p className="mt-2 text-xs text-quiet">
          {completedStages.length ? `已保存阶段：${completedStages.join("、")}` : "尚无已完成阶段。"}
        </p>
      </section>
    );
  }

  const evidenceOnly = publicationState === "evidence_only_report";
  const partial = publicationState === "partial_verified_report";
  const citations = recordList(report.citations);
  const marketingTracks = ([
    ["need", "场景与需求"],
    ["value", "可被相信的产品卖点"],
    ["message", "内容表达"],
  ] as const).map(([id, label]) => ({
    id,
    label,
    value: report.sections.marketing_conclusions?.[id] as Record<string, unknown> | undefined,
  }));
  const hasMarketingTracks = marketingTracks.every((item) => item.value);
  const priorityAction = report.sections.priority_action as Record<string, unknown> | null | undefined;
  const allCards = recordList(report.sections.main_findings);
  const findings = evidenceOnly ? [] : allCards.filter((item) =>
    stringField(item, "card_kind") !== "observation"
    && !(hasMarketingTracks && stringField(item, "direction") === "product_marketing")
  );
  const observations = evidenceOnly ? [] : allCards.filter((item) => stringField(item, "card_kind") === "observation");
  const leads = evidenceOnly ? [] : recordList(report.sections.weak_signals);
  const limitations = recordList(report.sections.limitations_scope);
  const frozenScopeQueries = frozenReportScopeQueries(report);
  const directionStates = evidenceOnly
    ? []
    : selectRequestedDirectionStates(report);
  const statusStrip = report.status_strip;
  const citationsById = new Map(citations.map((citation) => [stringField(citation, "citation_group_id"), citation]));
  const statusText = evidenceOnly
    ? `${numberField(statusStrip, "saved_evidence_count")} 条已保存依据`
    : `${numberField(statusStrip, "completed_direction_count")} 个方向完成 · ${numberField(statusStrip, "admitted_finding_count")} 条已验证发现 · ${numberField(statusStrip, "observation_count")} 条样本观察 · ${numberField(statusStrip, "lead_count")} 条线索`;
  const auditDirection = stringField(recordList(report.run_direction_states)[0], "direction", "product_marketing");
  const auditDecisions = new Map<string, Record<string, unknown>>(
    (candidateAudit ? [...candidateAudit.selections, ...candidateAudit.exclusions] : [])
      .flatMap((item) => {
        const id = stringField(item, "canonical_source_id");
        return id ? [[id, item] as [string, Record<string, unknown>]] : [];
      }),
  );

  async function openCandidateAudit() {
    setCandidateAuditError(null);
    try {
      setCandidateAudit(await getContentResearchDirectionEvidence(report.workflow_run_id, auditDirection));
    } catch (error) {
      setCandidateAuditError(error instanceof Error ? error.message : "候选笔记暂不可读取");
    }
  }

  function downloadCandidateAudit() {
    if (!candidateAudit) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(candidateAudit, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${report.workflow_run_id}-${candidateAudit.direction_id}-candidates.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  const citationButton = (citation: Record<string, unknown>, index: number) => {
    const displayIndex = String(citation.display_index ?? index + 1);
    const navigationState = stringField(citation, "navigation_state", "missing_source_url");
    const sourceUrl = stringField(citation, "source_url");
    return (
      <div
        key={stringField(citation, "citation_group_id", String(index))}
        className="flex flex-wrap gap-2"
      >
        {sourceUrl && navigationState === "available" && (
          <a
            className="rounded-lg border border-line bg-white px-2.5 py-1 text-xs hover:bg-slate-50"
            href={sourceUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            查看原笔记
          </a>
        )}
        <button
          type="button"
          className="rounded-lg border border-line bg-white px-2.5 py-1 text-xs hover:bg-slate-50"
          onClick={() => setSelectedCitation(citation)}
        >
          证据详情
        </button>
      </div>
    );
  };

  const card = (item: Record<string, unknown>, index: number, observation: boolean) => (
    <details
      key={`${observation ? "observation" : "finding"}-${index}`}
      aria-label={observation ? "样本观察卡" : "核心发现卡"}
      className="rounded-xl border border-line bg-white px-3 py-3"
    >
      <summary className="cursor-pointer font-medium">
        {stringField(item, "statement", observation ? "已验证样本观察" : "已验证发现")}
      </summary>
      <dl className="mt-3 grid gap-2 text-xs text-quiet">
        {stringField(item, "direction") && <div><dt className="font-medium text-ink">方向</dt><dd>{stringField(item, "direction")}</dd></div>}
        {stringField(item, "sample_summary") && <div><dt className="font-medium text-ink">样本范围</dt><dd>{stringField(item, "sample_summary")}</dd></div>}
        {stringField(item, "scope") && <div><dt className="font-medium text-ink">结论范围</dt><dd>{stringField(item, "scope")}</dd></div>}
      </dl>
      <div className="mt-3 flex flex-wrap gap-2">
        {arrayField(item, "citation_group_ids").map((id, citationIndex) => {
          const citation = citationsById.get(id);
          return citation ? citationButton(citation, citationIndex) : null;
        })}
      </div>
    </details>
  );

  const refs = selectedCitation ? recordList(selectedCitation.evidence_refs) : [];

  return (
    <div className="flex justify-start" data-report-publication-id={stringField(report.publication, "report_publication_id")}>
      <article
        className="w-full max-w-[92%] overflow-hidden rounded-2xl border border-line bg-white text-sm text-ink shadow-sm"
        aria-label="Content Research published report"
      >
        <header className="border-b border-line bg-[#fbfcfb] px-5 py-4" aria-label="Content Research report header">
          <p className="text-[10px] font-semibold tracking-wider text-[#4f6f5f]">研究结论</p>
          <div className="mt-1 flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="text-lg font-semibold leading-7">
                {report.subject || "本轮调研"} · {evidenceOnly ? "已保存依据" : "调研结果"}
              </h3>
              <p className="mt-1 text-xs text-quiet">{liteCollectedDate(report.collected_at)}</p>
            </div>
            <span className="rounded-full bg-[#fff4df] px-2.5 py-1 text-xs font-medium text-[#a16207]">
              {reportStateLabel(publicationState)}
            </span>
          </div>
        </header>

        <div className="space-y-5 px-5 py-5">
          <p className="rounded-xl bg-slate-50 px-3 py-2 text-xs text-quiet">{statusText}</p>

          {(partial || evidenceOnly) && stringField(report.publication, "publication_reason") && (
            <p className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
              {stringField(report.publication, "publication_reason")}
            </p>
          )}
          {evidenceOnly && (
            <section className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-3" aria-label="候选笔记与筛选">
              <p className="text-xs text-amber-900">报告未准入结论时，可查看候选笔记和每篇的筛选原因。</p>
              <button type="button" onClick={() => void openCandidateAudit()} className="mt-2 rounded-lg border border-amber-300 bg-white px-3 py-2 text-xs font-medium text-ink">查看候选与筛选</button>
              {candidateAuditError && <p className="mt-2 text-xs text-red-700">{candidateAuditError}</p>}
            </section>
          )}

          {hasMarketingTracks && marketingTracks.map(({ id, label, value }) => {
            const track = value as Record<string, unknown>;
            const selected = stringField(track, "state") === "selected";
            const directional = stringField(track, "state") === "directional";
            return (
              <section key={id} aria-label={label} className="border-t border-line pt-4">
                <h4 className="font-semibold">{label}</h4>
                {selected ? (
                  <div className="mt-3 rounded-xl border border-line bg-white px-3 py-3">
                    <p className="font-medium leading-6">{stringField(track, "statement")}</p>
                    <p className="mt-2 text-xs text-quiet">
                      {numberField(track, "supporting_note_count")} 篇笔记 · {numberField(track, "independent_author_count")} 位独立作者
                    </p>
                    {numberField(track, "additional_qualified_count") > 0 && (
                      <p className="mt-1 text-xs text-quiet">另有 {numberField(track, "additional_qualified_count")} 条合格结论</p>
                    )}
                    <div className="mt-3 flex flex-wrap gap-2">
                      {arrayField(track, "citation_group_ids").map((citationId, citationIndex) => {
                        const citation = citationsById.get(citationId);
                        return citation ? citationButton(citation, citationIndex) : null;
                      })}
                    </div>
                  </div>
                ) : directional ? (
                  <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-3 text-xs text-quiet">
                    <p className="font-medium text-ink">待验证方向</p>
                    <p className="mt-2 font-medium leading-6 text-ink">{stringField(track, "statement")}</p>
                    <p className="mt-2">当前 {numberField(track, "supporting_note_count")} 篇 / {numberField(track, "independent_author_count")} 位作者</p>
                    <p className="mt-1">还缺 {numberField(track, "note_gap")} 篇独立笔记、{numberField(track, "author_gap")} 位独立作者</p>
                    <p className="mt-2 font-medium text-amber-900">该方向不可作为功效或投放定论</p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {arrayField(track, "citation_group_ids").map((citationId, citationIndex) => {
                        const citation = citationsById.get(citationId);
                        return citation ? citationButton(citation, citationIndex) : null;
                      })}
                    </div>
                  </div>
                ) : (
                  <div className="mt-3 rounded-xl bg-slate-50 px-3 py-3 text-xs text-quiet">
                    <p className="font-medium text-ink">暂无可验证结论</p>
                    {arrayField(track, "reason_codes").map((reason) => (
                      <p key={reason} className="mt-1">{marketingConclusionReasonLabel(reason)}</p>
                    ))}
                    <p className="mt-2">验证方向：{stringField(track, "verification_direction")}</p>
                  </div>
                )}
              </section>
            );
          })}

          {hasMarketingTracks && priorityAction && (
            <section aria-label="优先行动建议" className="border-t border-line pt-4">
              <h4 className="font-semibold">优先行动建议</h4>
              <div className="mt-3 rounded-xl border border-[#c7d5cc] bg-[#f4f8f5] px-3 py-3">
                <p className="text-xs font-semibold text-[#4f6f5f]">{stringField(priorityAction, "label", "建议")}</p>
                <p className="mt-1 leading-6">{stringField(priorityAction, "statement")}</p>
              </div>
            </section>
          )}

          {findings.length > 0 && (
            <section aria-label="核心发现" className="border-t border-line pt-4">
              <h4 className="font-semibold">核心发现</h4>
              <div className="mt-3 space-y-2">{findings.map((item, index) => card(item, index, false))}</div>
            </section>
          )}

          {observations.length > 0 && (
            <section aria-label="样本观察" className="border-t border-line pt-4">
              <h4 className="font-semibold">样本观察</h4>
              <div className="mt-3 space-y-2">{observations.map((item, index) => card(item, index, true))}</div>
            </section>
          )}

          {leads.length > 0 && (
            <section aria-label="线索" className="border-t border-line pt-4">
              <h4 className="font-semibold">线索</h4>
              {leads.map((item, index) => (
                <details
                  key={`lead-${index}`}
                  className="mt-2 border-l-4 border-slate-300 bg-slate-50 px-3 py-2 text-xs leading-5 text-quiet"
                >
                  <summary className="cursor-pointer">{stringField(item, "statement", "证据尚不足以构成发现。")}</summary>
                  {stringField(item, "direction") && <p className="mt-2">方向：{stringField(item, "direction")}</p>}
                  {stringField(item, "sample_summary") && <p className="mt-1">样本范围：{stringField(item, "sample_summary")}</p>}
                  <p className="mt-1">{stringField(item, "qualification_reason", "证据范围或样本门槛尚不足。")}</p>
                  <p className="mt-1 font-medium text-ink">仅供参考，不构成结论。</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {arrayField(item, "citation_group_ids").map((id, citationIndex) => {
                      const citation = citationsById.get(id);
                      return citation ? citationButton(citation, citationIndex) : null;
                    })}
                  </div>
                </details>
              ))}
            </section>
          )}

          {directionStates.length > 0 && (
            <section aria-label="方向状态" className="border-t border-line pt-4">
              <h4 className="font-semibold">方向状态</h4>
              <div className="mt-2 space-y-2">
                {directionStates.map((item, index) => (
                  <div key={`${stringField(item, "direction")}-${index}`} className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-quiet">
                    <p className="font-medium text-ink">{liteDirectionLabel(stringField(item, "direction", "未知方向"))} · {stringField(item, "state", "unavailable")}</p>
                    {stringField(item, "reason_code") && <p className="mt-1">{stringField(item, "reason_code")}</p>}
                    {stringField(item, "recovery_action") && <p className="mt-1">{stringField(item, "recovery_action")}</p>}
                  </div>
                ))}
              </div>
            </section>
          )}

          {!evidenceOnly && (
            <section aria-label="范围与限制" className="border-t border-line pt-4">
              <h4 className="font-semibold">范围与限制</h4>
              <div className="mt-2 space-y-2 text-xs text-quiet">
                {frozenScopeQueries.length > 0 && (
                  <div aria-label="报告冻结检索范围">
                    <p className="font-medium text-ink">报告使用的冻结检索组</p>
                    {frozenScopeQueries.map((query, index) => <p key={`${query}-${index}`} className="mt-1">{index + 1}. {query}</p>)}
                  </div>
                )}
                {limitations.length
                  ? limitations.map((item, index) => <p key={`limitation-${index}`}>{firstString(item, ["message", "reason", "summary"])}</p>)
                  : <p>请在本次冻结样本与引用范围内理解结果。</p>}
              </div>
            </section>
          )}

          <section aria-label="证据与来源" className="border-t border-line pt-4">
            <h4 className="font-semibold">{evidenceOnly ? "已保存依据" : "证据与来源"}</h4>
            <div className="mt-2 flex flex-wrap gap-2">
              {citations.map(citationButton)}
              {citations.length === 0 && <span className="text-xs text-quiet">没有可展示的冻结引用。</span>}
            </div>
          </section>
        </div>

        {selectedCitation && (
          <aside className="border-t border-line bg-slate-50 px-5 py-4" aria-label="Content Research citation evidence">
            <div className="flex justify-between gap-3">
              <p className="font-semibold">引用 [{String(selectedCitation.display_index ?? "—")}]</p>
              <button type="button" onClick={() => setSelectedCitation(null)} aria-label="关闭引用依据">关闭</button>
            </div>
            {refs.length > 0 ? (
              <div className="mt-3 space-y-2 border-t border-line pt-3">
                <p className="text-xs font-semibold text-quiet">同组证据</p>
                {refs.map((ref, index) => {
                  const navigationState = stringField(ref, "navigation_state", "missing_source_url");
                  const sourceUrl = stringField(ref, "source_url");
                  return (
                    <div key={`${stringField(ref, "source_text_hash")}-${index}`} className="rounded-lg bg-white px-3 py-2 text-xs text-quiet">
                      <p>{stringField(ref, "quote", "已冻结证据")}</p>
                      <div className="mt-1 flex flex-wrap gap-x-2">
                        <span>{stringField(ref, "field_path", "字段未公开")}</span>
                        <span>{stringField(ref, "source_collected_at", "采集时间未公开")}</span>
                      </div>
                      {sourceUrl && navigationState === "available" && (
                        <a className="mt-1 inline-flex underline" href={sourceUrl} target="_blank" rel="noopener noreferrer">打开原笔记</a>
                      )}
                      {navigationState === "missing_source_url" && (
                        <p className="mt-1">未保存来源链接；可查看原文片段与采集时间</p>
                      )}
                      {navigationState === "navigation_unavailable" && (
                        <p className="mt-1">来源链接当前不可打开；可查看原文片段与采集时间</p>
                      )}
                      {stringField(ref, "navigation_reason") && <p className="mt-1">{stringField(ref, "navigation_reason")}</p>}
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="mt-3 text-xs text-quiet">该冻结引用未提供可展示的证据明细。</p>
            )}
          </aside>
        )}
        {candidateAudit && (
          <aside className="border-t border-line bg-slate-50 px-5 py-4" aria-label="候选笔记与筛选">
            <div className="flex items-center justify-between gap-3">
              <p className="font-semibold">候选笔记与筛选</p>
              <div className="flex gap-2">
                <button type="button" onClick={downloadCandidateAudit} className="text-xs underline">导出 JSON</button>
                <button type="button" onClick={() => setCandidateAudit(null)} aria-label="关闭候选笔记与筛选">关闭</button>
              </div>
            </div>
            <div className="mt-3 space-y-2 border-t border-line pt-3">
              <p className="text-xs text-quiet">检索来源（命中查询组）表示该笔记由本轮哪组检索发现，不要求正文逐字包含完整检索词。</p>
              {candidateAudit.candidates.map((candidate, index) => {
                const candidateId = stringField(candidate, "canonical_source_id") || stringField(candidate, "canonical_id");
                const decision = auditDecisions.get(candidateId);
                const sourceUrl = stringField(candidate, "source_url");
                return <div key={candidateId || index} className="rounded-lg bg-white px-3 py-2 text-xs text-quiet">
                  <p className="font-medium text-ink">{stringField(candidate, "title", "未提供标题")}</p>
                  <p className="mt-1">作者：{stringField(candidate, "author", stringField(candidate, "author_id", "未提供"))} · 详情：{candidate.detail_attempted ? "已抓取" : "未抓取"}</p>
                  {stringField(candidate, "retrieval_query") && <p className="mt-1">检索来源（命中查询组）：{stringField(candidate, "retrieval_query")}</p>}
                  {decision && <p className="mt-1">{decision.selected ? "已入选" : "未入选"}：{arrayField(decision, "reasons").join(" · ") || "未提供原因"}</p>}
                  {sourceUrl && <a className="mt-1 inline-flex underline" href={sourceUrl} target="_blank" rel="noopener noreferrer">打开原笔记</a>}
                </div>;
              })}
              {candidateAudit.candidates.length === 0 && <p className="text-xs text-quiet">本方向未保存可展示的候选笔记。</p>}
            </div>
          </aside>
        )}
      </article>
    </div>
  );
}

interface ContentResearchContextStage {
  id: string;
  title: string;
  detail: string;
  tone: "complete" | "warning" | "pending";
}

interface TraceTimelineStep {
  id: string;
  number: number;
  stage: string;
  title: string;
  durationText: string;
  status: string;
  output: string;
}

type TraceProviderOperation = ContentResearchTrace["provider_operations"][number];

interface ContentResearchRecoveryState {
  providerFailure: TraceProviderOperation | null;
  childFailure: Record<string, unknown> | null;
  authRequired: boolean;
  recoverable: boolean;
}

function traceStepTitle(stepName: string) {
  if (stepName === "presearch") return "识别调研主体与候选方向";
  if (stepName === "brief_confirm") return "确认调研 Brief";
  if (stepName === "plan_build") return "拆解调研计划";
  if (stepName === "formal_research") return "并行执行专家调研";
  return stepName.replaceAll("_", " ");
}

function traceStepGroup(stepName: string) {
  if (["presearch", "brief_confirm", "plan_build"].includes(stepName)) return "研究范围与计划";
  if (stepName === "formal_research") return "来源收集与字段提取";
  return "安全执行阶段";
}

function contentResearchTraceTimeline(trace: ContentResearchTrace | null): TraceTimelineStep[] {
  const workflowEvents = recordList(trace?.workflow_events);
  return recordList(trace?.runtime_steps).map((step, index) => {
    const stepName = stringField(step, "step_name", "执行阶段");
    const status = stringField(step, "status", "pending");
    return {
      id: stringField(step, "step_id", `${stepName}-${index}`),
      number: index + 1,
      stage: traceStepGroup(stepName),
      title: traceStepTitle(stepName),
      durationText: traceExecutionDurationText(step, workflowEvents),
      status: workflowStatusLabel(status),
      output: `状态：${workflowStatusLabel(status)}\n尝试：${numberField(step, "attempt_count")} / ${numberField(step, "max_attempts")}`,
    };
  });
}

function contentResearchRecoveryState(run: ContentResearchRunState): ContentResearchRecoveryState {
  const published = run.report ? litePublicationState(run.report) !== null : false;
  const providerFailures = [...(run.trace?.provider_operations ?? [])]
    .reverse()
    .filter((operation) => !["completed", "running", "pending"].includes(operation.status));
  const providerFailure = (
    !published && run.trace?.run_status !== "succeeded"
      ? providerFailures[0] ?? null
      : null
  );
  const childFailures = [...recordList(run.trace?.runtime_child_tasks)]
    .reverse()
    .filter((task) => stringField(task, "status") === "failed");
  const childFailure = childFailures[0] ?? null;
  const reportRecoveryReason = run.report?.recovery_projection
    ? stringField(run.report.recovery_projection, "reason_code")
    : "";
  const authCodes = new Set(["auth_expired", "auth_required"]);
  const childFailureCode = stringField(childFailure, "error_code");
  const authChildFailure = childFailures.find((task) => authCodes.has(stringField(task, "error_code"))) ?? null;
  const authProviderFailure = providerFailures.find((operation) => authCodes.has(operation.failure_code ?? "")) ?? null;
  const authRequired = !published && Boolean(
    authProviderFailure || authChildFailure || authCodes.has(reportRecoveryReason)
  );
  const selectedProviderFailure = authProviderFailure ?? providerFailure;
  const recoverableCodes = new Set(["auth_expired", "auth_required", "timeout", "transient_error", "rate_limited", "unavailable"]);
  const providerRecoverable = Boolean(selectedProviderFailure?.retryable && recoverableCodes.has(selectedProviderFailure.failure_code ?? ""));
  const childRecoverable = childFailures.some((task) => recoverableCodes.has(stringField(task, "error_code")));
  const reportRecoverable = recoverableCodes.has(reportRecoveryReason);
  return {
    providerFailure: selectedProviderFailure,
    childFailure,
    authRequired,
    recoverable: !published && (providerRecoverable || childRecoverable || reportRecoverable),
  };
}

function contentResearchContextStatus(run: ContentResearchRunState): string {
  const lifecycleState = run.summary.run.state;
  if (lifecycleState === "presearch_running") return "轻量预检索进行中";
  if (lifecycleState === "brief_confirmation_required") return "等待确认调研 Brief";
  if (lifecycleState === "scope_confirmation_required") return "等待确认检索范围";
  if (lifecycleState === "retrieval_queued") return "内容检索等待开始";
  if (lifecycleState === "retrieval_running") return "内容检索进行中";
  if (lifecycleState === "coverage_evaluating") return "正在评估证据覆盖";
  if (lifecycleState === "coverage_decision_required") return "等待覆盖范围决策";
  if (lifecycleState === "report_composing") return "正在生成调研报告";
  if (lifecycleState === "report_ready") return "调研报告已完成";
  if (lifecycleState === "recovery_required") return "本轮调研需要恢复";
  if (lifecycleState === "cancelled_or_failed") return "本轮调研已结束";
  if (run.report) {
    const state = litePublicationState(run.report);
    if (state) return reportStateLabel(state);
  }
  const recovery = contentResearchRecoveryState(run);
  if (recovery.authRequired) return "需要登录小红书网页端";
  if (recovery.recoverable) return "部分专家任务可继续";
  if (run.formalResearchStatus === "invalid") return "所属对话已不存在";
  if (run.formalResearchStatus === "failed") return "专家调研启动失败";
  if (run.trace?.run_status === "succeeded") return "专家调研已完成，等待正式报告";
  if (run.trace?.run_status === "paused") return "专家调研已暂停";
  if (run.trace?.run_status === "waiting_user") return "专家调研等待恢复";
  if (run.formalResearchStatus === "collecting" || run.trace?.run_status === "running") return "专家调研进行中";
  if (run.formalResearchStatus === "completed") return "专家调研已完成，等待正式报告";
  return "等待启动";
}

function contentResearchChildTaskTitle(task: Record<string, unknown>) {
  const taskType = stringField(task, "task_type", "专家任务");
  if (taskType.includes("product_marketing")) return "产品营销专家";
  if (taskType.includes("competitor_discovery")) return "竞品发现专家";
  if (taskType.includes("content_performance")) return "内容表现专家";
  return taskType.replaceAll("_", " ");
}

function contentResearchChildTaskStatus(task: Record<string, unknown>) {
  if (["auth_expired", "auth_required"].includes(stringField(task, "error_code"))) return "需要登录";
  return workflowStatusLabel(stringField(task, "status", "pending"));
}

function ContentResearchTraceInspector({
  run,
  expanded,
  onExpandedChange,
  onRefresh,
}: {
  run: ContentResearchRunState;
  expanded: boolean;
  onExpandedChange: (expanded: boolean) => void;
  onRefresh: () => Promise<void>;
}) {
  const [refreshing, setRefreshing] = useState(false);
  const timeline = contentResearchTraceTimeline(run.trace);
  const displayTimeline = [...timeline].reverse();
  const recovery = contentResearchRecoveryState(run);
  const childTasks = recordList(run.trace?.runtime_child_tasks);
  const logicalCheckpoints = recordList(run.trace?.logical_checkpoints);
  const terminalPublished = run.report ? litePublicationState(run.report) !== null : false;
  const [expandedStepId, setExpandedStepId] = useState<string | null>(null);
  const refreshTraceRef = useRef(onRefresh);
  const selectedStep = timeline.find((step) => step.id === expandedStepId) ?? timeline[timeline.length - 1] ?? null;

  useEffect(() => {
    refreshTraceRef.current = onRefresh;
  }, [onRefresh]);

  useEffect(() => {
    if (!expanded) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onExpandedChange(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [expanded, onExpandedChange]);

  useEffect(() => {
    if (!expanded || terminalPublished) return;
    const refresh = () => void refreshTraceRef.current().catch(() => undefined);
    refresh();
    const timer = window.setInterval(refresh, 3000);
    return () => window.clearInterval(timer);
  }, [expanded, run.workflowRunId, terminalPublished]);

  if (!expanded) return null;

  const refreshTrace = async () => {
    if (refreshing) return;
    setRefreshing(true);
    try {
      await onRefresh();
    } finally {
      setRefreshing(false);
    }
  };
  return (
    <section
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/30 p-6"
      aria-label="Content Research Trace"
      onClick={(event) => {
        if (event.target === event.currentTarget) onExpandedChange(false);
      }}
    >
      <div
        className="flex max-h-[min(760px,calc(100vh-48px))] w-full max-w-xl flex-col overflow-hidden rounded-2xl border border-line bg-white text-ink shadow-[0_24px_72px_rgba(15,23,42,0.20)]"
        role="dialog"
        aria-modal="true"
        aria-label="Agent 决策日志 · Trace"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-3 bg-[#486b5b] px-4 py-3 text-white">
          <div className="min-w-0 flex items-center gap-2">
            <span className="text-lg leading-none">⌁</span>
            <div className="min-w-0">
              <p className="truncate text-base font-semibold">查看调研过程</p>
              <p className="mt-0.5 truncate text-[11px] text-white/70">「{contentResearchSubject(run)}」</p>
              <p className="mt-0.5 truncate text-[11px] text-white/70">workflow_run · {run.workflowRunId}</p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              onClick={() => void refreshTrace()}
              disabled={refreshing}
              className="rounded-full bg-white/12 px-2.5 py-1 text-xs font-medium text-white hover:bg-white/20 disabled:opacity-40"
            >
              {refreshing ? "刷新中" : "刷新"}
            </button>
            <button
              type="button"
              onClick={() => onExpandedChange(false)}
              className="flex h-7 w-7 items-center justify-center rounded-lg bg-white/12 text-lg leading-none text-white hover:bg-white/20"
              aria-label="关闭 Trace 对话框"
            >
              ×
            </button>
          </div>
        </div>

        <div className="border-b border-line px-4 py-3 text-xs text-quiet">
          <span className="truncate">当前进度：{contentResearchContextStatus(run)}</span>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
          {recovery.providerFailure && (
            <article className="mb-3 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-ink" aria-label="采集异常诊断">
              <p className="font-semibold">采集异常诊断</p>
              <p className="mt-1 text-xs text-quiet">
                {recovery.providerFailure.provider_operation ?? recovery.providerFailure.operation ?? "provider"} · {recovery.providerFailure.failure_code ?? recovery.providerFailure.failure_reason ?? recovery.providerFailure.status}
              </p>
              <p className="mt-2 text-xs leading-5 text-ink">
                {recovery.authRequired ? "更新小红书登录态后继续。" : "该外部调用未产生可安全继续的结果，请先确认外部状态。"}
              </p>
            </article>
          )}
          {!recovery.providerFailure && recovery.authRequired && (
            <article className="mb-3 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-ink" aria-label="认证恢复">
              <p className="font-semibold">需要登录小红书网页端</p>
              <p className="mt-2 text-xs leading-5 text-ink">完成扫码认证后可继续同一轮调研。</p>
            </article>
          )}

          {logicalCheckpoints.length > 0 && (
            <section className="mb-4" aria-label="结构化调研决策">
              <p className="mb-2 text-xs font-semibold text-quiet">结构与覆盖决策</p>
              <div className="space-y-2">
                {logicalCheckpoints.map((checkpoint, index) => {
                  const stage = stringField(checkpoint, "stage");
                  const counts = checkpoint.counts && typeof checkpoint.counts === "object" && !Array.isArray(checkpoint.counts)
                    ? checkpoint.counts as Record<string, unknown>
                    : {};
                  const title = stage === "subject_structure"
                    ? "主题结构已确认"
                    : stage === "query_plan"
                      ? "检索计划已冻结"
                      : stage === "coverage_decision"
                        ? "证据覆盖判定"
                        : stage === "fallback_decision"
                          ? "补位检索判定"
                          : stage === "marketing_conclusion"
                            ? "营销结论判定"
                            : "历史相关性修订";
                  const marketingTracks = stage === "marketing_conclusion"
                    ? Object.entries(checkpoint.tracks && typeof checkpoint.tracks === "object" && !Array.isArray(checkpoint.tracks)
                      ? checkpoint.tracks as Record<string, unknown>
                      : {}).flatMap(([track, value]) => {
                        if (!value || typeof value !== "object" || Array.isArray(value)) return [];
                        const outcome = value as Record<string, unknown>;
                        const support = typeof outcome.supporting_note_count === "number" && typeof outcome.independent_author_count === "number"
                          ? ` · ${outcome.supporting_note_count} 篇 / ${outcome.independent_author_count} 位作者`
                          : "";
                        return [`${marketingConclusionTrackLabel(track)}：${marketingConclusionStateLabel(stringField(outcome, "state"))}${support}`];
                      })
                    : [];
                  const detail = stage === "query_plan"
                    ? `主检索 ${numberField(checkpoint, "primary_group_count")} 组 · 补位 ${numberField(checkpoint, "fallback_group_count")} 组 · 合并 ${numberField(checkpoint, "merged_group_count")} 组`
                    : stage === "coverage_decision"
                      ? `发现 ${numberField(counts, "discovered")} · 去重 ${numberField(counts, "deduplicated")} · 相关 ${numberField(counts, "relevant")} · 准入 ${numberField(counts, "admitted")}`
                      : stage === "fallback_decision"
                        ? `Q3：${stringField(checkpoint, "state", "未启用")}`
                        : stage === "marketing_conclusion"
                          ? marketingTracks.length > 0
                            ? marketingTracks.join("；")
                            : `状态：${marketingConclusionStateLabel(stringField(checkpoint, "status"))}`
                          : `状态：${stringField(checkpoint, "state", stringField(checkpoint, "status"))}`;
                  const trackReasons = stage === "marketing_conclusion"
                    ? Object.values(checkpoint.tracks && typeof checkpoint.tracks === "object" && !Array.isArray(checkpoint.tracks)
                      ? checkpoint.tracks as Record<string, unknown>
                      : {}).flatMap((value) => value && typeof value === "object" && !Array.isArray(value)
                      ? arrayField(value as Record<string, unknown>, "reason_codes")
                      : [])
                    : [];
                  const reasonCodes = [...arrayField(checkpoint, "reason_codes"), ...trackReasons];
                  const reasons = reasonCodes.map(stage === "marketing_conclusion" ? marketingConclusionReasonLabel : coverageReasonLabel);
                  return (
                    <article key={`${stage}-${index}`} className="rounded-xl border border-line bg-white px-3 py-2.5 text-xs">
                      <div className="flex items-center justify-between gap-3"><p className="font-medium text-ink">{title}</p><span className="text-quiet">{stringField(checkpoint, "status")}</span></div>
                      <p className="mt-1 leading-5 text-quiet">{detail}</p>
                      {reasons.length > 0 && <p className="mt-1 leading-5 text-amber-700">{reasons.join("·")}</p>}
                    </article>
                  );
                })}
              </div>
            </section>
          )}

          <section aria-label="Content Research Trace timeline">
            <p className="mb-2 text-xs font-semibold text-quiet">执行时间线</p>
            {displayTimeline.length ? (
              <div className="space-y-3">
                {displayTimeline.map((step) => {
                  const isExpanded = selectedStep?.id === step.id;
                  return (
                    <article
                      key={step.id}
                      className={[
                        "rounded-2xl border bg-white px-3 py-3 text-sm shadow-sm transition",
                        isExpanded ? "border-[#9fb8aa] ring-2 ring-[#e4eee8]" : "border-line hover:border-[#c7d5cc]",
                      ].join(" ")}
                    >
                      <button type="button" onClick={() => setExpandedStepId(isExpanded ? null : step.id)} className="w-full text-left">
                        <div className="flex items-start justify-between gap-3">
                          <div className="flex min-w-0 gap-2.5">
                            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[#e8f1ea] text-xs font-semibold text-[#4f6f5f]">{step.number}</span>
                            <div className="min-w-0"><span className="block text-xs text-quiet">{step.stage}</span><span className="mt-0.5 block font-semibold leading-5 text-ink">{step.title}</span><span className="mt-2 block text-xs text-quiet">内容调研 · {step.status}</span></div>
                          </div>
                          <div className="flex shrink-0 flex-col items-end gap-1 text-sm text-quiet"><span>{step.durationText}</span><span className="text-lg leading-none">{isExpanded ? "⌃" : "⌄"}</span></div>
                        </div>
                      </button>
                      {isExpanded && <div className="mt-4 border-t border-line pt-4"><p className="text-xs font-semibold tracking-wide text-quiet">安全执行状态</p><pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-xl bg-slate-50 px-3 py-3 text-xs leading-5 text-ink">{step.output}</pre></div>}
                    </article>
                  );
                })}
              </div>
            ) : (
              <div className="rounded-2xl border border-line bg-slate-50 px-4 py-5 text-sm text-quiet">暂无 trace 记录。确认 brief 后会自动写入执行轨迹。</div>
            )}
          </section>

          {childTasks.length > 0 && (
            <section className="mt-4" aria-label="内容调研子任务状态">
              <p className="mb-2 text-xs font-semibold text-quiet">专家子任务</p>
              <div className="space-y-2">
                {childTasks.map((task, index) => (
                  <article key={stringField(task, "child_task_id", String(index))} className="rounded-xl border border-line bg-white px-3 py-3 text-xs" aria-label="内容调研子任务">
                    <div className="flex items-center justify-between gap-3"><p className="font-medium text-ink">{contentResearchChildTaskTitle(task)}</p><span className="text-quiet">{contentResearchChildTaskStatus(task)}</span></div>
                    <p className="mt-1 text-quiet">尝试 {numberField(task, "attempt_count")} / {numberField(task, "max_attempts")}</p>
                  </article>
                ))}
              </div>
            </section>
          )}

        </div>
      </div>
    </section>
  );
}

function contentResearchContextStages(report: ContentResearchLiteReportResponse): ContentResearchContextStage[] {
  const state = litePublicationState(report);
  if (!state) return [];
  return [
    {
      id: "scope",
      title: "范围与检索冻结",
      detail: `${report.citations.length} 条冻结引用可追溯。`,
      tone: "complete",
    },
    {
      id: "governance",
      title: "证据准入与归纳",
      detail: `${numberField(report.status_strip, "admitted_finding_count")} 条已准入发现已进入报告。`,
      tone: "complete",
    },
    {
      id: "publication",
      title: "报告发布与范围",
      detail: `${reportStateLabel(state)}；请结合报告中的范围与限制使用。`,
      tone: state !== "complete_verified_report" ? "warning" : "complete",
    },
  ];
}

function ContentResearchContextSidebar({
  run,
  onExpandedChange,
  onModifyDirections,
  recoveryPending = false,
  recoveryRequiredSince = null,
  onContinuePresearch = async () => {},
}: {
  run: ContentResearchRunState;
  onExpandedChange: (expanded: boolean) => void;
  onModifyDirections: () => void;
  recoveryPending?: boolean;
  recoveryRequiredSince?: string | null;
  onContinuePresearch?: () => void | Promise<void>;
}) {
  const report = run.report;
  const published = report ? litePublicationState(report) !== null : false;
  const evidenceOnly = report ? litePublicationState(report) === "evidence_only_report" : false;
  const stages = report && !evidenceOnly ? contentResearchContextStages(report) : [];
  const requestedDirectionCount = report ? selectRequestedDirectionStates(report).length : 0;

  return (
    <aside className="hidden w-[300px] shrink-0 overflow-y-auto border-l border-line bg-slate-50 p-4 lg:block" aria-label="内容调研上下文">
      <h2 className="mb-3 text-sm font-semibold text-ink">研究运行 / Trace</h2>
      <section className="mb-4 rounded-xl border border-line bg-white p-4" aria-label="内容调研运行摘要">
        <p className="text-sm font-semibold text-ink">{contentResearchContextStatus(run)}</p>
        <p className="mt-1 text-xs leading-5 text-quiet">主体：{contentResearchSubject(run)}</p>
        {stages.length > 0 && <div className="mt-3 border-t border-line pt-2">
          {stages.map((stage) => <div key={stage.id} className="grid grid-cols-[12px_1fr] gap-2 py-2 text-xs">
            <span className={[
              "mt-1.5 h-2 w-2 rounded-full",
              stage.tone === "complete" ? "bg-[#4f6f5f]" : stage.tone === "warning" ? "bg-amber-500" : "bg-slate-300",
            ].join(" ")} aria-hidden="true" />
            <div><p className="font-medium text-ink">{stage.title}</p><p className="mt-0.5 leading-5 text-quiet">{stage.detail}</p></div>
          </div>)}
        </div>}
        {!published && <p className="mt-3 border-t border-line pt-3 text-xs leading-5 text-quiet">研究摘要将在正式报告发布后显示。</p>}
        {run.formalResearchStatus === "invalid" ? <p className="mt-3 text-xs leading-5 text-quiet">该历史任务不能继续。请新建或选择有效对话后重新发起调研。</p> : null}
        {run.formalResearchStatus === "failed" && <button type="button" onClick={onModifyDirections} className="mt-3 rounded-lg border border-line px-3 py-1.5 text-xs">返回 checklist</button>}
        {run.reportStatus === "failed" && run.reportError && (
          <p className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs leading-5 text-red-700">
            正式报告暂不可读取：{run.reportError}
          </p>
        )}
        <button type="button" onClick={() => onExpandedChange(true)} className="mt-3 w-full border-t border-line pt-3 text-left text-xs font-semibold text-blue-600 hover:text-blue-700" aria-label="查看完整 workflow trace">查看完整 workflow trace →</button>
      </section>

      <section className="mb-4 rounded-xl border border-line bg-white p-4 text-ink" aria-label="Content Research Trace inspector">
        <div className="flex items-start justify-between gap-3">
          <button type="button" onClick={() => onExpandedChange(true)} className="min-w-0 flex-1 text-left" aria-label="展开 Content Research Trace">
            <p className="text-sm font-semibold">证据与 Trace</p>
            <p className="mt-1 text-xs text-quiet">{contentResearchTraceTimeline(run.trace).length} 条执行记录 · {contentResearchContextStatus(run)}</p>
          </button>
          <button type="button" onClick={() => onExpandedChange(true)} className="rounded-lg border border-line px-2 py-1 text-xs hover:bg-slate-50" aria-label="查看 Trace">查看 Trace</button>
        </div>
      </section>

      <h2 className="mb-3 text-sm font-semibold text-ink">本次研究摘要</h2>
      <section className="mb-4 rounded-xl border border-line bg-white p-4" aria-label="内容调研研究摘要">
        {report && published ? <ul className="space-y-2 text-xs leading-5 text-quiet">
          <li>{evidenceOnly ? numberField(report.status_strip, "saved_evidence_count") : report.citations.length} 条冻结引用</li>
          {!evidenceOnly && <li>{numberField(report.status_strip, "admitted_finding_count")} 条已准入发现</li>}
          {!evidenceOnly && <li>{requestedDirectionCount} 个请求方向状态</li>}
          {!evidenceOnly && <li>{numberField(report.status_strip, "lead_count")} 条初步信号</li>}
        </ul> : <p className="text-xs leading-5 text-quiet">暂无已发布报告；此处不会显示未冻结的来源、结论或指标。</p>}
      </section>
      <ModelServiceCard recoveryPending={recoveryPending} recoveryRequiredSince={recoveryRequiredSince} onContinue={onContinuePresearch} onConfigurationChanged={() => undefined} />
      <XiaohongshuLoginCard />
    </aside>
  );
}

 function CreatorPage() {
  const { selectedBrandId } = useBrandContext();
  const [threads, setThreads] = useState<CreatorThreadSummary[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([WELCOME_MESSAGE]);
  const [input, setInput] = useState("");
  const [contentResearchMode, setContentResearchMode] = useState(false);
  const [contentResearchIntent, setContentResearchIntent] = useState<ContentResearchIntentState | null>(null);
  const [contentResearchRun, setContentResearchRun] = useState<ContentResearchRunState | null>(null);
  const [preparedScope, setPreparedScope] = useState<ContentResearchScopeDraft | null>(null);
  const [scopeProjectionsByRun, setScopeProjectionsByRun] = useState<Record<string, ContentResearchScopeProjection>>({});
  const [scopeActionBusy, setScopeActionBusy] = useState(false);
  const [traceExpanded, setTraceExpanded] = useState(false);
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
  const contentResearchRequestEpochRef = useRef(new ContentResearchRequestEpoch());
  const contentResearchSubmitEpochRef = useRef(0);
  const pendingPresearchCommandsRef = useRef(new Map<string, { seed: string; commandId: string }>());
  // Tracks the most recently *requested* thread load to discard stale responses
  const loadingThreadRef = useRef<string | null>(null);
  const scopeProjection = contentResearchRun
    ? scopeProjectionsByRun[contentResearchRun.workflowRunId] ?? null
    : null;

  const activeThread = threads.find((t) => t.thread_id === activeThreadId) ?? null;
  const activeTopicPoolUrl = activeThread
    ? `/topic-pool?thread_id=${encodeURIComponent(activeThread.thread_id)}${task?.runId ? `&run_id=${encodeURIComponent(task.runId)}` : ""}`
    : null;
  const isTaskRunning = task?.status === "running" || task?.status === "paused";
  const showTaskCard = task?.status === "running" || task?.status === "paused" || task?.status === "failed";
  const allArtifactRefs = collectArtifactRefs(messages);
  const modelRecovery = resolveContentResearchModelRecovery({
    transientPresearch: contentResearchIntent
      ? {
          workflowRunId: contentResearchIntent.presearch.workflow_run_id,
          status: contentResearchIntent.presearch.status,
        }
      : null,
    durableRun: contentResearchRun
      ? {
          workflowRunId: contentResearchRun.workflowRunId,
          llmRecovery: contentResearchRun.trace?.llm_recovery,
        }
      : null,
  });
  const projectedModelRecoveryPending = projectedModelRecoveryVisible({
    recoveryPending: modelRecovery.recoveryPending,
    lifecycleState: contentResearchRun?.trace?.state,
    currentStage: contentResearchRun?.trace?.current_stage,
    hasDurableRun: contentResearchRun !== null,
  });

  useEffect(() => { taskRef.current = task; }, [task]);
  useEffect(() => { activeThreadIdRef.current = activeThreadId; }, [activeThreadId]);

  // Auto-scroll only for chat/result changes. Task progress stays inside the
  // progress card and must not push the conversation downward on every event.
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, generatedResult]);

  useEffect(() => {
    let cancelled = false;

    async function loadInitialThread() {
      const workflowRunId = new URLSearchParams(window.location.search).get("contentResearchRunId")?.trim() || null;
      let restoredThreadId: string | null = null;
      let workflowRestoreError: string | null = null;
      if (workflowRunId) {
        try {
          const workflow = await getContentResearchWorkflow(workflowRunId);
          if (cancelled) return;
          restoredThreadId = workflow.run.thread_id;
          saveContentResearchRunForThread(restoredThreadId, workflowRunId);
        } catch (error) {
          if (isExpectedLiteReportAbsence(error)) {
            // A broken direct link must not reuse an unrelated thread's run.
            restoredThreadId = "__content_research_run_not_found__";
          } else {
            workflowRestoreError = error instanceof Error ? error.message : "运行读取失败";
            restoredThreadId = contentResearchThreadForRun(workflowRunId)
              ?? "__content_research_run_restore_failed__";
          }
        }
      }

      try {
        // A direct run link is authoritative: its thread may predate brand
        // scoping and therefore have no brand_id. The normal Creator landing
        // page keeps the selected-brand filter for its history list.
        const items = await listThreads(workflowRunId ? null : selectedBrandId);
        if (cancelled) return;
        setThreads(items);
        // A user can create/send while the initial list is still in flight.
        // Never let late hydration reset that live presearch interaction.
        if (activeThreadIdRef.current) return;
        if (restoredThreadId) {
          const restoredThread = items.find((item) => item.thread_id === restoredThreadId);
          if (restoredThread) {
            await selectThread(restoredThread.thread_id);
          } else {
            // Do not silently replace a requested report with the first thread.
            resetConversation();
            setActiveThreadId(null);
            setMessages([
              WELCOME_MESSAGE,
              workflowRestoreError
                ? { id: "content-research-run-restore-failed", role: "system", text: `内容调研运行暂不可读取：${workflowRestoreError}` }
                : { id: "content-research-run-thread-unavailable", role: "system", text: "该内容调研所属对话不可访问，未切换到其他对话。" },
            ]);
          }
          return;
        }
        if (items.length > 0) await selectThread(items[0].thread_id);
      } catch {
        // Keep the initial welcome state if the thread list cannot be read.
      }
    }

    void loadInitialThread();
    return () => {
      cancelled = true;
    };
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
            appendMessage({ role: "assistant", text: "任务完成，但读取结果失败，请刷新页面后继续查看。" });
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

  function appendLiteReportMessage(report: ContentResearchLiteReportResponse) {
    const reportId = report.workflow_run_id;
    setMessages((current) => {
      const existing = current.findIndex((message) => message.report?.workflow_run_id === reportId);
      if (existing < 0) {
        return [...current, { id: `report-${reportId}`, role: "assistant", text: "", messageType: "artifact_result", report }];
      }
      return current.map((message, index) => index === existing ? { ...message, report } : message);
    });
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
    // Switching conversations must not erase another thread's resumable research run.
    contentResearchRequestEpochRef.current.activate(null);
    setMessages([WELCOME_MESSAGE]);
    setContentResearchIntent(null);
    setContentResearchRun(null);
    setPreparedScope(null);
    setScopeActionBusy(false);
    setTraceExpanded(false);
    setTask(null);
    setStatusLog([]);
    setGeneratedResult(null);
    setIsAccepted(false);
  }

  async function refreshContentResearchTrace(workflowRunId: string) {
    const ticket = contentResearchRequestEpochRef.current.ticket(workflowRunId, "trace");
    const trace = await getContentResearchTrace(workflowRunId);
    if (!contentResearchRequestEpochRef.current.accepts(ticket)) return null;
    setContentResearchRun((current) =>
      current && current.workflowRunId === workflowRunId
        ? { ...current, trace }
        : current
    );
    return trace;
  }

  async function refreshContentResearchScope(workflowRunId: string) {
    const ticket = contentResearchRequestEpochRef.current.ticket(workflowRunId, "scope");
    const projection = await getContentResearchScope(workflowRunId);
    if (!contentResearchRequestEpochRef.current.accepts(ticket)) return null;
    setScopeProjectionsByRun((current) => ({ ...current, [workflowRunId]: projection }));
    setPreparedScope(null);
    return projection;
  }

  async function restoreInterruptedContentResearchRun(threadId: string): Promise<boolean> {
    try {
      const { thread } = await getThreadTimeline(threadId);
      const workflowRunId = thread.active_run_id ?? contentResearchRunForThread(threadId);
      if (!workflowRunId) return false;
      contentResearchRequestEpochRef.current.activate(workflowRunId);
      const ticket = contentResearchRequestEpochRef.current.ticket(workflowRunId, "hydrate");
      const workflow = await getContentResearchWorkflow(workflowRunId);
      if (!contentResearchRequestEpochRef.current.accepts(ticket)) return false;
      const trace = await getContentResearchTrace(workflowRunId);
      if (!contentResearchRequestEpochRef.current.accepts(ticket)) return false;
      if (
        ["brief_confirmation_required", "recovery_required"].includes(workflow.run.state)
        && workflow.brief
      ) {
        setContentResearchIntent({
          seed: stringField(workflow.brief.payload, "seed_text", "本轮调研"),
          presearch: presearchFromWorkflowSummary(workflow),
        });
      } else {
        setContentResearchIntent(null);
      }
      setContentResearchRun(contentResearchRunWithReport(
        workflowRunId,
        workflow,
        trace,
        null,
        null,
      ));
      saveContentResearchRunForThread(threadId, workflowRunId);
      return true;
    } catch {
      return false;
    }
  }

  async function refreshContentResearchReport(workflowRunId: string): Promise<ContentResearchLiteReportResponse | null> {
    const ticket = contentResearchRequestEpochRef.current.ticket(workflowRunId, "report");
    setContentResearchRun((current) =>
      current && current.workflowRunId === workflowRunId
        ? { ...current, reportStatus: "loading", reportError: null }
        : current
    );
    try {
      const report = await getContentResearchLiteReportWithRetry(workflowRunId);
      if (!contentResearchRequestEpochRef.current.accepts(ticket)) return null;
      appendLiteReportMessage(report);
      setContentResearchRun((current) =>
        current && current.workflowRunId === workflowRunId
          ? {
              ...current,
              report,
              reportStatus: "ready",
              reportError: null,
            }
          : current
      );
      return report;
    } catch (error) {
      if (!contentResearchRequestEpochRef.current.accepts(ticket)) return null;
      const unavailable = isExpectedLiteReportAbsence(error);
      setContentResearchRun((current) =>
        current && current.workflowRunId === workflowRunId
          ? {
              ...current,
              reportStatus: unavailable ? "unavailable" : "failed",
              reportError: unavailable ? null : error instanceof Error ? error.message : "报告读取失败",
            }
          : current
      );
      if (unavailable) return null;
      throw error;
    }
  }

  async function pollContentResearchReport(
    workflowRunId: string,
    { requirePublication = false }: { requirePublication?: boolean } = {}
  ): Promise<ContentResearchLiteReportResponse | null> {
    for (let attempt = 0; attempt < 120; attempt += 1) {
      const report = await refreshContentResearchReport(workflowRunId);
      if (
        report
        && (
          litePublicationState(report)
          || (!requirePublication && report.publication.state === null && report.recovery_projection)
        )
      ) {
        return report;
      }
      await new Promise<void>((resolve) => window.setTimeout(resolve, 1000));
    }
    return null;
  }

  async function modifyContentResearchDirections() {
    const run = contentResearchRun;
    if (!run || !activeThreadId || !run.summary.brief) return;
    const payload = run.summary.brief.payload;
    const subject = stringField(payload, "seed_text") ||
      stringField(payload, "confirmed_subject") ||
      stringField(payload, "subject_confirmation") ||
      run.summary.brief.id;
    const competitors = [
      ...arrayField(payload, "selected_competitors"),
      ...arrayField(payload, "custom_competitors"),
      ...arrayField(payload, "competitor_tags"),
    ];
    const directions = arrayField(payload, "selected_directions").length
      ? arrayField(payload, "selected_directions")
      : arrayField(payload, "research_directions");
    try {
      await endContentResearchWorkflow(run.summary.run);
      removeContentResearchRunForThread(run.summary.brief.thread_id);
      const presearch = await createContentResearchPresearch({ command_id: createContentResearchCommandId(), seed_text: subject, user_note: null, thread_id: activeThreadId });
      setContentResearchIntent({ seed: subject, presearch: { ...presearch, competitor_tags: [...new Set([...presearch.competitor_tags, ...competitors])], research_directions: directions.length ? directions : presearch.research_directions } });
      setContentResearchRun(null);
      setPreparedScope(null);
      contentResearchRequestEpochRef.current.activate(presearch.workflow_run_id);
      setTraceExpanded(false);
      appendMessage({ role: "assistant", text: "已新建一轮调研 checklist，可以修改主体、竞品或调研方向后重新确认。" });
    } catch {
      appendMessage({ role: "system", text: "回到 checklist 失败，请稍后重试。" });
    }
  }

  async function endActiveContentResearch() {
    const run = contentResearchRun;
    if (!run) return;
    try {
      await endContentResearchWorkflow(run.summary.run);
      if (run.summary.brief) removeContentResearchRunForThread(run.summary.brief.thread_id);
      setContentResearchRun(null);
      setContentResearchIntent(null);
      setPreparedScope(null);
      contentResearchRequestEpochRef.current.activate(null);
      setScopeProjectionsByRun((current) => {
        const next = { ...current };
        delete next[run.workflowRunId];
        return next;
      });
      setTraceExpanded(false);
      appendMessage({ role: "assistant", text: "已结束本次内容调研，并清除当前线程的调研恢复入口。" });
    } catch (error) {
      appendMessage({
        role: "system",
        text: `结束本次调研失败：${error instanceof Error ? error.message : "请稍后重试。"}`,
      });
    }
  }

  async function continueModelRecovery() {
    const workflowRunId = modelRecovery.workflowRunId;
    if (!workflowRunId) return;
    const projection = contentResearchRun?.workflowRunId === workflowRunId
      ? contentResearchRun.summary.run
      : contentResearchIntent?.presearch.workflow_run_id === workflowRunId
        ? contentResearchIntent.presearch.run
        : null;
    if (!projection) throw new Error("当前 Run 状态不可用，请刷新后重试。");
    const presearch = await retryContentResearchPresearch(projection);
    const durablePayload = contentResearchRun?.summary.brief?.payload ?? {};
    const seed = contentResearchIntent?.seed
      || stringField(durablePayload, "seed_text")
      || stringField(durablePayload, "subject_confirmation")
      || "本轮调研";
    setContentResearchIntent({ seed, presearch });
    setContentResearchRun(null);
    setPreparedScope(null);
    contentResearchRequestEpochRef.current.activate(presearch.workflow_run_id);
    setTraceExpanded(false);
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
      let restoredRunId: string | null = null;
      let latestReport: ContentResearchLiteReportResponse | null = null;
      let latestFailure: { messageId: string; workflowRunId: string; error: string } | undefined;
      if (history.length > 0) {
        const timelineMessages = history.map(chatMessageFromRecord);
        const artifactRunIds = history
          .filter((message) => message.message_type === "artifact_result" && message.run_id)
          .map((message) => message.run_id as string);
        const reports = await Promise.all(history.map(async (message) => {
          if (message.message_type !== "artifact_result" || !message.run_id) return null;
          try {
            return { messageId: message.message_id, report: await getContentResearchLiteReportWithRetry(message.run_id) };
          } catch (error) {
            return { messageId: message.message_id, workflowRunId: message.run_id, error: error instanceof Error ? error.message : "报告读取失败" };
          }
        }));
        if (loadingThreadRef.current !== threadId) return;
        const reportResults = reports.filter((value): value is { messageId: string; report: ContentResearchLiteReportResponse } => value !== null && "report" in value);
        const reportByMessageId = new Map(reportResults.map((value) => [value.messageId, value.report]));
        const reportFailures = reports.filter((value): value is { messageId: string; workflowRunId: string; error: string } => value !== null && "error" in value);
        const visibleFailures = reportFailures.filter(
          (failure) => !isContentResearchReportPending(new Error(failure.error))
        );
        setMessages([
          ...timelineMessages.map((message) => ({ ...message, report: reportByMessageId.get(message.id) })),
          ...visibleFailures.map((failure) => ({ id: `report-error-${failure.messageId}`, role: "system" as const, text: `正式报告暂不可读取：${failure.error}` })),
        ]);
        latestReport = reportResults[reportResults.length - 1]?.report ?? null;
        latestFailure = visibleFailures[visibleFailures.length - 1];
        restoredRunId = latestReport?.workflow_run_id ?? artifactRunIds[artifactRunIds.length - 1] ?? null;
      }
      // The workflow API is the authority for classifying this durable run.
      // A legacy Creator run simply returns the documented Content Research
      // 404 and is hydrated by the legacy snapshot path below.
      const durableContentResearchRunId = thread.active_run_id ?? null;
      const runIdForThread =
        durableContentResearchRunId
        ?? contentResearchRunForThread(threadId)
        ?? restoredRunId;
      if (runIdForThread) {
        let currentReport = latestReport?.workflow_run_id === runIdForThread
          ? latestReport
          : null;
        let currentFailure = latestFailure?.workflowRunId === runIdForThread
          ? latestFailure
          : undefined;
        try {
          const workflow = await getContentResearchWorkflow(runIdForThread);
          if (loadingThreadRef.current !== threadId || workflow.run.thread_id !== threadId) return;
          if (
            ["brief_confirmation_required", "recovery_required"].includes(workflow.run.state)
            && workflow.brief
          ) {
            setContentResearchIntent({
              seed: stringField(workflow.brief.payload, "seed_text", thread.title),
              presearch: presearchFromWorkflowSummary(workflow),
            });
          } else {
            setContentResearchIntent(null);
          }
          contentResearchRequestEpochRef.current.activate(runIdForThread);
          const requestTicket = contentResearchRequestEpochRef.current.ticket(runIdForThread, "hydrate");
          saveContentResearchRunForThread(threadId, runIdForThread);
          let restoredScopeProjection: ContentResearchScopeProjection | null = null;
          try {
            restoredScopeProjection = await getContentResearchScope(runIdForThread);
          } catch {
            // Runs created before Scope confirmation have no persisted projection.
          }
          if (
            loadingThreadRef.current !== threadId
            || !contentResearchRequestEpochRef.current.accepts(requestTicket)
          ) return;
          if (restoredScopeProjection) {
            setScopeProjectionsByRun((current) => ({
              ...current,
              [runIdForThread]: restoredScopeProjection!,
            }));
          }
          setPreparedScope(null);
          let trace: ContentResearchTrace | null = null;
          try {
            trace = await getContentResearchTrace(runIdForThread);
          } catch {
            // Workflow/report restoration remains usable when Trace is temporarily unavailable.
          }
          if (!contentResearchRequestEpochRef.current.accepts(requestTicket)) return;
          if (!currentReport && !currentFailure) {
            try {
              const projection = await getContentResearchLiteReportWithRetry(runIdForThread);
              if (
                litePublicationState(projection)
                || (projection.publication.state === null && projection.recovery_projection)
              ) {
                if (!contentResearchRequestEpochRef.current.accepts(requestTicket)) return;
                currentReport = projection;
                appendLiteReportMessage(projection);
              }
            } catch (error) {
              if (!contentResearchRequestEpochRef.current.accepts(requestTicket)) return;
              if (!isExpectedLiteReportAbsence(error)) {
                const detail = error instanceof Error ? error.message : "报告读取失败";
                currentFailure = {
                  messageId: `active-${runIdForThread}`,
                  workflowRunId: runIdForThread,
                  error: detail,
                };
                appendMessage({ role: "system", text: `正式报告暂不可读取：${detail}` });
              }
            }
          }
          if (!contentResearchRequestEpochRef.current.accepts(requestTicket)) return;
          const restoredRun = contentResearchRunWithReport(runIdForThread, workflow, trace, null, currentReport);
          // A historical artifact can outlive its readable publication. Keep
          // the restored workflow visible and expose the report failure; do
          // not silently substitute a legacy result or clear the Timeline.
          if (currentFailure) {
            setContentResearchRun({
              ...restoredRun,
              reportStatus: "failed",
              reportError: currentFailure.error,
            });
          } else {
            setContentResearchRun(restoredRun);
          }
        } catch (error) {
          if (isExpectedLiteReportAbsence(error)) {
            removeContentResearchRunForThread(threadId);
            if (durableContentResearchRunId === runIdForThread) {
              const detail = error instanceof Error ? error.message : "运行读取失败";
              appendMessage({ role: "system", text: `内容调研运行暂不可读取：${detail}` });
            }
          } else {
            const detail = error instanceof Error ? error.message : "运行读取失败";
            appendMessage({ role: "system", text: `内容调研运行暂不可读取：${detail}` });
          }
          // The Timeline report remains visible even if an old workflow summary is unavailable.
        }
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

      // Content Research also records its own run id on the thread. Only the
      // legacy workflow has a session and may hydrate the legacy task card.
      if (thread.active_workflow_session_id && thread.active_run_id && thread.status !== "accepted") {
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
      activeThreadIdRef.current = created.thread_id;
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
      activeThreadIdRef.current = created.thread_id;
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

    if (contentResearchMode) {
      const submitEpoch = ++contentResearchSubmitEpochRef.current;
      const pending = pendingPresearchCommandsRef.current.get(threadId);
      const commandId = pending?.seed === text
        ? pending.commandId
        : createContentResearchCommandId();
      pendingPresearchCommandsRef.current.set(threadId, { seed: text, commandId });
      try {
        appendMessage({
          role: "assistant",
          text: "我会先做一次轻量预检索，确认你要研究的主体、竞品范围和调研方向。这个阶段不会生成正式结论。",
        });
        const result = await createContentResearchPresearch({
          command_id: commandId,
          seed_text: text,
          user_note: null,
          thread_id: threadId,
        });
        if (
          submitEpoch !== contentResearchSubmitEpochRef.current
          || activeThreadIdRef.current !== threadId
        ) return;
        pendingPresearchCommandsRef.current.delete(threadId);
        contentResearchRequestEpochRef.current.activate(result.workflow_run_id);
        saveContentResearchRunForThread(threadId, result.workflow_run_id);
        setContentResearchIntent({ seed: text, presearch: result });
        setContentResearchMode(false);
      } catch (error) {
        if (
          submitEpoch !== contentResearchSubmitEpochRef.current
          || activeThreadIdRef.current !== threadId
        ) return;
        const restored = await restoreInterruptedContentResearchRun(threadId);
        if (restored) pendingPresearchCommandsRef.current.delete(threadId);
        appendMessage({
          role: "system",
          text: restored
            ? "预检索连接中断，已恢复本次运行的 Trace；请打开 Trace 查看当前步骤与日志。"
            : contentResearchErrorFeedback(error, "内容调研预检索失败"),
        });
      } finally {
        if (submitEpoch === contentResearchSubmitEpochRef.current) {
          setIsLoading(false);
          inputRef.current?.focus();
        }
      }
      return;
    }

    if (
      contentResearchIntent
      && contentResearchIntent.presearch.run.allowed_actions.includes("revise_subject")
    ) {
      const ticket = contentResearchRequestEpochRef.current.ticket(
        contentResearchIntent.presearch.workflow_run_id,
        "revise-subject",
      );
      try {
        const revised = await reviseContentResearchSubject(
          contentResearchIntent.presearch.run,
          text,
        );
        if (!contentResearchRequestEpochRef.current.accepts(ticket)) return;
        setContentResearchIntent({ seed: text, presearch: revised.result });
        appendMessage({
          role: "assistant",
          text: "已根据你的补充重新完成轻量预检索，请确认更新后的 Brief。",
        });
      } catch (error) {
        if (!contentResearchRequestEpochRef.current.accepts(ticket)) return;
        appendMessage({
          role: "system",
          text: contentResearchErrorFeedback(error, "更新调研主体失败"),
        });
      } finally {
        if (contentResearchRequestEpochRef.current.accepts(ticket)) setIsLoading(false);
        inputRef.current?.focus();
      }
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
                  {message.report ? (
                    <ContentResearchReportMessage report={message.report} />
                  ) : (
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
                        <div className="whitespace-pre-wrap">{displayChatText(message.text)}</div>
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
                  )}
                </div>
              );
            })}

            {contentResearchIntent && (
              <ContentResearchIntentCard
                intent={contentResearchIntent}
              />
            )}

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
          <div
            className={[
              "mx-auto max-w-3xl rounded-[28px] border bg-white px-5 py-4 shadow-[0_16px_48px_rgba(15,23,42,0.08)] transition",
              contentResearchMode ? "border-blue-300 ring-4 ring-blue-50" : "border-line",
            ].join(" ")}
          >
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
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
              className="max-h-32 min-h-[42px] w-full resize-none overflow-y-auto bg-transparent text-base leading-7 text-ink outline-none placeholder:text-slate-400 disabled:opacity-50"
              placeholder={
                contentResearchMode
                  ? "输入品类、品牌或 SKU，发送后开始内容调研"
                  : isTaskRunning
                    ? "任务进行中，可继续补充要求..."
                    : "发消息..."
              }
            />
            <div className="mt-3 flex items-center justify-between gap-3">
              <div className="flex min-w-0 items-center gap-2 overflow-x-auto">
                <button
                  type="button"
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-2xl leading-none text-ink hover:bg-slate-100"
                  aria-label="添加"
                >
                  +
                </button>
                <span className="h-6 w-px shrink-0 bg-line" />
                <button
                  type="button"
                  onClick={() => setContentResearchMode((current) => !current)}
                  className={[
                    "inline-flex h-9 shrink-0 items-center gap-2 rounded-full border px-3 text-sm font-medium transition",
                    contentResearchMode
                      ? "border-ink bg-slate-100 text-ink"
                      : "border-transparent text-ink hover:bg-slate-100",
                  ].join(" ")}
                >
                  <span className="text-base">⌕</span>
                  内容调研
                </button>
              </div>
              <button
                type="button"
                onClick={() => void sendMessage()}
                disabled={isLoading || !input.trim()}
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-ink text-white transition hover:bg-slate-800 disabled:opacity-30"
                aria-label="发送"
              >
                ↑
              </button>
            </div>
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
      {contentResearchRun && <button
        type="button"
        onClick={() => setTraceExpanded(true)}
        className="fixed bottom-20 right-4 z-30 rounded-full border border-line bg-white px-3 py-2 text-xs font-semibold text-blue-600 shadow-lg lg:hidden"
        aria-label="查看完整 workflow trace"
      >
        查看完整 Trace
      </button>}
      {contentResearchRun && <ContentResearchContextSidebar
        run={contentResearchRun}
        onExpandedChange={setTraceExpanded}
        onModifyDirections={() => void modifyContentResearchDirections()}
        recoveryPending={projectedModelRecoveryPending}
        recoveryRequiredSince={modelRecovery.requiredSince}
        onContinuePresearch={continueModelRecovery}
      />}
      {!contentResearchRun && contentResearchMode && <aside className="hidden w-[300px] shrink-0 overflow-y-auto border-l border-line bg-slate-50 p-4 lg:block" aria-label="内容调研上下文">
        <h2 className="mb-3 text-sm font-semibold text-ink">本次研究摘要</h2>
        <section className="mb-4 rounded-xl border border-line bg-white p-4" aria-label="内容调研研究摘要">
          <p className="text-xs leading-5 text-quiet">
            {contentResearchIntent
              ? "调研范围尚未确认；正式报告发布后将在此显示冻结摘要。"
              : "暂无进行中的内容调研。发起调研后，此处会显示冻结范围与研究摘要。"}
          </p>
        </section>
        <ModelServiceCard recoveryPending={projectedModelRecoveryPending} recoveryRequiredSince={modelRecovery.requiredSince} onContinue={continueModelRecovery} onConfigurationChanged={() => undefined} />
        <XiaohongshuLoginCard />
      </aside>}
      {contentResearchRun && <ContentResearchTraceInspector
        run={contentResearchRun}
        expanded={traceExpanded}
        onExpandedChange={setTraceExpanded}
        onRefresh={async () => { await refreshContentResearchTrace(contentResearchRun.workflowRunId); }}
      />}
    </div>
  );
}

CreatorPage.ContentResearchIntentCard = ContentResearchIntentCard;

export default CreatorPage;
