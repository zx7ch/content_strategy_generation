"""Read-only public projection of one materialized Content Research report."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.content_research.evidence.governance_reader import safe_public_projection
from app.content_research.models import ResearchResultSnapshotRecord
from app.content_research.persistence_models import (
    ReportFaithfulnessDecisionRecord,
    ReportPublicationRecord,
    StageCheckpointRecord,
)
from app.content_research.scope_contract import thaw_execution_payload
from app.content_research.stores.base import ContentResearchStore
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowArtifactType


class PublishedReportNotFoundError(ValueError):
    """Raised when a run-scoped published report cannot be resolved safely."""


_SAFE_EXECUTION_FACT_FIELDS = frozenset(
    {
        "provider",
        "provider_operation",
        "operation",
        "operation_fingerprint",
        "source_kind",
        "result_status",
        "provider_state",
        "status",
        "failure_code",
        "retryable",
        "reason",
        "coverage_snapshot_id",
        "publication_id",
        "report_publication_id",
    }
)


class ExecutionTraceReader:
    """Project one execution unit solely from its ordered durable facts."""

    def __init__(self, store: ContentResearchStore) -> None:
        self._store = store

    def read(self, execution_unit_id: str) -> dict[str, Any]:
        unit = self._store.get_scope_execution_unit(execution_unit_id)
        if unit is None:
            raise PublishedReportNotFoundError("execution unit not found")
        facts = self._store.execution_trace(execution_unit_id)
        sequence = [fact.sequence_no for fact in facts]
        if sequence != sorted(sequence) or len(sequence) != len(set(sequence)):
            raise PublishedReportNotFoundError("execution facts are not monotonic")

        outcome_state = "not_requested"
        for fact in facts:
            payload = thaw_execution_payload(fact.payload)
            payload = payload if isinstance(payload, dict) else {}
            if fact.kind == "provider_request_recorded":
                outcome_state = "outcome_unknown"
            elif fact.kind == "provider_outcome_recorded":
                outcome_state = str(
                    payload.get("provider_state")
                    or payload.get("result_status")
                    or "outcome_unknown"
                )
            elif fact.kind == "outcome_unknown":
                outcome_state = "outcome_unknown"

        return {
            "execution_unit_id": unit.id,
            "scope_contract_id": unit.scope_contract_id,
            "coverage_snapshot_id": unit.coverage_snapshot_id,
            "state": unit.state,
            "outcome_state": outcome_state,
            "facts": [
                {
                    "attempt_no": fact.attempt_no,
                    "sequence_no": fact.sequence_no,
                    "kind": fact.kind,
                    "payload": _safe_execution_fact_payload(fact.kind, fact.payload),
                }
                for fact in facts
            ],
        }


def _safe_execution_fact_payload(kind: str, value: object) -> dict[str, Any]:
    payload = thaw_execution_payload(value)
    if not isinstance(payload, dict):
        return {}
    if kind == "decision_accepted":
        decision = payload.get("decision")
        if isinstance(decision, str):
            try:
                decision = json.loads(decision)
            except (TypeError, json.JSONDecodeError):
                decision = None
        if not isinstance(decision, dict):
            return {}
        return {
            "decision": safe_public_projection(
                {
                    key: item
                    for key, item in decision.items()
                    if key
                    in {
                        "schema",
                        "coverage_snapshot_id",
                        "source_scope_contract_id",
                        "resulting_scope_contract_id",
                        "resolution",
                        "target_constraint_id",
                        "supplementary_queries",
                    }
                }
            )
        }
    return {
        key: safe_public_projection(item)
        for key, item in payload.items()
        if key in _SAFE_EXECUTION_FACT_FIELDS
        and (item is None or isinstance(item, str | int | float | bool))
    }


class PublishedReportReader:
    """Resolve only R1's materialized publication; never compose or read a draft."""

    def __init__(self, store: ContentResearchStore, db_path: str) -> None:
        self._store = store
        self._db_path = db_path

    async def read(
        self,
        *,
        workflow_run_id: str,
        research_plan_id: str | None = None,
        publication_id: str | None = None,
        citation_offset: int = 0,
        citation_limit: int = 50,
    ) -> dict[str, Any]:
        publication = self._publication(
            workflow_run_id=workflow_run_id,
            research_plan_id=research_plan_id,
            publication_id=publication_id,
        )
        decision = self._record(
            ReportFaithfulnessDecisionRecord, publication.faithfulness_decision_id, "audit"
        )
        snapshot = self._snapshot(publication)
        self._validate_lineage(publication, decision, snapshot)
        artifact, terminal_state = await self._artifact(publication)
        payload = artifact.payload_json
        if not isinstance(payload, dict):
            raise PublishedReportNotFoundError("published report artifact payload is missing")
        if payload.get("report_publication_id") != publication.id:
            raise PublishedReportNotFoundError("published report artifact lineage mismatch")
        sections = payload.get("sections")
        groups = payload.get("citation_groups")
        if not isinstance(sections, list) or not isinstance(groups, list):
            raise PublishedReportNotFoundError("published report artifact is malformed")
        sorted_groups = sorted(
            (dict(group) for group in groups if isinstance(group, dict)),
            key=lambda group: (
                group.get("display_index", 0),
                str(group.get("citation_group_id", "")),
            ),
        )
        page = sorted_groups[citation_offset : citation_offset + citation_limit]
        governed = snapshot.metadata.get("governed_snapshot")
        if not isinstance(governed, dict):
            raise PublishedReportNotFoundError("published report governed snapshot is missing")
        policy_scope = governed.get("policy_scope") if isinstance(governed.get("policy_scope"), dict) else {}
        execution_lineage = governed.get("execution_lineage")
        if not isinstance(execution_lineage, dict):
            execution_lineage = {}
        direction_ids = list(policy_scope.get("direction_ids") or [])
        direction_results = {
            item.get("direction_id"): item
            for item in governed.get("direction_results") or []
            if isinstance(item, dict) and item.get("direction_id")
        }
        return {
            "workflow_run_id": workflow_run_id,
            "scope_contract_id": execution_lineage.get("scope_contract_id"),
            "scope_contract": safe_public_projection(governed.get("scope_contract") or {}),
            "coverage_snapshot": safe_public_projection(
                governed.get("coverage_snapshot") or {}
            ),
            "workflow_terminal_state": terminal_state,
            "publication_state": publication.publication_state,
            "artifact": {
                "artifact_id": artifact.artifact_id,
                "artifact_version": artifact.artifact_version,
                "artifact_type": artifact.artifact_type.value,
                "payload_mode": artifact.payload_mode.value,
            },
            "publication": {
                "report_publication_id": publication.id,
                "faithfulness_decision_id": decision.id,
                "governed_snapshot_id": publication.governed_snapshot_id,
                "governed_snapshot_version": publication.governed_snapshot_version,
                "research_plan_id": publication.research_plan_id,
                "audit_state": decision.payload.get("audit_state"),
                "reason_codes": list(decision.payload.get("reason_codes") or []),
                "omitted_section_ids": list(publication.payload.get("omitted_section_ids") or []),
                "audit_recovery_state": publication.payload.get("audit_recovery_state"),
                "compose_mode": publication.payload.get("compose_mode") or "prose",
                "scope_contract_id": publication.scope_contract_id,
                "execution_unit_id": publication.execution_unit_id,
                "coverage_snapshot_id": publication.coverage_snapshot_id,
                "attempt_no": publication.attempt_no,
            },
            "sections": safe_public_projection(sections),
            "citation_groups": [_citation_group(group) for group in page],
            "citation_total": len(sorted_groups),
            "citation_offset": citation_offset,
            "citation_limit": citation_limit,
            "claim_cards": safe_public_projection(governed.get("claim_cards") or []),
            "weak_signals": safe_public_projection(governed.get("weak_signals") or []),
            "cross_direction_records": safe_public_projection(
                governed.get("cross_direction_records") or []
            ),
            "aggregate_claims": safe_public_projection(governed.get("aggregate_claims") or []),
            "limitations_recovery": safe_public_projection(
                governed.get("limitations_recovery") or []
            ),
            "release": {
                "direction_set_version": policy_scope.get("direction_set_version"),
                "direction_ids": direction_ids,
            },
            "run_direction_states": [
                {
                    "direction": direction_id,
                    "state": (direction_results.get(direction_id) or {}).get("state", "unavailable"),
                    "reason_codes": (direction_results.get(direction_id) or {}).get("limitations", []),
                    "recovery_actions": (direction_results.get(direction_id) or {}).get("recovery_actions", []),
                }
                for direction_id in direction_ids
            ],
            "trace": {
                **_trace_projection(self._store, publication, decision),
                "execution": safe_public_projection(governed.get("execution_trace") or {}),
            },
        }

    async def citation_groups(
        self,
        *,
        workflow_run_id: str,
        research_plan_id: str | None = None,
        publication_id: str | None = None,
        citation_group_ids: set[str],
    ) -> list[dict[str, Any]]:
        """Return requested immutable artifact citations after publication-scoped validation."""
        publication = self._publication(
            workflow_run_id=workflow_run_id,
            research_plan_id=research_plan_id,
            publication_id=publication_id,
        )
        decision = self._record(
            ReportFaithfulnessDecisionRecord, publication.faithfulness_decision_id, "audit"
        )
        snapshot = self._snapshot(publication)
        self._validate_lineage(publication, decision, snapshot)
        artifact, _ = await self._artifact(publication)
        payload = artifact.payload_json
        groups = payload.get("citation_groups") if isinstance(payload, dict) else None
        if not isinstance(groups, list):
            raise PublishedReportNotFoundError("published report artifact is malformed")
        by_id = {
            str(group["citation_group_id"]): group
            for group in groups
            if isinstance(group, dict) and isinstance(group.get("citation_group_id"), str)
        }
        missing = citation_group_ids - set(by_id)
        if missing:
            raise PublishedReportNotFoundError(
                "requested citation groups are absent from the publication: "
                + ", ".join(sorted(missing))
            )
        return [_citation_group(dict(group)) for group in groups if isinstance(group, dict) and group.get("citation_group_id") in citation_group_ids]

    def _publication(
        self, *, workflow_run_id: str, research_plan_id: str | None, publication_id: str | None
    ) -> ReportPublicationRecord:
        matches = [
            item
            for item in self._store.list_typed_records(ReportPublicationRecord)
            if item.workflow_run_id == workflow_run_id
            and (research_plan_id is None or item.research_plan_id == research_plan_id)
            and (publication_id is None or item.id == publication_id)
        ]
        if not matches:
            raise PublishedReportNotFoundError("published report not found")
        if publication_id is None and len(matches) > 1:
            matches.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return matches[0]

    def _record(self, record_type: type[Any], record_id: str, name: str) -> Any:
        record = self._store.get_typed_record(record_type, record_id)
        if record is None:
            raise PublishedReportNotFoundError(f"published report {name} is missing")
        return record

    def _snapshot(self, publication: ReportPublicationRecord) -> ResearchResultSnapshotRecord:
        snapshot = next(
            (
                item
                for item in self._store.list_result_snapshots_for_workflow(
                    publication.workflow_run_id
                )
                if item.id == publication.governed_snapshot_id
            ),
            None,
        )
        if snapshot is None:
            raise PublishedReportNotFoundError("published report snapshot is missing")
        return snapshot

    @staticmethod
    def _validate_lineage(
        publication: ReportPublicationRecord,
        decision: ReportFaithfulnessDecisionRecord,
        snapshot: ResearchResultSnapshotRecord,
    ) -> None:
        fields = (
            "workflow_run_id",
            "research_plan_id",
            "governed_snapshot_id",
            "governed_snapshot_version",
            "input_fingerprint",
            "policy_version",
            "algorithm_version",
            "scope_contract_id",
            "execution_unit_id",
            "coverage_snapshot_id",
            "attempt_no",
        )
        if any(getattr(publication, field) != getattr(decision, field) for field in fields):
            raise PublishedReportNotFoundError("published report audit lineage mismatch")
        if (
            snapshot.snapshot_version != publication.governed_snapshot_version
            or snapshot.research_plan_id != publication.research_plan_id
        ):
            raise PublishedReportNotFoundError("published report snapshot lineage mismatch")
        governed = snapshot.metadata.get("governed_snapshot")
        lineage = governed.get("execution_lineage") if isinstance(governed, dict) else None
        if publication.execution_unit_id is not None and (
            not isinstance(lineage, dict)
            or lineage.get("scope_contract_id") != publication.scope_contract_id
            or lineage.get("execution_unit_id") != publication.execution_unit_id
            or lineage.get("coverage_snapshot_id") != publication.coverage_snapshot_id
            or lineage.get("successful_attempt_no") != publication.attempt_no
        ):
            raise PublishedReportNotFoundError(
                "published report frozen execution lineage mismatch"
            )

    async def _artifact(self, publication: ReportPublicationRecord) -> tuple[Any, str]:
        async with WorkflowStore(self._db_path) as workflow_store:
            run = await workflow_store.get_run(publication.workflow_run_id)
            artifacts = await workflow_store.list_artifacts(publication.workflow_run_id)
        terminal_state = (
            run.status.value
            if run is not None and hasattr(run.status, "value")
            else (str(run.status) if run else "unknown")
        )
        # A materialized artifact is an internal finalization product until the
        # workflow commits success.  Exposing it earlier lets Creator render a
        # completed report for a run that may still fail its publication step.
        if terminal_state != "succeeded":
            raise PublishedReportNotFoundError("published report is not ready")
        artifact = next(
            (
                item
                for item in artifacts
                if item.artifact_type == WorkflowArtifactType.FINAL_RESULT
                and isinstance(item.payload_json, dict)
                and item.payload_json.get("report_publication_id") == publication.id
            ),
            None,
        )
        if artifact is None:
            raise PublishedReportNotFoundError("published report artifact is missing")
        return artifact, terminal_state


