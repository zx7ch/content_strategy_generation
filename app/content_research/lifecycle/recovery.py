"""Pure authority for safe Content Research recovery actions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from app.content_research.lifecycle.models import (
    ContentResearchState,
    RecoveryPlan,
)

_RECOVERY_ACTION_STAGES: dict[str, frozenset[str]] = {
    "retry_presearch": frozenset({"presearch"}),
    "retry_retrieval": frozenset(
        {"retrieval", "retrieval_queued", "retrieval_running"}
    ),
    "retry_analysis": frozenset({"analysis", "marketing_analysis"}),
    "retry_report": frozenset({"report", "report_composing"}),
}

_LOCAL_NON_RECOVERABLE_MARKERS = (
    "LOCAL_",
    "SQLITE_",
    "IDENTITY_CONFLICT",
    "DATA_CONFLICT",
    "CONTRACT_CONFLICT",
    "PERSISTENCE_UNAVAILABLE",
)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _failure_class(*, code: str, operation: str) -> str:
    normalized = f"{code}:{operation}".lower()
    if "auth" in normalized or "config" in normalized:
        return "configuration"
    if "interrupt" in normalized:
        return "interrupted_process"
    if "dispatch" in normalized:
        return "dispatch"
    if "provider" in normalized or "timeout" in normalized or "rate_limit" in normalized:
        return "provider"
    return "execution"


def plan_recovery(
    *,
    state: ContentResearchState,
    state_revision: int,
    error: Mapping[str, Any] | None,
    brief_id: str | None,
    scope_contract_id: str | None,
    dispatch_attempt_id: str | None,
    execution_attempt_id: str | None,
    analysis_attempt_id: str | None,
    publication_id: str | None,
) -> RecoveryPlan | None:
    """Return one deterministic safe plan, or no recovery authority."""

    if state is not ContentResearchState.RECOVERY_REQUIRED or error is None:
        return None
    action = str(error.get("recovery_action") or "")
    code = str(error.get("code") or "")
    stage = str(error.get("stage") or "")
    if (
        action not in _RECOVERY_ACTION_STAGES
        or stage not in _RECOVERY_ACTION_STAGES[action]
        or error.get("retryable") is not True
        or not code
        or any(marker in code.upper() for marker in _LOCAL_NON_RECOVERABLE_MARKERS)
    ):
        return None

    if action == "retry_presearch":
        checkpoint_references = tuple(item for item in (brief_id,) if item)
        expected_attempt_id = str(error.get("attempt_id") or "")
    elif action == "retry_retrieval":
        actual_attempt_id = execution_attempt_id or dispatch_attempt_id
        provided_attempt_id = str(error.get("attempt_id") or "")
        if provided_attempt_id and provided_attempt_id != actual_attempt_id:
            return None
        checkpoint_references = tuple(
            item for item in (scope_contract_id, actual_attempt_id) if item
        )
        expected_attempt_id = provided_attempt_id or str(actual_attempt_id or "")
    elif action == "retry_analysis":
        provided_attempt_id = str(error.get("attempt_id") or "")
        if provided_attempt_id and provided_attempt_id != analysis_attempt_id:
            return None
        checkpoint_references = tuple(
            item for item in (scope_contract_id, analysis_attempt_id) if item
        )
        expected_attempt_id = provided_attempt_id or str(analysis_attempt_id or "")
    else:
        preserved_attempt_id = str(error.get("preserved_analysis_attempt_id") or "")
        if preserved_attempt_id != str(analysis_attempt_id or ""):
            return None
        checkpoint_references = tuple(
            item
            for item in (scope_contract_id, analysis_attempt_id, publication_id)
            if item
        )
        expected_attempt_id = preserved_attempt_id

    if not checkpoint_references or not expected_attempt_id:
        return None
    raw_attempt_no = error.get("attempt_no")
    attempt_no = raw_attempt_no if isinstance(raw_attempt_no, int) and raw_attempt_no > 0 else None
    semantic_plan = {
        "action": action,
        "reason_code": code,
        "failed_stage": stage,
        "failure_class": _failure_class(
            code=code,
            operation=str(error.get("operation") or ""),
        ),
        "expected_attempt_id": expected_attempt_id,
        "attempt_no": attempt_no,
        "expected_state_revision": state_revision,
        "checkpoint_references": list(checkpoint_references),
    }
    digest = hashlib.sha256(_canonical_json(semantic_plan).encode("utf-8")).hexdigest()
    return RecoveryPlan(
        recoverable=True,
        action=action,
        reason_code=code,
        recovery_plan_id=f"recovery_{digest[:24]}",
        plan_fingerprint=f"sha256:{digest}",
        failed_stage=stage,
        failure_class=str(semantic_plan["failure_class"]),
        expected_attempt_id=expected_attempt_id,
        attempt_no=attempt_no,
        expected_state_revision=state_revision,
        checkpoint_references=checkpoint_references,
    )
