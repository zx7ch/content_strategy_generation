import assert from "node:assert/strict";
import test from "node:test";

import {
  ContentResearchApiError,
  clarifyContentResearchSubject,
  startContentResearchFormalResearch,
  confirmContentResearchBrief,
  createContentResearchPresearch,
  endContentResearchWorkflow,
  getContentResearchDecisions,
  getContentResearchLiteReport,
  getContentResearchWorkflow,
  retryContentResearchFormalResearch,
  resumeContentResearchFormalResearch,
  submitContentResearchBrandDecision,
  submitContentResearchContentDecision,
  isContentResearchReportPending,
  retryContentResearchPresearch,
  saveLLMConfiguration,
} from "./content-research-api.ts";
import { setWorkspaceContext } from "./api.ts";

setWorkspaceContext("ws_test", "user_test");

test("report publication gaps remain pending instead of becoming permanent failures", () => {
  assert.equal(
    isContentResearchReportPending(
      new ContentResearchApiError("published report artifact is missing", 404)
    ),
    true
  );
  assert.equal(
    isContentResearchReportPending(new ContentResearchApiError("published report not found", 404)),
    true
  );
  assert.equal(
    isContentResearchReportPending(new ContentResearchApiError("database unavailable", 500)),
    false
  );
});

test("createContentResearchPresearch posts seed to real P0 endpoint", async () => {
  let requestUrl = "";
  let requestBody = "";
  globalThis.fetch = (async (input, init) => {
    requestUrl = String(input);
    requestBody = String(init?.body ?? "");
    return jsonResponse({
      attempt_id: "att_1",
      workflow_run_id: "run_1",
      brief_id: "rb_1",
      status: "completed",
      subject_confirmation: "徒步短裤",
      competitor_tags: ["迪卡侬"],
      research_directions: ["产品营销"],
      direction_catalog: ["product_marketing", "competitor_discovery", "content_performance"],
      custom_research_question: "",
      custom_competitor_input: "",
      timeout_status: "none",
      fallback_used: false,
    });
  }) as typeof fetch;

  const result = await createContentResearchPresearch({
    seed_text: "徒步短裤",
    user_note: "关注夏季",
    thread_id: "thread_1",
  });

  assert.ok(requestUrl.endsWith("/content-research/presearch"));
  assert.match(requestBody, /"seed_text":"徒步短裤"/);
  assert.equal(result.workflow_run_id, "run_1");
  assert.deepEqual(result.direction_catalog, [
    "product_marketing",
    "competitor_discovery",
    "content_performance",
  ]);
});

test("model configuration requests are Workspace scoped and never expect a returned key", async () => {
  setWorkspaceContext("ws_1", "user_1");
  let requestHeaders = new Headers();
  globalThis.fetch = (async (_input, init) => {
    requestHeaders = new Headers(init?.headers);
    return jsonResponse({
      source: "user", status: "validated", base_url: "https://proxy.example/v1", model: "model-x",
      api_key_configured: true, api_key_suffix: "1234", validated_at: "2026-08-03T00:00:00Z", error_code: null,
    });
  }) as typeof fetch;
  const result = await saveLLMConfiguration({ base_url: "https://proxy.example/v1", model: "model-x", api_key: "secret-1234" });
  assert.equal(requestHeaders.get("X-Workspace-Id"), "ws_1");
  assert.equal(requestHeaders.get("X-User-Id"), "user_1");
  assert.equal("api_key" in result, false);
});

