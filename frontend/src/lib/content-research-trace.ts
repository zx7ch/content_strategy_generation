type TraceRecord = Record<string, unknown>;

function stringValue(record: TraceRecord, key: string): string {
  const value = record[key];
  return typeof value === "string" ? value : "";
}

function objectValue(record: TraceRecord, key: string): TraceRecord | null {
  const value = record[key];
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as TraceRecord
    : null;
}

function numberValue(record: TraceRecord | null, key: string): number | null {
  const value = record?.[key];
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

function durationText(durationMs: number): string {
  return durationMs > 0 && durationMs < 100 ? "<0.1s" : `${(durationMs / 1000).toFixed(1)}s`;
}

function recordedDurationText(timing: TraceRecord, status: string): string | null {
  const activeDurationMs = numberValue(timing, "active_duration_ms");
  const source = stringValue(timing, "timing_source");
  const queueDurationMs = numberValue(timing, "queue_duration_ms");
  if (activeDurationMs === null) {
    if (source === "recorded" && status === "pending" && queueDurationMs !== null) {
      return `排队 ${durationText(queueDurationMs)} · 等待执行`;
    }
    return null;
  }

  const prefix = source === "estimated" ? "执行约 " : "执行 ";
  const segments = [`${prefix}${durationText(activeDurationMs)}`];
  if (source === "recorded" && queueDurationMs !== null && queueDurationMs > 0) {
    segments.push(`排队 ${durationText(queueDurationMs)}`);
  }
  if (source === "recorded" && status === "retrying" && stringValue(timing, "waiting_started_at")) {
    segments.push("等待恢复中");
  } else if (source === "recorded" && status === "retrying" && stringValue(timing, "retry_backoff_started_at")) {
    segments.push("重试退避中");
  }
  return segments.join(" · ");
}

function parseTraceTimestamp(value: string): number {
  if (!value) return Number.NaN;
  // SQLite stores UTC timestamps without an offset.  JavaScript otherwise
  // interprets that format in the browser's local timezone.
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : `${value}Z`;
  return Date.parse(normalized);
}

function latestEventTime(
  events: TraceRecord[],
  stepId: string,
  eventTypes: string[]
): number {
  return events.reduce((latest, event) => {
    if (stringValue(event, "step_id") !== stepId || !eventTypes.includes(stringValue(event, "event_type"))) {
      return latest;
    }
    const timestamp = parseTraceTimestamp(stringValue(event, "created_at"));
    if (!Number.isFinite(timestamp)) return latest;
    return Number.isFinite(latest) ? Math.max(latest, timestamp) : timestamp;
  }, Number.NaN);
}

export function traceExecutionDurationText(
  step: TraceRecord,
  events: TraceRecord[],
  now = Date.now()
): string {
  const status = stringValue(step, "status");
  if (status === "waiting_user") return "等待用户操作";
  const projectedTiming = objectValue(step, "timing");
  const projectedText = projectedTiming ? recordedDurationText(projectedTiming, status) : null;
  if (projectedText) return projectedText;

  const stepId = stringValue(step, "step_id");
  const startedAt = latestEventTime(events, stepId, ["step_started"])
    || parseTraceTimestamp(stringValue(step, "started_at"));
  if (!Number.isFinite(startedAt)) return "执行耗时未记录";

  const completedAt = latestEventTime(events, stepId, ["step_completed", "step_failed"])
    || parseTraceTimestamp(stringValue(step, "completed_at"));
  const waitingAt = latestEventTime(events, stepId, ["run_waiting_user", "step_retry_scheduled"]);
  const endAt = Number.isFinite(completedAt)
    ? completedAt
    : Number.isFinite(waitingAt)
      ? waitingAt
      : now;
  const suffix = Number.isFinite(completedAt)
    ? ""
    : Number.isFinite(waitingAt) || status === "retrying"
      ? "（等待恢复）"
      : "（执行中）";
  return `${Math.max(0, (endAt - startedAt) / 1000).toFixed(1)}s${suffix}`;
}

export function workflowStatusLabel(status: string) {
  if (["completed", "succeeded", "success"].includes(status)) return "已完成";
  if (["running", "collecting"].includes(status)) return "进行中";
  if (status === "pending") return "等待开始";
  if (status === "retrying") return "等待恢复";
  if (status === "waiting_user") return "等待用户确认";
  if (status === "failed") return "未完成";
  if (["cancelled", "cancelling"].includes(status)) return "已结束";
  return "等待处理";
}

export function traceStepTitle(stepName: string) {
  if (stepName === "presearch") return "识别调研主体与候选方向";
  if (stepName === "brief_confirm") return "确认调研需求";
  if (stepName === "scope_confirm") return "确认检索范围";
  if (stepName === "plan_build") return "准备调研计划";
  if (stepName === "formal_research") return "采集公开内容";
  if (stepName === "coverage") return "检查证据完整性";
  if (stepName === "marketing_analysis") return "营销结论分析";
  if (stepName === "report") return "组装并发布调研报告";
  return "处理调研任务";
}

export function traceStepGroup(stepName: string) {
  if (["presearch", "brief_confirm", "scope_confirm", "plan_build"].includes(stepName)) {
    return "研究范围与计划";
  }
  if (stepName === "formal_research") return "来源采集";
  if (stepName === "coverage") return "证据质量检查";
  if (stepName === "marketing_analysis") return "结论分析";
  if (stepName === "report") return "报告生成";
  return "调研任务";
}
