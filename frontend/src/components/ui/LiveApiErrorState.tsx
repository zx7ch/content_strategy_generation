"use client";

import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";

interface LiveApiErrorStateProps {
  title: string;
  message: string;
  retryLabel?: string;
  onRetry?: () => void;
}

export function LiveApiErrorState({
  title,
  message,
  retryLabel = "重试",
  onRetry
}: LiveApiErrorStateProps) {
  return (
    <div className="space-y-5">
      <section className="rounded-panel border border-rose-200 bg-rose-50/80 p-6 shadow-panel backdrop-blur">
        <p className="text-sm text-rose-700">Live API Error</p>
        <h1 className="mt-1 text-3xl font-semibold tracking-tight text-rose-950">{title}</h1>
        <p className="mt-3 text-sm text-rose-800">{message}</p>
        {onRetry ? (
          <div className="mt-4">
            <Button variant="outline" onClick={onRetry}>
              {retryLabel}
            </Button>
          </div>
        ) : null}
      </section>

      <Card>
        <h2 className="text-lg font-semibold text-ink">本地排查建议</h2>
        <div className="mt-4 space-y-2 text-sm text-slate-700">
          <p>1. 确认后端进程正在监听 `http://127.0.0.1:8000`。</p>
          <p>2. 访问 `GET /workspaces/default`，确认返回 `workspace_id` 与 `user_id`。</p>
          <p>3. 如已启用认证，确认 `X-Workspace-Id`、`X-User-Id` 和 token 配置一致。</p>
        </div>
      </Card>
    </div>
  );
}