test("content research reuses the optional Bearer header without leaking it into the body", async () => {
  const previousToken = process.env.NEXT_PUBLIC_XHS_AUTH_TOKEN;
  process.env.NEXT_PUBLIC_XHS_AUTH_TOKEN = "frontend-auth-token";
  setWorkspaceContext("ws_1", "user_1");
  let requestHeaders = new Headers();
  let requestBody = "";
  globalThis.fetch = (async (_input, init) => {
    requestHeaders = new Headers(init?.headers);
    requestBody = String(init?.body ?? "");
    return jsonResponse({
      source: "user", status: "validated", base_url: "https://proxy.example/v1", model: "model-x",
      api_key_configured: true, api_key_suffix: "1234", validated_at: "2026-08-03T00:00:00Z", error_code: null,
    });
  }) as typeof fetch;

  try {
    await saveLLMConfiguration({ base_url: "https://proxy.example/v1", model: "model-x", api_key: "model-key-1234" });
  } finally {
    if (previousToken === undefined) delete process.env.NEXT_PUBLIC_XHS_AUTH_TOKEN;
    else process.env.NEXT_PUBLIC_XHS_AUTH_TOKEN = previousToken;
  }

  assert.equal(requestHeaders.get("Authorization"), "Bearer frontend-auth-token");
  assert.equal(requestBody.includes("frontend-auth-token"), false);
});

test("retry presearch returns the same persisted identifiers", async () => {
  let requestBody = "";
  globalThis.fetch = (async (_input, init) => {
    requestBody = String(init?.body ?? "");
    return jsonResponse({ schema_version: "content_research_workflow_action_response_v1", workflow_run_id: "run_1", action: "retry_presearch", status: "completed", execution_mode: "local", sync_status: "local_only", result: {
      attempt_id: "att_1", workflow_run_id: "run_1", brief_id: "rb_1", status: "completed", subject_confirmation: "短裤", competitor_tags: [], research_directions: [], direction_catalog: [], custom_research_question: "", timeout_status: "none", fallback_used: false,
    }});
  }) as typeof fetch;
  const result = await retryContentResearchPresearch("run_1");
  assert.match(requestBody, /"action":"retry_presearch"/);
  assert.equal(result.attempt_id, "att_1");
  assert.equal(result.brief_id, "rb_1");
});

test("subject clarification stays on the same workflow run", async () => {
  let requestBody = "";
  globalThis.fetch = (async (_input, init) => {
    requestBody = String(init?.body ?? "");
    return jsonResponse({
      schema_version: "content_research_workflow_action_response_v1",
      workflow_run_id: "run_1",
      action: "clarify_subject",
      status: "completed",
      result: {
        attempt_id: "att_1",
        workflow_run_id: "run_1",
        brief_id: "rb_1",
        status: "completed",
        subject_confirmation: "Apple 品牌",
        competitor_tags: [],
        research_directions: [],
        direction_catalog: [],
        custom_research_question: "",
        timeout_status: "none",
        fallback_used: false,
        subject_structure: { canonical_subject: "Apple 品牌" },
        subject_structure_state: "confirmed",
        subject_structure_reason_codes: [],
      },
    });
  }) as typeof fetch;

  const response = await clarifyContentResearchSubject(
    "run_1",
    "这里指 Apple 品牌",
  );

  assert.match(requestBody, /"action":"clarify_subject"/);
  assert.match(requestBody, /"clarification_text":"这里指 Apple 品牌"/);
  assert.equal(response.result.workflow_run_id, "run_1");
});

test("confirmContentResearchBrief posts selected directions", async () => {
  let requestUrl = "";
  let requestBody = "";
  globalThis.fetch = (async (input, init) => {
    requestUrl = String(input);
    requestBody = String(init?.body ?? "");
    return jsonResponse({
      schema_version: "content_research_workflow_action_response_v1",
      workflow_run_id: "run_1",
      action: "confirm_brief",
      status: "completed",
      result: workflowPayload(),
      execution_mode: "local",
      remote_run_id: null,
      local_cache_id: "rb_1",
      sync_status: "local_only",
    });
  }) as typeof fetch;

  const result = await confirmContentResearchBrief("run_1", {
    confirmed_subject: "徒步短裤",
    subject_type: "category",
    selected_competitors: ["迪卡侬"],
    custom_competitors: ["凯乐石"],
    selected_directions: ["product_marketing"],
    custom_research_question: "轻量速干",
  });

  assert.ok(requestUrl.endsWith("/content-research/workflows/run_1/actions"));
  assert.match(requestBody, /"action":"confirm_brief"/);
  assert.match(requestBody, /"selected_directions":\["product_marketing"\]/);
  assert.equal(result.workflow_run_id, "run_1");
});

