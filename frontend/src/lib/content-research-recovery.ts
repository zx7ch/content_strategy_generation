export interface TransientPresearchRecovery {
  workflowRunId: string;
  status: string;
}

export interface DurableModelRecovery {
  workflowRunId: string;
  llmRecovery?: { required?: boolean; required_since?: string | null } | null;
}

export function resolveContentResearchModelRecovery({
  transientPresearch,
  durableRun,
}: {
  transientPresearch: TransientPresearchRecovery | null;
  durableRun: DurableModelRecovery | null;
}): { recoveryPending: boolean; workflowRunId: string | null; requiredSince: string | null } {
  if (transientPresearch?.status === "waiting_model_config") {
    return {
      recoveryPending: true,
      workflowRunId: transientPresearch.workflowRunId,
      requiredSince: durableRun?.workflowRunId === transientPresearch.workflowRunId
        ? durableRun.llmRecovery?.required_since ?? null
        : null,
    };
  }
  if (durableRun?.llmRecovery?.required) {
    return {
      recoveryPending: true,
      workflowRunId: durableRun.workflowRunId,
      requiredSince: durableRun.llmRecovery.required_since ?? null,
    };
  }
  return { recoveryPending: false, workflowRunId: null, requiredSince: null };
}
