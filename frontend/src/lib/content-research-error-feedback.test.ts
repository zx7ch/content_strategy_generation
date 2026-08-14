import assert from "node:assert/strict";
import test from "node:test";

import { ContentResearchApiError } from "./content-research-api";
import { contentResearchErrorFeedback } from "./content-research-error-feedback";

test("maps Runtime login error codes to a safe corrective action", () => {
  const error = new ContentResearchApiError("upstream unavailable", 503, "runtime_unavailable");

  assert.equal(
    contentResearchErrorFeedback(error, "二维码登录失败"),
    "二维码登录失败：Runtime 未启动或暂不可用。请启动 Runtime 后重试。",
  );
});

test("keeps a non-API login failure readable", () => {
  assert.equal(contentResearchErrorFeedback(new Error("network down"), "Cookie 保存失败"), "Cookie 保存失败：network down");
});
