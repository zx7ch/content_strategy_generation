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
