"""User-owned Coverage decisions for an insufficient frozen Scope."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from app.content_research.api_schemas import (
    ContentResearchWorkflowActionResponse,
    ResolveCoverageRequest,
)
from app.content_research.commands.dispatcher import WorkflowActionContext
from app.content_research.lifecycle.coordinator import LifecycleCommandConflict
from app.content_research.lifecycle.models import ContentResearchState, LifecycleCommand

_RESOLUTION_BY_ACTION = {
    "expand_coverage": "expand_required_constraint",
    "relax_coverage": "relax_constraint",
    "generate_limited_report": "generate_limited_report",
}


@dataclass(frozen=True)
class ResolveCoverageHandler:
    action: ClassVar[str]

    async def execute(
        self,
        context: WorkflowActionContext,
    ) -> ContentResearchWorkflowActionResponse:
        application = context.application
        brief = context.require_brief()
        request = context.request
        declared_state = ContentResearchState(request.expected_state)
        if declared_state is not ContentResearchState.COVERAGE_DECISION_REQUIRED:
            raise LifecycleCommandConflict(
                f"{self.action} requires expected_state coverage_decision_required"
            )
        resolution = ResolveCoverageRequest(**request.payload)
        expected_resolution = _RESOLUTION_BY_ACTION[self.action]
        if resolution.resolution != expected_resolution:
            raise LifecycleCommandConflict(
                f"{self.action} requires resolution {expected_resolution}"
            )
        coverage, projection = await application._resolve_coverage(
            workflow_run_id=context.workflow_run_id,
            request=resolution,
            lifecycle_command=LifecycleCommand(
                command_id=request.command_id,
                run_id=context.workflow_run_id,
                expected_state=declared_state,
                expected_revision=request.expected_revision,
                kind=self.action,
                payload=request.payload,
            ),
        )
        return application._action_response(
            workflow_run_id=context.workflow_run_id,
            action=self.action,
            status="queued",
            result={
                "run": application._run_projection_payload(projection),
                "coverage": coverage,
            },
            local_cache_id=brief.id,
        )


@dataclass(frozen=True)
class ExpandCoverageHandler(ResolveCoverageHandler):
    action: ClassVar[str] = "expand_coverage"


@dataclass(frozen=True)
class RelaxCoverageHandler(ResolveCoverageHandler):
    action: ClassVar[str] = "relax_coverage"


@dataclass(frozen=True)
class GenerateLimitedReportHandler(ResolveCoverageHandler):
    action: ClassVar[str] = "generate_limited_report"
