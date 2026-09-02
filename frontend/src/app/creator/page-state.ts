export interface ContentResearchRequestTicket {
  workflowRunId: string;
  epoch: number;
  channel: string;
  generation: number;
}

export function projectedModelRecoveryVisible({
  recoveryPending,
  lifecycleState,
  currentStage,
  hasDurableRun,
}: {
  recoveryPending: boolean;
  lifecycleState?: string | null;
  currentStage?: string | null;
  hasDurableRun: boolean;
}) {
  if (!recoveryPending) return false;
  if (!hasDurableRun) return true;
  return lifecycleState === "recovery_required" && currentStage === "presearch";
}

export class ContentResearchRequestEpoch {
  private workflowRunId: string | null = null;
  private epoch = 0;
  private generations = new Map<string, number>();

  activate(workflowRunId: string | null) {
    if (workflowRunId !== this.workflowRunId) {
      this.workflowRunId = workflowRunId;
      this.epoch += 1;
      this.generations.clear();
    }
  }

  ticket(workflowRunId: string, channel = "command"): ContentResearchRequestTicket {
    const generation = (this.generations.get(channel) ?? 0) + 1;
    this.generations.set(channel, generation);
    return { workflowRunId, epoch: this.epoch, channel, generation };
  }

  accepts(ticket: ContentResearchRequestTicket) {
    return ticket.workflowRunId === this.workflowRunId
      && ticket.epoch === this.epoch
      && ticket.generation === this.generations.get(ticket.channel);
  }
}

export class ContentResearchTraceRevisionGuard {
  private revision = -1;
  private consecutiveFailures = 0;

  accept(revision: number) {
    if (!Number.isSafeInteger(revision) || revision < 0 || revision < this.revision) {
      return false;
    }
    this.revision = revision;
    this.consecutiveFailures = 0;
    return true;
  }

  recordFailure() {
    this.consecutiveFailures += 1;
    return this.consecutiveFailures >= 3;
  }

  isUncertain() {
    return this.consecutiveFailures >= 3;
  }

  minimumRevision() {
    return this.revision >= 0 ? this.revision : undefined;
  }

  reset() {
    this.revision = -1;
    this.consecutiveFailures = 0;
  }
}

export class LatestScopeDraftSaveQueue<Snapshot, Authority> {
  private authority: Authority;
  private pending: Snapshot | null = null;
  private draining: Promise<void> | null = null;

  constructor(private readonly options: {
    initialAuthority: Authority;
    send: (authority: Authority, snapshot: Snapshot) => Promise<Authority>;
    recover?: (
      error: unknown,
      authority: Authority,
      snapshot: Snapshot,
    ) => Promise<{ authority: Authority; accepted: boolean }>;
    onAuthority?: (authority: Authority, snapshot: Snapshot) => void;
    onAccepted: (authority: Authority, snapshot: Snapshot) => void;
    onError?: (error: unknown) => void;
  }) {
    this.authority = options.initialAuthority;
  }

  enqueue(snapshot: Snapshot) {
    this.pending = snapshot;
    if (!this.draining) {
      this.draining = this.drain().finally(() => { this.draining = null; });
    }
  }

  async idle() {
    while (this.draining) await this.draining;
  }

  currentAuthority() {
    return this.authority;
  }

  private async drain() {
    while (this.pending) {
      const snapshot = this.pending;
      this.pending = null;
      let retriedAfterRecovery = false;
      while (true) {
        try {
          this.authority = await this.options.send(this.authority, snapshot);
          this.options.onAuthority?.(this.authority, snapshot);
          if (!this.pending) this.options.onAccepted(this.authority, snapshot);
          break;
        } catch (error) {
          if (!this.options.recover) {
            this.options.onError?.(error);
            break;
          }
          try {
            const recovered = await this.options.recover(
              error,
              this.authority,
              snapshot,
            );
            this.authority = recovered.authority;
            this.options.onAuthority?.(this.authority, snapshot);
            if (recovered.accepted) {
              if (!this.pending) this.options.onAccepted(this.authority, snapshot);
              break;
            }
          } catch (recoveryError) {
            this.options.onError?.(recoveryError);
            break;
          }
          // A newer local edit supersedes the ambiguous failed snapshot. It
          // will be sent by the outer loop using the refreshed authority.
          if (this.pending) break;
          if (retriedAfterRecovery) {
            this.options.onError?.(error);
            break;
          }
          retriedAfterRecovery = true;
        }
      }
    }
  }
}

interface ComparableScopeDraftSnapshot {
  core_object: string;
  product_experience_aspect?: string | null;
  context_audience_aspect?: string | null;
}

function normalizedScopeDraftTerm(value: string | null | undefined) {
  const normalized = String(value ?? "").trim().replace(/\s+/g, " ");
  return normalized || null;
}

export function scopeDraftSnapshotMatches(
  persisted: ComparableScopeDraftSnapshot,
  local: ComparableScopeDraftSnapshot,
) {
  return normalizedScopeDraftTerm(persisted.core_object)
      === normalizedScopeDraftTerm(local.core_object)
    && normalizedScopeDraftTerm(persisted.product_experience_aspect)
      === normalizedScopeDraftTerm(local.product_experience_aspect)
    && normalizedScopeDraftTerm(persisted.context_audience_aspect)
      === normalizedScopeDraftTerm(local.context_audience_aspect);
}

export function frozenReportScopeQueries(report: { frozen_scope?: Record<string, unknown> }) {
  const groups = report.frozen_scope?.query_groups;
  if (!Array.isArray(groups)) return [];
  return groups.flatMap((group) => {
    if (!group || typeof group !== "object" || Array.isArray(group)) return [];
    const query = (group as Record<string, unknown>).final_query;
    return typeof query === "string" && query ? [query] : [];
  });
}
