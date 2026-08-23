"""Pure transition policy for the single Content Research state machine."""

from __future__ import annotations

from app.content_research.lifecycle.models import (
    ContentResearchState,
    TransitionDecision,
)


class LifecycleTransitionError(ValueError):
    """Raised before persistence when a lifecycle transition is illegal."""


_TRANSITIONS: dict[tuple[ContentResearchState, str], ContentResearchState] = {
    (
        ContentResearchState.PRESEARCH_RUNNING,
        "presearch_completed",
    ): ContentResearchState.BRIEF_CONFIRMATION_REQUIRED,
    (
        ContentResearchState.BRIEF_CONFIRMATION_REQUIRED,
        "revise_subject",
    ): ContentResearchState.PRESEARCH_RUNNING,
    (
        ContentResearchState.BRIEF_CONFIRMATION_REQUIRED,
        "confirm_brief",
    ): ContentResearchState.SCOPE_CONFIRMATION_REQUIRED,
    (
        ContentResearchState.SCOPE_CONFIRMATION_REQUIRED,
        "confirm_scope",
    ): ContentResearchState.RETRIEVAL_QUEUED,
    (
        ContentResearchState.RETRIEVAL_QUEUED,
        "worker_claimed",
    ): ContentResearchState.RETRIEVAL_RUNNING,
    (
        ContentResearchState.RETRIEVAL_RUNNING,
        "retrieval_completed",
    ): ContentResearchState.COVERAGE_EVALUATING,
    (
        ContentResearchState.COVERAGE_EVALUATING,
        "coverage_satisfied",
    ): ContentResearchState.REPORT_COMPOSING,
    (
        ContentResearchState.COVERAGE_EVALUATING,
        "coverage_insufficient",
    ): ContentResearchState.COVERAGE_DECISION_REQUIRED,
    (
        ContentResearchState.COVERAGE_DECISION_REQUIRED,
        "expand_coverage",
    ): ContentResearchState.RETRIEVAL_QUEUED,
    (
        ContentResearchState.COVERAGE_DECISION_REQUIRED,
        "relax_coverage",
    ): ContentResearchState.RETRIEVAL_QUEUED,
    (
        ContentResearchState.COVERAGE_DECISION_REQUIRED,
        "generate_limited_report",
    ): ContentResearchState.REPORT_COMPOSING,
    (
        ContentResearchState.REPORT_COMPOSING,
        "report_published",
    ): ContentResearchState.REPORT_READY,
    (
        ContentResearchState.RECOVERY_REQUIRED,
        "retry_presearch",
    ): ContentResearchState.PRESEARCH_RUNNING,
    (
        ContentResearchState.RECOVERY_REQUIRED,
        "retry_retrieval",
    ): ContentResearchState.RETRIEVAL_QUEUED,
    (
        ContentResearchState.RECOVERY_REQUIRED,
        "retry_report",
    ): ContentResearchState.REPORT_COMPOSING,
}

_TERMINAL_STATES = {
    ContentResearchState.REPORT_READY,
    ContentResearchState.CANCELLED_OR_FAILED,
}


def transition(
    *,
    current_state: ContentResearchState,
    current_revision: int,
    event: str,
) -> TransitionDecision:
    """Return the one legal next state without reading or writing persistence."""

    if current_revision < 1:
        raise LifecycleTransitionError("current lifecycle revision must be positive")

    if current_state not in _TERMINAL_STATES and event == "fail":
        next_state = ContentResearchState.RECOVERY_REQUIRED
    elif current_state not in _TERMINAL_STATES and event == "cancel":
        next_state = ContentResearchState.CANCELLED_OR_FAILED
    else:
        next_state = _TRANSITIONS.get((current_state, event))

    if next_state is None:
        raise LifecycleTransitionError(
            f"event {event!r} is not allowed from {current_state.value!r}"
        )

    return TransitionDecision(
        from_state=current_state,
        to_state=next_state,
        event=event,
        next_revision=current_revision + 1,
    )
