from __future__ import annotations

import uuid

from app.config import Settings
from app.v2.foundation.bootstrap import DEFAULT_WORKSPACE_ID, build_master_data_runtime


def test_build_master_data_runtime_seeds_default_workspace_demo_data(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        SQLITE_DB_PATH=str(tmp_path / "runtime.sqlite"),
        CHROMA_PERSIST_DIR=str(tmp_path / "chroma"),
    )

    _store, service = build_master_data_runtime(settings)

    workspace = service.get_workspace(DEFAULT_WORKSPACE_ID)
    brands = service.list_brands(workspace_id=DEFAULT_WORKSPACE_ID)

    assert workspace.id == DEFAULT_WORKSPACE_ID
    assert brands

    demo_brand = brands[0]
    channels = service.list_brand_channels(
        workspace_id=DEFAULT_WORKSPACE_ID,
        brand_id=demo_brand.id,
    )
    active_policy = service.get_active_brand_policy_config(
        workspace_id=DEFAULT_WORKSPACE_ID,
        brand_id=demo_brand.id,
    )
    snapshots = service.list_brand_state_snapshots(
        workspace_id=DEFAULT_WORKSPACE_ID,
        brand_id=demo_brand.id,
    )

    assert demo_brand.name == "轻量户外"
    assert channels
    assert active_policy is not None
    assert snapshots


def test_build_master_data_runtime_keeps_demo_brand_available_when_default_workspace_already_has_other_brands(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        SQLITE_DB_PATH=str(tmp_path / "runtime.sqlite"),
        CHROMA_PERSIST_DIR=str(tmp_path / "chroma"),
    )

    _store, service = build_master_data_runtime(settings)
    service.create_brand(
        workspace_id=DEFAULT_WORKSPACE_ID,
        name=f"历史品牌-{uuid.uuid4()}",
        category="beauty",
        stage="growth",
    )

    _store, second_service = build_master_data_runtime(settings)
    brands = second_service.list_brands(workspace_id=DEFAULT_WORKSPACE_ID)

    demo_brand = next((brand for brand in brands if brand.name == "轻量户外"), None)
    assert demo_brand is not None
    assert second_service.list_brand_channels(
        workspace_id=DEFAULT_WORKSPACE_ID,
        brand_id=demo_brand.id,
    )
