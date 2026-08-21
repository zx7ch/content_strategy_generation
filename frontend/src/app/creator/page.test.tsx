import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { JSDOM } from "jsdom";
import type { ContentResearchCoverageResolution, ContentResearchScopeProjection } from "../../lib/content-research-api.ts";

test("Creator always renders the content research entry instead of preview-gating it", () => {
  const source = readFileSync(new URL("./page.tsx", import.meta.url), "utf8");

  assert.doesNotMatch(source, /\{F003_LITE_PREVIEW_ENABLED\s*&&\s*\(/);
  assert.match(source, /内容调研/);
});

test("Creator restores the durable presearch Trace when the request disconnects after acceptance", () => {
  const source = readFileSync(new URL("./page.tsx", import.meta.url), "utf8");

  assert.match(source, /restoreInterruptedContentResearchRun/);
  assert.match(source, /getThreadTimeline\(threadId\)/);
  assert.match(source, /预检索连接中断，已恢复本次运行的 Trace/);
});

test("evidence-only reports expose a candidate audit instead of only an insufficient summary", () => {
  const source = readFileSync(new URL("./page.tsx", import.meta.url), "utf8");

  assert.match(source, /查看候选与筛选/);
  assert.match(source, /候选笔记与筛选/);
  assert.match(source, /检索来源（命中查询组）/);
  assert.match(source, /表示该笔记由本轮哪组检索发现，不要求正文逐字包含完整检索词/);
  assert.match(source, /导出 JSON/);
  assert.match(source, /getContentResearchDirectionEvidence/);
});

test("Xiaohongshu login card exposes both setup paths and only redacted status metadata", async () => {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost" });
  Object.assign(globalThis, {
    window: dom.window,
    document: dom.window.document,
    HTMLElement: dom.window.HTMLElement,
    HTMLButtonElement: dom.window.HTMLButtonElement,
    Event: dom.window.Event,
    IS_REACT_ACT_ENVIRONMENT: true,
  });
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: dom.window.navigator });
  const previousFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({
    authenticated: false,
    source: "manual_cookie",
    updated_at: "2026-08-12T00:00:00+00:00",
    failure_code: "auth_required",
  }), { status: 200, headers: { "Content-Type": "application/json" } });
  const React = await import("react");
  Object.assign(globalThis, { React });
  const { act } = React;
  const { createRoot } = await import("react-dom/client");
  const { setWorkspaceContext } = await import("@/lib/api.ts");
  const { XiaohongshuLoginCard } = await import("@/components/content-research/XiaohongshuLoginCard.tsx");
  setWorkspaceContext("workspace_test", "user_test");
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  await act(async () => { root.render(React.createElement(XiaohongshuLoginCard)); });
  await act(async () => { await Promise.resolve(); });

  assert.match(container.textContent ?? "", /扫码登录/);
  assert.match(container.textContent ?? "", /粘贴 Cookie/);
  assert.match(container.textContent ?? "", /登录状态已失效/);
  assert.match(container.textContent ?? "", /更新于/);
  assert.doesNotMatch(container.textContent ?? "", /very-secret/);

  await act(async () => { root.unmount(); });
  container.remove();
  globalThis.fetch = previousFetch;
});

