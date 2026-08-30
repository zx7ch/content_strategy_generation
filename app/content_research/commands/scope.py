"""Brief and scope workflow actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from app.content_research.api_schemas import (
    ContentResearchBriefConfirmationRequest,
    ContentResearchSubjectRevisionRequest,
    ContentResearchWorkflowActionResponse,
    ReplaceScopeDraftRequest,
)
from app.content_research.commands.dispatcher import WorkflowActionContext
from app.content_research.contracts import freeze_provider_capabilities
from app.content_research.lifecycle.coordinator import LifecycleCommandConflict
from app.content_research.lifecycle.models import ContentResearchState, LifecycleCommand


@dataclass(frozen=True)
class ReviseSubjectHandler:
    action: ClassVar[str] = "revise_subject"

    async def execute(
        self,
        context: WorkflowActionContext,
    ) -> ContentResearchWorkflowActionResponse:
        context.require_brief()
        request = context.request
        declared_state = ContentResearchState(request.expected_state)
        if declared_state is not ContentResearchState.BRIEF_CONFIRMATION_REQUIRED:
            raise LifecycleCommandConflict(
                "revise_subject requires expected_state brief_confirmation_required"
            )
        clarification = ContentResearchSubjectRevisionRequest(**request.payload)
        response = await context.command_service.revise_subject(
            workflow_run_id=context.workflow_run_id,
            command_id=request.command_id,
            expected_state=declared_state,
            expected_revision=request.expected_revision,
            clarification_text=clarification.clarification_text,
        )
        return context.application._action_response(
            workflow_run_id=context.workflow_run_id,
            action=self.action,
            status=response.status,
            result=response.model_dump(mode="json"),
            local_cache_id=response.brief_id,
        )


@dataclass(frozen=True)
class ConfirmBriefHandler:
    action: ClassVar[str] = "confirm_brief"

    async def execute(
        self,
        context: WorkflowActionContext,
    ) -> ContentResearchWorkflowActionResponse:
        application = context.application
        brief = context.require_brief()
        request = context.request
        confirmation = ContentResearchBriefConfirmationRequest(**request.payload)
        declared_state = ContentResearchState(request.expected_state)
        if declared_state is not ContentResearchState.BRIEF_CONFIRMATION_REQUIRED:
            raise LifecycleCommandConflict(
                "confirm_brief requires expected_state brief_confirmation_required"
            )
        projection = await application._lifecycle.apply(
            LifecycleCommand(
                command_id=request.command_id,
                run_id=context.workflow_run_id,
                expected_state=declared_state,
                expected_revision=request.expected_revision,
                kind=self.action,
                payload=application._build_confirm_brief_command_payload(
                    workflow_run_id=context.workflow_run_id,
                    brief=brief,
                    confirmation=confirmation,
                    command_id=request.command_id,
                ),
            )
        )
        scope = await application.query_interface.get_scope_projection(context.workflow_run_id)
        return application._action_response(
            workflow_run_id=context.workflow_run_id,
            action=self.action,
            status="completed",
            result={
                "run": application._run_projection_payload(projection),
                "scope": scope.model_dump(mode="json"),
            },
            local_cache_id=brief.id,
        )


@dataclass(frozen=True)
class ReplaceScopeDraftHandler:
    action: ClassVar[str] = "replace_scope_draft"

    async def execute(
        self,
        context: WorkflowActionContext,
    ) -> ContentResearchWorkflowActionResponse:
        application = context.application
        brief = context.require_brief()
        request = context.request
        replacement = ReplaceScopeDraftRequest(**request.payload)
        declared_state = ContentResearchState(request.expected_state)
        if declared_state is not ContentResearchState.SCOPE_CONFIRMATION_REQUIRED:
            raise LifecycleCommandConflict(
                "replace_scope_draft requires expected_state scope_confirmation_required"
            )
        latest = application._store.get_scope_draft(replacement.scope_draft_id)
        if latest is None or latest.workflow_run_id != context.workflow_run_id:
            raise LifecycleCommandConflict("Scope Draft does not belong to this Run")
        projection = await application._lifecycle.apply(
            LifecycleCommand(
                command_id=request.command_id,
                run_id=context.workflow_run_id,
                expected_state=declared_state,
                expected_revision=request.expected_revision,
                kind=self.action,
                payload=application._build_scope_draft_replacement_payload(
                    latest=latest,
                    replacement=replacement,
                    command_id=request.command_id,
                ),
            )
        )
        scope = await application.query_interface.get_scope_projection(context.workflow_run_id)
        return application._action_response(
            workflow_run_id=context.workflow_run_id,
            action=self.action,
            status="completed",
            result={
                "run": application._run_projection_payload(projection),
                "scope": scope.model_dump(mode="json"),
            },
            local_cache_id=brief.id,
        )


@dataclass(frozen=True)
class ConfirmScopeHandler:
    action: ClassVar[str] = "confirm_scope"

    async def execute(
        self,
        context: WorkflowActionContext,
    ) -> ContentResearchWorkflowActionResponse:
        application = context.application
        brief = context.require_brief()
        request = context.request
        declared_state = ContentResearchState(request.expected_state)
        if declared_state is not ContentResearchState.SCOPE_CONFIRMATION_REQUIRED:
            raise LifecycleCommandConflict(
                "confirm_scope requires expected_state scope_confirmation_required"
            )
        latest = application._store.get_latest_scope_draft(context.workflow_run_id)
        if latest is None:
            raise LifecycleCommandConflict("Scope Draft does not belong to this Run")
        requested_draft_id = str(request.payload.get("scope_draft_id") or "")
        if requested_draft_id != latest.id:
            raise LifecycleCommandConflict("Scope confirmation requires the latest draft")
        projection = await application._lifecycle.apply(
            LifecycleCommand(
                command_id=request.command_id,
                run_id=context.workflow_run_id,
                expected_state=declared_state,
                expected_revision=request.expected_revision,
                kind=self.action,
                payload={
                    "scope_draft_id": latest.id,
                    "provider": "xiaohongshu",
                    "source_kind": "search_result",
                    "limit": 20,
                    "provider_capabilities": freeze_provider_capabilities(
                        application._source_registry
                    )
                    or {},
                },
            )
        )
        scope = await application.query_interface.get_scope_projection(context.workflow_run_id)
        if application._dispatch_wake_event is not None:
            application._dispatch_wake_event.set()
        return application._action_response(
            workflow_run_id=context.workflow_run_id,
            action=self.action,
            status="queued",
            result={
                "run": application._run_projection_payload(projection),
                "scope": scope.model_dump(mode="json"),
            },
            local_cache_id=brief.id,
        )