def _citation_group(group: dict[str, Any]) -> dict[str, Any]:
    copy = safe_public_projection(group)
    refs = copy.get("evidence_refs") if isinstance(copy, dict) else None
    if isinstance(refs, list):
        copy["evidence_refs"] = [
            {**ref, "jump_state": "available" if ref.get("source_url") else "unavailable"}
            for ref in refs
            if isinstance(ref, dict)
        ]
    preview = copy.get("preview_ref") if isinstance(copy, dict) else None
    if isinstance(preview, dict):
        copy["preview_ref"] = {
            **preview,
            "jump_state": "available" if preview.get("source_url") else "unavailable",
        }
    return copy


def _trace_projection(
    store: ContentResearchStore,
    publication: ReportPublicationRecord,
    decision: ReportFaithfulnessDecisionRecord,
) -> dict[str, Any]:
    checkpoints = [
        item
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.workflow_run_id == publication.workflow_run_id
    ]
    semantic = decision.payload.get("semantic_audit")
    semantic = semantic if isinstance(semantic, dict) else {}
    usage = semantic.get("usage") if isinstance(semantic.get("usage"), dict) else {}
    return {
        "checkpoint_summary": {
            "stages": [
                {
                    "stage_name": item.stage_name,
                    "status": item.status,
                    "input_fingerprint": item.input_fingerprint,
                    "retry_count": item.retry_count,
                    "duration_ms": _checkpoint_duration_ms(item),
                    "output_refs": safe_public_projection(item.payload.get("output_refs") or []),
                }
                for item in _trace_visible_checkpoints(checkpoints)
            ],
        },
        "faithfulness": {
            "audit_state": decision.payload.get("audit_state"),
            "reason_codes": list(decision.payload.get("reason_codes") or []),
            "semantic_state": semantic.get("state"),
            "model_version": semantic.get("model_version"),
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "cost_usd": usage.get("cost_usd"),
                "cost_unknown": bool(usage.get("cost_unknown", not bool(usage))),
            },
        },
    }


