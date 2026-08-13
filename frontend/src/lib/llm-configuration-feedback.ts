const FEEDBACK_BY_ERROR_CODE: Record<string, string> = {
  llm_auth_invalid: "API Key 无效、已撤销，或无权调用该模型服务。请更换有效的 Key 后重试。",
  llm_account_unavailable: "模型账户的余额、额度或套餐不可用。请检查账户后重试。",
  llm_model_unavailable: "该模型不存在，或当前 API Key 无权使用该模型。请检查模型名称和账户权限。",
  llm_rate_limited: "模型服务请求过于频繁，请稍后再试。",
  llm_service_unavailable: "无法连接模型服务。请检查网络和 Base URL 后重试。",
  llm_protocol_incompatible: "Base URL 或模型服务接口不兼容。请检查服务地址和模型名称。",
};

const DEFAULT_FEEDBACK = "连接验证未完成，请稍后重试。";

export function llmConfigurationFeedback(errorCode: string | null | undefined): string {
  return errorCode ? FEEDBACK_BY_ERROR_CODE[errorCode] ?? DEFAULT_FEEDBACK : DEFAULT_FEEDBACK;
}
