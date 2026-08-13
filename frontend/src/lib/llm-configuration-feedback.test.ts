import assert from "node:assert/strict";
import test from "node:test";

import { llmConfigurationFeedback } from "./llm-configuration-feedback";

test("explains how a user can correct each known model connection failure", () => {
  assert.equal(
    llmConfigurationFeedback("llm_auth_invalid"),
    "API Key 无效、已撤销，或无权调用该模型服务。请更换有效的 Key 后重试。"
  );
  assert.equal(
    llmConfigurationFeedback("llm_account_unavailable"),
    "模型账户的余额、额度或套餐不可用。请检查账户后重试。"
  );
  assert.equal(
    llmConfigurationFeedback("llm_model_unavailable"),
    "该模型不存在，或当前 API Key 无权使用该模型。请检查模型名称和账户权限。"
  );
  assert.equal(
    llmConfigurationFeedback("llm_rate_limited"),
    "模型服务请求过于频繁，请稍后再试。"
  );
  assert.equal(
    llmConfigurationFeedback("llm_service_unavailable"),
    "无法连接模型服务。请检查网络和 Base URL 后重试。"
  );
  assert.equal(
    llmConfigurationFeedback("llm_protocol_incompatible"),
    "Base URL 或模型服务接口不兼容。请检查服务地址和模型名称。"
  );
});

test("keeps an unexpected provider failure safe and actionable", () => {
  assert.equal(
    llmConfigurationFeedback("unknown_provider_failure"),
    "连接验证未完成，请稍后重试。"
  );
  assert.equal(llmConfigurationFeedback(null), "连接验证未完成，请稍后重试。");
});
