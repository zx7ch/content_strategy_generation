"""Store protocol and in-memory implementation for V2 master data."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from app.v2.foundation.models import (
    BrandChannelRecord,
    BrandPolicyConfigRecord,
    BrandRecord,
    BrandStateSnapshotRecord,
    WorkspaceRecord,
)


class MasterDataStore(Protocol):
    def save_workspace(self, workspace: WorkspaceRecord) -> WorkspaceRecord: ...

    def get_workspace(self, workspace_id: str) -> WorkspaceRecord | None: ...

    def get_workspace_by_slug(self, slug: str) -> WorkspaceRecord | None: ...

    def save_brand(self, brand: BrandRecord) -> BrandRecord: ...

    def get_brand(self, brand_id: str) -> BrandRecord | None: ...

    def list_brands(self, workspace_id: str) -> list[BrandRecord]: ...

    def save_brand_channel(self, channel: BrandChannelRecord) -> BrandChannelRecord: ...

    def get_brand_channel(self, channel_id: str) -> BrandChannelRecord | None: ...

    def list_brand_channels(self, brand_id: str) -> list[BrandChannelRecord]: ...

    def save_policy_config(self, policy: BrandPolicyConfigRecord) -> BrandPolicyConfigRecord: ...

    def list_policy_configs(self, brand_id: str) -> list[BrandPolicyConfigRecord]: ...

    def save_state_snapshot(self, snapshot: BrandStateSnapshotRecord) -> BrandStateSnapshotRecord: ...

    def list_state_snapshots(self, brand_id: str) -> list[BrandStateSnapshotRecord]: ...


class InMemoryMasterDataStore:
    def __init__(self) -> None:
        self._workspaces: dict[str, WorkspaceRecord] = {}
        self._workspace_slugs: dict[str, str] = {}
        self._brands: dict[str, BrandRecord] = {}
        self._channels: dict[str, BrandChannelRecord] = {}
        self._policies: dict[str, BrandPolicyConfigRecord] = {}
        self._snapshots: dict[str, BrandStateSnapshotRecord] = {}

    def save_workspace(self, workspace: WorkspaceRecord) -> WorkspaceRecord:
        existing_id = self._workspace_slugs.get(workspace.slug)
        if existing_id is not None and existing_id != workspace.id:
            raise ValueError(f"Workspace slug already exists: {workspace.slug}")
        self._workspaces[workspace.id] = workspace
        self._workspace_slugs[workspace.slug] = workspace.id
        return workspace

    def get_workspace(self, workspace_id: str) -> WorkspaceRecord | None:
        return self._workspaces.get(workspace_id)

    def get_workspace_by_slug(self, slug: str) -> WorkspaceRecord | None:
        workspace_id = self._workspace_slugs.get(slug)
        if workspace_id is None:
            return None
        return self._workspaces.get(workspace_id)

    def save_brand(self, brand: BrandRecord) -> BrandRecord:
        self._brands[brand.id] = brand
        return brand

    def get_brand(self, brand_id: str) -> BrandRecord | None:
        return self._brands.get(brand_id)

    def list_brands(self, workspace_id: str) -> list[BrandRecord]:
        brands = [brand for brand in self._brands.values() if brand.workspace_id == workspace_id]
        brands.sort(key=lambda item: item.created_at)
        return brands

    def save_brand_channel(self, channel: BrandChannelRecord) -> BrandChannelRecord:
        self._channels[channel.id] = channel
        return channel

    def get_brand_channel(self, channel_id: str) -> BrandChannelRecord | None:
        return self._channels.get(channel_id)

    def list_brand_channels(self, brand_id: str) -> list[BrandChannelRecord]:
        channels = [channel for channel in self._channels.values() if channel.brand_id == brand_id]
        channels.sort(key=lambda item: item.created_at)
        return channels

    def save_policy_config(self, policy: BrandPolicyConfigRecord) -> BrandPolicyConfigRecord:
        if policy.is_active:
            for existing_id, existing in list(self._policies.items()):
                if existing.brand_id == policy.brand_id and existing.is_active and existing.id != policy.id:
                    self._policies[existing_id] = replace(existing, is_active=False)
        self._policies[policy.id] = policy
        return policy

    def list_policy_configs(self, brand_id: str) -> list[BrandPolicyConfigRecord]:
        return [policy for policy in self._policies.values() if policy.brand_id == brand_id]

    def save_state_snapshot(self, snapshot: BrandStateSnapshotRecord) -> BrandStateSnapshotRecord:
        self._snapshots[snapshot.id] = snapshot
        return snapshot

    def list_state_snapshots(self, brand_id: str) -> list[BrandStateSnapshotRecord]:
        snapshots = [item for item in self._snapshots.values() if item.brand_id == brand_id]
        snapshots.sort(key=lambda item: item.valid_from)
        return snapshots
