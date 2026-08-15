import assert from "node:assert/strict";
import test from "node:test";

import { ContentResearchApiError } from "./content-research-api.ts";
import { contentResearchErrorFeedback } from "./content-research-error-feedback.ts";

test("released-content feature errors tell the Creator to restart the upgraded Runtime", () => {
  const feedback = contentResearchErrorFeedback(
    new ContentResearchApiError("legacy feature disabled", 403, "F003_LITE_PREVIEW_DISABLED"),
    "内容调研预检索失败",
  );

  assert.match(feedback, /升级并重启 Runtime/);
  assert.doesNotMatch(feedback, /小红书登录态/);
});

test("unknown API errors preserve a safe actionable server message", () => {
  const feedback = contentResearchErrorFeedback(
    new ContentResearchApiError("服务暂时不可用。请稍后重试", 503, "provider_unavailable"),
    "内容调研预检索失败",
  );

  assert.equal(feedback, "内容调研预检索失败：服务暂时不可用。请稍后重试");
});
