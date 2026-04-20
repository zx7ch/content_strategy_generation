from __future__ import annotations

import pytest

from app.v2.foundation.service import (
    MasterDataConflictError,
    MasterDataError,
    MasterDataInvariantError,
    MasterDataService,
    MasterDataValidationError,
)
from app.v2.foundation.store import InMemoryMasterDataStore


def test_master_data_service_creates_workspace_and_brand() -> None:
    service = MasterDataService(InMemoryMasterDataStore())

    workspace = service.create_workspace(name="Acme", slug="acme")
    brand = service.create_brand(
        workspace_id=workspace.id,
        name="Acme Outdoor",
        category="outdoor",
        stage="cold_start",
    )

    assert brand.workspace_id == workspace.id
    assert brand.name == "Acme Outdoor"


def test_master_data_service_registers_channel_in_same_workspace_scope() -> None:
    service = MasterDataService(InMemoryMasterDataStore())
    workspace = service.create_workspace(name="Acme", slug="acme")
    brand = service.create_brand(
        workspace_id=workspace.id,
        name="Acme Outdoor",
        category="outdoor",
        stage="cold_start",
    )

    channel = service.create_brand_channel(
        workspace_id=workspace.id,
        brand_id=brand.id,
        platform="xhs",
        account_name="Acme Outdoor",
        profile_url="https://www.xiaohongshu.com/user/profile/acme-outdoor",
    )

    assert channel.workspace_id == workspace.id
    assert channel.brand_id == brand.id
    assert channel.platform == "xhs"


def test_replace_active_brand_policy_config_keeps_single_active_policy() -> None:
    service = MasterDataService(InMemoryMasterDataStore())
    workspace = service.create_workspace(name="Acme", slug="acme")
    brand = service.create_brand(
        workspace_id=workspace.id,
        name="Acme Outdoor",
        category="outdoor",
        stage="cold_start",
    )

    first = service.replace_active_brand_policy_config(
        workspace_id=workspace.id,
        brand_id=brand.id,
        policy_name="baseline_rule_v1",
        policy_version="v1",
    )
    second = service.replace_active_brand_policy_config(
        workspace_id=workspace.id,
        brand_id=brand.id,
        policy_name="baseline_rule_v1",
        policy_version="v2",
    )

    active = service.get_active_brand_policy_config(
        workspace_id=workspace.id,
        brand_id=brand.id,
    )

    assert first.id != second.id
    assert active is not None
    assert active.id == second.id
    assert active.policy_version == "v2"


def test_create_brand_state_snapshot_returns_traceable_lineage_ids() -> None:
    service = MasterDataService(InMemoryMasterDataStore())
    workspace = service.create_workspace(name="Acme", slug="acme")
    brand = service.create_brand(
        workspace_id=workspace.id,
        name="Acme Outdoor",
        category="outdoor",
        stage="cold_start",
    )

    policy = service.replace_active_brand_policy_config(
        workspace_id=workspace.id,
        brand_id=brand.id,
        policy_name="baseline_rule_v1",
        policy_version="v1",
    )
    snapshot = service.create_brand_state_snapshot(
        workspace_id=workspace.id,
        brand_id=brand.id,
        state_version="state_v1",
        stage="cold_start",
        state_features={"recent_post_count_90d": 0},
    )

    assert policy.workspace_id == workspace.id
    assert policy.brand_id == brand.id
    assert snapshot.workspace_id == workspace.id
    assert snapshot.brand_id == brand.id
    assert snapshot.id


def test_master_data_service_rejects_cross_workspace_brand_access() -> None:
    service = MasterDataService(InMemoryMasterDataStore())
    first_workspace = service.create_workspace(name="Acme", slug="acme")
    second_workspace = service.create_workspace(name="Beta", slug="beta")
    brand = service.create_brand(
        workspace_id=first_workspace.id,
        name="Acme Outdoor",
        category="outdoor",
        stage="cold_start",
    )

    with pytest.raises(MasterDataError, match="does not belong to workspace"):
        service.replace_active_brand_policy_config(
            workspace_id=second_workspace.id,
            brand_id=brand.id,
            policy_name="baseline_rule_v1",
            policy_version="v1",
        )