test("Xiaohongshu login keeps a rejected Cookie editable and blocks a duplicate save", async () => {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost" });
  Object.assign(globalThis, {
    window: dom.window,
    document: dom.window.document,
    HTMLElement: dom.window.HTMLElement,
    HTMLButtonElement: dom.window.HTMLButtonElement,
    HTMLInputElement: dom.window.HTMLInputElement,
    Event: dom.window.Event,
    IS_REACT_ACT_ENVIRONMENT: true,
  });
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: dom.window.navigator });
  const previousFetch = globalThis.fetch;
  let resolveSave: ((response: Response) => void) | undefined;
  let requestCount = 0;
  globalThis.fetch = ((_: RequestInfo | URL, init?: RequestInit) => {
    requestCount += 1;
    if ((init?.method ?? "GET") === "PUT") {
      return new Promise<Response>((resolve) => { resolveSave = resolve; });
    }
    return Promise.resolve(new Response(JSON.stringify({ authenticated: false }), {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
  }) as typeof fetch;
  const React = await import("react");
  Object.assign(globalThis, { React });
  const { act } = React;
  const { createRoot } = await import("react-dom/client");
  const { setWorkspaceContext } = await import("@/lib/api.ts");
  const { XiaohongshuLoginCard } = await import("@/components/content-research/XiaohongshuLoginCard.tsx");
  setWorkspaceContext("workspace_test", "user_test");
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  await act(async () => { root.render(React.createElement(XiaohongshuLoginCard)); });
  await act(async () => { await Promise.resolve(); });

  const input = container.querySelector("#xhs-cookie") as HTMLInputElement;
  await act(async () => {
    const valueSetter = Object.getOwnPropertyDescriptor(
      dom.window.HTMLInputElement.prototype,
      "value",
    )?.set;
    valueSetter?.call(input, "a1=incomplete");
    input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  });
  const save = [...container.querySelectorAll("button")].find((button) => button.textContent === "保存 Cookie") as HTMLButtonElement;
  assert.equal(save.disabled, false);
  await act(async () => { save.click(); await Promise.resolve(); });
  assert.equal(requestCount, 2);
  assert.equal(save.disabled, true);
  assert.equal(save.textContent, "保存中…");

  await act(async () => {
    resolveSave?.(new Response(JSON.stringify({
      error_code: "invalid_cookie", error_message: "Cookie 格式无效",
    }), { status: 422, headers: { "Content-Type": "application/json" } }));
    await Promise.resolve();
  });
  assert.equal(input.value, "a1=incomplete");
  assert.match(container.textContent ?? "", /Cookie 格式无效/);

  await act(async () => { root.unmount(); });
  container.remove();
  globalThis.fetch = previousFetch;
});

test("model setup explains the action a Creator can take when a Key is rejected", async () => {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost" });
  Object.assign(globalThis, {
    window: dom.window,
    document: dom.window.document,
    HTMLElement: dom.window.HTMLElement,
    HTMLButtonElement: dom.window.HTMLButtonElement,
    HTMLInputElement: dom.window.HTMLInputElement,
    Event: dom.window.Event,
    IS_REACT_ACT_ENVIRONMENT: true,
  });
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: dom.window.navigator });
  const previousFetch = globalThis.fetch;
  let requestCount = 0;
  globalThis.fetch = async () => {
    requestCount += 1;
    const payload = requestCount === 1
      ? { source: "system_default", status: "not_configured", base_url: "", model: "", api_key_configured: false }
      : { source: "candidate", status: "invalid", base_url: "https://api.openai.com/v1", model: "gpt-4o-mini", api_key_configured: true, error_code: "llm_auth_invalid" };
    return new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
  };
  const React = await import("react");
  Object.assign(globalThis, { React });
  const { act } = React;
  const { createRoot } = await import("react-dom/client");
  const { setWorkspaceContext } = await import("@/lib/api.ts");
  const { ModelServiceCard } = await import("@/components/content-research/ModelServiceCard.tsx");
  setWorkspaceContext("workspace_test", "user_test");
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(React.createElement(ModelServiceCard, {
      recoveryPending: false,
      onContinue: () => undefined,
      onConfigurationChanged: () => undefined,
    }));
  });
  await act(async () => { await Promise.resolve(); });

  const buttonNamed = (name: string) =>
    [...container.querySelectorAll("button")].find((button) => button.textContent === name);
  await act(async () => { buttonNamed("配置模型")?.click(); });
  for (const [label, value] of Object.entries({
    "Base URL": "https://api.openai.com/v1",
    "模型": "gpt-4o-mini",
    "API Key": "never-render-this-key",
  })) {
    const input = container.querySelector(`input[aria-label="${label}"]`) as HTMLInputElement;
    input.value = value;
    input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  }
  await act(async () => { buttonNamed("测试连接")?.click(); await Promise.resolve(); });

  assert.match(container.textContent ?? "", /API Key 无效、已撤销，或无权调用该模型服务/);
  assert.doesNotMatch(container.textContent ?? "", /never-render-this-key/);

  await act(async () => { root.unmount(); });
  container.remove();
  globalThis.fetch = previousFetch;
});

