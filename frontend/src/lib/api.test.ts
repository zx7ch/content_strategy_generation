import assert from "node:assert/strict";
import test from "node:test";

import {
  createBrand,
  getDecisionsPageData,
  getEvaluationPageData,
  getPublishPageData,
  getRuntimeApiErrorMessage,
  getTopicPoolPageData,
  getWorkspaceContext,
  initializeWorkspaceContext,
  REQUIRED_API_CONTRACT,
  RUNTIME_BASE_URL,
  setWorkspaceContext
} from "./api.ts";

test("topic pool loader throws on transport failure instead of returning mock data", async () => {
  setWorkspaceContext("ws-1", "operator");
  globalThis.fetch = (async () => new Response("boom", { status: 500 })) as typeof fetch;

  await assert.rejects(() => getTopicPoolPageData("brand-1"), /request failed: 500/);
});

test("decision loader keeps documented 404 as empty live state", async () => {
  setWorkspaceContext("ws-1", "operator");
  globalThis.fetch = (async () => new Response("missing", { status: 404 })) as typeof fetch;

  const data = await getDecisionsPageData("brand-1");

  assert.equal(data.source, "live");
  assert.equal(data.items.length, 0);
  assert.equal(data.stats.selectedCount, 0);
});

test("decision loader throws on non-404 failure", async () => {
  setWorkspaceContext("ws-1", "operator");
  globalThis.fetch = (async () => new Response("boom", { status: 503 })) as typeof fetch;

  await assert.rejects(() => getDecisionsPageData("brand-1"), /request failed: 503/);
});

test("publish loader throws on transport failure instead of returning mock rows", async () => {
  setWorkspaceContext("ws-1", "operator");
  globalThis.fetch = (async () => new Response("boom", { status: 502 })) as typeof fetch;

  await assert.rejects(() => getPublishPageData("brand-1"), /request failed: 502/);
});

test("evaluation loader keeps documented 404 as empty live state", async () => {
  setWorkspaceContext("ws-1", "operator");
  globalThis.fetch = (async () => new Response("missing", { status: 404 })) as typeof fetch;

  const data = await getEvaluationPageData("brand-1");

  assert.equal(data.source, "live");
  assert.equal(data.slices.length, 0);
  assert.equal(data.summary.note, "当前品牌还没有 evaluation run。");
});

test("create brand posts a real live payload", async () => {
  setWorkspaceContext("ws-1", "operator");
  let requestBody = "";
  globalThis.fetch = (async (_input, init) => {
    requestBody = String(init?.body ?? "");
    return new Response(
      JSON.stringify({
        id: "brand-1",
        workspace_id: "ws-1",
        name: "Trail Brand",
        stage: "growth",
        target_audience: {},
        brand_voice: {},
        goals: {},
        created_at: "2026-04-17T00:00:00Z",
        updated_at: "2026-04-17T00:00:00Z"
      }),
      {
        status: 201,
        headers: { "Content-Type": "application/json" }
      }
    );
  }) as typeof fetch;

  const created = await createBrand({
    name: "Trail Brand",
    category: "outdoor",
    stage: "growth",
    audienceSummary: "25-34 岁城市女性"
  });

  assert.deepEqual(created, { id: "brand-1", name: "Trail Brand" });
  assert.match(requestBody, /"name":"Trail Brand"/);
  assert.match(requestBody, /"category":"outdoor"/);
  assert.match(requestBody, /"stage":"growth"/);
  assert.match(requestBody, /"summary":"25-34 岁城市女性"/);
});

test("workspace bootstrap failure does not install fake workspace identity", async () => {
  setWorkspaceContext("", "");
  // health check fails first → initializeWorkspaceContext throws before touching workspace context
  globalThis.fetch = (async () => new Response("offline", { status: 503 })) as typeof fetch;

  await assert.rejects(() => initializeWorkspaceContext(), /Agent Runtime/);

  assert.deepEqual(getWorkspaceContext(), { workspaceId: "", userId: "" });
});

