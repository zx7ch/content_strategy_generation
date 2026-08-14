"use client";

import { useEffect, useState } from "react";
import {
  clearXHSLogin,
  getXHSLoginStatus,
  getCurrentXHSQRLogin,
  saveXHSManualCookie,
  startXHSQRLogin,
  type XHSLoginStatus,
} from "@/lib/content-research-api";
import { contentResearchErrorFeedback } from "@/lib/content-research-error-feedback";

export function XiaohongshuLoginCard() {
  const [status, setStatus] = useState<XHSLoginStatus | null>(null);
  const [cookie, setCookie] = useState("");
  const [qrImage, setQrImage] = useState<string | null>(null);
  const [qrPending, setQrPending] = useState(false);
  const [startingQr, setStartingQr] = useState(false);
  const [savingCookie, setSavingCookie] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { void getXHSLoginStatus().then(setStatus).catch(() => setError("登录状态暂不可用")); }, []);
  useEffect(() => {
    if (!qrPending) return;
    const poll = () => void getCurrentXHSQRLogin().then((result) => {
      setQrImage(result.qr_image_data_url ?? null);
      if (result.status === "authenticated") {
        setQrPending(false);
        void getXHSLoginStatus().then(setStatus);
      } else if (result.status === "failed" || result.status === "expired") setQrPending(false);
    }).catch((reason) => {
      setQrPending(false);
      setError(contentResearchErrorFeedback(reason, "二维码登录失败"));
    });
    poll(); const timer = window.setInterval(poll, 1500); return () => window.clearInterval(timer);
  }, [qrPending]);

  const startQr = async () => {
    if (startingQr) return;
    setError(null);
    setStartingQr(true);
    try {
      const result = await startXHSQRLogin();
      setQrImage(result.qr_image_data_url ?? null);
      setQrPending(result.status === "pending");
      if (result.status === "failed") setError("二维码登录失败：二维码暂不可用，请稍后重试或改用粘贴 Cookie。");
    } catch (reason) {
      setError(contentResearchErrorFeedback(reason, "二维码登录失败"));
    } finally { setStartingQr(false); }
  };
  const saveCookie = async () => {
    if (!cookie.trim() || savingCookie) return;
    setError(null);
    setSavingCookie(true);
    try { setStatus(await saveXHSManualCookie(cookie)); setCookie(""); }
    catch (reason) { setError(contentResearchErrorFeedback(reason, "Cookie 保存失败")); }
    finally { setSavingCookie(false); }
  };
  const clear = async () => { setError(null); try { setStatus(await clearXHSLogin()); } catch { setError("清除登录信息失败"); } };
  const source = status?.source === "qr" ? "扫码登录" : status?.source === "manual_cookie" ? "Cookie" : null;
  const loginExpired = status?.failure_code === "auth_required" || status?.failure_code === "auth_expired";
  const updatedAt = status?.updated_at ? new Date(status.updated_at).toLocaleString("zh-CN") : null;

  return <section className="mb-4 rounded-xl border border-line bg-white p-4" aria-label="小红书登录">
    <div className="flex items-center justify-between gap-2"><h2 className="text-sm font-semibold text-ink">小红书登录</h2>{status?.authenticated && <span className="text-xs text-emerald-700">已登录</span>}</div>
    <p className="mt-1 text-xs leading-5 text-quiet">{status?.authenticated ? `方式：${source}；重启 Runtime 后仍会保留。` : loginExpired ? "登录状态已失效，请重新扫码或粘贴 Cookie。" : "使用扫码或粘贴 Cookie 登录。"}</p>
    {updatedAt && <p className="mt-1 text-xs text-quiet">更新于：{updatedAt}</p>}
    <button type="button" onClick={() => void startQr()} disabled={startingQr || qrPending} className="mt-3 rounded-lg bg-[#486b5b] px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40">{startingQr ? "正在获取二维码…" : qrPending ? "等待扫码…" : "扫码登录"}</button>
    {qrImage && <img className="mx-auto mt-3 h-36 w-36" src={qrImage} alt="小红书登录二维码" />}
    <label className="mt-3 block text-xs font-medium text-ink" htmlFor="xhs-cookie">粘贴 Cookie</label>
    <input id="xhs-cookie" type="password" autoComplete="off" value={cookie} onChange={(event) => setCookie(event.target.value)} className="mt-1 w-full rounded-lg border border-line px-2 py-1.5 text-xs" placeholder="粘贴完整 Cookie" />
    <div className="mt-2 flex gap-2"><button type="button" onClick={() => void saveCookie()} disabled={!cookie.trim() || savingCookie} className="rounded-lg border border-line px-3 py-1.5 text-xs disabled:opacity-40">{savingCookie ? "保存中…" : "保存 Cookie"}</button>{status?.authenticated && <button type="button" onClick={() => void clear()} className="rounded-lg border border-line px-3 py-1.5 text-xs">清除</button>}</div>
    {error && <p role="alert" className="mt-2 text-xs text-red-600">{error}</p>}
  </section>;
}
