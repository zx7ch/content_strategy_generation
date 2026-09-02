"""Closed Writer mutation for materializing a published report artifact."""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Any

from app.core.runtime_write_coordinator import (
    DomainMutationRejectedError,
    MutationApplication,
    MutationIdentityConflictError,
    RuntimeMutationHandler,
    TypedMutation,
)


class _ReportArtifactMaterializationHandler:
    mutation_kind = "materialize_content_research_report_artifact"

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        from app.content_research.execution_lease import (
            workflow_dispatch_guard,
            workflow_execution_guard,
        )
        from app.content_research.scope_contract import (
            DispatchLeaseContext,
            ExecutionContext,
            ExecutionLeaseFencedError,
        )
        from app.models.workflow import (
            WorkflowArtifactPayloadMode,
            WorkflowArtifactType,
        )
        from app.services.workflow_run_manager import (
            WorkflowRunManager,
            WorkflowTransitionError,
        )
        from app.services.workflow_run_mutations import _AsyncConnectionFacade

        payload = dict(mutation.domain_payload)
        required = {
            "publication_id",
            "run_id",
            "artifact_payload",
            "parent_artifact_id",
            "summary_text",
            "execution_context",
            "dispatch_context",
        }
        if set(payload) != required:
            raise MutationIdentityConflictError()
        publication_id = payload["publication_id"]
        run_id = payload["run_id"]
        artifact_payload = payload["artifact_payload"]
        if (
            not isinstance(publication_id, str)
            or not isinstance(run_id, str)
            or not isinstance(artifact_payload, dict)
        ):
            raise MutationIdentityConflictError()

        previous_row_factory = connection.row_factory
        connection.row_factory = sqlite3.Row
        try:
            manager = WorkflowRunManager(":coordinator:")
            manager._conn = _AsyncConnectionFacade(connection)  # type: ignore[assignment]
            manager._transaction_depth = 1

            async def materialize() -> Any:
                execution_context = payload["execution_context"]
                dispatch_context = payload["dispatch_context"]
                if isinstance(execution_context, dict):
                    await workflow_execution_guard(
                        ExecutionContext(**execution_context),
                        operation="materialize_report_publication",
                    )(manager._conn)
                elif isinstance(dispatch_context, dict):
                    await workflow_dispatch_guard(
                        DispatchLeaseContext(**dispatch_context),
                        operation="materialize_report_publication",
                    )(manager._conn)

                flagged = await manager._conn.execute(
                    """SELECT 1 FROM content_research_report_integrity_events
                       WHERE publication_id=? AND event_type='integrity_flagged'
                       LIMIT 1""",
                    (publication_id,),
                )
                if await flagged.fetchone() is not None:
                    raise ValueError(
                        "cannot materialize an integrity-flagged report publication"
                    )

                return await manager.attach_artifact(
                    run_id=run_id,
                    artifact_type=WorkflowArtifactType.FINAL_RESULT,
                    payload=artifact_payload,
                    payload_mode=WorkflowArtifactPayloadMode.SNAPSHOT,
                    parent_artifact_id=payload["parent_artifact_id"],
                    summary_text=payload["summary_text"],
                )

            try:
                artifact = asyncio.run(materialize())
            except ExecutionLeaseFencedError as exc:
                return MutationApplication(
                    result_contract="content_research_report_artifact_result",
                    result_fields={"rejected": "lease_fenced", "message": str(exc)},
                )
            except (ValueError, WorkflowTransitionError) as exc:
                raise DomainMutationRejectedError(str(exc)) from exc
        finally:
            connection.row_factory = previous_row_factory

        return MutationApplication(
            result_contract="content_research_report_artifact_result",
            result_fields={"artifact": artifact.model_dump(mode="json")},
            advances_trace_revision=True,
        )