test("Brief confirmation requires an explicit marketing goal before enabling confirmation", async () => {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", {
    url: "http://localhost",
  });
  Object.assign(globalThis, {
    window: dom.window,
    document: dom.window.document,
    HTMLElement: dom.window.HTMLElement,
    HTMLSelectElement: dom.window.HTMLSelectElement,
    HTMLButtonElement: dom.window.HTMLButtonElement,
    Event: dom.window.Event,
    IS_REACT_ACT_ENVIRONMENT: true,
  });
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: dom.window.navigator,
  });
  const React = await import("react");
  const { act } = React;
  const { createRoot } = await import("react-dom/client");
  const { ContentResearchIntentCard } = await import("./page-components.ts");
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(
      React.createElement(ContentResearchIntentCard, {
        intent: {
          seed: "夏季凉感T恤",
          presearch: {
            attempt_id: "attempt_1",
            workflow_run_id: "run_1",
            brief_id: "brief_1",
            status: "completed",
            subject_confirmation: "确认夏季凉感T恤",
            competitor_tags: [],
            research_directions: [],
            direction_catalog: ["product_marketing"],
            custom_research_question: "",
            timeout_status: "none",
            fallback_used: false,
            subject_structure: {
              canonical_subject: "夏季凉感T恤",
              core_entities: [{ canonical_name: "T恤" }],
              research_intents: ["凉感"],
            },
            subject_structure_state: "confirmed",
            subject_structure_reason_codes: [],
          },
        },
        onConfirmed: () => undefined,
        onPresearchUpdated: () => undefined,
        onError: () => undefined,
      }),
    );
  });

  const buttonNamed = (name: string) =>
    [...container.querySelectorAll("button")].find((button) => button.textContent === name);
  const confirm = buttonNamed("确认并开始调研") as HTMLButtonElement;
  assert.ok(confirm);
  assert.equal(confirm.disabled, true);

  await act(async () => {
    buttonNamed("准确，继续")?.click();
    buttonNamed("产品营销")?.click();
  });
  assert.equal(confirm.disabled, true);

  const goalSelector = container.querySelector(
    'select[aria-label="产品营销目标"]',
  ) as HTMLSelectElement;
  await act(async () => {
    goalSelector.value = "content_seeding";
    goalSelector.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  });
  assert.equal(confirm.disabled, false);

  await act(async () => {
    root.unmount();
  });
  container.remove();
});

test("Creator submits only final-query edits for every server-provided Draft group", async () => {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost" });
  Object.assign(globalThis, {
    window: dom.window,
    document: dom.window.document,
    HTMLElement: dom.window.HTMLElement,
    HTMLInputElement: dom.window.HTMLInputElement,
    HTMLButtonElement: dom.window.HTMLButtonElement,
    Event: dom.window.Event,
    IS_REACT_ACT_ENVIRONMENT: true,
  });
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: dom.window.navigator });
  const React = await import("react");
  const { act } = React;
  const { createRoot } = await import("react-dom/client");
  const { default: CreatorPage } = await import("./page.tsx");
  const { ContentResearchScopeCard } = CreatorPage;
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  let submitted: unknown = null;

  await act(async () => {
    root.render(React.createElement(ContentResearchScopeCard, {
      draft: scopeDraftFixture(),
      projection: null,
      busy: false,
      onConfirm: (payload: unknown) => { submitted = payload; },
      onResolve: () => undefined,
    }));
  });

  const inputs = [...container.querySelectorAll('input[aria-label^="检索组 "]')] as HTMLInputElement[];
  assert.equal(inputs.length, 3);
  assert.equal(inputs[0].value, "夏季长袖通勤衬衫");
  assert.match(container.textContent ?? "", /系统建议：夏季 长袖衬衫 通勤/);
  await act(async () => {
    const valueSetter = Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, "value")?.set;
    valueSetter?.call(inputs[0], "白衬衫通勤穿搭");
    inputs[0].dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  });
  const confirm = [...container.querySelectorAll("button")].find((button) => button.textContent === "确认并开始调研");
  await act(async () => { confirm?.click(); });

  assert.deepEqual(submitted, {
    scope_draft_id: "scope_draft_1",
    structure_hash: "structure_hash_1",
    query_groups: [
      { final_query: "白衬衫通勤穿搭" },
      { final_query: "长袖衬衫" },
      { final_query: "长袖衬衫 通勤" },
    ],
  });

  await act(async () => { root.unmount(); });
  container.remove();
});

