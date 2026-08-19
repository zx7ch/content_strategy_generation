import assert from "node:assert/strict";
import test from "node:test";

import {
  ContentResearchApiError,
  confirmContentResearchScope,
  confirmContentResearchSubjectStructure,
  startContentResearchFormalResearch,
  confirmContentResearchBrief,
  createContentResearchPresearch,
  endContentResearchWorkflow,
  getContentResearchDecisions,
  getContentResearchDirectionEvidence,
  getContentResearchLiteReport,
  getContentResearchLiteReportWithRetry,
  getContentResearchScope,
  getContentResearchTrace,
  getContentResearchWorkflow,
  prepareContentResearchScope,
  retryContentResearchFormalResearch,
  resumeContentResearchFormalResearch,
  submitContentResearchBrandDecision,
  submitContentResearchContentDecision,
  isContentResearchReportPending,
  retryContentResearchPresearch,
  resolveContentResearchCoverage,
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

test("trace contract exposes stored decision identity and ordered safe execution facts", async () => {
  globalThis.fetch = (async () => jsonResponse({
    schema_version: "content_research_api_v1",
    workflow_run_id: "run_1",
    recoverable: true,
    duration_ms: 0,
    error_count: 0,
    retry_count: 0,
    traces: [],
    observation_events: [],
    workflow_events: [],
    runtime_steps: [],
    runtime_child_tasks: [],
    execution_units: [{
      id: "seu_1",
      state: "pending",
      recovery_state: "replayable",
      identity_schema: "execution_decision_identity_v1",
      identity_state: "canonical",
      identity_json: {
        schema: "execution_decision_identity_v1",
        coverage_snapshot_id: "scv_1",
      },
      facts: [{ attempt_no: 0, sequence_no: 1, kind: "decision_accepted", payload: {} }],
    }],
    usage_summary: {},
    external_api_summary: {},
    provider_operations: [],
    logical_checkpoints: [],
    usage_steps: [],
    usage_events: [],
  })) as typeof fetch;

  const trace = await getContentResearchTrace("run_1");

  assert.equal(trace.execution_units[0].identity_json.coverage_snapshot_id, "scv_1");
  assert.deepEqual(
    trace.execution_units[0].facts.map((fact) => [fact.attempt_no, fact.sequence_no, fact.kind]),
    [[0, 1, "decision_accepted"]],
  );
});

test("retries a transient Lite report network failure but not an HTTP failure", async () => {
  let calls = 0;
  globalThis.fetch = (async () => {
    calls += 1;
    if (calls === 1) throw new TypeError("Failed to fetch");
    return jsonResponse({ workflow_run_id: "run_1", publication: { state: "complete_verified_report" } });
  }) as typeof fetch;

  const report = await getContentResearchLiteReportWithRetry("run_1", [0]);

  assert.equal(calls, 2);
  assert.equal(report.workflow_run_id, "run_1");
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

test("structured subject confirmation stays on the same workflow run", async () => {
  let requestBody = "";
  globalThis.fetch = (async (_input, init) => {
    requestBody = String(init?.body ?? "");
    return jsonResponse({
      schema_version: "content_research_workflow_action_response_v1",
      workflow_run_id: "run_1",
      action: "confirm_subject_structure",
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

  const response = await confirmContentResearchSubjectStructure("run_1", {
    subject_structure_hash: "structure_1",
    core_object: "Apple 品牌",
    research_intent: "年轻人偏好",
    context_modifiers: "大学生",
  });

  assert.match(requestBody, /"action":"confirm_subject_structure"/);
  assert.match(requestBody, /"core_object":"Apple 品牌"/);
  assert.equal(response.result.workflow_run_id, "run_1");
});

test("confirmContentResearchBrief posts product structure confirmation with selected directions", async () => {
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
    primary_marketing_goal: "content_seeding",
    subject_structure_confirmation: {
      core_object: "徒步短裤",
      research_intent: "速干",
      context_modifiers: ["夏季"],
    },
  });

  assert.ok(requestUrl.endsWith("/content-research/workflows/run_1/actions"));
  assert.match(requestBody, /"action":"confirm_brief"/);
  assert.match(requestBody, /"selected_directions":\["product_marketing"\]/);
  assert.match(requestBody, /"primary_marketing_goal":"content_seeding"/);
  assert.match(requestBody, /"subject_structure_confirmation":\{"core_object":"徒步短裤","research_intent":"速干","context_modifiers":\["夏季"\]\}/);
  assert.equal(result.workflow_run_id, "run_1");
});

test("scope actions use the finalized server-owned payload contract", async () => {
  const requestBodies: string[] = [];
  globalThis.fetch = (async (_input, init) => {
    const requestBody = String(init?.body ?? "");
    requestBodies.push(requestBody);
    const action = JSON.parse(requestBody).action as string;
    if (action === "prepare_scope") {
      return jsonResponse(workflowActionPayload(action, {
        scope: scopeDraftPayload(),
      }));
    }
    if (action === "confirm_scope") {
      return jsonResponse(workflowActionPayload(action, {
        scope_contract: scopeContractPayload(),
        audit_event: scopeAuditPayload("scope_confirmed"),
      }));
    }
    return jsonResponse(workflowActionPayload(action, {
      report_mode: "limited",
      scope_contract: scopeContractPayload(),
      unmet_constraint_ids: ["season"],
      audit_event: scopeAuditPayload("coverage_resolved"),
    }));
  }) as typeof fetch;

  const draft = await prepareContentResearchScope("run_1", { direction_id: "product_marketing" });
  await confirmContentResearchScope("run_1", {
    scope_draft_id: draft.id,
    structure_hash: draft.structure_hash,
    query_groups: [
      { final_query: "白衬衫通勤穿搭" },
      { final_query: "长袖衬衫" },
      { final_query: "长袖衬衫 通勤" },
    ],
  });
  await resolveContentResearchCoverage("run_1", {
    scope_contract_version: 1,
    coverage_snapshot_id: "coverage_1",
    resolution: "expand_required_constraint",
    constraint_id: "season",
    supplementary_queries: ["夏季 防晒 长袖衬衫"],
  });

  assert.deepEqual(JSON.parse(requestBodies[0]), {
    action: "prepare_scope",
    payload: { direction_id: "product_marketing" },
  });
  assert.deepEqual(JSON.parse(requestBodies[1]), {
    action: "confirm_scope",
    payload: {
      scope_draft_id: "scope_draft_1",
      structure_hash: "structure_hash_1",
      query_groups: [
        { final_query: "白衬衫通勤穿搭" },
        { final_query: "长袖衬衫" },
        { final_query: "长袖衬衫 通勤" },
      ],
    },
  });
  assert.deepEqual(JSON.parse(requestBodies[2]), {
    action: "resolve_coverage",
    payload: {
      scope_contract_version: 1,
      coverage_snapshot_id: "coverage_1",
      resolution: "expand_required_constraint",
      constraint_id: "season",
      supplementary_queries: ["夏季 防晒 长袖衬衫"],
    },
  });
});

test("getContentResearchScope reads the persisted Scope projection and optional version", async () => {
  const requestUrls: string[] = [];
  globalThis.fetch = (async (input) => {
    requestUrls.push(String(input));
    return jsonResponse({
      schema_version: "content_research_api_v1",
      workflow_run_id: "run_1",
      draft: scopeDraftPayload(),
      scope_contract: scopeContractPayload(),
      audit_events: [scopeAuditPayload("scope_suggested"), scopeAuditPayload("scope_confirmed")],
    });
  }) as typeof fetch;

  const latest = await getContentResearchScope("run_1");
  const versioned = await getContentResearchScope("run_1", 1);

  assert.ok(requestUrls[0].endsWith("/content-research/workflows/run_1/scope"));
  assert.ok(requestUrls[1].endsWith("/content-research/workflows/run_1/scope?version=1"));
  assert.equal(latest.scope_contract.query_groups[0].suggested_query, "夏季 长袖衬衫 通勤");
  assert.equal(latest.scope_contract.query_groups[0].final_query, "白衬衫通勤穿搭");
  assert.equal(latest.scope_contract.query_groups[0].execution_role, "exploratory");
  assert.equal(versioned.scope_contract.version, 1);
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

test("getContentResearchDirectionEvidence reads the safe direction evidence projection", async () => {
  let requestUrl = "";
  globalThis.fetch = (async (input) => {
    requestUrl = String(input);
    return jsonResponse({
      workflow_run_id: "run_1",
      direction_id: "product_marketing",
      candidates: [{ title: "防晒长袖实测", source_url: "https://example.test/note" }],
      selections: [],
      exclusions: [{ canonical_source_id: "note_1", reasons: ["core_entity_not_supported"] }],
      packets: [],
    });
  }) as typeof fetch;

  const evidence = await getContentResearchDirectionEvidence("run_1", "product_marketing");

  assert.ok(requestUrl.endsWith("/content-research/workflows/run_1/directions/product_marketing/evidence?limit=50"));
  assert.equal(evidence.candidates[0].title, "防晒长袖实测");
  assert.equal(evidence.exclusions[0].reasons?.[0], "core_entity_not_supported");
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

function workflowActionPayload(action: string, result: Record<string, unknown>) {
  return {
    schema_version: "content_research_workflow_action_response_v1",
    workflow_run_id: "run_1",
    action,
    status: "completed",
    result,
    execution_mode: "local",
    remote_run_id: null,
    local_cache_id: "rb_1",
    sync_status: "local_only",
  };
}

function scopeDraftPayload() {
  return {
    id: "scope_draft_1",
    workflow_run_id: "run_1",
    research_plan_id: "plan_1",
    structure_hash: "structure_hash_1",
    constraints: [
      { id: "core_object", label: "核心对象", value: "长袖衬衫", mode: "required", allowed_aliases: [] },
      { id: "season", label: "季节", value: "夏季", mode: "required", allowed_aliases: [] },
    ],
    query_groups: [
      { suggested_query: "夏季 长袖衬衫 通勤", final_query: "夏季长袖通勤衬衫", targeted_required_terms: ["夏季", "长袖衬衫", "通勤"] },
      { suggested_query: "长袖衬衫", final_query: "长袖衬衫", targeted_required_terms: ["长袖衬衫"] },
      { suggested_query: "长袖衬衫 通勤", final_query: "长袖衬衫 通勤", targeted_required_terms: ["长袖衬衫", "通勤"] },
    ],
    created_at: "2026-08-18T00:00:00+08:00",
  };
}

function scopeContractPayload() {
  return {
    id: "scope_contract_1",
    workflow_run_id: "run_1",
    research_plan_id: "plan_1",
    version: 1,
    schema_version: "content_research_scope_contract_v1",
    constraints: scopeDraftPayload().constraints,
    query_groups: [
      { id: "group_1", suggested_query: "夏季 长袖衬衫 通勤", final_query: "白衬衫通勤穿搭", origin: "user_edited", execution_role: "exploratory" },
      { id: "group_2", suggested_query: "长袖衬衫", final_query: "长袖衬衫", origin: "system_suggested", execution_role: "coverage" },
      { id: "group_3", suggested_query: "长袖衬衫 通勤", final_query: "长袖衬衫 通勤", origin: "system_suggested", execution_role: "coverage" },
    ],
    created_at: "2026-08-18T00:01:00+08:00",
  };
}

function scopeAuditPayload(eventName: string) {
  return {
    id: `audit_${eventName}`,
    workflow_run_id: "run_1",
    scope_contract_id: "scope_contract_1",
    scope_contract_version: 1,
    event_name: eventName,
    payload: {},
    created_at: "2026-08-18T00:02:00+08:00",
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
