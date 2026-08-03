type TraceRecord = Record<string, unknown>;

function stringValue(record: TraceRecord, key: string): string {
  const value = record[key];
  return typeof value === "string" ? value : "";
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
  const stepId = stringValue(step, "step_id");
  const startedAt = latestEventTime(events, stepId, ["step_started"])
    || parseTraceTimestamp(stringValue(step, "started_at"));
  if (!Number.isFinite(startedAt)) return "执行耗时未记录";

  const status = stringValue(step, "status");
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
