"""Server-owned projection and guard for legacy workflow recovery commands."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from app.content_research.contracts import QUERY_RELEVANCE_ALGORITHM_VERSION
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
    def get_brief_by_workflow(self, workflow_run_id: str): ...

    def get_run_policy_snapshot_for_workflow(self, workflow_run_id: str): ...

    def list_direction_contracts(self, snapshot_id: str) -> list: ...

    def get_sample_policy(self, sample_policy_id: str): ...

    def get_typed_record(self, record_type: type, record_id: str): ...

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
        ):
            repair_error = persisted_packet_replay_unavailable_reason(
                store, workflow_run_id
            )
            if repair_error is None:
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
                    reason_code="query_subject_not_supported",
                    unavailable_reason=repair_error,
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


def persisted_packet_replay_unavailable_reason(
    store: ScopeAuthorityStore,
    workflow_run_id: str,
) -> str | None:
    """Return the first durable fact that makes packet replay non-executable.

    The check is intentionally pure: projection, mutation admission, and the
    replay boundary can use the same answer before admission/report writes.
    Remote/model outcomes remain execution failures rather than authority
    facts.
    """
    if store.get_brief_by_workflow(workflow_run_id) is None:
        return "persisted_packet_brief_missing"
    snapshot = store.get_run_policy_snapshot_for_workflow(workflow_run_id)
    if snapshot is None:
        return "persisted_packet_policy_missing"
    tasks = store.list_subagent_tasks_for_workflow(workflow_run_id)
    if not tasks:
        return "persisted_packet_tasks_missing"
    if any(task.status not in {"completed", "partial_completed"} for task in tasks):
        return "persisted_packet_tasks_not_terminal"
    if any(not str(task.direction_id or "") for task in tasks):
        return "persisted_packet_task_direction_missing"

    contracts = {
        contract.direction_id: contract
        for contract in store.list_direction_contracts(snapshot.id)
    }
    for task in tasks:
        direction_id = str(task.direction_id)
        contract = contracts.get(direction_id)
        if contract is None:
            return "persisted_packet_direction_contract_missing"
        if store.get_sample_policy(contract.sample_policy_id) is None:
            return "persisted_packet_sample_policy_missing"

    relevance_by_direction = dict(snapshot.effective_policy.get("query_relevance") or {})
    has_current_relevance = bool(relevance_by_direction) and all(
        str(value.get("algorithm_version") or "")
        == QUERY_RELEVANCE_ALGORITHM_VERSION
        for value in relevance_by_direction.values()
        if isinstance(value, dict)
    )
    if not has_current_relevance:
        locked_directions = dict(
            snapshot.effective_policy.get("locked_query_plan", {}).get(
                "directions", {}
            )
            or {}
        )
        if set(locked_directions) != set(contracts):
            return "persisted_packet_relevance_directions_mismatch"
        for direction_id, contract in contracts.items():
            locked_group_ids = {
                str(item.get("id") or "")
                for item in locked_directions[direction_id].get("query_groups") or ()
                if str(item.get("id") or "")
            }
            original_ids = {
                str(item)
                for item in (contract.metadata.get("query_relevance") or {}).get(
                    "query_group_ids", ()
                )
            }
            if not locked_group_ids or original_ids != locked_group_ids:
                return "persisted_packet_relevance_query_groups_mismatch"

    checkpoints = [
        item
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.workflow_run_id == workflow_run_id and item.status == "completed"
    ]
    for task in tasks:
        task_checkpoints = [
            item for item in checkpoints if item.subagent_task_id == task.id
        ]
        packet_checkpoint = next(
            (
                item
                for item in reversed(task_checkpoints)
                if item.stage_name == "packet"
            ),
            None,
        )
        selection_checkpoint = next(
            (
                item
                for item in reversed(task_checkpoints)
                if item.stage_name == "detail" and item.payload.get("selection")
            ),
            None,
        ) or next(
            (
                item
                for item in reversed(task_checkpoints)
                if item.stage_name == "selection" and item.payload.get("selection")
            ),
            None,
        )
        if selection_checkpoint is None:
            return "persisted_packet_selection_checkpoint_missing"
        if packet_checkpoint is None:
            return "persisted_packet_packet_checkpoint_missing"
        if str(packet_checkpoint.payload.get("direction_id") or "") != str(
            task.direction_id
        ):
            return "persisted_packet_checkpoint_direction_mismatch"
        comment_checkpoint = next(
            (
                item
                for item in reversed(task_checkpoints)
                if item.stage_name == "comments"
            ),
            None,
        )
        packet_ids = tuple(packet_checkpoint.payload.get("packet_ids") or ())
        comment_packet_ids = (
            tuple(comment_checkpoint.payload.get("packet_ids") or ())
            if comment_checkpoint is not None
            else ()
        )
        if not packet_ids and not comment_packet_ids:
            return "persisted_packet_packets_missing"
        if any(
            store.get_typed_record(DirectionalEvidencePacketRecord, str(packet_id))
            is None
            for packet_id in (*packet_ids, *comment_packet_ids)
        ):
            return "persisted_packet_record_missing"
    return None


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
