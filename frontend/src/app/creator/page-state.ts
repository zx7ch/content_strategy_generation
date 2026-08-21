export interface ContentResearchRequestTicket {
  workflowRunId: string;
  epoch: number;
  channel: string;
  generation: number;
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

export function frozenReportScopeQueries(report: { frozen_scope?: Record<string, unknown> }) {
  const groups = report.frozen_scope?.query_groups;
  if (!Array.isArray(groups)) return [];
  return groups.flatMap((group) => {
    if (!group || typeof group !== "object" || Array.isArray(group)) return [];
    const query = (group as Record<string, unknown>).final_query;
    return typeof query === "string" && query ? [query] : [];
  });
}