test("Creator renders only server-declared Coverage actions and reasons", async () => {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost" });
  Object.assign(globalThis, {
    window: dom.window,
    document: dom.window.document,
    HTMLElement: dom.window.HTMLElement,
    HTMLInputElement: dom.window.HTMLInputElement,
    HTMLButtonElement: dom.window.HTMLButtonElement,
    Event: dom.window.Event,
    IS_REACT_ACT_ENVIRONMENT: true,
  });
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: dom.window.navigator });
  const React = await import("react");
  const { act } = React;
  const { createRoot } = await import("react-dom/client");
  const { default: CreatorPage } = await import("./page.tsx");
  const { ContentResearchScopeCard } = CreatorPage;
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  const resolutions: unknown[] = [];

  await act(async () => {
    root.render(React.createElement(ContentResearchScopeCard, {
      draft: null,
      projection: scopeProjectionFixture({
        allowedResolutions: ["generate_limited_report"],
      }),
      busy: false,
      onConfirm: () => undefined,
      onResolve: (payload: unknown) => { resolutions.push(payload); },
    }));
  });

  assert.match(container.textContent ?? "", /白衬衫通勤穿搭/);
  assert.match(container.textContent ?? "", /探索补充/);
  assert.match(container.textContent ?? "", /基于现有证据生成受限报告/);
  assert.match(container.textContent ?? "", /夏季条件的合格样本不足/);
  assert.doesNotMatch(container.textContent ?? "", /继续补充夏季样本/);
  assert.doesNotMatch(container.textContent ?? "", /放宽夏季约束/);
  assert.doesNotMatch(container.textContent ?? "", /研究结论/);
  const buttonNamed = (name: string) =>
    [...container.querySelectorAll("button")].find((button) => button.textContent === name);
  await act(async () => {
    buttonNamed("基于现有证据生成受限报告")?.click();
  });

  assert.deepEqual(resolutions, [
    { scope_contract_version: 1, coverage_snapshot_id: "coverage_1", resolution: "generate_limited_report" },
  ]);

  await act(async () => { root.unmount(); });
  container.remove();
});

test("Creator restores a persisted pending Draft after reload", async () => {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost" });
  Object.assign(globalThis, {
    window: dom.window,
    document: dom.window.document,
    HTMLElement: dom.window.HTMLElement,
    HTMLInputElement: dom.window.HTMLInputElement,
    HTMLButtonElement: dom.window.HTMLButtonElement,
    Event: dom.window.Event,
    IS_REACT_ACT_ENVIRONMENT: true,
  });
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: dom.window.navigator });
  const React = await import("react");
  const { act } = React;
  const { createRoot } = await import("react-dom/client");
  const { default: CreatorPage } = await import("./page.tsx");
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);

  await act(async () => {
    root.render(React.createElement(CreatorPage.ContentResearchScopeCard, {
      draft: null,
      projection: {
        ...scopeProjectionFixture(),
        state: "awaiting_confirmation",
        scope_contract: null,
        coverage_snapshot: null,
        allowed_resolutions: [],
        decision_recovery: null,
        execution_unit: null,
      },
      busy: false,
      onConfirm: () => undefined,
      onResolve: () => undefined,
    }));
  });

  assert.equal((container.querySelector('input[aria-label="检索组 1"]') as HTMLInputElement).value, "夏季长袖通勤衬衫");
  assert.ok([...container.querySelectorAll("button")].some((button) => button.textContent === "确认并开始调研"));

  await act(async () => { root.unmount(); });
  container.remove();
});

