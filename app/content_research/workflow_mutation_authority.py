"""Server-owned projection and guard for legacy workflow recovery commands."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from app.content_research.persistence_models import (
    DirectionalEvidencePacketRecord,
    ReportPublicationRecord,
    StageCheckpointRecord,
)
from app.memory.workflow_store import WorkflowStore

_RECOVERABLE_RUN_STATES = {"failed", "paused", "waiting_user", "running"}
_RECOVERABLE_FAILURE_CODES = {
    "auth_required",
    "auth_expired",
    "timeout",
    "transient_error",
    "rate_limited",
    "unavailable",
}
_FAILURE_CHECKPOINT_STATUSES = {
    "failed",
    "failed_recoverable",
    "outcome_unknown",
    "auth_required",
    "rate_limited",
    "timed_out",
}


class ScopeAuthorityStore(Protocol):
    def get_unresolved_coverage_snapshot(self, workflow_run_id: str): ...

    def list_scope_execution_authorizations(self, workflow_run_id: str) -> list: ...

    def list_scope_execution_units(self, workflow_run_id: str) -> list: ...

    def list_subagent_tasks_for_workflow(self, workflow_run_id: str) -> list: ...

    def list_typed_records(self, record_type: type) -> list: ...


@dataclass(frozen=True)
class WorkflowMutationAction:
    action: str
    request: Mapping[str, Any]


@dataclass(frozen=True)
class LegacyRecoveryAuthority:
    actions: tuple[WorkflowMutationAction, ...] = ()
    reason_code: str | None = None
    completed_stages: tuple[str, ...] = ()
    unavailable_reason: str | None = None

    @property
    def available(self) -> bool:
        return bool(self.actions)

    def require(self, action: str) -> None:
        if any(item.action == action for item in self.actions):
            return
        raise LegacyRecoveryActionUnavailableError(
            self.unavailable_reason or "legacy_recovery_action_not_available"
        )


class LegacyRecoveryActionUnavailableError(ValueError):
    pass


def legacy_recovery_ownership_unavailable(
    store: ScopeAuthorityStore,
    workflow_run_id: str,
) -> str | None:
    if (
        store.get_unresolved_coverage_snapshot(workflow_run_id) is not None
        or store.list_scope_execution_authorizations(workflow_run_id)
        or store.list_scope_execution_units(workflow_run_id)
    ):
        return "scope_execution_authorization_required"
    return None


async def project_legacy_recovery_authority(
    store: ScopeAuthorityStore,
    db_path: str,
    workflow_run_id: str,
    *,
    published_report: Mapping[str, Any] | None = None,
) -> LegacyRecoveryAuthority:
    """Project the exact legacy command accepted from current durable facts.

    Historical workflows without Scope lineage remain recoverable. Once an
    unresolved Coverage decision or execution unit exists, only its exact
    resolution/replay command owns further execution.
    """
    if published_report is not None:
        publication = published_report.get("publication")
        publication = publication if isinstance(publication, Mapping) else {}
        if (
            publication.get("state") == "evidence_only_report"
            and publication.get("publication_reason") == "query_subject_not_supported"
            and store.list_subagent_tasks_for_workflow(workflow_run_id)
            and any(
                packet.workflow_run_id == workflow_run_id
                for packet in store.list_typed_records(DirectionalEvidencePacketRecord)
            )
        ):
            candidate = LegacyRecoveryAuthority(
                actions=(
                    WorkflowMutationAction(
                        action="repair_from_persisted_packets",
                        request={},
                    ),
                ),
                reason_code="query_subject_not_supported",
            )
        else:
            candidate = LegacyRecoveryAuthority(
                unavailable_reason="legacy_recovery_action_not_available"
            )
    else:
        if any(
            publication.workflow_run_id == workflow_run_id
            for publication in store.list_typed_records(ReportPublicationRecord)
        ):
            candidate = LegacyRecoveryAuthority(
                unavailable_reason="legacy_recovery_action_not_available"
            )
        else:
            checkpoints = [
                item
                for item in store.list_typed_records(StageCheckpointRecord)
                if item.workflow_run_id == workflow_run_id
            ]
            failures = [
                item for item in checkpoints if item.status in _FAILURE_CHECKPOINT_STATUSES
            ]
            reason_code = _recovery_reason(failures)
            async with WorkflowStore(db_path) as workflow_store:
                run = await workflow_store.get_run(workflow_run_id)
            state = getattr(getattr(run, "status", None), "value", None) or str(
                getattr(run, "status", "unknown")
            )
            if (
                run is None
                or state not in _RECOVERABLE_RUN_STATES
                or not failures
                or reason_code not in _RECOVERABLE_FAILURE_CODES
            ):
                candidate = LegacyRecoveryAuthority(
                    reason_code=reason_code,
                    unavailable_reason="legacy_recovery_action_not_available",
                )
            else:
                candidate = LegacyRecoveryAuthority(
                    actions=(
                        WorkflowMutationAction(
                            action=(
                                "resume_formal_research"
                                if state == "paused"
                                else "retry_formal_research"
                            ),
                            request={},
                        ),
                    ),
                    reason_code=reason_code,
                    completed_stages=tuple(
                        sorted(
                            {
                                item.stage_name
                                for item in checkpoints
                                if item.status == "completed"
                            }
                        )
                    ),
                )

    if not candidate.available:
        return candidate
    ownership_error = legacy_recovery_ownership_unavailable(store, workflow_run_id)
    if ownership_error is not None:
        return LegacyRecoveryAuthority(
            reason_code=candidate.reason_code,
            completed_stages=candidate.completed_stages,
            unavailable_reason=ownership_error,
        )
    return candidate


def _recovery_reason(failures: list[StageCheckpointRecord]) -> str:
    if not failures:
        return "temporary_error"
    payload = failures[-1].payload
    return str(
        payload.get("reason_code")
        or (payload.get("completion") or {}).get("failure_code")
        or payload.get("failure_reason")
        or "temporary_error"
    )
