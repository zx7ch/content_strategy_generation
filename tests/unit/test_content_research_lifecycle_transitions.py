from __future__ import annotations

import pytest

from app.content_research.lifecycle.models import ContentResearchState
from app.content_research.lifecycle.transitions import (
    LifecycleTransitionError,
    transition,
)


@pytest.mark.parametrize(
    ("current", "event", "expected"),
    [
        (
            ContentResearchState.PRESEARCH_RUNNING,
            "presearch_completed",
            ContentResearchState.BRIEF_CONFIRMATION_REQUIRED,
        ),
        (
            ContentResearchState.BRIEF_CONFIRMATION_REQUIRED,
            "revise_subject",
            ContentResearchState.PRESEARCH_RUNNING,
        ),
        (
            ContentResearchState.BRIEF_CONFIRMATION_REQUIRED,
            "confirm_brief",
            ContentResearchState.SCOPE_CONFIRMATION_REQUIRED,
        ),
        (
            ContentResearchState.SCOPE_CONFIRMATION_REQUIRED,
            "confirm_scope",
            ContentResearchState.RETRIEVAL_QUEUED,
        ),
        (
            ContentResearchState.RETRIEVAL_QUEUED,
            "worker_claimed",
            ContentResearchState.RETRIEVAL_RUNNING,
        ),
        (
            ContentResearchState.RETRIEVAL_RUNNING,
            "retrieval_completed",
            ContentResearchState.COVERAGE_EVALUATING,
        ),
        (
            ContentResearchState.COVERAGE_EVALUATING,
            "coverage_satisfied",
            ContentResearchState.REPORT_COMPOSING,
        ),
        (
            ContentResearchState.COVERAGE_EVALUATING,
            "coverage_insufficient",
            ContentResearchState.COVERAGE_DECISION_REQUIRED,
        ),
        (
            ContentResearchState.COVERAGE_DECISION_REQUIRED,
            "expand_coverage",
            ContentResearchState.RETRIEVAL_QUEUED,
        ),
        (
            ContentResearchState.COVERAGE_DECISION_REQUIRED,
            "relax_coverage",
            ContentResearchState.RETRIEVAL_QUEUED,
        ),
        (
            ContentResearchState.COVERAGE_DECISION_REQUIRED,
            "generate_limited_report",
            ContentResearchState.REPORT_COMPOSING,
        ),
        (
            ContentResearchState.REPORT_COMPOSING,
            "report_published",
            ContentResearchState.REPORT_READY,
        ),
        (
            ContentResearchState.RECOVERY_REQUIRED,
            "retry_presearch",
            ContentResearchState.PRESEARCH_RUNNING,
        ),
        (
            ContentResearchState.RECOVERY_REQUIRED,
            "retry_retrieval",
            ContentResearchState.RETRIEVAL_QUEUED,
        ),
        (
            ContentResearchState.RECOVERY_REQUIRED,
            "retry_report",
            ContentResearchState.REPORT_COMPOSING,
        ),
    ],
)
def test_transition_table_advances_only_to_the_contract_state(
    current: ContentResearchState,
    event: str,
    expected: ContentResearchState,
) -> None:
    decision = transition(current_state=current, current_revision=7, event=event)

    assert decision.from_state is current
    assert decision.to_state is expected
    assert decision.event == event
    assert decision.next_revision == 8


@pytest.mark.parametrize(
    ("current", "event"),
    [
        (ContentResearchState.PRESEARCH_RUNNING, "confirm_brief"),
        (ContentResearchState.BRIEF_CONFIRMATION_REQUIRED, "confirm_scope"),
        (ContentResearchState.SCOPE_CONFIRMATION_REQUIRED, "worker_claimed"),
        (ContentResearchState.RETRIEVAL_RUNNING, "report_published"),
        (ContentResearchState.REPORT_READY, "retry_report"),
        (ContentResearchState.CANCELLED_OR_FAILED, "retry_presearch"),
    ],
)
def test_transition_table_rejects_illegal_stage_skips(
    current: ContentResearchState,
    event: str,
) -> None:
    with pytest.raises(LifecycleTransitionError, match="not allowed"):
        transition(current_state=current, current_revision=3, event=event)


@pytest.mark.parametrize(
    "current",
    [
        state
        for state in ContentResearchState
        if state
        not in {
            ContentResearchState.REPORT_READY,
            ContentResearchState.CANCELLED_OR_FAILED,
        }
    ],
)
def test_known_failure_converges_every_nonterminal_state_to_recovery(
    current: ContentResearchState,
) -> None:
    decision = transition(current_state=current, current_revision=2, event="fail")

    assert decision.to_state is ContentResearchState.RECOVERY_REQUIRED
    assert decision.next_revision == 3


@pytest.mark.parametrize(
    "current",
    [
        state
        for state in ContentResearchState
        if state
        not in {
            ContentResearchState.REPORT_READY,
            ContentResearchState.CANCELLED_OR_FAILED,
        }
    ],
)
def test_cancel_fences_every_nonterminal_state(current: ContentResearchState) -> None:
    decision = transition(current_state=current, current_revision=4, event="cancel")

    assert decision.to_state is ContentResearchState.CANCELLED_OR_FAILED
    assert decision.next_revision == 5


def test_transition_rejects_invalid_persisted_revision() -> None:
    with pytest.raises(LifecycleTransitionError, match="revision"):
        transition(
            current_state=ContentResearchState.PRESEARCH_RUNNING,
            current_revision=0,
            event="presearch_completed",
        )
