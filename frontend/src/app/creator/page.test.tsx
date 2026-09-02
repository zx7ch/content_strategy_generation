import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { JSDOM } from "jsdom";

test("Creator always renders the content research entry instead of preview-gating it", () => {
  const source = readFileSync(new URL("./page.tsx", import.meta.url), "utf8");

  assert.doesNotMatch(source, /\{F003_LITE_PREVIEW_ENABLED\s*&&\s*\(/);
  assert.match(source, /内容调研/);
});

test("Creator Trace does not expose internal safe-execution wording", () => {
  const source = readFileSync(new URL("./page.tsx", import.meta.url), "utf8");

  assert.doesNotMatch(source, /安全执行阶段|安全执行状态/);
});

test("Creator restores the durable presearch Trace when the request disconnects after acceptance", () => {
  const source = readFileSync(new URL("./page.tsx", import.meta.url), "utf8");

  assert.match(source, /restoreInterruptedContentResearchRun/);
  assert.match(source, /getThreadTimeline\(threadId\)/);
  assert.match(source, /预检索连接中断，已恢复本次运行的 Trace/);
  assert.doesNotMatch(source, /pending-\$\{workflowRunId\}/);
  assert.doesNotMatch(source, /function interruptedPresearchSummary/);
});

test("Creator exposes retrieval recovery without mislabeling it as a model configuration failure", () => {
  const source = readFileSync(new URL("./page.tsx", import.meta.url), "utf8");

  assert.match(source, /retryContentResearchRetrieval/);
  assert.match(source, /继续失败的检索/);
  assert.match(source, /recovery_plan\?\.action === "retry_presearch"/);
  assert.match(source, /recovery_plan\?\.action === "retry_retrieval"/);
  assert.doesNotMatch(source, /allowed_actions\.includes\("retry_/);
});

test("Creator rejects a late presearch response before it can activate an old Run", () => {
  const source = readFileSync(new URL("./page.tsx", import.meta.url), "utf8");
  const guard = source.indexOf("activeThreadIdRef.current !== threadId", source.indexOf("async function sendMessage"));
  const activate = source.indexOf("contentResearchRequestEpochRef.current.activate(result.workflow_run_id)", guard);

  assert.ok(guard > 0);
  assert.ok(activate > guard);
  assert.match(source, /submitEpoch !== contentResearchSubmitEpochRef\.current/);
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

test("Xiaohongshu login card refreshes when workflow authentication state changes", async () => {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost" });
  Object.assign(globalThis, {
    window: dom.window,
    document: dom.window.document,
    HTMLElement: dom.window.HTMLElement,
    Event: dom.window.Event,
    IS_REACT_ACT_ENVIRONMENT: true,
  });
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: dom.window.navigator });
  const previousFetch = globalThis.fetch;
  let requestCount = 0;
  globalThis.fetch = async () => {
    requestCount += 1;
    return new Response(JSON.stringify(requestCount === 1 ? {
      authenticated: true,
      source: "manual_cookie",
    } : {
      authenticated: false,
      source: "manual_cookie",
      failure_code: "auth_required",
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  };
  const React = await import("react");
  Object.assign(globalThis, { React });
  const { act } = React;
  const { createRoot } = await import("react-dom/client");
  const { XiaohongshuLoginCard } = await import("@/components/content-research/XiaohongshuLoginCard.tsx");
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);

  await act(async () => { root.render(React.createElement(XiaohongshuLoginCard, { refreshKey: "collecting:1" })); });
  await act(async () => { await Promise.resolve(); });
  assert.match(container.textContent ?? "", /已登录/);

  await act(async () => { root.render(React.createElement(XiaohongshuLoginCard, { refreshKey: "recovery_required:2" })); });
  await act(async () => { await Promise.resolve(); });
  assert.match(container.textContent ?? "", /登录状态已失效/);
  assert.doesNotMatch(container.textContent ?? "", /已登录/);

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
  const sameRunReport = requests.ticket("run_b", "report");
  const newerSameRunScope = requests.ticket("run_b", "scope");
  assert.equal(requests.accepts(earlierSameRunScope), false);
  assert.equal(requests.accepts(newerSameRunScope), true);
  assert.equal(requests.accepts(sameRunReport), true);

  const earlierRecovery = requests.ticket("run_b", "recovery-command");
  const newerRecovery = requests.ticket("run_b", "recovery-command");
  assert.equal(requests.accepts(earlierRecovery), false);
  assert.equal(requests.accepts(newerRecovery), true);
  assert.equal(requests.accepts(sameRunReport), true);
});

test("Trace revision guard rejects older snapshots and becomes uncertain after three failures", async () => {
  const { ContentResearchTraceRevisionGuard } = await import("./page-state.ts");
  const guard = new ContentResearchTraceRevisionGuard();

  assert.equal(guard.accept(12), true);
  assert.equal(guard.minimumRevision(), 12);
  assert.equal(guard.accept(11), false);
  assert.equal(guard.recordFailure(), false);
  assert.equal(guard.recordFailure(), false);
  assert.equal(guard.recordFailure(), true);
  assert.equal(guard.isUncertain(), true);
  assert.equal(guard.accept(13), true);
  assert.equal(guard.minimumRevision(), 13);
  assert.equal(guard.isUncertain(), false);
});

test("scope draft saves are serialized and coalesce to the latest edit", async () => {
  const { LatestScopeDraftSaveQueue } = await import("./page-state.ts");
  type Snapshot = { core: string; product: string; context: string };
  type Authority = { draftId: string; revision: number };
  const sends: Array<{ authority: Authority; snapshot: Snapshot }> = [];
  const releases: Array<() => void> = [];
  const accepted: Snapshot[] = [];
  const queue = new LatestScopeDraftSaveQueue<Snapshot, Authority>({
    initialAuthority: { draftId: "draft-1", revision: 1 },
    send: async (authority, snapshot) => {
      sends.push({ authority, snapshot });
      await new Promise<void>((resolve) => { releases.push(resolve); });
      return { draftId: `draft-${sends.length + 1}`, revision: authority.revision + 1 };
    },
    onAccepted: (_authority, snapshot) => { accepted.push(snapshot); },
  });

  queue.enqueue({ core: "T恤", product: "凉感", context: "" });
  queue.enqueue({ core: "T恤", product: "凉感", context: "夏季" });
  queue.enqueue({ core: "T恤", product: "冰感", context: "夏季通勤" });
  await Promise.resolve();
  assert.equal(sends.length, 1);

  releases.shift()?.();
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(sends.length, 2);
  assert.deepEqual(sends[1], {
    authority: { draftId: "draft-2", revision: 2 },
    snapshot: { core: "T恤", product: "冰感", context: "夏季通勤" },
  });

  releases.shift()?.();
  await queue.idle();
  assert.equal(sends.length, 2);
  assert.deepEqual(accepted, [{ core: "T恤", product: "冰感", context: "夏季通勤" }]);
});

test("a newer scope edit survives an earlier save failure", async () => {
  const { LatestScopeDraftSaveQueue } = await import("./page-state.ts");
  const sends: Array<{ authority: number; snapshot: string }> = [];
  let rejectFirst: ((error: Error) => void) | undefined;
  const queue = new LatestScopeDraftSaveQueue<string, number>({
    initialAuthority: 1,
    send: async (authority, snapshot) => {
      sends.push({ authority, snapshot });
      if (snapshot === "first") {
        await new Promise<void>((_resolve, reject) => { rejectFirst = reject; });
      }
      return authority + 1;
    },
    recover: async () => ({ authority: 2, accepted: true }),
    onAccepted: () => undefined,
  });

  queue.enqueue("first");
  queue.enqueue("latest");
  rejectFirst?.(new Error("temporary failure"));
  await queue.idle();

  assert.deepEqual(sends, [
    { authority: 1, snapshot: "first" },
    { authority: 2, snapshot: "latest" },
  ]);
});

test("an uncommitted ambiguous scope save is retried once after authority recovery", async () => {
  const { LatestScopeDraftSaveQueue } = await import("./page-state.ts");
  const sends: Array<{ authority: number; snapshot: string }> = [];
  let attempts = 0;
  const accepted: string[] = [];
  const queue = new LatestScopeDraftSaveQueue<string, number>({
    initialAuthority: 1,
    send: async (authority, snapshot) => {
      sends.push({ authority, snapshot });
      attempts += 1;
      if (attempts === 1) throw new Error("response lost before commit");
      return authority + 1;
    },
    recover: async () => ({ authority: 1, accepted: false }),
    onAccepted: (_authority, snapshot) => { accepted.push(snapshot); },
  });

  queue.enqueue("only-edit");
  await queue.idle();

  assert.deepEqual(sends, [
    { authority: 1, snapshot: "only-edit" },
    { authority: 1, snapshot: "only-edit" },
  ]);
  assert.deepEqual(accepted, ["only-edit"]);
});

test("scope recovery compares terms using the backend whitespace normalization", async () => {
  const { scopeDraftSnapshotMatches } = await import("./page-state.ts");

  assert.equal(scopeDraftSnapshotMatches(
    {
      core_object: "短袖T恤",
      product_experience_aspect: "冰  感",
      context_audience_aspect: "夏季 通勤",
    },
    {
      core_object: "  短袖T恤 ",
      product_experience_aspect: "冰 感",
      context_audience_aspect: "夏季  通勤",
    },
  ), true);
  assert.equal(scopeDraftSnapshotMatches(
    { core_object: "T恤", product_experience_aspect: "凉感" },
    { core_object: "T恤", product_experience_aspect: "冰感" },
  ), false);
});

test("presearch model recovery stays visible without a report-stage recovery action", async () => {
  const { projectedModelRecoveryVisible } = await import("./page-state.ts");

  assert.equal(projectedModelRecoveryVisible({
    recoveryPending: true,
    lifecycleState: "recovery_required",
    currentStage: "presearch",
    hasDurableRun: true,
  }), true);
  assert.equal(projectedModelRecoveryVisible({
    recoveryPending: true,
    lifecycleState: "report_composing",
    currentStage: "report",
    hasDurableRun: true,
  }), false);
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
