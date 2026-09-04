"""Pure serializers and recovery projections for Content Research scope reads."""

from __future__ import annotations

from typing import Any

from app.content_research.scope_contract import (
    ResearchScopeDraft,
    ScopeAuditEvent,
    ScopeConstraint,
    ScopeDraftAuditEvent,
    ScopeExecutionAuthorization,
    ScopeExecutionUnit,
    ScopeQueryGroupInput,
)


def scope_constraint_payload(item: ScopeConstraint) -> dict[str, Any]:
    return {
        "id": item.id,
        "label": item.label,
        "value": item.value,
        "mode": item.mode,
        "allowed_aliases": list(item.allowed_aliases),
    }


def scope_query_input_payload(item: ScopeQueryGroupInput) -> dict[str, Any]:
    return {
        "suggested_query": item.suggested_query,
        "final_query": item.final_query,
        "targeted_required_terms": list(item.targeted_required_terms),
        "origin": item.origin,
    }


def scope_query_group_payload(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "suggested_query": item.suggested_query,
        "final_query": item.final_query,
        "origin": item.origin,
        "execution_role": item.execution_role,
    }


def scope_draft_payload(draft: ResearchScopeDraft) -> dict[str, Any]:
    return {
        "schema_version": draft.schema_version,
        "id": draft.id,
        "workflow_run_id": draft.workflow_run_id,
        "research_plan_id": draft.research_plan_id,
        "structure_hash": draft.structure_hash,
        "core_object": draft.core_object,
        "product_experience_aspect": draft.product_experience_aspect,
        "context_audience_aspect": draft.context_audience_aspect,
        "constraints": [scope_constraint_payload(item) for item in draft.constraints],
        "query_groups": [scope_query_input_payload(item) for item in draft.query_groups],
        "created_at": draft.created_at.isoformat(),
    }


def scope_draft_audit_payload(event: ScopeDraftAuditEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "workflow_run_id": event.workflow_run_id,
        "scope_draft_id": event.scope_draft_id,
        "event_name": event.event_name,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
    }


def scope_contract_payload(contract: Any) -> dict[str, Any]:
    return {
        "id": contract.id,
        "workflow_run_id": contract.workflow_run_id,
        "research_plan_id": contract.research_plan_id,
        "version": contract.version,
        "schema_version": contract.schema_version,
        "constraints": [scope_constraint_payload(item) for item in contract.constraints],
        "query_groups": [scope_query_group_payload(item) for item in contract.query_groups],
        "created_at": contract.created_at.isoformat(),
    }


def scope_audit_payload(event: ScopeAuditEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "workflow_run_id": event.workflow_run_id,
        "scope_contract_id": event.scope_contract_id,
        "scope_contract_version": event.scope_contract_version,
        "event_name": event.event_name,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
    }


def scope_execution_authorization_payload(
    authorization: ScopeExecutionAuthorization,
) -> dict[str, Any]:
    return {
        "id": authorization.id,
        "execution_unit_id": authorization.execution_unit_id,
        "workflow_run_id": authorization.workflow_run_id,
        "scope_contract_id": authorization.scope_contract_id,
        "scope_contract_version": authorization.scope_contract_version,
        "coverage_snapshot_id": authorization.coverage_snapshot_id,
        "resolution": authorization.resolution,
        "execution_revision": authorization.execution_revision,
        "state": authorization.state,
        "created_at": authorization.created_at.isoformat(),
    }


