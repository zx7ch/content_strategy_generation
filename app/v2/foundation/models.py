"""Typed master-data records for V2 foundation services."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class WorkspaceRecord:
    id: str
    name: str
    slug: str
    timezone: str = "Asia/Shanghai"
    status: str = "active"
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class BrandRecord:
    id: str
    workspace_id: str
    name: str
    category: str | None
    stage: str
    target_audience: dict[str, Any]
    brand_voice: dict[str, Any]
    goals: dict[str, Any]
    is_demo: bool = False
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class BrandChannelRecord:
    id: str
    workspace_id: str
    brand_id: str
    platform: str
    external_account_id: str | None = None
    account_name: str | None = None
    profile_url: str | None = None
    status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class BrandPolicyConfigRecord:
    id: str
    workspace_id: str
    brand_id: str
    policy_name: str
    policy_version: str
    hard_filter_rules: dict[str, Any]
    brand_fit_rules: dict[str, Any]
    exploration_preset_override: dict[str, Any]
    topic_type_targets: dict[str, Any]
    is_active: bool = True
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class BrandStateSnapshotRecord:
    id: str
    workspace_id: str
    brand_id: str
    state_version: str
    stage: str
    state_features: dict[str, Any]
    source_type: str = "rule_engine"
    source_version: str = "v1"
    computed_at: datetime = field(default_factory=utcnow)
    valid_from: datetime = field(default_factory=utcnow)
    valid_to: datetime | None = None
    created_at: datetime = field(default_factory=utcnow)
