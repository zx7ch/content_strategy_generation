"use client";

import { useEffect, useState, type PropsWithChildren } from "react";

import { LiveApiErrorState } from "@/components/ui/LiveApiErrorState";
import { getRuntimeApiErrorMessage, initializeWorkspaceContext } from "@/lib/api";

/**
 * Fetches the default workspace identity from the backend on mount and initializes
 * the client-side API context. Client-rendered pages depend on this runtime
 * context; SSR pages resolve the same default workspace on the server via
 * frontend/src/lib/server-api.ts.
 *
 * Future: replace with proper auth session once multi-tenant login is implemented.
 * See docs/v2/development_tasks.md §6 AUTH-1.
 */
export function WorkspaceProvider({ children }: PropsWithChildren) {
  const [ready, setReady] = useState(false);
  const [bootstrapError, setBootstrapError] = useState<string | null>(null);
  const [retryToken, setRetryToken] = useState(0);

  useEffect(() => {
    setReady(false);
    setBootstrapError(null);
    initializeWorkspaceContext()
      .then(() => {
        setReady(true);
      })
      .catch((error) => {
        setBootstrapError(
          `无法初始化 workspace 上下文。${getRuntimeApiErrorMessage(error)}。请确认后端可用后重试。`
        );
      });
  }, [retryToken]);

  if (bootstrapError) {
    return (
      <LiveApiErrorState
        title="工作台初始化失败"
        message={bootstrapError}
        retryLabel="重新连接"
        onRetry={() => setRetryToken((current) => current + 1)}
      />
    );
  }

  if (!ready) {
    return (
      <div className="flex h-screen items-center justify-center text-sm text-gray-400">
        正在连接...
      </div>
    );
  }

  return children;
}