def test_create_workspace_rejects_duplicate_slug() -> None:
    service = MasterDataService(InMemoryMasterDataStore())

    service.create_workspace(name="Acme", slug="acme")

    with pytest.raises(MasterDataConflictError, match="slug already exists"):
        service.create_workspace(name="Acme 2", slug="acme")


def test_replace_active_brand_policy_config_rejects_topic_type_targets_sum_above_one() -> None:
    service = MasterDataService(InMemoryMasterDataStore())
    workspace = service.create_workspace(name="Acme", slug="acme")
    brand = service.create_brand(
        workspace_id=workspace.id,
        name="Acme Outdoor",
        category="outdoor",
        stage="cold_start",
    )

    with pytest.raises(MasterDataValidationError, match="sum\\(min_ratio\\)"):
        service.replace_active_brand_policy_config(
            workspace_id=workspace.id,
            brand_id=brand.id,
            policy_name="baseline_rule_v1",
            policy_version="v1",
            topic_type_targets={
                "targets": [
                    {"topic_type": "scenario", "min_ratio": 0.7, "max_ratio": 0.9},
                    {"topic_type": "problem", "min_ratio": 0.4, "max_ratio": 0.6},
                ]
            },
        )


def test_replace_active_brand_policy_config_rejects_max_ratio_below_min_ratio() -> None:
    service = MasterDataService(InMemoryMasterDataStore())
    workspace = service.create_workspace(name="Acme", slug="acme")
    brand = service.create_brand(
        workspace_id=workspace.id,
        name="Acme Outdoor",
        category="outdoor",
        stage="cold_start",
    )

    with pytest.raises(MasterDataValidationError, match="max_ratio < min_ratio"):
        service.replace_active_brand_policy_config(
            workspace_id=workspace.id,
            brand_id=brand.id,
            policy_name="baseline_rule_v1",
            policy_version="v1",
            topic_type_targets={
                "targets": [
                    {"topic_type": "scenario", "min_ratio": 0.6, "max_ratio": 0.4},
                ]
            },
        )


def test_replace_active_brand_policy_config_rejects_unexpected_shape() -> None:
    service = MasterDataService(InMemoryMasterDataStore())
    workspace = service.create_workspace(name="Acme", slug="acme")
    brand = service.create_brand(
        workspace_id=workspace.id,
        name="Acme Outdoor",
        category="outdoor",
        stage="cold_start",
    )

    with pytest.raises(MasterDataValidationError, match="unsupported keys"):
        service.replace_active_brand_policy_config(
            workspace_id=workspace.id,
            brand_id=brand.id,
            policy_name="baseline_rule_v1",
            policy_version="v1",
            topic_type_targets={"target": []},
        )


def test_get_active_brand_policy_config_raises_on_multiple_active_configs() -> None:
    store = InMemoryMasterDataStore()
    service = MasterDataService(store)
    workspace = service.create_workspace(name="Acme", slug="acme")
    brand = service.create_brand(
        workspace_id=workspace.id,
        name="Acme Outdoor",
        category="outdoor",
        stage="cold_start",
    )
    first = service.replace_active_brand_policy_config(
        workspace_id=workspace.id,
        brand_id=brand.id,
        policy_name="baseline_rule_v1",
        policy_version="v1",
    )
    second = service.replace_active_brand_policy_config(
        workspace_id=workspace.id,
        brand_id=brand.id,
        policy_name="baseline_rule_v1",
        policy_version="v2",
    )
    store._policies[first.id] = first
    store._policies[second.id] = second

    with pytest.raises(MasterDataInvariantError, match="multiple active"):
        service.get_active_brand_policy_config(
            workspace_id=workspace.id,
            brand_id=brand.id,
        )
