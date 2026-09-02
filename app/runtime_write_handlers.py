"""Complete production handler set for the canonical SQLite Writer."""

from __future__ import annotations

from app.content_research.analysis_mutations import content_research_analysis_handlers
from app.content_research.dispatch_mutations import content_research_dispatch_handlers
from app.content_research.lifecycle.mutations import content_research_lifecycle_handlers
from app.content_research.pipeline_mutations import content_research_pipeline_handlers
from app.content_research.reporting.mutations import content_research_reporting_handlers
from app.content_research.runtime_mutations import content_research_runtime_handlers
from app.content_research.stores.mutations import content_research_store_handlers
from app.core.runtime_write_coordinator import RuntimeMutationHandler
from app.memory.checkpoint_mutations import checkpoint_mutation_handlers
from app.memory.job_mutations import job_mutation_handlers
from app.memory.session_mutations import session_mutation_handlers
from app.memory.thread_mutations import thread_mutation_handlers
from app.memory.workflow_mutations import workflow_mutation_handlers
from app.services.runtime_accounting_mutations import runtime_accounting_mutation_handlers
from app.services.workflow_run_mutations import workflow_run_mutation_handlers
from app.v2.discovery.mutations import discovery_mutation_handlers
from app.v2.foundation.mutations import foundation_mutation_handlers


def production_runtime_write_handlers() -> tuple[RuntimeMutationHandler, ...]:
    return (
        *job_mutation_handlers(),
        *thread_mutation_handlers(),
        *workflow_mutation_handlers(),
        *session_mutation_handlers(),
        *checkpoint_mutation_handlers(),
        *workflow_run_mutation_handlers(),
        *runtime_accounting_mutation_handlers(),
        *foundation_mutation_handlers(),
        *discovery_mutation_handlers(),
        *content_research_lifecycle_handlers(),
        *content_research_store_handlers(),
        *content_research_analysis_handlers(),
        *content_research_dispatch_handlers(),
        *content_research_pipeline_handlers(),
        *content_research_reporting_handlers(),
        *content_research_runtime_handlers(),
    )
