"""User-command seam for Content Research lifecycle mutations.

The protocol is the stable dependency for HTTP mutation routes. The legacy
adapter is temporary migration scaffolding; command families move behind this
seam without changing lifecycle authority, transaction boundaries, or public
errors.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol

from app.content_research.api_schemas import (
    ContentResearchPresearchResponse,
    ContentResearchWorkflowActionRequest,
    ContentResearchWorkflowActionResponse,
    HumanDecisionRequest,
    HumanDecisionResponse,
)
from app.content_research.commands.dispatcher import (
    WorkflowActionContext,
    build_workflow_action_dispatcher,
)
from app.content_research.errors import (
    ContentResearchNotFoundError,
    ContentResearchValidationError,
)
from app.content_research.lifecycle.coordinator import LifecyclePersistenceBusy
from app.content_research.lifecycle.models import (
    ContentResearchState,
    LifecycleCommand,
    RunProjection,
)
from app.content_research.presearch.service import PresearchInput, PresearchOutcome

if TYPE_CHECKING:
    from app.content_research.service import ContentResearchService


class ContentResearchCommand(Protocol):
    """Public user mutations; lifecycle transitions remain coordinator-owned."""

    async def submit_presearch(
        self,
        *,
        command_id: str,
        seed_text: str,
        user_note: str | None,
        thread_id: str,
        workspace_id: str,
        user_id: str,
    ) -> ContentResearchPresearchResponse: ...

    async def submit_brand_decision(
        self,
        *,
        workflow_run_id: str,
        request: HumanDecisionRequest,
        user_id: str,
    ) -> HumanDecisionResponse: ...

    async def submit_content_decision(
        self,
        *,
        workflow_run_id: str,
        request: HumanDecisionRequest,
        user_id: str,
    ) -> HumanDecisionResponse: ...

    async def run_workflow_action(
        self,
        *,
        workflow_run_id: str,
        request: ContentResearchWorkflowActionRequest,
    ) -> ContentResearchWorkflowActionResponse: ...


class LegacyContentResearchCommandAdapter:
    """Temporary compatibility adapter over the pre-refactor application module."""

    def __init__(self, source: ContentResearchCommand) -> None:
        self._source = source

    def is_for(self, source: ContentResearchCommand) -> bool:
        return self._source is source

    async def submit_presearch(
        self,
        *,
        command_id: str,
        seed_text: str,
        user_note: str | None,
        thread_id: str,
        workspace_id: str,
        user_id: str,
    ) -> ContentResearchPresearchResponse:
        return await self._source.submit_presearch(
            command_id=command_id,
            seed_text=seed_text,
            user_note=user_note,
            thread_id=thread_id,
            workspace_id=workspace_id,
            user_id=user_id,
        )

    async def submit_brand_decision(
        self,
        *,
        workflow_run_id: str,
        request: HumanDecisionRequest,
        user_id: str,
    ) -> HumanDecisionResponse:
        return await self._source.submit_brand_decision(
            workflow_run_id=workflow_run_id,
            request=request,
            user_id=user_id,
        )

    async def submit_content_decision(
        self,
        *,
        workflow_run_id: str,
        request: HumanDecisionRequest,
        user_id: str,
    ) -> HumanDecisionResponse:
        return await self._source.submit_content_decision(
            workflow_run_id=workflow_run_id,
            request=request,
            user_id=user_id,
        )

    async def run_workflow_action(
        self,
        *,
        workflow_run_id: str,
        request: ContentResearchWorkflowActionRequest,
    ) -> ContentResearchWorkflowActionResponse:
        return await self._source.run_workflow_action(
            workflow_run_id=workflow_run_id,
            request=request,
        )


class ContentResearchCommandService(LegacyContentResearchCommandAdapter):
    """Command module whose implementation migrates one command family at a time."""

    def __init__(self, source: ContentResearchService) -> None:
        super().__init__(source)
        self._application = source
        self._workflow_action_dispatcher = build_workflow_action_dispatcher()

    async def submit_presearch(
        self,
        *,
        command_id: str,
        seed_text: str,
        user_note: str | None,
        thread_id: str,
        workspace_id: str,
        user_id: str,
    ) -> ContentResearchPresearchResponse:
        normalized_command_id = command_id.strip()
        if not normalized_command_id:
            raise ContentResearchValidationError("command_id is required")
        lock = self._application._presearch_command_locks.setdefault(
            normalized_command_id,
            asyncio.Lock(),
        )
        async with lock:
            return await self._submit_presearch_locked(
                command_id=normalized_command_id,
                seed_text=seed_text,
                user_note=user_note,
                thread_id=thread_id,
                user_id=user_id,
                workspace_id=workspace_id,
            )

    async def submit_brand_decision(
        self,
        *,
        workflow_run_id: str,
        request: HumanDecisionRequest,
        user_id: str,
    ) -> HumanDecisionResponse:
        return await self._submit_human_decision(
            workflow_run_id=workflow_run_id,
            target_type="brand_candidate",
            request=request,
            user_id=user_id,
        )

    async def submit_content_decision(
        self,
        *,
        workflow_run_id: str,
        request: HumanDecisionRequest,
        user_id: str,
    ) -> HumanDecisionResponse:
        return await self._submit_human_decision(
            workflow_run_id=workflow_run_id,
            target_type="recommended_content",
            request=request,
            user_id=user_id,
        )

    async def _submit_human_decision(
        self,
        *,
        workflow_run_id: str,
        target_type: str,
        request: HumanDecisionRequest,
        user_id: str,
    ) -> HumanDecisionResponse:
        application = self._application
        brief = application._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(
                f"Content research workflow not found: {workflow_run_id}"
            )
        response = await application._decision_service.submit_decision(
            brief=brief,
            target_type=target_type,
            request=request,
            user_id=user_id,
        )
        if response.idempotent_replay:
            advancement = application._decision_advancement_service.describe(
                brief=brief,
                decision=response,
            )
            return response.model_copy(
                update={"advancement": {**response.advancement, **advancement}}
            )
        advancement = application._decision_advancement_service.advance(
            brief=brief,
            decision=response,
        )
        await application._workflow_runtime.append_event(
            workflow_run_id=workflow_run_id,
            thread_id=brief.thread_id,
            event_type="decision_deep_research_advanced",
            payload={
                "schema_version": "content_research_workflow_event_payload_v1",
                "decision_id": response.decision_id,
                **advancement,
            },
        )
        return response.model_copy(
            update={"advancement": {**response.advancement, **advancement}}
        )

    async def run_workflow_action(
        self,
        *,
        workflow_run_id: str,
        request: ContentResearchWorkflowActionRequest,
    ) -> ContentResearchWorkflowActionResponse:
        return await self._workflow_action_dispatcher.dispatch(
            WorkflowActionContext(
                application=self._application,
                command_service=self,
                workflow_run_id=workflow_run_id,
                request=request,
            )
        )

    async def _submit_presearch_locked(
        self,
        *,
        command_id: str,
        seed_text: str,
        user_note: str | None,
        thread_id: str,
        user_id: str,
        workspace_id: str,
    ) -> ContentResearchPresearchResponse:
        application = self._application
        normalized_seed = seed_text.strip()
        if not normalized_seed:
            raise ContentResearchValidationError("seed_text is required")

        workflow_run_id = application._stable_id("run", command_id)
        submitted = await application._lifecycle.apply(
            LifecycleCommand(
                command_id=command_id,
                run_id=workflow_run_id,
                expected_state=None,
                expected_revision=0,
                kind="submit_research_subject",
                payload={
                    "thread_id": thread_id,
                    "user_id": user_id,
                    "seed_text": normalized_seed,
                    "user_note": user_note,
                    "workspace_id": workspace_id,
                },
            )
        )
        if submitted.brief_id is not None:
            existing_brief = application._store.get_brief(submitted.brief_id)
            if existing_brief is None:
                raise ContentResearchValidationError(
                    "lifecycle projection references a missing Brief"
                )
            return application._response_from_brief(
                existing_brief,
                run_projection=submitted,
            )
        attempt_id = application._stable_id("att", f"{command_id}:attempt")
        brief_id = application._stable_id("rb", f"{command_id}:brief")
        request = PresearchInput(
            seed_text=normalized_seed,
            user_note=user_note,
            thread_id=thread_id,
            workflow_run_id=workflow_run_id,
            user_id=user_id,
            workspace_id=workspace_id,
        )

        llm_task = await application._presearch.create_llm_task(request)
        outcome = await application._presearch.wait_for_first_feedback(
            request=request,
            task=llm_task,
        )
        if llm_task is not None and outcome.timeout_status == "first_timeout":
            settled = await application._presearch.wait_for_hard_cutoff(
                request=request,
                task=llm_task,
            )
            if settled is not None:
                outcome = settled
        brief_payload = {
            "brief_id": brief_id,
            "schema_version": "content_research_brief_v1",
            "brief_status": "draft" if outcome.status == "completed" else "failed",
            "subject": outcome.checklist.subject_confirmation or normalized_seed,
            "competitors": list(outcome.checklist.competitor_tags),
            "directions": list(outcome.checklist.research_directions)
            or ["product_marketing"],
            "attempt_id": attempt_id,
            "seed_text": normalized_seed,
            "user_note": user_note,
            "workspace_id": workspace_id,
            "user_id": user_id,
            **application._outcome_payload(outcome),
        }
        run_projection = await self._commit_presearch_outcome(
            command_id=command_id,
            workflow_run_id=workflow_run_id,
            expected_revision=1,
            brief_payload=brief_payload,
            outcome=outcome,
        )
        brief = application._store.get_brief(brief_id)
        assert brief is not None
        return application._response_from_brief(
            brief,
            run_projection=run_projection,
        )

    async def retry_presearch(
        self,
        workflow_run_id: str,
        *,
        command_id: str,
        expected_state: ContentResearchState,
        expected_revision: int,
        recovery_plan_id: str,
        plan_fingerprint: str,
    ) -> ContentResearchPresearchResponse:
        return await self._rerun_presearch(
            workflow_run_id=workflow_run_id,
            event="retry_presearch",
            expected_state=expected_state,
            expected_revision=expected_revision,
            command_id=command_id,
            clarification_text=None,
            recovery_plan_id=recovery_plan_id,
            plan_fingerprint=plan_fingerprint,
        )

    async def revise_subject(
        self,
        *,
        workflow_run_id: str,
        command_id: str,
        expected_state: ContentResearchState,
        expected_revision: int,
        clarification_text: str,
    ) -> ContentResearchPresearchResponse:
        clarification = clarification_text.strip()
        if not clarification:
            raise ContentResearchValidationError("clarification_text is required")
        return await self._rerun_presearch(
            workflow_run_id=workflow_run_id,
            event="revise_subject",
            expected_state=expected_state,
            expected_revision=expected_revision,
            command_id=command_id,
            clarification_text=clarification,
            recovery_plan_id=None,
            plan_fingerprint=None,
        )

    async def _rerun_presearch(
        self,
        *,
        workflow_run_id: str,
        event: str,
        expected_state: ContentResearchState,
        expected_revision: int,
        command_id: str,
        clarification_text: str | None,
        recovery_plan_id: str | None,
        plan_fingerprint: str | None,
    ) -> ContentResearchPresearchResponse:
        normalized_command_id = command_id.strip()
        if not normalized_command_id:
            raise ContentResearchValidationError("command_id is required")
        lock = self._application._presearch_command_locks.setdefault(
            normalized_command_id,
            asyncio.Lock(),
        )
        async with lock:
            return await self._rerun_presearch_locked(
                workflow_run_id=workflow_run_id,
                event=event,
                expected_state=expected_state,
                expected_revision=expected_revision,
                command_id=normalized_command_id,
                clarification_text=clarification_text,
                recovery_plan_id=recovery_plan_id,
                plan_fingerprint=plan_fingerprint,
            )

    async def _rerun_presearch_locked(
        self,
        *,
        workflow_run_id: str,
        event: str,
        expected_state: ContentResearchState,
        expected_revision: int,
        command_id: str,
        clarification_text: str | None,
        recovery_plan_id: str | None,
        plan_fingerprint: str | None,
    ) -> ContentResearchPresearchResponse:
        application = self._application
        brief = application._store.get_brief_by_workflow(workflow_run_id)
        if brief is None:
            raise ContentResearchNotFoundError(
                f"Content research workflow not found: {workflow_run_id}"
            )
        started = await application._lifecycle.apply(
            LifecycleCommand(
                command_id=command_id,
                run_id=workflow_run_id,
                expected_state=expected_state,
                expected_revision=expected_revision,
                kind=event,
                payload={
                    "clarification_text": clarification_text,
                    **(
                        {
                            "recovery_plan_id": recovery_plan_id,
                            "plan_fingerprint": plan_fingerprint,
                        }
                        if event == "retry_presearch"
                        else {}
                    ),
                },
            )
        )
        if started.state is not ContentResearchState.PRESEARCH_RUNNING:
            existing_brief = application._store.get_brief_by_workflow(
                workflow_run_id
            )
            if existing_brief is None:
                raise ContentResearchValidationError(
                    "lifecycle projection references a missing Brief"
                )
            return application._response_from_brief(
                existing_brief,
                run_projection=started,
            )
        previous = brief.payload
        prior_clarifications = [
            str(item).strip()
            for item in list(previous.get("subject_clarifications") or [])
            if str(item).strip()
        ]
        if clarification_text:
            prior_clarifications.append(clarification_text)
        original_note = str(previous.get("user_note") or "").strip()
        accumulated_note = "\n".join(
            item for item in [original_note, *prior_clarifications] if item
        )
        attempt_id = application._stable_id("att", f"{command_id}:attempt")
        request = PresearchInput(
            seed_text=str(previous["seed_text"]),
            user_note=accumulated_note or None,
            thread_id=brief.thread_id,
            workflow_run_id=workflow_run_id,
            user_id=str(previous.get("user_id") or "default"),
            workspace_id=str(previous.get("workspace_id") or "default"),
        )
        llm_task = await application._presearch.create_llm_task(request)
        outcome = await application._presearch.wait_for_first_feedback(
            request=request,
            task=llm_task,
        )
        if llm_task is not None and outcome.timeout_status == "first_timeout":
            settled = await application._presearch.wait_for_hard_cutoff(
                request=request,
                task=llm_task,
            )
            if settled is not None:
                outcome = settled
        brief_payload = {
            "brief_id": brief.id,
            "schema_version": "content_research_brief_v1",
            "brief_status": "draft" if outcome.status == "completed" else "failed",
            "subject": outcome.checklist.subject_confirmation or request.seed_text,
            "competitors": list(outcome.checklist.competitor_tags),
            "directions": list(outcome.checklist.research_directions)
            or ["product_marketing"],
            "attempt_id": attempt_id,
            "seed_text": request.seed_text,
            "user_note": request.user_note,
            "workspace_id": request.workspace_id,
            "user_id": request.user_id,
            "subject_clarifications": prior_clarifications,
            **application._outcome_payload(outcome),
        }
        projection = await self._commit_presearch_outcome(
            command_id=command_id,
            workflow_run_id=workflow_run_id,
            expected_revision=started.state_revision,
            brief_payload=brief_payload,
            outcome=outcome,
        )
        updated = application._store.get_brief(brief.id)
        assert updated is not None
        return application._response_from_brief(
            updated,
            run_projection=projection,
        )

    async def _commit_presearch_outcome(
        self,
        *,
        command_id: str,
        workflow_run_id: str,
        expected_revision: int,
        brief_payload: dict[str, Any],
        outcome: PresearchOutcome,
    ) -> RunProjection:
        application = self._application
        error = {
            "code": outcome.error_code or "PRESEARCH_FAILED",
            "stage": "presearch",
            "operation": "llm_presearch",
            "message": outcome.error_message or "轻量预检索未能完成。",
            "retryable": bool(outcome.recoverable),
            "recovery_action": "retry_presearch",
        }
        try:
            return await application._lifecycle.apply(
                LifecycleCommand(
                    command_id=f"{command_id}:presearch-outcome",
                    run_id=workflow_run_id,
                    expected_state=ContentResearchState.PRESEARCH_RUNNING,
                    expected_revision=expected_revision,
                    kind=(
                        "presearch_completed"
                        if outcome.status == "completed"
                        else "fail"
                    ),
                    payload=(
                        brief_payload
                        if outcome.status == "completed"
                        else {**brief_payload, "error": error}
                    ),
                )
            )
        except LifecyclePersistenceBusy:
            persistence_error = {
                "code": "LOCAL_PERSISTENCE_BUSY",
                "stage": "presearch",
                "operation": "persist_presearch_outcome",
                "message": "本地数据写入暂时繁忙，自动重试未成功。",
                "retryable": True,
                "automatic_attempts": 3,
                "recovery_action": "retry_presearch",
            }
            recovery_command = LifecycleCommand(
                command_id=f"{command_id}:persistence-failure",
                run_id=workflow_run_id,
                expected_state=ContentResearchState.PRESEARCH_RUNNING,
                expected_revision=expected_revision,
                kind="fail",
                payload={
                    **brief_payload,
                    "brief_status": "failed",
                    "status": "failed",
                    "error_code": persistence_error["code"],
                    "error_message": persistence_error["message"],
                    "recoverable": True,
                    "error": persistence_error,
                },
            )
            try:
                current = await application._lifecycle.load(workflow_run_id)
            except LifecyclePersistenceBusy:
                application._schedule_lifecycle_reconciliation(recovery_command)
                raise
            if current.state is not ContentResearchState.PRESEARCH_RUNNING:
                return current
            try:
                return await application._lifecycle.apply(recovery_command)
            except LifecyclePersistenceBusy:
                application._schedule_lifecycle_reconciliation(recovery_command)
                raise
