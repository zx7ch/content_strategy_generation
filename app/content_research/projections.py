"""Pure public projection helpers shared by Content Research boundaries."""

from __future__ import annotations

from typing import Any

from app.content_research.lifecycle.models import RunProjection


def recovery_plan_payload(run_projection: RunProjection) -> dict[str, Any] | None:
    plan = run_projection.recovery_plan
    if plan is None:
        return None
    payload: dict[str, Any] = {
        "recoverable": plan.recoverable,
        "action": plan.action,
        "reason_code": plan.reason_code,
        "recovery_plan_id": plan.recovery_plan_id,
        "plan_fingerprint": plan.plan_fingerprint,
        "failed_stage": plan.failed_stage,
        "failure_class": plan.failure_class,
        "expected_attempt_id": plan.expected_attempt_id,
        "expected_state_revision": plan.expected_state_revision,
        "checkpoint_references": list(plan.checkpoint_references),
    }
    if plan.attempt_no is not None:
        payload["attempt_no"] = plan.attempt_no
    return payload


def run_projection_payload(run_projection: RunProjection) -> dict[str, Any]:
    return {
        "run_id": run_projection.run_id,
        "thread_id": run_projection.thread_id,
        "state": run_projection.state.value,
        "state_revision": run_projection.state_revision,
        "entered_at": run_projection.entered_at,
        "allowed_actions": list(run_projection.allowed_actions),
        "recovery_plan": recovery_plan_payload(run_projection),
        "reason_code": run_projection.reason_code,
        "error": dict(run_projection.error) if run_projection.error else None,
        "brief_id": run_projection.brief_id,
        "scope_contract_id": run_projection.scope_contract_id,
        "execution_attempt_id": run_projection.execution_attempt_id,
        "coverage_snapshot_id": run_projection.coverage_snapshot_id,
        "publication_id": run_projection.publication_id,
    }


def safe_read_model(value: Any) -> Any:
    """Remove provider secrets from a public evidence projection recursively."""
    forbidden = {
        "raw_payload",
        "access_token",
        "token",
        "cookie",
        "cookies",
        "authorization",
    }
    if isinstance(value, dict):
        return {
            key: safe_read_model(item)
            for key, item in value.items()
            if key.lower() not in forbidden
        }
    if isinstance(value, list):
        return [safe_read_model(item) for item in value]
    return value
