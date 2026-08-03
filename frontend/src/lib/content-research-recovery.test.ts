import assert from "node:assert/strict";
import test from "node:test";

import { resolveContentResearchModelRecovery } from "./content-research-recovery.ts";


test("reload recovery uses the durable workflow and Trace without transient intent", () => {
  assert.deepEqual(
    resolveContentResearchModelRecovery({
      transientPresearch: null,
      durableRun: {
        workflowRunId: "run_durable",
        llmRecovery: { required: true, required_since: "2026-08-03T01:02:03+00:00" },
      },
    }),
    { recoveryPending: true, workflowRunId: "run_durable", requiredSince: "2026-08-03T01:02:03+00:00" },
  );
});


test("non-recovery durable runs do not expose a model continuation", () => {
  assert.deepEqual(
    resolveContentResearchModelRecovery({
      transientPresearch: null,
      durableRun: {
        workflowRunId: "run_complete",
        llmRecovery: { required: false },
      },
    }),
    { recoveryPending: false, workflowRunId: null, requiredSince: null },
  );
});


test("the current presearch remains the first recovery target before reload", () => {
  assert.deepEqual(
    resolveContentResearchModelRecovery({
      transientPresearch: {
        workflowRunId: "run_transient",
        status: "waiting_model_config",
      },
      durableRun: {
        workflowRunId: "run_old",
        llmRecovery: { required: true },
      },
    }),
    { recoveryPending: true, workflowRunId: "run_transient", requiredSince: null },
  );
});