def _trace_visible_checkpoints(
    checkpoints: list[StageCheckpointRecord],
) -> list[StageCheckpointRecord]:
    """Project one current operation outcome without deleting its lifecycle facts.

    ``running`` and the terminal record intentionally coexist in persistence so
    recovery can prove what was committed before a provider call.  A completed
    report Trace must show the terminal record for that operation, not a stale
    duplicate that falsely looks in progress.
    """
    chosen: dict[str, StageCheckpointRecord] = {}
    visible: list[StageCheckpointRecord] = []
    for item in sorted(checkpoints, key=lambda item: (item.created_at, item.id)):
        if item.stage_name != "operation":
            visible.append(item)
            continue
        fingerprint = str(item.payload.get("operation_fingerprint") or item.input_fingerprint)
        prior = chosen.get(fingerprint)
        if prior is None or (prior.status == "running" and item.status != "running"):
            chosen[fingerprint] = item
    return sorted([*visible, *chosen.values()], key=lambda item: (item.created_at, item.id))


def _checkpoint_duration_ms(checkpoint: StageCheckpointRecord) -> int | None:
    """Expose a duration only when persisted start and end boundaries exist."""
    if not isinstance(checkpoint.started_at, datetime) or not isinstance(checkpoint.finished_at, datetime):
        return None
    return max(0, int((checkpoint.finished_at - checkpoint.started_at).total_seconds() * 1000))
