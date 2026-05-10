"""Deterministic master-data service for V2 foundation work."""

from __future__ import annotations

import uuid
from typing import Any

from app.v2.foundation.models import (
    BrandChannelRecord,
    BrandPolicyConfigRecord,
    BrandRecord,
    BrandStateSnapshotRecord,
    WorkspaceRecord,
    utcnow,
)
from app.v2.foundation.store import MasterDataStore


class MasterDataError(ValueError):
    """Raised when master-data operations violate workspace or brand contracts."""


class MasterDataNotFoundError(MasterDataError):
    """Raised when a requested workspace or brand does not exist."""


class MasterDataScopeError(MasterDataError):
    """Raised when a brand-scoped request crosses workspace boundaries."""


class MasterDataConflictError(MasterDataError):
    """Raised when a write would violate a uniqueness or active-row contract."""


class MasterDataValidationError(MasterDataError):
    """Raised when a payload violates a normative V2 contract."""


class MasterDataInvariantError(MasterDataError):
    """Raised when persisted master data violates an assumed invariant."""


class MasterDataService:
    def __init__(self, store: MasterDataStore) -> None:
        self._store = store

    @staticmethod
    def _normalize_brand_stage(stage: str) -> str:
        normalized = stage.strip().lower()
        aliases = {
            "seed": "cold_start",
            "cold_start": "cold_start",
            "validation": "validation",
            "growth": "growth",
            "mature": "scaled",
            "scaled": "scaled",
        }
        if normalized not in aliases:
            raise MasterDataValidationError(f"unsupported brand stage: {stage}")
        return aliases[normalized]

    def create_workspace(self, *, name: str, slug: str, timezone: str = "Asia/Shanghai") -> WorkspaceRecord:
        if self._store.get_workspace_by_slug(slug) is not None:
            raise MasterDataConflictError(f"Workspace slug already exists: {slug}")
        now = utcnow()
        workspace = WorkspaceRecord(
            id=str(uuid.uuid4()),
            name=name,
            slug=slug,
            timezone=timezone,
            created_at=now,
            updated_at=now,
        )
        try:
            return self._store.save_workspace(workspace)
        except ValueError as exc:
            raise MasterDataConflictError(str(exc)) from exc

    def get_workspace(self, workspace_id: str) -> WorkspaceRecord:
        return self._require_workspace(workspace_id)

    def create_brand(
        self,
        *,
        workspace_id: str,
        name: str,
        category: str | None,
        stage: str,
        target_audience: dict[str, Any] | None = None,
        brand_voice: dict[str, Any] | None = None,
        goals: dict[str, Any] | None = None,
        is_demo: bool = False,
    ) -> BrandRecord:
        self._require_workspace(workspace_id)
        now = utcnow()
        brand = BrandRecord(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            name=name,
            category=category,
            stage=self._normalize_brand_stage(stage),
            target_audience=target_audience or {},
            brand_voice=brand_voice or {},
            goals=goals or {},
            is_demo=is_demo,
            created_at=now,
            updated_at=now,
        )
        return self._store.save_brand(brand)

    def delete_brand(self, *, workspace_id: str, brand_id: str) -> bool:
        self._require_brand_scope(workspace_id=workspace_id, brand_id=brand_id)
        self._store.delete_state_snapshots(brand_id)
        self._store.delete_policy_configs(brand_id)
        self._store.delete_brand_channels(brand_id)
        return self._store.delete_brand(brand_id)

    def update_brand(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        name: str | None = None,
        category: str | None = None,
        stage: str | None = None,
        target_audience: dict[str, Any] | None = None,
        brand_voice: dict[str, Any] | None = None,
        goals: dict[str, Any] | None = None,
    ) -> BrandRecord:
        existing = self._require_brand_scope(workspace_id=workspace_id, brand_id=brand_id)
        now = utcnow()
        updated = BrandRecord(
            id=existing.id,
            workspace_id=existing.workspace_id,
            name=name if name is not None else existing.name,
            category=category if category is not None else existing.category,
            stage=self._normalize_brand_stage(stage) if stage is not None else existing.stage,
            target_audience=target_audience if target_audience is not None else existing.target_audience,
            brand_voice=brand_voice if brand_voice is not None else existing.brand_voice,
            goals=goals if goals is not None else existing.goals,
            created_at=existing.created_at,
            updated_at=now,
        )
        return self._store.save_brand(updated)

    def get_brand(self, *, workspace_id: str, brand_id: str) -> BrandRecord:
        return self._require_brand_scope(workspace_id=workspace_id, brand_id=brand_id)

    def list_brands(self, *, workspace_id: str) -> list[BrandRecord]:
        self._require_workspace(workspace_id)
        return self._store.list_brands(workspace_id)

    def create_brand_channel(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        platform: str,
        external_account_id: str | None = None,
        account_name: str | None = None,
        profile_url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> BrandChannelRecord:
        self._require_brand_scope(workspace_id=workspace_id, brand_id=brand_id)
        now = utcnow()
        channel = BrandChannelRecord(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            brand_id=brand_id,
            platform=platform,
            external_account_id=external_account_id,
            account_name=account_name,
            profile_url=profile_url,
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )
        return self._store.save_brand_channel(channel)

    def update_brand_channel(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        channel_id: str,
        external_account_id: str | None = None,
        account_name: str | None = None,
        profile_url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> BrandChannelRecord:
        self._require_brand_scope(workspace_id=workspace_id, brand_id=brand_id)
        channel = self._store.get_brand_channel(channel_id)
        if channel is None or channel.brand_id != brand_id or channel.workspace_id != workspace_id:
            raise MasterDataNotFoundError(f"Brand channel not found: {channel_id}")
        now = utcnow()
        updated = BrandChannelRecord(
            id=channel.id,
            workspace_id=channel.workspace_id,
            brand_id=channel.brand_id,
            platform=channel.platform,
            external_account_id=external_account_id if external_account_id is not None else channel.external_account_id,
            account_name=account_name if account_name is not None else channel.account_name,
            profile_url=profile_url if profile_url is not None else channel.profile_url,
            status=channel.status,
            metadata=metadata if metadata is not None else channel.metadata,
            created_at=channel.created_at,
            updated_at=now,
        )
        return self._store.save_brand_channel(updated)

    def list_brand_channels(
        self,
        *,
        workspace_id: str,
        brand_id: str,
    ) -> list[BrandChannelRecord]:
        self._require_brand_scope(workspace_id=workspace_id, brand_id=brand_id)
        return self._store.list_brand_channels(brand_id)

    def validate_policy_targets(self, topic_type_targets: dict[str, Any]) -> None:
        if not isinstance(topic_type_targets, dict):
            raise MasterDataValidationError("topic_type_targets must be an object")
        allowed_keys = {"targets"}
        unexpected_keys = sorted(set(topic_type_targets) - allowed_keys)
        if unexpected_keys:
            raise MasterDataValidationError(
                f"topic_type_targets contains unsupported keys: {', '.join(unexpected_keys)}"
            )
        targets = topic_type_targets.get("targets")
        if targets is None:
            if topic_type_targets:
                raise MasterDataValidationError("topic_type_targets.targets is required when provided")
            return
        if not isinstance(targets, list):
            raise MasterDataValidationError("topic_type_targets.targets must be a list")

        min_ratio_sum = 0.0
        for index, target in enumerate(targets):
            if not isinstance(target, dict):
                raise MasterDataValidationError(
                    f"topic_type_targets.targets[{index}] must be an object"
                )
            min_ratio = float(target.get("min_ratio", 0))
            max_ratio = float(target.get("max_ratio", 1))
            if max_ratio < min_ratio:
                raise MasterDataValidationError(
                    f"topic_type_targets.targets[{index}] has max_ratio < min_ratio"
                )
            min_ratio_sum += min_ratio

        if min_ratio_sum > 1.0:
            raise MasterDataValidationError("topic_type_targets sum(min_ratio) must be <= 1.0")

    def replace_active_brand_policy_config(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        policy_name: str,
        policy_version: str,
        hard_filter_rules: dict[str, Any] | None = None,
        brand_fit_rules: dict[str, Any] | None = None,
        exploration_preset_override: dict[str, Any] | None = None,
        topic_type_targets: dict[str, Any] | None = None,
    ) -> BrandPolicyConfigRecord:
        self._require_brand_scope(workspace_id=workspace_id, brand_id=brand_id)
        self.validate_policy_targets(topic_type_targets or {})
        now = utcnow()
        policy = BrandPolicyConfigRecord(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            brand_id=brand_id,
            policy_name=policy_name,
            policy_version=policy_version,
            hard_filter_rules=hard_filter_rules or {},
            brand_fit_rules=brand_fit_rules or {},
            exploration_preset_override=exploration_preset_override or {},
            topic_type_targets=topic_type_targets or {},
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        return self._store.save_policy_config(policy)

    def get_active_brand_policy_config(
        self,
        *,
        workspace_id: str,
        brand_id: str,
    ) -> BrandPolicyConfigRecord | None:
        self._require_brand_scope(workspace_id=workspace_id, brand_id=brand_id)
        policies = self._store.list_policy_configs(brand_id)
        active = [policy for policy in policies if policy.is_active]
        if len(active) > 1:
            raise MasterDataInvariantError(
                f"Brand {brand_id} has multiple active policy configs"
            )
        return active[0] if active else None

    def create_brand_state_snapshot(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        state_version: str,
        stage: str,
        state_features: dict[str, Any] | None = None,
        source_version: str = "v1",
    ) -> BrandStateSnapshotRecord:
        self._require_brand_scope(workspace_id=workspace_id, brand_id=brand_id)
        now = utcnow()
        snapshot = BrandStateSnapshotRecord(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            brand_id=brand_id,
            state_version=state_version,
            stage=self._normalize_brand_stage(stage),
            state_features=state_features or {},
            source_version=source_version,
            computed_at=now,
            valid_from=now,
            created_at=now,
        )
        return self._store.save_state_snapshot(snapshot)

    def list_brand_state_snapshots(
        self,
        *,
        workspace_id: str,
        brand_id: str,
    ) -> list[BrandStateSnapshotRecord]:
        self._require_brand_scope(workspace_id=workspace_id, brand_id=brand_id)
        snapshots = self._store.list_state_snapshots(brand_id)
        snapshots.sort(key=lambda item: item.valid_from, reverse=True)
        return snapshots

    def _require_workspace(self, workspace_id: str) -> WorkspaceRecord:
        workspace = self._store.get_workspace(workspace_id)
        if workspace is None:
            raise MasterDataNotFoundError(f"Workspace not found: {workspace_id}")
        return workspace

    def _require_brand_scope(self, *, workspace_id: str, brand_id: str) -> BrandRecord:
        self._require_workspace(workspace_id)
        brand = self._store.get_brand(brand_id)
        if brand is None:
            raise MasterDataNotFoundError(f"Brand not found: {brand_id}")
        if brand.workspace_id != workspace_id:
            raise MasterDataScopeError(
                f"Brand {brand_id} does not belong to workspace {workspace_id}"
            )
        return brand
