"""Queue dispatcher for WorkflowRun step execution.

This bugfix restores the workflow-v2 execution loop: a run is not useful until
its pending steps are enqueued, and a completed step must drive the next step.
"""

from __future__ import annotations

from typing import Optional

from app.config import settings
from app.memory.job_store import JobStore
from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowArtifact, WorkflowRunStatus, WorkflowStep
from app.services.workflow_run_manager import WorkflowRunManager, WorkflowTransitionError


class WorkflowStepDispatcher:
    """Owns workflow step queueing and run completion side effects."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or settings.SQLITE_DB_PATH

    async def enqueue_first_step(self, run_id: str) -> Optional[str]:
        return await self._enqueue_next_step(run_id)

    async def enqueue_next_step_or_complete(self, run_id: str) -> Optional[str]:
        enqueued = await self._enqueue_next_step(run_id)
        if enqueued is not None:
            return enqueued
        if await self._all_steps_succeeded_or_skipped(run_id):
            await self._complete_run_and_append_result(run_id)
        return None

    async def _enqueue_next_step(self, run_id: str) -> Optional[str]:
        async with WorkflowStore(self.db_path) as store:
            run = await store.get_run(run_id)
            steps = await store.list_steps(run_id)
        if run is None or run.status != WorkflowRunStatus.RUNNING:
            return None
        next_step = self._first_runnable_step(steps)
        if next_step is None:
            return None

        async with JobStore(self.db_path) as job_store:
            await job_store.enqueue(
                session_id=run_id,
                job_type="strategy",
                payload={"step_name": next_step.step_name},
                idempotency_key=f"workflow-step:{run_id}:{next_step.step_id}",
                run_id=run_id,
                step_id=next_step.step_id,
                max_attempts=next_step.max_attempts,
            )
        return next_step.step_name

    @staticmethod
    def _first_runnable_step(steps: list[WorkflowStep]) -> Optional[WorkflowStep]:
        for step in steps:
            if step.status.value in {"pending", "retrying"}:
                return step
        return None

    async def _all_steps_succeeded_or_skipped(self, run_id: str) -> bool:
        async with WorkflowStore(self.db_path) as store:
            steps = await store.list_steps(run_id)
        return bool(steps) and all(step.status.value in {"succeeded", "skipped"} for step in steps)

    async def _complete_run_and_append_result(self, run_id: str) -> None:
        async with WorkflowRunManager(self.db_path) as manager:
            try:
                run = await manager.complete_run(run_id)
            except WorkflowTransitionError:
                return

        refs = await self._result_artifact_refs(run_id)
        if not refs:
            return
        async with ThreadStore(self.db_path) as thread_store:
            await thread_store.append_artifact_result_message(
                thread_id=run.thread_id,
                run_id=run_id,
                artifact_refs=refs,
                text="创作结果已生成。",
                idempotent=True,
            )

    async def _result_artifact_refs(self, run_id: str) -> list[dict]:
        async with WorkflowStore(self.db_path) as store:
            artifacts = await store.list_artifacts(run_id)
        selected = self._preferred_result_artifacts(artifacts)
        return [
            {
                "artifact_id": artifact.artifact_id,
                "artifact_type": artifact.artifact_type.value,
                "artifact_version": artifact.artifact_version,
                "parent_artifact_id": artifact.parent_artifact_id,
            }
            for artifact in selected
        ]

    @staticmethod
    def _preferred_result_artifacts(artifacts: list[WorkflowArtifact]) -> list[WorkflowArtifact]:
        final = [item for item in artifacts if item.artifact_type.value == "final_result"]
        if final:
            return final[-1:]
        notes = [item for item in artifacts if item.artifact_type.value == "generated_note"]
        if notes:
            return notes
        strategies = [item for item in artifacts if item.artifact_type.value == "strategy"]
        return strategies[-1:]
