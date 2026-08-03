import assert from "node:assert/strict";
import test from "node:test";

import { traceExecutionDurationText } from "./content-research-trace.ts";

test("trace duration treats backend UTC timestamps without a suffix as UTC", () => {
  const value = traceExecutionDurationText(
    {
      step_id: "step_formal",
      status: "running",
      started_at: "2026-08-02T03:16:58",
    },
    [],
    Date.parse("2026-08-02T03:17:08Z")
  );

  assert.equal(value, "10.0s（执行中）");
});

test("trace duration stops at the recovery boundary instead of counting wait time", () => {
  const value = traceExecutionDurationText(
    {
      step_id: "step_formal",
      status: "retrying",
      started_at: "2026-08-02T03:16:58",
    },
    [
      { step_id: "step_formal", event_type: "step_started", created_at: "2026-08-02T03:16:58" },
      { step_id: "step_formal", event_type: "run_waiting_user", created_at: "2026-08-02T03:17:01" },
    ],
    Date.parse("2026-08-02T11:17:01Z")
  );

  assert.equal(value, "3.0s（等待恢复）");
});

test("recorded timing separates active queue and waiting", () => {
  const value = traceExecutionDurationText(
    {
      status: "retrying",
      timing: {
        active_duration_ms: 24_407,
        queue_duration_ms: 570,
        waiting_started_at: "2026-08-03T01:00:25.000001+00:00",
        timing_source: "recorded",
      },
    },
    []
  );

  assert.equal(value, "执行 24.4s · 排队 0.6s · 等待恢复中");
});

test("recorded timing below one tenth second remains visible", () => {
  const value = traceExecutionDurationText(
    {
      status: "succeeded",
      timing: { active_duration_ms: 23, queue_duration_ms: 0, timing_source: "recorded" },
    },
    []
  );

  assert.equal(value, "执行 <0.1s");
});

test("estimated timing labels the legacy duration as approximate", () => {
  const value = traceExecutionDurationText(
    {
      status: "succeeded",
      timing: { active_duration_ms: 3_000, timing_source: "estimated" },
    },
    []
  );

  assert.equal(value, "执行约 3.0s");
});

test("recorded queue-only timing renders the pending execution wording", () => {
  const value = traceExecutionDurationText(
    {
      status: "pending",
      timing: { queue_duration_ms: 1_500, timing_source: "recorded" },
    },
    []
  );

  assert.equal(value, "排队 1.5s · 等待执行");
});

test("recorded timing ignores stale waiting and backoff markers outside retrying", () => {
  assert.equal(
    traceExecutionDurationText(
      {
        status: "succeeded",
        timing: {
          active_duration_ms: 800,
          waiting_started_at: "2026-08-03T01:00:00.900001+00:00",
          timing_source: "recorded",
        },
      },
      []
    ),
    "执行 0.8s"
  );
  assert.equal(
    traceExecutionDurationText(
      {
        status: "running",
        timing: {
          active_duration_ms: 800,
          retry_backoff_started_at: "2026-08-03T01:00:00.900001+00:00",
          timing_source: "recorded",
        },
      },
      []
    ),
    "执行 0.8s"
  );
});
