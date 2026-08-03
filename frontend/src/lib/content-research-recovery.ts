export interface TransientPresearchRecovery {
  workflowRunId: string;
  status: string;
}

export interface DurableModelRecovery {
  workflowRunId: string;
  llmRecovery?: { required?: boolean } | null;
}

export function resolveContentResearchModelRecovery({
  transientPresearch,
  durableRun,
}: {
  transientPresearch: TransientPresearchRecovery | null;
  durableRun: DurableModelRecovery | null;
}): { recoveryPending: boolean; workflowRunId: string | null } {
  if (transientPresearch?.status === "waiting_model_config") {
    return {
      recoveryPending: true,
      workflowRunId: transientPresearch.workflowRunId,
    };
  }
  if (durableRun?.llmRecovery?.required) {
    return {
      recoveryPending: true,
      workflowRunId: durableRun.workflowRunId,
    };
  }
  return { recoveryPending: false, workflowRunId: null };
}
