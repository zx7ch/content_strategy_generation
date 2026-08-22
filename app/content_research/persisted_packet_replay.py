"""Build the complete immutable input for persisted-packet report repair."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from app.content_research.contracts import (
    QUERY_RELEVANCE_ALGORITHM_VERSION,
    DirectionContract,
    RunPolicySnapshot,
    SamplePolicy,
)
from app.content_research.models import ResearchBriefRecord, SubagentTaskRecord
from app.content_research.persistence_models import (
    DirectionalEvidencePacketRecord,
    StageCheckpointRecord,
)
from app.content_research.workflow.directional_pipeline import (
    DirectionSelection,
    direction_selection_from_payload,
)


class PersistedPacketReplayStore(Protocol):
    def get_brief_by_workflow(
        self, workflow_run_id: str
    ) -> ResearchBriefRecord | None: ...

    def get_run_policy_snapshot_for_workflow(
        self, workflow_run_id: str
    ) -> RunPolicySnapshot | None: ...

    def list_direction_contracts(
        self, snapshot_id: str
    ) -> list[DirectionContract]: ...

    def get_sample_policy(self, sample_policy_id: str) -> SamplePolicy | None: ...

    def get_typed_record(self, record_type: type, record_id: str) -> Any | None: ...

    def list_subagent_tasks_for_workflow(
        self, workflow_run_id: str
    ) -> list[SubagentTaskRecord]: ...

    def list_typed_records(self, record_type: type) -> list: ...


@dataclass(frozen=True)
class PersistedPacketDirectionReplayInput:
    task: SubagentTaskRecord
    contract: DirectionContract
    sample_policy: SamplePolicy
    selection: DirectionSelection
    packet_ids: tuple[str, ...]
    comment_packet_ids: tuple[str, ...]
    packets: tuple[DirectionalEvidencePacketRecord, ...]
    scope_contract_id: str | None
    execution_unit_id: str | None
    attempt_no: int
    execution_revision: int

    @property
    def direction_id(self) -> str:
        return str(self.task.direction_id)

    @property
    def execution_ownership(self) -> dict[str, Any]:
        return {
            "scope_contract_id": self.scope_contract_id,
            "execution_unit_id": self.execution_unit_id,
            "attempt_no": self.attempt_no,
            "execution_revision": self.execution_revision,
        }


@dataclass(frozen=True)
class PersistedPacketReplayInput:
    workflow_run_id: str
    publication_state: str
    publication_reason: str
    brief: ResearchBriefRecord
    snapshot: RunPolicySnapshot
    directions: tuple[PersistedPacketDirectionReplayInput, ...]


@dataclass(frozen=True)
class PersistedPacketReplayUnavailable:
    reason: str


PersistedPacketReplayBuildResult = (
    PersistedPacketReplayInput | PersistedPacketReplayUnavailable
)


def build_persisted_packet_replay_input(
    store: PersistedPacketReplayStore,
    workflow_run_id: str,
    *,
    publication: Mapping[str, Any],
) -> PersistedPacketReplayBuildResult:
    """Resolve every frozen fact needed by Repair through one read-only seam."""
    publication_state = str(publication.get("state") or "")
    publication_reason = str(publication.get("publication_reason") or "")
    if (
        publication_state != "evidence_only_report"
        or publication_reason != "query_subject_not_supported"
    ):
        return _unavailable("persisted_packet_publication_not_eligible")

    brief = store.get_brief_by_workflow(workflow_run_id)
    if brief is None or brief.workflow_run_id != workflow_run_id:
        return _unavailable("persisted_packet_brief_missing")
    snapshot = store.get_run_policy_snapshot_for_workflow(workflow_run_id)
    if snapshot is None or snapshot.workflow_run_id != workflow_run_id:
        return _unavailable("persisted_packet_policy_missing")
    tasks = store.list_subagent_tasks_for_workflow(workflow_run_id)
    if not tasks:
        return _unavailable("persisted_packet_tasks_missing")
    if any(task.status not in {"completed", "partial_completed"} for task in tasks):
        return _unavailable("persisted_packet_tasks_not_terminal")
    direction_ids = tuple(str(task.direction_id or "") for task in tasks)
    if any(not direction_id for direction_id in direction_ids):
        return _unavailable("persisted_packet_task_direction_missing")
    if len(set(direction_ids)) != len(direction_ids):
        return _unavailable("persisted_packet_task_directions_duplicate")

    contract_list = store.list_direction_contracts(snapshot.id)
    if any(contract.snapshot_id != snapshot.id for contract in contract_list):
        return _unavailable("persisted_packet_direction_contract_snapshot_mismatch")
    contracts = {contract.direction_id: contract for contract in contract_list}
    if any(direction_id not in contracts for direction_id in direction_ids):
        return _unavailable("persisted_packet_direction_contract_missing")
    if set(contracts) != set(direction_ids):
        return _unavailable("persisted_packet_direction_contracts_mismatch")

    locked_plan = snapshot.effective_policy.get("locked_query_plan")
    locked_directions = (
        locked_plan.get("directions") if isinstance(locked_plan, Mapping) else None
    )
    if not isinstance(locked_directions, Mapping) or set(locked_directions) != set(
        direction_ids
    ):
        return _unavailable("persisted_packet_locked_directions_mismatch")
    relevance_by_direction = snapshot.effective_policy.get("query_relevance")
    if not isinstance(relevance_by_direction, Mapping) or set(
        relevance_by_direction
    ) != set(direction_ids):
        return _unavailable("persisted_packet_relevance_directions_mismatch")

    completed_checkpoints = [
        item
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.workflow_run_id == workflow_run_id and item.status == "completed"
    ]
    direction_inputs: list[PersistedPacketDirectionReplayInput] = []
    for task in tasks:
        direction_id = str(task.direction_id)
        contract = contracts[direction_id]
        sample_policy = store.get_sample_policy(contract.sample_policy_id)
        if sample_policy is None:
            return _unavailable("persisted_packet_sample_policy_missing")
        if sample_policy.direction_id != direction_id:
            return _unavailable("persisted_packet_sample_policy_direction_mismatch")

        relevance = relevance_by_direction[direction_id]
        contract_relevance = contract.metadata.get("query_relevance")
        if not isinstance(relevance, Mapping) or not isinstance(
            contract_relevance, Mapping
        ):
            return _unavailable("persisted_packet_relevance_invalid")
        if (
            str(relevance.get("algorithm_version") or "")
            != QUERY_RELEVANCE_ALGORITHM_VERSION
        ):
            return _unavailable("persisted_packet_relevance_version_mismatch")
        if dict(relevance) != dict(contract_relevance):
            return _unavailable("persisted_packet_relevance_contract_mismatch")

        locked_direction = locked_directions[direction_id]
        if not isinstance(locked_direction, Mapping):
            return _unavailable("persisted_packet_locked_direction_invalid")
        locked_groups = locked_direction.get("query_groups")
        if not isinstance(locked_groups, list | tuple) or not locked_groups:
            return _unavailable("persisted_packet_locked_query_groups_missing")
        locked_group_ids = tuple(
            str(group.get("id") or "")
            for group in locked_groups
            if isinstance(group, Mapping) and str(group.get("id") or "")
        )
        relevance_group_ids = relevance.get("query_group_ids")
        if (
            len(locked_group_ids) != len(locked_groups)
            or not isinstance(relevance_group_ids, list | tuple)
            or set(str(value) for value in relevance_group_ids)
            != set(locked_group_ids)
        ):
            return _unavailable("persisted_packet_relevance_query_groups_mismatch")

        task_checkpoints = [
            item for item in completed_checkpoints if item.subagent_task_id == task.id
        ]
        selection_checkpoint = _selection_checkpoint(task_checkpoints)
        if selection_checkpoint is None:
            return _unavailable("persisted_packet_selection_checkpoint_missing")
        try:
            selection = direction_selection_from_payload(
                selection_checkpoint.payload["selection"]
            )
        except (KeyError, TypeError, ValueError):
            return _unavailable("persisted_packet_selection_invalid")
        frozen_query_plan_hash = str(locked_direction.get("query_plan_hash") or "")
        if not frozen_query_plan_hash or selection.query_plan_hash != frozen_query_plan_hash:
            return _unavailable("persisted_packet_selection_query_plan_mismatch")
        if any(
            query_group_id not in locked_group_ids
            for decision in selection.decisions
            for query_group_id in decision.query_group_ids
        ) or any(
            query_group_id not in locked_group_ids
            for query_group_id in selection.coverage_unmet_query_group_ids
        ):
            return _unavailable("persisted_packet_selection_query_groups_mismatch")

        packet_checkpoint = next(
            (
                item
                for item in reversed(task_checkpoints)
                if item.stage_name == "packet"
            ),
            None,
        )
        if packet_checkpoint is None:
            return _unavailable("persisted_packet_packet_checkpoint_missing")
        if str(packet_checkpoint.payload.get("direction_id") or "") != direction_id:
            return _unavailable("persisted_packet_checkpoint_direction_mismatch")
        comment_checkpoint = next(
            (
                item
                for item in reversed(task_checkpoints)
                if item.stage_name == "comments"
            ),
            None,
        )
        packet_ids = _record_ids(packet_checkpoint.payload.get("packet_ids"))
        comment_packet_ids = _record_ids(
            comment_checkpoint.payload.get("packet_ids")
            if comment_checkpoint is not None
            else ()
        )
        if packet_ids is None or comment_packet_ids is None:
            return _unavailable("persisted_packet_ids_invalid")
        if not packet_ids and not comment_packet_ids:
            return _unavailable("persisted_packet_packets_missing")
        if len(set((*packet_ids, *comment_packet_ids))) != len(
            (*packet_ids, *comment_packet_ids)
        ):
            return _unavailable("persisted_packet_ids_duplicate")
        packets: list[DirectionalEvidencePacketRecord] = []
        for packet_id in (*packet_ids, *comment_packet_ids):
            packet = store.get_typed_record(
                DirectionalEvidencePacketRecord, packet_id
            )
            if packet is None:
                return _unavailable("persisted_packet_record_missing")
            if (
                packet.workflow_run_id != workflow_run_id
                or packet.research_direction_id != direction_id
            ):
                return _unavailable("persisted_packet_record_ownership_mismatch")
            packets.append(packet)
        first_packet = packets[0]
        execution_ownership = (
            first_packet.scope_contract_id,
            first_packet.execution_unit_id,
            first_packet.attempt_no,
            first_packet.execution_revision,
        )
        if any(
            (
                packet.scope_contract_id,
                packet.execution_unit_id,
                packet.attempt_no,
                packet.execution_revision,
            )
            != execution_ownership
            for packet in packets
        ) or any(
            (
                checkpoint.scope_contract_id,
                checkpoint.execution_unit_id,
                checkpoint.attempt_no,
                checkpoint.execution_revision,
            )
            != execution_ownership
            for checkpoint in (
                selection_checkpoint,
                packet_checkpoint,
                *((comment_checkpoint,) if comment_checkpoint is not None else ()),
            )
        ):
            return _unavailable("persisted_packet_execution_ownership_mismatch")
        direction_inputs.append(
            PersistedPacketDirectionReplayInput(
                task=task,
                contract=contract,
                sample_policy=sample_policy,
                selection=selection,
                packet_ids=packet_ids,
                comment_packet_ids=comment_packet_ids,
                packets=tuple(packets),
                scope_contract_id=execution_ownership[0],
                execution_unit_id=execution_ownership[1],
                attempt_no=execution_ownership[2],
                execution_revision=execution_ownership[3],
            )
        )

    direction_inputs.sort(key=lambda item: (item.direction_id, item.task.id))
    return PersistedPacketReplayInput(
        workflow_run_id=workflow_run_id,
        publication_state=publication_state,
        publication_reason=publication_reason,
        brief=brief,
        snapshot=snapshot,
        directions=tuple(direction_inputs),
    )


def _selection_checkpoint(
    checkpoints: list[StageCheckpointRecord],
) -> StageCheckpointRecord | None:
    return next(
        (
            item
            for item in reversed(checkpoints)
            if item.stage_name == "detail" and item.payload.get("selection")
        ),
        None,
    ) or next(
        (
            item
            for item in reversed(checkpoints)
            if item.stage_name == "selection" and item.payload.get("selection")
        ),
        None,
    )


def _record_ids(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, list | tuple):
        return None
    result = tuple(str(item).strip() for item in value)
    return result if all(result) else None


def _unavailable(reason: str) -> PersistedPacketReplayUnavailable:
    return PersistedPacketReplayUnavailable(reason=reason)
