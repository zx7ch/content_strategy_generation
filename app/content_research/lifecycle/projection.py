"""Build the public lifecycle projection from the authoritative Run row."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from app.content_research.lifecycle.models import ContentResearchState, RunProjection

_ALLOWED_ACTIONS: dict[ContentResearchState, tuple[str, ...]] = {
    ContentResearchState.PRESEARCH_RUNNING: ("cancel",),
    ContentResearchState.BRIEF_CONFIRMATION_REQUIRED: (
        "confirm_brief",
        "revise_subject",
        "cancel",
    ),
    ContentResearchState.SCOPE_CONFIRMATION_REQUIRED: (
        "confirm_scope",
        "replace_scope_draft",
        "cancel",
    ),
    ContentResearchState.RETRIEVAL_QUEUED: ("cancel",),
    ContentResearchState.RETRIEVAL_RUNNING: ("cancel",),
    ContentResearchState.COVERAGE_EVALUATING: (),
    ContentResearchState.COVERAGE_DECISION_REQUIRED: (
        "expand_coverage",
        "generate_limited_report",
        "relax_coverage",
        "cancel",
    ),
    ContentResearchState.REPORT_COMPOSING: ("cancel",),
    ContentResearchState.REPORT_READY: (),
    ContentResearchState.RECOVERY_REQUIRED: ("retry_presearch", "cancel"),
    ContentResearchState.CANCELLED_OR_FAILED: (),
}


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _json_mapping(value: Any) -> Mapping[str, Any] | None:
    if not value:
        return None
    parsed = json.loads(str(value))
    return parsed if isinstance(parsed, dict) else None


def projection_from_row(
    row: Mapping[str, Any],
    *,
    brief_id: str | None,
    scope_contract_id: str | None = None,
    has_dispatch: bool = False,
    execution_attempt_id: str | None = None,
    publication_id: str | None = None,
) -> RunProjection:
    raw_state = row["content_research_state"]
    raw_revision = row["state_revision"]
    if raw_state is None or raw_revision is None:
        raise ValueError("historical workflow run has no current lifecycle authority")
    state = ContentResearchState(str(raw_state))
    current_brief_id = (
        None if state is ContentResearchState.PRESEARCH_RUNNING else brief_id
    )
    if state is ContentResearchState.BRIEF_CONFIRMATION_REQUIRED and current_brief_id is None:
        raise ValueError("brief confirmation state requires a current Brief")
    if state in {
        ContentResearchState.PRESEARCH_RUNNING,
        ContentResearchState.BRIEF_CONFIRMATION_REQUIRED,
        ContentResearchState.SCOPE_CONFIRMATION_REQUIRED,
    } and (scope_contract_id is not None or has_dispatch or execution_attempt_id is not None):
        raise ValueError(
            f"{state.value} cannot own frozen Scope, dispatch, or execution artifacts"
        )
    error = _json_mapping(row["lifecycle_error_json"])
    allowed_actions = _ALLOWED_ACTIONS[state]
    if (
        state is ContentResearchState.RECOVERY_REQUIRED
        and error is not None
        and error.get("recovery_action") == "retry_retrieval"
    ):
        allowed_actions = ("retry_retrieval", "cancel")
    elif (
        state is ContentResearchState.RECOVERY_REQUIRED
        and error is not None
        and error.get("code") == "MARKETING_ANALYSIS_FAILED"
    ):
        allowed_actions = ("retry_analysis", "cancel")
    elif (
        state is ContentResearchState.RECOVERY_REQUIRED
        and error is not None
        and (
            error.get("code") == "REPORT_FINALIZATION_FAILED"
            or error.get("stage") == ContentResearchState.REPORT_COMPOSING.value
        )
    ):
        allowed_actions = ("retry_report", "cancel")
    return RunProjection(
        run_id=str(row["run_id"]),
        thread_id=str(row["thread_id"]),
        state=state,
        state_revision=int(raw_revision),
        entered_at=_parse_datetime(row["state_entered_at"]),
        allowed_actions=allowed_actions,
        reason_code=str(row["error_code"]) if row["error_code"] else None,
        error=error,
        brief_id=current_brief_id,
        scope_contract_id=scope_contract_id,
        execution_attempt_id=execution_attempt_id,
        publication_id=publication_id,
    )