test("runtime api error helper keeps message readable", () => {
  assert.equal(getRuntimeApiErrorMessage(new Error("network down")), "network down");
  assert.equal(getRuntimeApiErrorMessage("bad"), "unknown error");
});

// ALIGN-1: Runtime connection layer tests

test("RUNTIME_BASE_URL is exported and starts with http", () => {
  assert.equal(typeof RUNTIME_BASE_URL, "string");
  assert.ok(RUNTIME_BASE_URL.startsWith("http"), `Expected RUNTIME_BASE_URL to start with http, got: ${RUNTIME_BASE_URL}`);
});

test("initializeWorkspaceContext succeeds when health and workspace both respond ok", async () => {
  setWorkspaceContext("", "");
  let callCount = 0;
  globalThis.fetch = (async (input) => {
    callCount++;
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : (input as Request).url;
    if (url.endsWith("/health")) {
      return new Response(
        JSON.stringify({ status: "healthy", version: "0.1.0", api_contract: REQUIRED_API_CONTRACT }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      );
    }
    if (url.endsWith("/runtime/prewarm")) {
      return new Response(null, { status: 202 });
    }
    return new Response(JSON.stringify({ workspace_id: "ws-test", user_id: "u-test" }), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;

  const result = await initializeWorkspaceContext();

  assert.equal(result.workspace_id, "ws-test");
  assert.equal(result.user_id, "u-test");
  assert.deepEqual(getWorkspaceContext(), { workspaceId: "ws-test", userId: "u-test" });
  assert.equal(callCount, 3, "should call /health, /runtime/prewarm, then /workspaces/default");
});

test("initializeWorkspaceContext accepts local dev runtime version", async () => {
  setWorkspaceContext("", "");
  globalThis.fetch = (async (input) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : (input as Request).url;
    if (url.endsWith("/health")) {
      return new Response(
        JSON.stringify({ status: "healthy", version: "dev", api_contract: REQUIRED_API_CONTRACT }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      );
    }
    if (url.endsWith("/runtime/prewarm")) {
      return new Response(null, { status: 202 });
    }
    return new Response(JSON.stringify({ workspace_id: "ws-dev", user_id: "u-dev" }), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    });
  }) as typeof fetch;

  const result = await initializeWorkspaceContext();

  assert.deepEqual(result, { workspace_id: "ws-dev", user_id: "u-dev" });
  assert.deepEqual(getWorkspaceContext(), { workspaceId: "ws-dev", userId: "u-dev" });
});

test("initializeWorkspaceContext throws when health returns non-ok status", async () => {
  setWorkspaceContext("", "");
  globalThis.fetch = (async () => new Response("error", { status: 500 })) as typeof fetch;

  await assert.rejects(
    () => initializeWorkspaceContext(),
    /Agent Runtime 未启动或不可达/
  );

  assert.deepEqual(getWorkspaceContext(), { workspaceId: "", userId: "" });
});

test("initializeWorkspaceContext throws when health fetch fails with network error", async () => {
  setWorkspaceContext("", "");
  globalThis.fetch = (async () => { throw new TypeError("Failed to fetch"); }) as typeof fetch;

  await assert.rejects(
    () => initializeWorkspaceContext(),
    /Agent Runtime 未启动或不可达/
  );
});

test("initializeWorkspaceContext throws when workspace endpoint fails after healthy check", async () => {
  setWorkspaceContext("", "");
  globalThis.fetch = (async (input) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : (input as Request).url;
    if (url.endsWith("/health")) {
      return new Response(
        JSON.stringify({ status: "healthy", version: "0.1.0", api_contract: REQUIRED_API_CONTRACT }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      );
    }
    if (url.endsWith("/runtime/prewarm")) {
      return new Response(null, { status: 202 });
    }
    return new Response("workspace not found", { status: 404 });
  }) as typeof fetch;

  await assert.rejects(
    () => initializeWorkspaceContext(),
    /request failed: 404/
  );

  assert.deepEqual(getWorkspaceContext(), { workspaceId: "", userId: "" });
});