test("formal research dispatch preserves failed specialist state", async () => {
  globalThis.fetch = (async (input, init) => {
    assert.ok(String(input).endsWith("/content-research/workflows/run_1/actions"));
    assert.match(String(init?.body ?? ""), /"action":"start_formal_research"/);
    assert.match(String(init?.body ?? ""), /"source_kind":"search_result"/);
    return jsonResponse({
      schema_version: "content_research_workflow_action_response_v1",
      workflow_run_id: "run_1",
      action: "start_formal_research",
      status: "failed",
      result: {
        workflow_run_id: "run_1",
        provider: "xiaohongshu",
        source_kind: "search_result",
        status: "failed",
        task_count: 2,
        completed_task_count: 1,
        partial_completed_task_count: 0,
        failed_tasks: [{ task_id: "task_2", agent_name: "UGCCommunityResearchAgent", error: "auth_required" }],
        limit_per_specialist: 50,
      },
      execution_mode: "local",
      remote_run_id: null,
      local_cache_id: "rb_1",
      sync_status: "local_only",
    });
  }) as typeof fetch;

  const result = await startContentResearchFormalResearch("run_1", {
    source_kind: "search_result",
  });

  assert.equal(result.status, "failed");
  assert.equal(result.failed_tasks[0].error, "auth_required");
});

test("retry formal research invokes the same-run requeue action", async () => {
  let requestBody = "";
  globalThis.fetch = (async (_input, init) => {
    requestBody = String(init?.body ?? "");
    return jsonResponse({
      schema_version: "content_research_workflow_action_response_v1",
      workflow_run_id: "run_1",
      action: "retry_formal_research",
      status: "queued",
      result: {
        workflow_run_id: "run_1", provider: "xiaohongshu", source_kind: "search_result",
        status: "queued", task_count: 2, completed_task_count: 1,
        partial_completed_task_count: 0, failed_tasks: [], limit_per_specialist: 20,
      },
      execution_mode: "local", remote_run_id: null, local_cache_id: "rb_1", sync_status: "local_only",
    });
  }) as typeof fetch;

  const result = await retryContentResearchFormalResearch("run_1", { limit: 20 });

  assert.match(requestBody, /"action":"retry_formal_research"/);
  assert.equal(result.workflow_run_id, "run_1");
});

test("getContentResearchLiteReport fetches the Lite report contract", async () => {
  let requestUrl = "";
  globalThis.fetch = (async (input) => {
    requestUrl = String(input);
    return jsonResponse({
      schema_version: "content_research_api_v1",
      workflow_run_id: "run_1",
      workflow_execution_state: "succeeded",
      publication: { state: "partial_verified_report" },
      sections: { main_findings: [], weak_signals: [], limitations_scope: [] },
      status_strip: {}, citations: [], run_direction_states: [], recovery_projection: null,
    });
  }) as typeof fetch;

  const report = await getContentResearchLiteReport("run_1", { citationGroupIds: ["cg_1", "cg_2"] });

  assert.ok(requestUrl.endsWith("/content-research/workflows/run_1/lite-report?citation_group_ids=cg_1&citation_group_ids=cg_2"));
  assert.equal(report.publication.state, "partial_verified_report");
  assert.equal(report.workflow_execution_state, "succeeded");
});

test("submitContentResearchBrandDecision posts selected decision", async () => {
  let requestUrl = "";
  let requestBody = "";
  globalThis.fetch = (async (input, init) => {
    requestUrl = String(input);
    requestBody = String(init?.body ?? "");
    return jsonResponse(decisionPayload({ target_type: "brand_candidate", decision_status: "selected" }));
  }) as typeof fetch;

  const result = await submitContentResearchBrandDecision("run_1", {
    target_id: "brand_satisfy",
    decision_request_id: "req_1",
    decision_status: "selected",
    rationale: "值得深入研究",
  });

  assert.ok(requestUrl.endsWith("/content-research/workflows/run_1/brand-decisions"));
  assert.match(requestBody, /"decision_status":"selected"/);
  assert.equal(result.target_type, "brand_candidate");
  assert.equal(result.advancement.resource_policy, "full_deep_research");
});