class _ReportPublicationCommitHandler:
    mutation_kind = "commit_content_research_report_publication"

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        from app.content_research.execution_lease import (
            workflow_dispatch_guard,
            workflow_execution_guard,
        )
        from app.content_research.lifecycle.coordinator import (
            ContentResearchPersistenceCoordinator,
            LifecycleCommandConflict,
        )
        from app.content_research.lifecycle.models import (
            ContentResearchState,
            LifecycleCommand,
        )
        from app.content_research.lifecycle.mutations import (
            AsyncSQLiteConnectionFacade,
            encode_run_projection,
        )
        from app.content_research.lifecycle.transitions import LifecycleTransitionError
        from app.content_research.scope_contract import (
            DispatchLeaseContext,
            ExecutionContext,
            ExecutionLeaseFencedError,
        )
        from app.memory.thread_store import ThreadStore
        from app.memory.workflow_store import WorkflowStore
        from app.models.workflow import (
            WorkflowArtifactPayloadMode,
            WorkflowArtifactType,
        )
        from app.services.workflow_run_manager import (
            WorkflowRunManager,
            WorkflowTransitionError,
        )
        from app.services.workflow_run_mutations import _AsyncConnectionFacade

        payload = dict(mutation.domain_payload)
        required = {
            "publication_id",
            "run_id",
            "expected_state",
            "expected_revision",
            "artifact_payload",
            "parent_artifact_id",
            "summary_text",
            "execution_context",
            "dispatch_context",
        }
        if set(payload) != required:
            raise MutationIdentityConflictError()
        publication_id = payload["publication_id"]
        run_id = payload["run_id"]
        artifact_payload = payload["artifact_payload"]
        if (
            not isinstance(publication_id, str)
            or not isinstance(run_id, str)
            or not isinstance(artifact_payload, dict)
            or payload["expected_state"] != ContentResearchState.REPORT_COMPOSING.value
            or not isinstance(payload["expected_revision"], int)
        ):
            raise MutationIdentityConflictError()

        previous_row_factory = connection.row_factory
        connection.row_factory = sqlite3.Row
        try:
            manager = WorkflowRunManager(":coordinator:")
            workflow_connection = _AsyncConnectionFacade(connection)
            manager._conn = workflow_connection  # type: ignore[assignment]
            manager._transaction_depth = 1
            coordinator = ContentResearchPersistenceCoordinator(":coordinator:")
            coordinator._borrowed_connection = AsyncSQLiteConnectionFacade(connection)
            thread_store = ThreadStore(":coordinator:")
            thread_store._writer = None
            thread_store._conn = workflow_connection  # type: ignore[assignment]

            async def commit() -> tuple[Any, Any]:
                execution_context = payload["execution_context"]
                dispatch_context = payload["dispatch_context"]
                if isinstance(execution_context, dict):
                    await workflow_execution_guard(
                        ExecutionContext(**execution_context),
                        operation="commit_report_publication",
                    )(manager._conn)
                elif isinstance(dispatch_context, dict):
                    await workflow_dispatch_guard(
                        DispatchLeaseContext(**dispatch_context),
                        operation="commit_report_publication",
                    )(manager._conn)

                run = connection.execute(
                    """SELECT run.*, attempt.state AS analysis_state,
                              unit.workflow_run_id AS analysis_run_id
                       FROM workflow_runs AS run
                       LEFT JOIN content_research_analysis_attempts AS attempt
                         ON attempt.id=run.effective_analysis_attempt_id
                       LEFT JOIN content_research_analysis_units AS unit
                         ON unit.id=attempt.analysis_unit_id
                       WHERE run.run_id=?""",
                    (run_id,),
                ).fetchone()
                if (
                    run is None
                    or run["status"] != "finalizing_report"
                    or run["content_research_state"] != payload["expected_state"]
                    or int(run["state_revision"] or 0) != payload["expected_revision"]
                ):
                    raise ValueError("report publication authority is stale")
                if (
                    run["effective_analysis_attempt_id"] is not None
                    and (
                        run["analysis_state"] != "succeeded"
                        or run["analysis_run_id"] != run_id
                    )
                ):
                    raise ValueError("effective analysis attempt is stale")
                publication = connection.execute(
                    """SELECT 1 FROM content_research_report_publications
                       WHERE id=? AND workflow_run_id=?""",
                    (publication_id, run_id),
                ).fetchone()
                if publication is None:
                    raise ValueError("report publication lineage is stale")
                flagged = connection.execute(
                    """SELECT 1 FROM content_research_report_integrity_events
                       WHERE publication_id=? AND event_type='integrity_flagged'
                       LIMIT 1""",
                    (publication_id,),
                ).fetchone()
                if flagged is not None:
                    raise ValueError(
                        "cannot commit an integrity-flagged report publication"
                    )

                existing_row = connection.execute(
                    """SELECT * FROM workflow_artifacts
                       WHERE run_id=? AND artifact_type='final_result'
                         AND json_extract(payload_json, '$.report_publication_id')=?
                       LIMIT 1""",
                    (run_id, publication_id),
                ).fetchone()
                artifact = (
                    WorkflowStore._row_to_artifact(existing_row)
                    if existing_row is not None
                    else await manager.attach_artifact(
                        run_id=run_id,
                        artifact_type=WorkflowArtifactType.FINAL_RESULT,
                        payload=artifact_payload,
                        payload_mode=WorkflowArtifactPayloadMode.SNAPSHOT,
                        parent_artifact_id=payload["parent_artifact_id"],
                        summary_text=payload["summary_text"],
                    )
                )
                await manager.complete_report_finalization(run_id)
                projection = await coordinator._apply_once(
                    LifecycleCommand(
                        command_id=f"report-publication:{publication_id}",
                        run_id=run_id,
                        expected_state=ContentResearchState.REPORT_COMPOSING,
                        expected_revision=payload["expected_revision"],
                        kind="report_published",
                        payload={"publication_id": publication_id},
                    )
                )
                await thread_store.append_artifact_result_message(
                    thread_id=projection.thread_id,
                    run_id=run_id,
                    artifact_refs=[
                        {
                            "artifact_id": artifact.artifact_id,
                            "artifact_type": artifact.artifact_type.value,
                            "artifact_version": artifact.artifact_version,
                            "parent_artifact_id": artifact.parent_artifact_id,
                        }
                    ],
                    text="内容调研报告已生成。",
                    idempotent=True,
                )
                return artifact, projection

            try:
                artifact, projection = asyncio.run(commit())
            except ExecutionLeaseFencedError as exc:
                return MutationApplication(
                    result_contract="content_research_report_commit_result",
                    result_fields={"rejected": "lease_fenced", "message": str(exc)},
                )
            except (
                LifecycleCommandConflict,
                LifecycleTransitionError,
                ValueError,
                WorkflowTransitionError,
            ) as exc:
                raise DomainMutationRejectedError(str(exc)) from exc
        finally:
            connection.row_factory = previous_row_factory

        return MutationApplication(
            result_contract="content_research_report_commit_result",
            result_fields={
                "artifact": artifact.model_dump(mode="json"),
                "projection": encode_run_projection(projection),
            },
            committed_revision=projection.state_revision,
            advances_trace_revision=True,
        )


def content_research_reporting_handlers() -> tuple[RuntimeMutationHandler, ...]:
    return (
        _ReportArtifactMaterializationHandler(),
        _ReportPublicationCommitHandler(),
    )
