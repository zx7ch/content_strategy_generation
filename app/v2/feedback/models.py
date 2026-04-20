"""Typed publish, performance, and evaluation records for V2 P1-5."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.v2.foundation.models import utcnow


@dataclass(frozen=True)
class PublishRecordRecord:
    id: str
    workspace_id: str
    brand_id: str
    channel_id: str
    topic_pool_item_id: str | None
    decision_event_id: str | None
    decision_batch_id: str | None
    publish_status: str
    published_at: datetime | None = None
    content_item_id: str | None = None
    creative_variant: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class PerformanceSnapshotRecord:
    id: str
    workspace_id: str
    brand_id: str
    publish_record_id: str
    observation_window_hours: int
    snapshot_at: datetime
    reward_version: str
    raw_metrics: dict[str, Any] = field(default_factory=dict)
    normalized_metrics: dict[str, Any] = field(default_factory=dict)
    short_term_reward: float = 0.0
    long_term_reward: float = 0.0
    composite_reward: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class FeedbackEventRecord:
    id: str
    workspace_id: str
    brand_id: str
    publish_record_id: str
    decision_event_id: str | None
    event_type: str
    observation_window_hours: int | None
    reward_version: str
    reward_window_start_at: datetime | None
    reward_window_end_at: datetime | None
    reward_payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class EvaluationRunRecord:
    id: str
    workspace_id: str
    brand_id: str
    evaluation_type: str
    policy_name: str
    policy_version: str
    baseline_policy_name: str | None
    baseline_policy_version: str | None
    dataset_start_at: datetime | None
    dataset_end_at: datetime | None
    sample_count: int
    status: str
    summary: dict[str, Any] = field(default_factory=dict)
    created_by_type: str = "operator"
    created_by_id: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None


@dataclass(frozen=True)
class EvaluationRunSliceRecord:
    id: str
    evaluation_run_id: str
    slice_key: str
    slice_value: str
    sample_count: int
    metrics: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class PublishRecordView:
    publish_record_id: str
    brand_id: str
    channel_id: str
    channel_label: str
    title: str
    topic_pool_item_id: str | None
    decision_event_id: str | None
    decision_batch_id: str | None
    decision_source: str
    publish_status: str
    published_at: datetime | None
    creative_variant: str | None
    created_at: datetime


@dataclass(frozen=True)
class PerformanceSnapshotView:
    performance_snapshot_id: str
    publish_record_id: str
    publish_title: str
    observation_window_hours: int
    snapshot_at: datetime
    reward_version: str
    impressions: int
    clicks: int
    engagement_rate: float
    conversion_proxy_label: str
    short_term_reward: float
    composite_reward: float


@dataclass(frozen=True)
class EvaluationRunDetail:
    evaluation_run_id: str
    brand_id: str
    evaluation_type: str
    policy_name: str
    policy_version: str
    status: str
    sample_count: int
    summary: dict[str, Any]
    slices: list[EvaluationRunSliceRecord]
    created_at: datetime
    finished_at: datetime | None
