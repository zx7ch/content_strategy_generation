import { ContentResearchApiError } from "./content-research-api";

const MESSAGE_BY_CODE: Record<string, string> = {
  runtime_unavailable: "Runtime 未启动或暂不可用。请启动 Runtime 后重试。",
  xhs_cookie_invalid: "Cookie 格式无效或已过期。请重新获取完整 Cookie。",
  xhs_qr_unavailable: "二维码服务暂不可用，请稍后重试或改用粘贴 Cookie。",
};

export function contentResearchErrorFeedback(reason: unknown, prefix: string): string {
  if (reason instanceof ContentResearchApiError && reason.code && MESSAGE_BY_CODE[reason.code]) {
    return `${prefix}：${MESSAGE_BY_CODE[reason.code]}`;
  }
  if (reason instanceof Error && reason.message) return `${prefix}：${reason.message}`;
  return `${prefix}，请确认后重试。`;
}
