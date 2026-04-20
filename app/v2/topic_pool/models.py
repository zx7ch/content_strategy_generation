"""Typed models for V2 topic pool generation and persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.v2.foundation.models import utcnow


@dataclass(frozen=True)
class TopicPoolItemRecord:
    id: str
    workspace_id: str
    brand_id: str
    topic_id: str
    title: str
    angle: str
    hypothesis: str
    evidence_summary: dict[str, Any] = field(default_factory=dict)
    source_agent: str = "topic_hypothesis_agent"
    source_run_id: str | None = None
    status: str = "candidate"
    novelty_score: float = 0.0
    fit_score: float = 0.0
    trend_score: float = 0.0
    historical_reward_score: float = 0.0
    policy_score: float = 0.0
    final_score: float = 0.0
    last_scored_at: datetime | None = None
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class TopicPoolRefreshResult:
    refresh_run_id: str
    status: str
    generated_item_count: int
    archived_item_count: int
    total_candidate_count: int
    refreshed_at: datetime = field(default_factory=utcnow)
    error_summary: dict[str, Any] | None = None


@dataclass(frozen=True)
class TopicPoolListItem:
    id: str
    topic_id: str
    display_name: str
    normalized_name: str
    topic_type: str
    title: str
    angle: str
    hypothesis: str
    evidence_summary: dict[str, Any]
    source_agent: str
    status: str
    final_score: float
    updated_at: datetime
    score_breakdown: dict[str, Any] = field(default_factory=dict)
    evidence_provenance: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class TopicPoolListResult:
    brand_id: str
    brand_name: str
    brand_stage: str
    target_audience: dict[str, Any]
    total_candidate_count: int
    best_score: float
    last_refresh_at: datetime | None
    items: list[TopicPoolListItem]
