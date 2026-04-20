"""Typed decision records and API-facing service results for V2 P1-4."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.v2.foundation.models import utcnow


@dataclass(frozen=True)
class DecisionBatchRecord:
    id: str
    workspace_id: str
    brand_id: str
    brand_state_snapshot_id: str
    brand_policy_config_id: str
    objective: str
    exploration_mode: str
    context_snapshot: dict[str, Any]
    policy_name: str
    policy_version: str
    candidate_count: int
    chosen_count: int
    requested_slot_count: int
    batch_status: str = "completed"
    created_by_type: str = "system"
    created_by_id: str | None = None
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class DecisionEventRecord:
    id: str
    workspace_id: str
    brand_id: str
    decision_batch_id: str
    brand_state_snapshot_id: str
    brand_policy_config_id: str
    slot_index: int
    serving_policy_name: str
    serving_policy_version: str
    logging_policy_name: str
    logging_policy_version: str
    decision_mode: str
    exploration_mode: str
    objective: str
    context_features: dict[str, Any]
    candidate_set: list[dict[str, Any]]
    ranked_list: list[dict[str, Any]]
    chosen_action_id: str
    propensities: list[dict[str, Any]]
    reward_version: str = "phase1_reward_v1"
    normalization_window_spec: dict[str, Any] = field(default_factory=dict)
    sampling_metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class DecisionBatchItemRecord:
    batch_id: str
    topic_pool_item_id: str
    selected_slot_index: int
    final_rank_position: int
    source_decision_event_id: str | None
    review_status: str = "pending"
    reviewed_at: datetime | None = None
    reviewed_by_type: str | None = None
    reviewed_by_id: str | None = None
    edited_title: str | None = None
    edited_angle: str | None = None
    edited_hypothesis: str | None = None
    review_notes: str | None = None
    score: float = 0.0
    reason_codes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateSetSnapshotRecord:
    id: str
    workspace_id: str
    brand_id: str
    decision_batch_id: str
    decision_event_id: str | None
    snapshot_scope: str
    slot_index: int | None
    candidate_count: int
    candidate_set: list[dict[str, Any]]
    metrics: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class DecisionSelection:
    slot_index: int
    topic_pool_item_id: str
    decision_event_id: str
    title: str
    angle: str
    hypothesis: str
    score: float
    topic_type: str
    decision_mode: str
    review_status: str
    reason_codes: list[str] = field(default_factory=list)
    edited_title: str | None = None
    edited_angle: str | None = None
    edited_hypothesis: str | None = None
    review_notes: str | None = None


@dataclass(frozen=True)
class DecisionRunResult:
    batch_id: str
    workspace_id: str
    brand_id: str
    brand_state_snapshot_id: str
    brand_policy_config_id: str
    objective: str
    exploration_mode: str
    requested_slot_count: int
    candidate_count: int
    chosen_count: int
    items: list[DecisionSelection]
    created_at: datetime


@dataclass(frozen=True)
class DecisionReviewResult:
    batch_id: str
    slot_index: int
    topic_pool_item_id: str
    decision_event_id: str | None
    review_status: str
    title: str
    angle: str
    hypothesis: str
    score: float
    reason_codes: list[str] = field(default_factory=list)
    review_notes: str | None = None
    reviewed_at: datetime | None = None


@dataclass(frozen=True)
class DecisionBatchDetailResult:
    batch_id: str
    workspace_id: str
    brand_id: str
    brand_state_snapshot_id: str
    brand_policy_config_id: str
    objective: str
    exploration_mode: str
    requested_slot_count: int
    candidate_count: int
    chosen_count: int
    items: list[DecisionSelection]
    created_at: datetime