def scope_execution_unit_projection(
    *,
    execution_unit: ScopeExecutionUnit | None,
    authorization: ScopeExecutionAuthorization | None,
    audit_events: list[dict[str, Any]],
    execution_facts: list[Any],
) -> dict[str, Any] | None:
    """Expose recovery authority without leaking an attempt lease to Creator."""
    if execution_unit is None or authorization is None:
        return None
    latest_attempt_no = max(
        (int(fact.attempt_no) for fact in execution_facts),
        default=0,
    )
    replay_actions: list[dict[str, Any]] = []
    if (
        execution_unit.state == "failed"
        and execution_unit.recovery_state == "replayable"
        and execution_unit.latest_provider_state == "retryable_failed"
    ):
        resolution_event = next(
            (
                event
                for event in reversed(audit_events)
                if event.get("event_name") == "coverage_resolved"
                and str((event.get("payload") or {}).get("coverage_snapshot_id") or "")
                == execution_unit.coverage_snapshot_id
                and str((event.get("payload") or {}).get("resolution") or "")
                == execution_unit.resolution
            ),
            None,
        )
        payload = dict((resolution_event or {}).get("payload") or {})
        replay_request: dict[str, Any] = {
            "scope_contract_version": int(
                payload.get("source_scope_contract_version") or authorization.scope_contract_version
            ),
            "coverage_snapshot_id": execution_unit.coverage_snapshot_id,
            "resolution": execution_unit.resolution,
        }
        constraint_id = str(payload.get("constraint_id") or "")
        if constraint_id:
            replay_request["constraint_id"] = constraint_id
        supplementary_queries = [
            str(query) for query in payload.get("supplementary_queries") or [] if str(query).strip()
        ]
        if supplementary_queries:
            replay_request["supplementary_queries"] = supplementary_queries
        replay_actions.append(
            {
                "action": "replay_coverage_decision",
                "available": True,
                "request": replay_request,
            }
        )
    return {
        "id": execution_unit.id,
        "state": execution_unit.state,
        "attempt_no": latest_attempt_no,
        "recovery_state": execution_unit.recovery_state,
        "allowed_actions": replay_actions,
        "trace_summary": {
            "fact_count": len(execution_facts),
            "attempt_count": len({int(fact.attempt_no) for fact in execution_facts}),
            "last_fact_kind": execution_facts[-1].kind if execution_facts else None,
        },
    }


def coverage_snapshot_payload(snapshot: Any) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "workflow_run_id": snapshot.workflow_run_id,
        "scope_contract_id": snapshot.scope_contract_id,
        "scope_contract_version": snapshot.scope_contract_version,
        "execution_revision": snapshot.execution_revision,
        "source_coverage_snapshot_id": snapshot.source_coverage_snapshot_id,
        "state": snapshot.state,
        "constraint_counts": snapshot.constraint_counts,
        "unmet_constraint_ids": list(snapshot.unmet_constraint_ids),
        "created_at": snapshot.created_at.isoformat(),
    }


def scope_projection_resolutions(
    *,
    contract: Any | None,
    coverage_snapshot: Any | None,
    authorizations: list[ScopeExecutionAuthorization],
) -> list[dict[str, Any]]:
    if contract is None or coverage_snapshot is None:
        return []
    authorized = any(item.coverage_snapshot_id == coverage_snapshot.id for item in authorizations)
    valid_constraint_ids = [
        item.id
        for item in contract.constraints
        if item.id in coverage_snapshot.unmet_constraint_ids and item.mode == "required"
    ]
    decision_open = coverage_snapshot.state == "awaiting_scope_decision" and not authorized
    no_required_constraint_reason = "no_unmet_required_constraints"
    closed_reason = (
        "coverage_resolution_already_authorized"
        if authorized
        else "coverage_resolution_not_required"
    )
    return [
        {
            "action": "expand_required_constraint",
            "available": decision_open and bool(valid_constraint_ids),
            "valid_constraint_ids": valid_constraint_ids if decision_open else [],
            "supplementary_queries_required": True,
            "unavailable_reason": (
                None
                if decision_open and valid_constraint_ids
                else no_required_constraint_reason
                if decision_open
                else closed_reason
            ),
        },
        {
            "action": "generate_limited_report",
            "available": decision_open,
            "valid_constraint_ids": [],
            "supplementary_queries_required": False,
            "unavailable_reason": None if decision_open else closed_reason,
        },
        {
            "action": "relax_constraint",
            "available": decision_open and bool(valid_constraint_ids),
            "valid_constraint_ids": valid_constraint_ids if decision_open else [],
            "supplementary_queries_required": False,
            "unavailable_reason": (
                None
                if decision_open and valid_constraint_ids
                else no_required_constraint_reason
                if decision_open
                else closed_reason
            ),
        },
    ]


def scope_decision_recovery(
    *,
    coverage_snapshot: Any | None,
    authorizations: list[ScopeExecutionAuthorization],
    allowed_resolutions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if coverage_snapshot is None:
        return None
    authorization = next(
        (
            item
            for item in sorted(
                authorizations,
                key=lambda value: (value.execution_revision, value.created_at, value.id),
                reverse=True,
            )
            if item.coverage_snapshot_id == coverage_snapshot.id
        ),
        None,
    )
    return {
        "coverage_snapshot_id": coverage_snapshot.id,
        "state": "authorized" if authorization is not None else "decision_required",
        "authorization_id": authorization.id if authorization is not None else None,
        "available_actions": [
            str(item["action"])
            for item in allowed_resolutions
            if item.get("available") is True and isinstance(item.get("action"), str)
        ],
    }
