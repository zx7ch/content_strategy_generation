"""Stable state and transition value objects for Content Research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping


class ContentResearchState(str, Enum):
    PRESEARCH_RUNNING = "presearch_running"
    BRIEF_CONFIRMATION_REQUIRED = "brief_confirmation_required"
    SCOPE_CONFIRMATION_REQUIRED = "scope_confirmation_required"
    RETRIEVAL_QUEUED = "retrieval_queued"
    RETRIEVAL_RUNNING = "retrieval_running"
    COVERAGE_EVALUATING = "coverage_evaluating"
    COVERAGE_DECISION_REQUIRED = "coverage_decision_required"
    REPORT_COMPOSING = "report_composing"
    REPORT_READY = "report_ready"
    RECOVERY_REQUIRED = "recovery_required"
    CANCELLED_OR_FAILED = "cancelled_or_failed"


@dataclass(frozen=True)
class TransitionDecision:
    from_state: ContentResearchState
    to_state: ContentResearchState
    event: str
    next_revision: int


@dataclass(frozen=True)
class LifecycleCommand:
    command_id: str
    run_id: str
    expected_state: ContentResearchState | None
    expected_revision: int
    kind: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class ExecutionEvent:
    run_id: str
    expected_revision: int
    attempt_id: str | None
    lease_token: str | None
    kind: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class RecoveryPlan:
    recoverable: bool
    action: str
    reason_code: str
    recovery_plan_id: str
    plan_fingerprint: str
    failed_stage: str
    failure_class: str
    expected_attempt_id: str
    attempt_no: int | None
    expected_state_revision: int
    checkpoint_references: tuple[str, ...]


@dataclass(frozen=True)
class RunProjection:
    run_id: str
    thread_id: str
    state: ContentResearchState
    state_revision: int
    entered_at: datetime
    allowed_actions: tuple[str, ...]
    recovery_plan: RecoveryPlan | None = None
    reason_code: str | None = None
    error: Mapping[str, Any] | None = None
    brief_id: str | None = None
    scope_contract_id: str | None = None
    execution_attempt_id: str | None = None
    coverage_snapshot_id: str | None = None
    publication_id: str | None = None
