import assert from "node:assert/strict";
import test from "node:test";

import {
  startContentResearchFormalResearch,
  confirmContentResearchBrief,
  createContentResearchPresearch,
  endContentResearchWorkflow,
  getContentResearchDecisions,
  getContentResearchEvidenceBundle,
  getContentResearchPublishedReport,
  getContentResearchTrace,
  getContentResearchWorkflow,
  retryContentResearchFormalResearch,
  submitContentResearchBrandDecision,
  submitContentResearchContentDecision,
} from "./content-research-api.ts";

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

test("getContentResearchPublishedReport fetches only the R4 materialized report contract", async () => {
  let requestUrl = "";
  globalThis.fetch = (async (input) => {
    requestUrl = String(input);
    return jsonResponse({
      schema_version: "content_research_api_v1",
      workflow_run_id: "run_1",
      workflow_terminal_state: "succeeded",
      publication_state: "partial_verified_report",
      artifact: { artifact_id: "artifact_1" }, publication: {}, sections: [], citation_groups: [],
      citation_total: 0, citation_offset: 0, citation_limit: 50, claim_cards: [], weak_signals: [],
      cross_direction_records: [], aggregate_claims: [], limitations_recovery: [], trace: {},
    });
  }) as typeof fetch;

  const report = await getContentResearchPublishedReport("run_1", { citationOffset: 10, citationLimit: 5 });

  assert.ok(requestUrl.endsWith("/content-research/workflows/run_1/report?citation_offset=10&citation_limit=5"));
  assert.equal(report.publication_state, "partial_verified_report");
  assert.equal(report.workflow_terminal_state, "succeeded");
});

test("getContentResearchEvidenceBundle fetches expanded bundle", async () => {
  let requestUrl = "";
  globalThis.fetch = (async (input) => {
    requestUrl = String(input);
    return jsonResponse(evidenceBundlePayload());
  }) as typeof fetch;

  const result = await getContentResearchEvidenceBundle("eb_1");

  assert.ok(requestUrl.endsWith("/content-research/evidence-bundles/eb_1"));
  assert.equal(result.bundle_id, "eb_1");
  assert.equal(result.source_links.length, 1);
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

test("retryContentResearchFormalResearch sends retry workflow action", async () => {
  let requestBody = "";
  globalThis.fetch = (async (_input, init) => {
    requestBody = String(init?.body ?? "");
    return jsonResponse({
      schema_version: "content_research_workflow_action_response_v1",
      workflow_run_id: "run_1",
      action: "retry_formal_research",
      status: "completed",
      result: {
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
      execution_mode: "local",
      remote_run_id: null,
      local_cache_id: "rb_1",
      sync_status: "local_only",
    });
  }) as typeof fetch;

  const result = await retryContentResearchFormalResearch("run_1", {});

  assert.match(requestBody, /"action":"retry_formal_research"/);
  assert.equal(result.status, "completed");
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
  await assert.rejects(() => getContentResearchTrace("run_missing"), /Content research workflow not found/);
  await assert.rejects(() => getContentResearchEvidenceBundle("eb_missing"), /Content research workflow not found/);
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

function tracePayload() {
  return {
    workflow_run_id: "run_1",
    thread_id: "thread_1",
    current_stage: "formal_research",
    run_status: "running",
    recoverable: true,
    duration_ms: 10,
    error_count: 0,
    retry_count: 0,
    traces: [],
    observation_events: [],
    workflow_events: [],
    runtime_steps: [],
    runtime_child_tasks: [],
    usage_summary: { total_tokens: 0 },
    usage_steps: [],
    usage_events: [],
  };
}

function resultsPayload() {
  return {
    schema_version: "content_research_api_v1",
    snapshot_id: "rrs_1",
    workflow_run_id: "run_1",
    research_brief_id: "rb_1",
    research_plan_id: "rp_1",
    snapshot_version: "1",
    result_type: "topic_research",
    status: "ready",
    title: "调研结果",
    executive_summary: "通勤场景值得优先研究。",
    items: [
      {
        result_item_id: "ri_1",
        claim: "通勤场景值得优先研究。",
        summary: "通勤场景值得优先研究。",
        evidence_bundle_id: "eb_1",
        evidence_bundle_ids: ["eb_1"],
        support_level: "medium",
        claim_status: "supported",
        priority: { label: "high_priority", rank: 1 },
        priority_label: "high_priority",
        evidence_state: "partially_supported",
        evidence_grade: "B",
        claim_scope: { allowed: ["Use as a bounded research signal."] },
        next_action: { type: "content_experiment" },
        decision_card: {},
        risk_flags: [],
        missing_evidence: [],
        source_count: 2,
      },
    ],
    findings: [],
    recommendations: [],
    evidence_bundle_ids: ["eb_1"],
    claim_count: 1,
    supported_claim_count: 1,
    unsupported_claim_count: 0,
    citation_coverage_score: 0.8,
    faithfulness_score: 0.76,
    answer_relevancy_score: 0.72,
    derivation_completeness_score: 1,
    evidence_boundary_calibration_score: 1,
    decision_summary: {},
    decision_cards: [],
    priority_summary: {},
    evidence_boundary_summary: {},
    limitations: [],
    abstentions: [],
    metadata: {},
    created_at: "2026-07-06T00:00:00+08:00",
  };
}

function evidenceBundlePayload() {
  return {
    schema_version: "content_research_api_v1",
    bundle_id: "eb_1",
    workflow_run_id: "run_1",
    research_brief_id: "rb_1",
    research_plan_id: "rp_1",
    research_direction_id: "rd_1",
    status: "ready",
    bundle_type: "research_direction",
    bundle_version: "v1",
    summary: "通勤场景证据包",
    coverage: { source_count: 2 },
    retrieval_metrics: {},
    faithfulness_metrics: {},
    cross_source_metrics: {},
    contradiction_summary: {},
    citation_coverage: {},
    unsupported_claim_count: 0,
    missing_evidence: [],
    priority_policy_id: "pp_content_research_default_v1",
    evidence_boundary_policy_id: "ebp_content_research_default_v1",
    decision_card: {},
    priority: { label: "high_priority" },
    evidence_state: "partially_supported",
    evidence_grade: "B",
    claim_scope: { allowed: ["Use as a bounded research signal."] },
    next_action: { type: "content_experiment" },
    items: [],
    evidence_by_role: {
      supporting_fact: [{ id: "ev_1", title: "通勤背包测评" }],
    },
    lineage_by_evidence_id: { ev_1: [{ transformation_type: "captured" }] },
    source_links: [{ evidence_id: "ev_1", source_url: "https://example.com/note" }],
    metadata: {},
    created_at: "2026-07-06T00:00:00+08:00",
    updated_at: "2026-07-06T00:00:00+08:00",
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