test("submitContentResearchContentDecision posts watchlist decision", async () => {
  let requestUrl = "";
  globalThis.fetch = (async (input) => {
    requestUrl = String(input);
    return jsonResponse(decisionPayload({ target_type: "recommended_content", decision_status: "watchlist" }));
  }) as typeof fetch;

  const result = await submitContentResearchContentDecision("run_1", {
    target_id: "content_commute",
    decision_request_id: "req_content_1",
    decision_status: "watchlist",
  });

  assert.ok(requestUrl.endsWith("/content-research/workflows/run_1/content-decisions"));
  assert.equal(result.target_type, "recommended_content");
  assert.equal(result.advancement.resource_policy, "deferred");
});

test("getContentResearchDecisions fetches replayable decision state", async () => {
  let requestUrl = "";
  globalThis.fetch = (async (input) => {
    requestUrl = String(input);
    return jsonResponse(decisionsPayload());
  }) as typeof fetch;

  const result = await getContentResearchDecisions("run_1");

  assert.ok(requestUrl.endsWith("/content-research/workflows/run_1/decisions"));
  assert.equal(result.decisions.length, 2);
  assert.equal(result.current_decisions.length, 1);
  assert.equal(result.current_decisions[0].decision_status, "selected");
});

test("formal research preserves completed and failed specialist states distinctly", async () => {
  const responses = [
    {
      workflow_run_id: "run_1",
      provider: "xiaohongshu",
      source_kind: "search_result",
      status: "completed",
      task_count: 2,
      completed_task_count: 2,
      partial_completed_task_count: 0,
      failed_tasks: [],
      limit_per_specialist: 50,
    },
    {
      workflow_run_id: "run_1",
      provider: "xiaohongshu",
      source_kind: "search_result",
      status: "failed",
      task_count: 2,
      completed_task_count: 1,
      partial_completed_task_count: 0,
      failed_tasks: [{ task_id: "task_2", error: "rate_limited" }],
      limit_per_specialist: 50,
    },
  ];
  globalThis.fetch = (async () =>
    jsonResponse({
      schema_version: "content_research_workflow_action_response_v1",
      workflow_run_id: "run_1",
      action: "start_formal_research",
      status: responses[0].status,
      result: responses.shift(),
      execution_mode: "local",
      remote_run_id: null,
      local_cache_id: "rb_1",
      sync_status: "local_only",
    })) as typeof fetch;

  const completed = await startContentResearchFormalResearch("run_1", {});
  const failed = await startContentResearchFormalResearch("run_1", {});

  assert.equal(completed.status, "completed");
  assert.equal(completed.failed_tasks.length, 0);
  assert.equal(failed.status, "failed");
  assert.equal(failed.failed_tasks[0].error, "rate_limited");
});

test("resumeContentResearchFormalResearch sends the recovery continuation action", async () => {
  let requestBody = "";
  globalThis.fetch = (async (_input, init) => {
    requestBody = String(init?.body ?? "");
    return jsonResponse({
      schema_version: "content_research_workflow_action_response_v1",
      workflow_run_id: "run_1",
      action: "resume_formal_research",
      status: "running",
      result: {
        workflow_run_id: "run_1",
        status: "running",
        recoverable: true,
      },
      execution_mode: "local",
      remote_run_id: null,
      local_cache_id: "rb_1",
      sync_status: "local_only",
    });
  }) as typeof fetch;

  const result = await resumeContentResearchFormalResearch("run_1");

  assert.match(requestBody, /"action":"resume_formal_research"/);
  assert.equal(result.status, "running");
});

