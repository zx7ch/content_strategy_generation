"""Cancellation workflow action."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from app.content_research.api_schemas import ContentResearchWorkflowActionResponse
from app.content_research.commands.dispatcher import WorkflowActionContext
from app.content_research.lifecycle.models import ContentResearchState, LifecycleCommand


@dataclass(frozen=True)
class CancelHandler:
    action: ClassVar[str] = "cancel"

    async def execute(
        self,
        context: WorkflowActionContext,
    ) -> ContentResearchWorkflowActionResponse:
        application = context.application
        request = context.request
        cancelled = await application._lifecycle.apply(
            LifecycleCommand(
                command_id=request.command_id,
                run_id=context.workflow_run_id,
                expected_state=ContentResearchState(request.expected_state),
                expected_revision=request.expected_revision,
                kind=self.action,
                payload=request.payload,
            )
        )
        return application._action_response(
            workflow_run_id=context.workflow_run_id,
            action=self.action,
            status="completed",
            result={"run": application._run_projection_payload(cancelled)},
            local_cache_id=cancelled.brief_id,
        )
