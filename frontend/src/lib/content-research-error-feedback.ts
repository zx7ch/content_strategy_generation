import { ContentResearchApiError } from "./content-research-api.ts";

const feedbackByCode: Record<string, string> = {
  F003_LITE_PREVIEW_DISABLED: "当前 Runtime 版本尚未启用内容调研，请升级并重启 Runtime 后重试。",
  llm_auth_invalid: "模型 API Key 无效、已撤销，或无权调用当前模型；请在右侧栏重新配置并测试连接。",
  llm_connection_failed: "模型服务连接失败，请检查代理、Base URL 和网络连接后重新测试。",
  xhs_login_required: "小红书登录状态不可用，请重新扫码登录或粘贴有效 Cookie。",
  xhs_qr_unavailable: "二维码暂不可用，请稍后重试或改用粘贴 Cookie。",
  invalid_cookie: "Cookie 格式无效，请粘贴完整的小红书 Cookie 后重试。",
};

export function contentResearchErrorFeedback(error: unknown, prefix: string): string {
  if (error instanceof ContentResearchApiError) {
    const mapped = error.code ? feedbackByCode[error.code] : undefined;
    if (mapped) return `${prefix}：${mapped}`;
    if (error.message.trim()) return `${prefix}：${error.message.trim()}`;
  }
  return `${prefix}：请求未完成，请检查 Runtime 是否在线后重试。`;
}
