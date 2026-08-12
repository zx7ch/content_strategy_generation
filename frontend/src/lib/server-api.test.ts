import assert from "node:assert/strict";
import test from "node:test";

import { getServerBrandDetailPageData, getServerBrandsPageData } from "./server-api.ts";

test("SSR brands page resolves live data without client workspace context", async () => {
  const calls: string[] = [];
  globalThis.fetch = (async (input: string | URL) => {
    const url = input.toString();
    calls.push(url);
    if (url.endsWith("/workspaces/default")) {
      return Response.json({ workspace_id: "ws-1", user_id: "operator" });
    }
    if (url.endsWith("/brands")) {
      return Response.json({
        items: [
          {
            id: "brand-1",
            workspace_id: "ws-1",
            name: "轻量户外",
            category: "outdoor",
            stage: "growth",
            target_audience: { age_ranges: ["25-34"], gender_skew: "female" },
            brand_voice: {},
            goals: {},
            created_at: "2026-04-15T00:00:00Z",
            updated_at: "2026-04-15T00:00:00Z"
          }
        ]
      });
    }
    if (url.endsWith("/brands/brand-1/channels")) {
      return Response.json({
        items: [
          {
            id: "channel-1",
            platform: "xiaohongshu",
            account_handle: "light-outdoor-demo",
            account_name: "轻量户外官方号"
          }
        ]
      });
    }
    throw new Error(`Unexpected fetch: ${url}`);
  }) as typeof fetch;

  const data = await getServerBrandsPageData();

  assert.equal(data.source, "live");
  assert.equal(data.brands[0]?.name, "轻量户外");
  assert.equal(data.brands[0]?.accounts, 1);
  assert.ok(calls.some((url) => url.endsWith("/workspaces/default")));
});

test("SSR brand detail keeps live brand and channels when policy or snapshots are unavailable", async () => {
  globalThis.fetch = (async (input: string | URL) => {
    const url = input.toString();
    if (url.endsWith("/workspaces/default")) {
      return Response.json({ workspace_id: "ws-1", user_id: "operator" });
    }
    if (url.endsWith("/brands/brand-1/workspace")) {
      return Response.json({
        brand: {
          id: "brand-1",
          workspace_id: "ws-1",
          name: "轻量户外",
          category: "outdoor",
          stage: "growth",
          target_audience: { age_ranges: ["25-34"], gender_skew: "female" },
          brand_voice: {},
          goals: {},
          created_at: "2026-04-15T00:00:00Z",
          updated_at: "2026-04-15T00:00:00Z"
        },
        channels: [
          {
            id: "channel-1",
            platform: "xiaohongshu",
            account_handle: "light-outdoor-demo",
            account_name: "轻量户外官方号"
          }
        ],
        active_policy: null,
        latest_extension_capture_session: null,
        latest_data_import_preview: null
      });
    }
    throw new Error(`Unexpected fetch: ${url}`);
  }) as typeof fetch;

  const data = await getServerBrandDetailPageData("brand-1");

  assert.equal(data.source, "live");
  assert.equal(data.brand.name, "轻量户外");
  assert.equal(data.channels.length, 1);
  assert.equal(data.latestExtensionCaptureSession, undefined);
  assert.equal(data.latestDataImportPreview, undefined);
});
