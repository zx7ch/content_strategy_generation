import assert from "node:assert/strict";
import test from "node:test";

import {
  traceExecutionDurationText,
  traceStepGroup,
  traceStepTitle,
  workflowStatusLabel,
} from "./content-research-trace.ts";

test("waiting-user Trace is a user confirmation boundary, not running or recovery time", () => {
  const value = traceExecutionDurationText(
    {
      step_id: "scope-confirm",
      status: "waiting_user",
      started_at: "2026-08-02T03:16:58",
    },
    [],
    Date.parse("2026-08-02T11:17:01Z"),
  );

  assert.equal(value, "等待用户操作");
  assert.equal(workflowStatusLabel("waiting_user"), "等待用户确认");
});

test("Trace exposes only user-facing Chinese phase and group names", () => {
  assert.deepEqual(
    ["presearch", "brief_confirm", "scope_confirm", "formal_research", "coverage", "report"]
      .map((step) => [traceStepTitle(step), traceStepGroup(step)]),
    [
      ["识别调研主体与候选方向", "研究范围与计划"],
      ["确认调研需求", "研究范围与计划"],
      ["确认检索范围", "研究范围与计划"],
      ["采集与分析公开内容", "来源采集与分析"],
      ["检查证据完整性", "证据质量检查"],
      ["生成调研报告", "报告生成"],
    ],
  );
});

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