test("endContentResearchWorkflow sends end workflow action", async () => {
  let requestUrl = "";
  let requestBody = "";
  globalThis.fetch = (async (input, init) => {
    requestUrl = String(input);
    requestBody = String(init?.body ?? "");
    return jsonResponse({
      schema_version: "content_research_workflow_action_response_v1",
      workflow_run_id: "run_1",
      action: "end_content_research",
      status: "completed",
      result: { ended: true },
      execution_mode: "local",
      remote_run_id: null,
      local_cache_id: "rb_1",
      sync_status: "local_only",
    });
  }) as typeof fetch;

  const result = await endContentResearchWorkflow("run_1");

  assert.ok(requestUrl.endsWith("/content-research/workflows/run_1/actions"));
  assert.match(requestBody, /"action":"end_content_research"/);
  assert.equal(result.action, "end_content_research");
  assert.equal(result.result.ended, true);
});

test("content research helpers throw readable non-ok errors", async () => {
  globalThis.fetch = (async () =>
    jsonResponse({ error_message: "Content research workflow not found" }, 404)) as typeof fetch;

  await assert.rejects(() => getContentResearchWorkflow("run_missing"), /Content research workflow not found/);
  await assert.rejects(() => getContentResearchLiteReport("run_missing"), /Content research workflow not found/);
  await assert.rejects(() => getContentResearchDecisions("run_missing"), /Content research workflow not found/);
  await assert.rejects(
    () =>
      submitContentResearchBrandDecision("run_missing", {
        target_id: "brand_satisfy",
        decision_request_id: "req_missing",
        decision_status: "selected",
      }),
    /Content research workflow not found/
  );
});

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function workflowPayload() {
  return {
    workflow_run_id: "run_1",
    brief: {
      id: "rb_1",
      workflow_run_id: "run_1",
      thread_id: "thread_1",
      status: "ready",
      payload: {},
    },
    plan: null,
    directions: [],
    subagent_tasks: [],
    runtime_run: { run_id: "run_1", current_step: "formal_research", status: "running" },
    runtime_steps: [],
    runtime_child_tasks: [],
  };
}

function decisionPayload(overrides: Partial<Record<string, unknown>> = {}) {
  const targetType = String(overrides.target_type ?? "brand_candidate");
  const decisionStatus = String(overrides.decision_status ?? "selected");
  const resourcePolicy =
    targetType === "brand_candidate"
      ? decisionStatus === "selected"
        ? "full_deep_research"
        : decisionStatus === "watchlist"
          ? "lightweight_or_deferred"
          : "none"
      : decisionStatus === "watchlist"
        ? "deferred"
        : decisionStatus === "selected"
          ? "include_in_final_focus"
          : "none";
  return {
    schema_version: "content_research_api_v1",
    decision_id: String(overrides.decision_id ?? "hd_1"),
    workflow_run_id: "run_1",
    target_type: targetType,
    target_id: String(overrides.target_id ?? "brand_satisfy"),
    decision_request_id: String(overrides.decision_request_id ?? "req_1"),
    decision_status: decisionStatus,
    decision_payload: {},
    rationale: String(overrides.rationale ?? ""),
    created_by_type: "user",
    created_by_id: "user_1",
    research_brief_id: "rb_1",
    research_plan_id: "rp_1",
    research_result_snapshot_id: null,
    metadata: {},
    advancement: { resource_policy: resourcePolicy },
    is_current: Boolean(overrides.is_current ?? true),
    idempotent_replay: Boolean(overrides.idempotent_replay ?? false),
    history_count: Number(overrides.history_count ?? 1),
    created_at: "2026-07-08T00:00:00+08:00",
  };
}

function decisionsPayload() {
  const watchlist = decisionPayload({
    decision_id: "hd_1",
    decision_request_id: "req_1",
    decision_status: "watchlist",
    is_current: false,
    history_count: 2,
  });
  const selected = decisionPayload({
    decision_id: "hd_2",
    decision_request_id: "req_2",
    decision_status: "selected",
    is_current: true,
    history_count: 2,
  });
  return {
    schema_version: "content_research_api_v1",
    workflow_run_id: "run_1",
    decisions: [watchlist, selected],
    current_decisions: [selected],
  };
}