test("Creator reveals supplementary queries only for a server-allowed expand decision", async () => {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost" });
  Object.assign(globalThis, {
    window: dom.window,
    document: dom.window.document,
    HTMLElement: dom.window.HTMLElement,
    HTMLInputElement: dom.window.HTMLInputElement,
    HTMLButtonElement: dom.window.HTMLButtonElement,
    Event: dom.window.Event,
    IS_REACT_ACT_ENVIRONMENT: true,
  });
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: dom.window.navigator });
  const React = await import("react");
  const { act } = React;
  const { createRoot } = await import("react-dom/client");
  const { default: CreatorPage } = await import("./page.tsx");
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);

  await act(async () => {
    root.render(React.createElement(CreatorPage.ContentResearchScopeCard, {
      draft: null,
      projection: scopeProjectionFixture({
        allowedResolutions: ["expand_required_constraint", "generate_limited_report", "relax_constraint"],
      }),
      busy: false,
      onConfirm: () => undefined,
      onResolve: () => undefined,
    }));
  });

  assert.equal(container.querySelectorAll('input[aria-label^="补充检索词 "]').length, 0);
  await act(async () => {
    [...container.querySelectorAll("button")].find((button) => button.textContent === "继续补充夏季样本")?.click();
  });
  assert.equal(container.querySelectorAll('input[aria-label^="补充检索词 "]').length, 2);

  await act(async () => { root.unmount(); });
  container.remove();
});

test("Creator submits the constraint ids declared for each Coverage action", async () => {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost" });
  Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, HTMLInputElement: dom.window.HTMLInputElement, HTMLButtonElement: dom.window.HTMLButtonElement, Event: dom.window.Event, IS_REACT_ACT_ENVIRONMENT: true });
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: dom.window.navigator });
  const React = await import("react");
  const { act } = React;
  const { createRoot } = await import("react-dom/client");
  const { default: CreatorPage } = await import("./page.tsx");
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  const submitted: unknown[] = [];
  const projection = scopeProjectionFixture({
    allowedResolutions: ["expand_required_constraint", "relax_constraint"],
    expandConstraintIds: ["season"],
    relaxConstraintIds: ["core_object"],
  });

  await act(async () => {
    root.render(React.createElement(CreatorPage.ContentResearchScopeCard, {
      draft: null,
      projection,
      busy: false,
      onConfirm: () => undefined,
      onResolve: (payload: unknown) => submitted.push(payload),
    }));
  });

  await act(async () => { [...container.querySelectorAll("button")].find((button) => button.textContent === "放宽长袖衬衫约束")?.click(); });
  await act(async () => { [...container.querySelectorAll("button")].find((button) => button.textContent === "继续补充夏季样本")?.click(); });
  const input = container.querySelector('input[aria-label="补充检索词 1"]') as HTMLInputElement;
  await act(async () => {
    const valueSetter = Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, "value")?.set;
    valueSetter?.call(input, "夏季 防晒 长袖");
    input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  });
  await act(async () => { [...container.querySelectorAll("button")].find((button) => button.textContent === "提交补搜决定")?.click(); });

  assert.equal((submitted[0] as { constraint_id: string }).constraint_id, "core_object");
  assert.equal((submitted[1] as { constraint_id: string }).constraint_id, "season");
  await act(async () => { root.unmount(); });
  container.remove();
});

test("Creator shows manual recovery and no automatic replay for an unknown outcome", async () => {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost" });
  Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event, IS_REACT_ACT_ENVIRONMENT: true });
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: dom.window.navigator });
  const React = await import("react");
  const { act } = React;
  const { createRoot } = await import("react-dom/client");
  const { default: CreatorPage } = await import("./page.tsx");
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);

  await act(async () => {
    root.render(React.createElement(CreatorPage.ContentResearchScopeCard, {
      draft: null,
      projection: {
        ...scopeProjectionFixture({ allowedResolutions: [] }),
        decision_recovery: null,
        execution_unit: {
          id: "seu_unknown",
          state: "outcome_unknown",
          attempt_no: 1,
          recovery_state: "outcome_unknown",
          allowed_actions: [],
          trace_summary: { fact_count: 3, attempt_count: 1, last_fact_kind: "outcome_unknown" },
        },
      },
      busy: false,
      onConfirm: () => undefined,
      onResolve: () => undefined,
    }));
  });

  assert.match(container.textContent ?? "", /需要人工确认执行结果/);
  assert.match(container.textContent ?? "", /不会自动重放/);
  assert.equal(container.querySelectorAll('section[aria-label="覆盖不足决策"] button').length, 0);

  await act(async () => { root.unmount(); });
  container.remove();
});

