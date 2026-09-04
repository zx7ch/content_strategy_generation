"""Publication-repair workflow action."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from app.content_research.api_schemas import ContentResearchWorkflowActionResponse
from app.content_research.commands.dispatcher import WorkflowActionContext


@dataclass(frozen=True)
class RepairPublicationHandler:
    action: ClassVar[str] = "repair_publication"

    async def execute(
        self,
        context: WorkflowActionContext,
    ) -> ContentResearchWorkflowActionResponse:
        application = context.application
        request = context.request
        repaired = await application._repair_integrity_flagged_publication(
            workflow_run_id=context.workflow_run_id,
            command_id=request.command_id,
            expected_state=request.expected_state,
            expected_revision=request.expected_revision,
            publication_id=(
                str(request.payload.get("publication_id"))
                if request.payload.get("publication_id")
                else None
            ),
        )
        return application._action_response(
            workflow_run_id=context.workflow_run_id,
            action=self.action,
            status="completed",
            result={"publication_id": repaired.id},
            local_cache_id=repaired.id,
        )
