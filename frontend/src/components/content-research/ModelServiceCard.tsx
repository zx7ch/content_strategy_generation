"use client";

import { useEffect, useRef, useState } from "react";
import {
  deleteLLMConfiguration,
  getLLMConfiguration,
  saveLLMConfiguration,
  validateLLMConfiguration,
  type LLMConfiguration,
} from "@/lib/content-research-api";

function statusLabel(status: string): string {
  return status === "validated" ? "连接已验证" : status === "invalid" ? "配置需要修正" : "使用系统默认配置";
}

export function ModelServiceCard({
  recoveryPending,
  recoveryRequiredSince = null,
  onContinue,
  onConfigurationChanged,
}: {
  recoveryPending: boolean;
  recoveryRequiredSince?: string | null;
  onContinue: () => void | Promise<void>;
  onConfigurationChanged: (configuration: LLMConfiguration) => void;
}) {
  const [configuration, setConfiguration] = useState<LLMConfiguration | null>(null);
  const [editing, setEditing] = useState(false);
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [recoveryReady, setRecoveryReady] = useState(false);
  const busyRef = useRef(false);

  useEffect(() => {
    void getLLMConfiguration().then((value) => {
      setConfiguration(value); setBaseUrl(value.base_url); setModel(value.model);
    }).catch(() => setMessage("无法读取模型配置"));
  }, []);

  useEffect(() => {
    setRecoveryReady(false);
  }, [recoveryPending, recoveryRequiredSince]);

  const apply = (value: LLMConfiguration) => {
    setConfiguration(value); setBaseUrl(value.base_url); setModel(value.model); setApiKey("");
    onConfigurationChanged(value);
  };

  const validate = async () => {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setMessage("");
    try {
      const value = await validateLLMConfiguration({ base_url: baseUrl, model, api_key: apiKey || null });
      if (value.status === "validated") setMessage("连接验证成功");
      else setMessage("连接验证失败，请检查模型服务配置");
    } catch { setMessage("连接验证失败，请检查模型服务配置"); }
    finally { busyRef.current = false; setBusy(false); }
  };

  const save = async () => {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setMessage("");
    try {
      const saved = await saveLLMConfiguration({ base_url: baseUrl, model, api_key: apiKey || null });
      apply(saved); setEditing(false); setRecoveryReady(recoveryPending && saved.status === "validated");
    }
    catch { setMessage("保存失败，请先验证模型服务配置"); }
    finally { busyRef.current = false; setBusy(false); }
  };

  const remove = async () => {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setMessage("");
    try { apply(await deleteLLMConfiguration()); setEditing(false); setRecoveryReady(false); }
    catch { setMessage("删除配置失败"); }
    finally { busyRef.current = false; setBusy(false); }
  };

  const display = configuration ?? { source: "system_default", status: "not_configured", base_url: "", model: "", api_key_configured: false };
  const validatedAfterFailure = Boolean(
    recoveryRequiredSince
    && display.status === "validated"
    && display.validated_at
    && Date.parse(display.validated_at) > Date.parse(recoveryRequiredSince),
  );
  const canContinueRecovery = recoveryPending && (recoveryReady || validatedAfterFailure);

  const continueRecovery = async () => {
    if (busyRef.current || !canContinueRecovery) return;
    busyRef.current = true; setBusy(true); setMessage("");
    try { await onContinue(); }
    catch { setMessage("继续调研失败，请重试。"); }
    finally { busyRef.current = false; setBusy(false); }
  };
  return <section className="mb-4 rounded-xl border border-line bg-white p-4" aria-label="模型服务">
    <h2 className="text-sm font-semibold text-ink">模型服务</h2>
    <p className="mt-1 text-xs text-quiet">{statusLabel(display.status)}</p>
    <p className="mt-1 text-xs text-quiet">模型：{display.model || "系统默认"}</p>
    <p className="mt-1 text-xs text-quiet">来源：{display.source === "user" ? "用户配置" : "系统默认"}</p>
    {display.api_key_suffix && <p className="mt-1 text-xs text-quiet">API Key：••••{display.api_key_suffix}</p>}
    {recoveryPending && <p className="mt-3 rounded-lg bg-amber-50 px-2 py-1.5 text-xs text-amber-900">模型配置需要更新后才能继续调研。</p>}
    {message && <p className="mt-2 text-xs text-quiet">{message}</p>}
    {!editing ? <div className="mt-3 flex gap-2"><button type="button" onClick={() => setEditing(true)} className="rounded-lg border border-line px-3 py-1.5 text-xs">配置模型</button>
      {canContinueRecovery && <button type="button" disabled={busy} onClick={() => void continueRecovery()} className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs text-white disabled:opacity-40">{busy ? "继续中" : "继续调研"}</button>}
    </div> : <div className="mt-3 space-y-2 border-t border-line pt-3">
      <label className="block text-xs">Base URL<input aria-label="Base URL" value={baseUrl} onChange={(event) => { setBaseUrl(event.target.value); setRecoveryReady(false); }} className="mt-1 w-full rounded border border-line px-2 py-1" /></label>
      <label className="block text-xs">模型<input aria-label="模型" value={model} onChange={(event) => { setModel(event.target.value); setRecoveryReady(false); }} className="mt-1 w-full rounded border border-line px-2 py-1" /></label>
      <label className="block text-xs">API Key<input aria-label="API Key" type="password" value={apiKey} onChange={(event) => { setApiKey(event.target.value); setRecoveryReady(false); }} className="mt-1 w-full rounded border border-line px-2 py-1" /></label>
      <div className="flex flex-wrap gap-2"><button type="button" disabled={busy} onClick={() => void validate()} className="rounded border border-line px-2 py-1 text-xs">测试连接</button><button type="button" disabled={busy} onClick={() => void save()} className="rounded bg-blue-600 px-2 py-1 text-xs text-white">保存</button><button type="button" disabled={busy} onClick={() => void remove()} className="rounded border border-red-200 px-2 py-1 text-xs text-red-700">删除配置，恢复系统默认</button></div>
    </div>}
  </section>;
}