test("Creator replays a known retryable failure only through the server-declared command", async () => {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost" });
  Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, HTMLButtonElement: dom.window.HTMLButtonElement, Event: dom.window.Event, IS_REACT_ACT_ENVIRONMENT: true });
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: dom.window.navigator });
  const React = await import("react");
  const { act } = React;
  const { createRoot } = await import("react-dom/client");
  const { default: CreatorPage } = await import("./page.tsx");
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  const submitted: unknown[] = [];
  const replayRequest = {
    scope_contract_version: 1,
    coverage_snapshot_id: "coverage_1",
    resolution: "expand_required_constraint" as const,
    constraint_id: "season",
    supplementary_queries: ["夏季 防晒 长袖衬衫"],
  };

  await act(async () => {
    root.render(React.createElement(CreatorPage.ContentResearchScopeCard, {
      draft: null,
      projection: {
        ...scopeProjectionFixture({ allowedResolutions: [] }),
        decision_recovery: null,
        execution_unit: {
          id: "seu_retry",
          state: "failed",
          attempt_no: 1,
          recovery_state: "replayable",
          allowed_actions: [{ action: "replay_coverage_decision", available: true, request: replayRequest }],
          trace_summary: { fact_count: 4, attempt_count: 1, last_fact_kind: "provider_outcome_recorded" },
        },
      },
      busy: false,
      onConfirm: () => undefined,
      onResolve: (payload: unknown) => submitted.push(payload),
    }));
  });

  const replay = [...container.querySelectorAll("button")].find((button) => button.textContent === "重试本次已保存决定");
  assert.ok(replay);
  await act(async () => { replay.click(); });
  assert.deepEqual(submitted, [replayRequest]);

  await act(async () => { root.unmount(); });
  container.remove();
});

test("Creator thread restoration reads persisted Scope instead of browser-owned scope state", () => {
  const source = readFileSync(new URL("./page.tsx", import.meta.url), "utf8");

  assert.match(source, /getContentResearchScope\(runIdForThread\)/);
  assert.match(source, /scopeProjection/);
});

test("Creator request epochs reject late responses from a previously selected run", async () => {
  const { ContentResearchRequestEpoch } = await import("./page-state.ts");
  const requests = new ContentResearchRequestEpoch();
  requests.activate("run_a");
  const oldScope = requests.ticket("run_a");
  const oldReport = requests.ticket("run_a");

  requests.activate("run_b");
  const currentScope = requests.ticket("run_b");

  assert.equal(requests.accepts(oldScope), false);
  assert.equal(requests.accepts(oldReport), false);
  assert.equal(requests.accepts(currentScope), true);

  const earlierSameRunScope = requests.ticket("run_b", "scope");
  const newerSameRunScope = requests.ticket("run_b", "scope");
  assert.equal(requests.accepts(earlierSameRunScope), false);
  assert.equal(requests.accepts(newerSameRunScope), true);
});

test("report presentation reads query groups from the server-frozen Scope", async () => {
  const { frozenReportScopeQueries } = await import("./page-state.ts");

  assert.deepEqual(frozenReportScopeQueries({
    frozen_scope: {
      scope_contract_version: 1,
      query_groups: [
        { id: "frozen_1", final_query: "夏季 长袖衬衫 通勤" },
        { id: "frozen_2", final_query: "夏季 防晒 长袖衬衫" },
      ],
    },
  }), ["夏季 长袖衬衫 通勤", "夏季 防晒 长袖衬衫"]);
});

function scopeDraftFixture() {
  return {
    id: "scope_draft_1",
    workflow_run_id: "run_1",
    research_plan_id: "plan_1",
    structure_hash: "structure_hash_1",
    constraints: [
      { id: "core_object", label: "核心对象", value: "长袖衬衫", mode: "required" as const, allowed_aliases: [] },
      { id: "season", label: "季节", value: "夏季", mode: "required" as const, allowed_aliases: [] },
    ],
    query_groups: [
      { suggested_query: "夏季 长袖衬衫 通勤", final_query: "夏季长袖通勤衬衫", targeted_required_terms: ["夏季", "长袖衬衫", "通勤"] },
      { suggested_query: "长袖衬衫", final_query: "长袖衬衫", targeted_required_terms: ["长袖衬衫"] },
      { suggested_query: "长袖衬衫 通勤", final_query: "长袖衬衫 通勤", targeted_required_terms: ["长袖衬衫", "通勤"] },
    ],
    created_at: "2026-08-18T00:00:00+08:00",
  };
}

