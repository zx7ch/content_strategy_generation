from __future__ import annotations

from typing import Any

import pytest

from app.content_research.lifecycle.models import ContentResearchState
from app.content_research.lifecycle.recovery import plan_recovery


@pytest.mark.parametrize(
    ("error", "checkpoints", "expected_attempt_id"),
    (
        (
            {
                "code": "PROVIDER_TIMEOUT",
                "stage": "presearch",
                "operation": "llm_presearch",
                "retryable": True,
                "recovery_action": "retry_presearch",
                "attempt_id": "presearch-attempt",
            },
            {"brief_id": "brief-1"},
            "presearch-attempt",
        ),
        (
            {
                "code": "FORMAL_RESEARCH_DISPATCH_FAILED",
                "stage": "retrieval_running",
                "operation": "formal_research_dispatch",
                "retryable": True,
                "recovery_action": "retry_retrieval",
            },
            {
                "scope_contract_id": "scope-1",
                "dispatch_attempt_id": "run-1:2",
            },
            "run-1:2",
        ),
        (
            {
                "code": "MARKETING_ANALYSIS_FAILED",
                "stage": "marketing_analysis",
                "retryable": True,
                "recovery_action": "retry_analysis",
                "attempt_id": "analysis-attempt",
            },
            {
                "scope_contract_id": "scope-1",
                "analysis_attempt_id": "analysis-attempt",
            },
            "analysis-attempt",
        ),
        (
            {
                "code": "REPORT_FINALIZATION_FAILED",
                "stage": "report_composing",
                "retryable": True,
                "recovery_action": "retry_report",
                "preserved_analysis_attempt_id": "analysis-attempt",
            },
            {
                "scope_contract_id": "scope-1",
                "analysis_attempt_id": "analysis-attempt",
            },
            "analysis-attempt",
        ),
    ),
)
def test_recovery_plans_require_matching_persisted_checkpoints(
    error: dict[str, Any],
    checkpoints: dict[str, str],
    expected_attempt_id: str,
) -> None:
    arguments: dict[str, Any] = {
        "state": ContentResearchState.RECOVERY_REQUIRED,
        "state_revision": 4,
        "error": error,
        "brief_id": None,
        "scope_contract_id": None,
        "dispatch_attempt_id": None,
        "execution_attempt_id": None,
        "analysis_attempt_id": None,
        "publication_id": None,
        **checkpoints,
    }

    first = plan_recovery(**arguments)
    second = plan_recovery(**arguments)

    assert first is not None
    assert second == first
    assert first.action == error["recovery_action"]
    assert first.expected_attempt_id == expected_attempt_id
    assert first.expected_state_revision == 4
    assert first.plan_fingerprint.startswith("sha256:")

    revised = plan_recovery(**{**arguments, "state_revision": 5})
    assert revised is not None
    assert revised.plan_fingerprint != first.plan_fingerprint


def test_recovery_planner_rejects_local_conflicts_and_stale_attempts() -> None:
    base = {
        "state": ContentResearchState.RECOVERY_REQUIRED,
        "state_revision": 2,
        "brief_id": None,
        "scope_contract_id": "scope-1",
        "dispatch_attempt_id": None,
        "execution_attempt_id": "current-attempt",
        "analysis_attempt_id": None,
        "publication_id": None,
    }
    local_conflict = {
        "code": "LOCAL_IDENTITY_CONFLICT",
        "stage": "retrieval_running",
        "retryable": True,
        "recovery_action": "retry_retrieval",
        "attempt_id": "current-attempt",
    }
    stale_attempt = {
        **local_conflict,
        "code": "FORMAL_RESEARCH_DISPATCH_FAILED",
        "attempt_id": "stale-attempt",
    }

    assert plan_recovery(**base, error=local_conflict) is None
    assert plan_recovery(**base, error=stale_attempt) is None
