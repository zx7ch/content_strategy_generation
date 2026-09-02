from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.content_research.api_schemas import ContentResearchWorkflowActionRequest
from app.content_research.commands.dispatcher import WorkflowActionContext
from app.content_research.commands.recovery import RetryRetrievalHandler
from app.content_research.lifecycle.coordinator import LifecycleCommandConflict
from app.content_research.lifecycle.models import ContentResearchState


class _LifecycleRejectingForgedPlan:
    async def load(self, _workflow_run_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            state=ContentResearchState.RECOVERY_REQUIRED,
            allowed_actions=("retry_retrieval", "cancel"),
        )

    async def apply(self, _command: object) -> None:
        raise LifecycleCommandConflict("recovery plan is unavailable or stale")


class _RuntimeSnapshot:
    async def get_runtime_snapshot(self, _workflow_run_id: str) -> dict:
        return {"child_tasks": []}


class _RecoveryApplication:
    def __init__(self) -> None:
        self._store = SimpleNamespace(
            get_brief_by_workflow=lambda _workflow_run_id: SimpleNamespace(id="brief-1")
        )
        self._lifecycle = _LifecycleRejectingForgedPlan()
        self._workflow_runtime = _RuntimeSnapshot()
        self.requeue_calls = 0

    def _requeue_recoverable_tasks(self, *_args: object, **_kwargs: object) -> list[str]:
        if _kwargs.get("apply_changes", True):
            self.requeue_calls += 1
        return []


@pytest.mark.asyncio
async def test_forged_retrieval_plan_has_no_prevalidation_side_effects() -> None:
    application = _RecoveryApplication()
    context = WorkflowActionContext(
        application=application,  # type: ignore[arg-type]
        command_service=SimpleNamespace(),  # type: ignore[arg-type]
        workflow_run_id="run-1",
        request=ContentResearchWorkflowActionRequest(
            command_id="forged-retrieval-plan",
            expected_state="recovery_required",
            expected_revision=4,
            action="retry_retrieval",
            payload={
                "recovery_plan_id": "forged-plan",
                "plan_fingerprint": "sha256:forged",
            },
        ),
    )

    with pytest.raises(LifecycleCommandConflict, match="recovery plan"):
        await RetryRetrievalHandler().execute(context)

    assert application.requeue_calls == 0