function scopeProjectionFixture(options: {
  allowedResolutions?: ContentResearchCoverageResolution[];
  expandConstraintIds?: string[];
  relaxConstraintIds?: string[];
} = {}): ContentResearchScopeProjection {
  const allowed = new Set<ContentResearchCoverageResolution>(options.allowedResolutions ?? [
    "expand_required_constraint",
    "generate_limited_report",
    "relax_constraint",
  ]);
  return {
    schema_version: "content_research_api_v1",
    workflow_run_id: "run_1",
    state: "confirmed" as const,
    draft: scopeDraftFixture(),
    scope_contract: {
      id: "scope_contract_1",
      workflow_run_id: "run_1",
      research_plan_id: "plan_1",
      version: 1,
      schema_version: "content_research_scope_contract_v1",
      constraints: scopeDraftFixture().constraints,
      query_groups: [
        { id: "group_1", suggested_query: "夏季 长袖衬衫 通勤", final_query: "白衬衫通勤穿搭", origin: "user_edited" as const, execution_role: "exploratory" as const },
        { id: "group_2", suggested_query: "长袖衬衫", final_query: "长袖衬衫", origin: "system_suggested" as const, execution_role: "coverage" as const },
      ],
      created_at: "2026-08-18T00:01:00+08:00",
    },
    audit_events: [{
      id: "audit_coverage",
      workflow_run_id: "run_1",
      scope_contract_id: "scope_contract_1",
      scope_contract_version: 1,
      event_name: "coverage_evaluated",
      payload: {
        coverage_snapshot_id: "coverage_1",
        state: "awaiting_scope_decision",
        constraint_counts: { season: { matched_candidate_count: 1, independent_author_count: 1, required: true } },
        unmet_constraint_ids: ["season"],
        reason_codes: ["required_constraint_coverage_unmet:season"],
      },
      created_at: "2026-08-18T00:02:00+08:00",
    }],
    allowed_actions: [
      { action: "prepare_scope", available: false, unavailable_reason: "coverage_decision_required", recovery_action: "resolve_coverage" },
      { action: "confirm_scope", available: false, unavailable_reason: "coverage_decision_required", recovery_action: "resolve_coverage" },
      { action: "resolve_coverage", available: true, scope_contract_version: 1, coverage_snapshot_id: "coverage_1" },
    ],
    coverage_snapshot: {
      id: "coverage_1",
      workflow_run_id: "run_1",
      scope_contract_id: "scope_contract_1",
      scope_contract_version: 1,
      execution_revision: 1,
      execution_authorization_id: null,
      source_coverage_snapshot_id: null,
      state: "awaiting_scope_decision",
      constraint_counts: {
        season: { matched_candidate_count: 1, independent_author_count: 1, required: true },
        _summary: { reason_codes: ["required_constraint_coverage_unmet:season"] },
      },
      unmet_constraint_ids: ["season"],
      created_at: "2026-08-18T00:02:00+08:00",
    },
    allowed_resolutions: [
      { action: "expand_required_constraint", available: allowed.has("expand_required_constraint"), valid_constraint_ids: allowed.has("expand_required_constraint") ? (options.expandConstraintIds ?? ["season"]) : [], supplementary_queries_required: true, unavailable_reason: allowed.has("expand_required_constraint") ? null : "not_allowed" },
      { action: "generate_limited_report", available: allowed.has("generate_limited_report"), valid_constraint_ids: [], supplementary_queries_required: false, unavailable_reason: allowed.has("generate_limited_report") ? null : "not_allowed" },
      { action: "relax_constraint", available: allowed.has("relax_constraint"), valid_constraint_ids: allowed.has("relax_constraint") ? (options.relaxConstraintIds ?? ["season"]) : [], supplementary_queries_required: false, unavailable_reason: allowed.has("relax_constraint") ? null : "not_allowed" },
    ],
    decision_recovery: {
      state: "coverage_decision_required",
      message: "ordinary Scope confirmation unavailable",
      required_action: "resolve_coverage",
      allowed_resolutions: [...allowed],
    },
    execution_unit: null,
  };
}
