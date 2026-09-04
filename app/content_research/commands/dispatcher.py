"""Registry-driven dispatch for user-visible workflow actions.

This module owns orchestration dispatch only. Persisted lifecycle state,
revision guards, idempotency, and transactional transitions remain owned by
the lifecycle coordinator invoked by individual handlers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from app.content_research.api_schemas import (
    P0_WORKFLOW_ACTIONS,
    ContentResearchWorkflowActionRequest,
    ContentResearchWorkflowActionResponse,
)
from app.content_research.errors import (
    ContentResearchNotFoundError,
    ContentResearchValidationError,
)
from app.content_research.models import ResearchBriefRecord

if TYPE_CHECKING:
    from app.content_research.command import ContentResearchCommandService
    from app.content_research.service import ContentResearchService


@dataclass(frozen=True)
class WorkflowActionContext:
    """Dependencies and request facts shared by one action execution."""

    application: ContentResearchService
    command_service: ContentResearchCommandService
    workflow_run_id: str
    request: ContentResearchWorkflowActionRequest

    def require_brief(self) -> ResearchBriefRecord:
        brief = self.application._store.get_brief_by_workflow(self.workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(
                f"Content research workflow not found: {self.workflow_run_id}"
            )
        return brief


class WorkflowActionHandler(Protocol):
    """Internal seam implemented once for each public workflow action."""

    action: str

    async def execute(
        self,
        context: WorkflowActionContext,
    ) -> ContentResearchWorkflowActionResponse: ...


class WorkflowActionDispatcher:
    """Selects one action handler without becoming a lifecycle authority."""

    def __init__(self, handlers: tuple[WorkflowActionHandler, ...]) -> None:
        registry: dict[str, WorkflowActionHandler] = {}
        for handler in handlers:
            if handler.action in registry:
                raise ValueError(f"duplicate workflow action handler: {handler.action}")
            registry[handler.action] = handler
        self._registry = registry

    @property
    def registered_actions(self) -> tuple[str, ...]:
        return tuple(self._registry)

    async def dispatch(
        self,
        context: WorkflowActionContext,
    ) -> ContentResearchWorkflowActionResponse:
        action = context.request.action.strip()
        if action not in P0_WORKFLOW_ACTIONS:
            raise ContentResearchValidationError(
                f"Unsupported Content Research workflow action: {action}"
            )
        handler = self._registry.get(action)
        if handler is None:
            raise AssertionError(f"validated P0 action has no handler: {action}")
        return await handler.execute(context)


def build_workflow_action_dispatcher() -> WorkflowActionDispatcher:
    """Build the complete P0 registry in public contract order."""

    from app.content_research.commands.cancel import CancelHandler
    from app.content_research.commands.coverage import (
        ExpandCoverageHandler,
        GenerateLimitedReportHandler,
        RelaxCoverageHandler,
    )
    from app.content_research.commands.publication import RepairPublicationHandler
    from app.content_research.commands.recovery import (
        RetryAnalysisHandler,
        RetryPresearchHandler,
        RetryReportHandler,
        RetryRetrievalHandler,
    )
    from app.content_research.commands.scope import (
        ConfirmBriefHandler,
        ConfirmScopeHandler,
        ReplaceScopeDraftHandler,
        ReviseSubjectHandler,
    )

    dispatcher = WorkflowActionDispatcher(
        (
            CancelHandler(),
            RetryPresearchHandler(),
            RetryRetrievalHandler(),
            RetryAnalysisHandler(),
            RetryReportHandler(),
            RepairPublicationHandler(),
            ReviseSubjectHandler(),
            ConfirmBriefHandler(),
            ReplaceScopeDraftHandler(),
            ConfirmScopeHandler(),
            ExpandCoverageHandler(),
            RelaxCoverageHandler(),
            GenerateLimitedReportHandler(),
        )
    )
    if dispatcher.registered_actions != P0_WORKFLOW_ACTIONS:
        raise AssertionError("workflow action registry does not match P0 contract")
    return dispatcher
