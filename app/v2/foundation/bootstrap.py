"""Bootstrap helpers for selecting the V2 master-data backend."""

from __future__ import annotations

import sqlite3
from typing import Any

from app.config import Settings
from app.v2.db.runner import run_p1_1_migrations
from app.v2.foundation.models import BrandRecord, WorkspaceRecord, utcnow
from app.v2.foundation.postgres_store import PostgresMasterDataStore
from app.v2.foundation.sqlite_store import SQLiteMasterDataStore
from app.v2.runtime import resolve_v2_backend
from app.v2.foundation.service import MasterDataService
from app.v2.foundation.store import InMemoryMasterDataStore

# Default workspace seeded on startup so single-deployment users need no configuration.
# Frontend discovers this id via GET /workspaces/default on startup.
# Future: replace with proper user registration + workspace membership when multi-tenant
# auth is implemented. See docs/v2/development_tasks.md §6 Future TODOs.
DEFAULT_WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_USER_ID = "operator"
_DEFAULT_WORKSPACE_NAME = "default"
_DEFAULT_WORKSPACE_SLUG = "default"
_DEMO_BRAND_NAME = "轻量户外"
_DEMO_CHANNEL_PROFILE_URL = "https://www.xiaohongshu.com/user/profile/light-outdoor-demo"


def _ensure_default_workspace(service: MasterDataService) -> None:
    """Ensure the default workspace exists so GET /workspaces/default can serve it."""
    store = service._store
    if store.get_workspace(DEFAULT_WORKSPACE_ID) is not None:
        return
    now = utcnow()
    workspace = WorkspaceRecord(
        id=DEFAULT_WORKSPACE_ID,
        name=_DEFAULT_WORKSPACE_NAME,
        slug=_DEFAULT_WORKSPACE_SLUG,
        timezone="Asia/Shanghai",
        created_at=now,
        updated_at=now,
    )
    store.save_workspace(workspace)


def _find_demo_brand(service: MasterDataService) -> Any | None:
    brands = service.list_brands(workspace_id=DEFAULT_WORKSPACE_ID)
    for brand in brands:
        if brand.name == _DEMO_BRAND_NAME:
            return brand
    return None


def _ensure_default_demo_data(service: MasterDataService) -> None:
    """Seed a usable local demo brand under the default workspace for first-run UX."""
    _ensure_default_workspace(service)

    demo_brand = _find_demo_brand(service)
    if demo_brand is None:
        demo_brand = service.create_brand(
            workspace_id=DEFAULT_WORKSPACE_ID,
            name=_DEMO_BRAND_NAME,
            category="outdoor",
            stage="growth",
            target_audience={"age_ranges": ["25-34"], "gender_skew": "female"},
            brand_voice={"tone": "practical", "keywords": ["轻量", "通勤", "户外"]},
            goals={"primary": "topic_recommendation"},
        )
    channels = service.list_brand_channels(
        workspace_id=DEFAULT_WORKSPACE_ID,
        brand_id=demo_brand.id,
    )
    if not any(channel.profile_url == _DEMO_CHANNEL_PROFILE_URL for channel in channels):
        service.create_brand_channel(
            workspace_id=DEFAULT_WORKSPACE_ID,
            brand_id=demo_brand.id,
            platform="xiaohongshu",
            account_name="轻量户外官方号",
            profile_url=_DEMO_CHANNEL_PROFILE_URL,
            metadata={"seeded": True},
        )

    if service.get_active_brand_policy_config(
        workspace_id=DEFAULT_WORKSPACE_ID,
        brand_id=demo_brand.id,
    ) is None:
        service.replace_active_brand_policy_config(
            workspace_id=DEFAULT_WORKSPACE_ID,
            brand_id=demo_brand.id,
            policy_name="baseline_rule_v1",
            policy_version="v1",
            topic_type_targets={
                "targets": [
                    {"topic_type": "scenario", "min_ratio": 0.34, "max_ratio": 1.0, "priority_boost": 0.12},
                    {"topic_type": "problem", "min_ratio": 0.0, "max_ratio": 0.5, "priority_boost": 0.03},
                ]
            },
        )

    if not service.list_brand_state_snapshots(
        workspace_id=DEFAULT_WORKSPACE_ID,
        brand_id=demo_brand.id,
    ):
        service.create_brand_state_snapshot(
            workspace_id=DEFAULT_WORKSPACE_ID,
            brand_id=demo_brand.id,
            state_version="state_v1",
            stage="growth",
            state_features={"audience_focus": "urban commuting"},
            source_version="v1",
        )


def _reconcile_orphaned_brands(service: MasterDataService, db_path: str) -> None:
    """Re-create brand records for any brand_id referenced in PUBLISH_CANDIDATE artifacts
    that are missing from the store (e.g. brands created under the old InMemoryMasterDataStore)."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT DISTINCT
                   json_extract(payload_json, '$.brand_id') AS brand_id,
                   json_extract(payload_json, '$.workspace_id') AS workspace_id
               FROM workflow_artifacts
               WHERE artifact_type = 'publish_candidate'
                 AND json_extract(payload_json, '$.brand_id') IS NOT NULL"""
        ).fetchall()
        conn.close()
    except Exception:
        return

    now = utcnow()
    for row in rows:
        brand_id = row["brand_id"]
        workspace_id = row["workspace_id"] or DEFAULT_WORKSPACE_ID
        if not brand_id or service._store.get_brand(brand_id) is not None:
            continue
        # Ensure workspace exists before adding brand
        if service._store.get_workspace(workspace_id) is None:
            service._store.save_workspace(WorkspaceRecord(
                id=workspace_id,
                name="default",
                slug=f"ws-{workspace_id[:8]}",
                created_at=now,
                updated_at=now,
            ))
        service._store.save_brand(BrandRecord(
            id=brand_id,
            workspace_id=workspace_id,
            name="已完成创作品牌",
            category=None,
            stage="growth",
            target_audience={},
            brand_voice={},
            goals={},
            created_at=now,
            updated_at=now,
        ))


def build_master_data_runtime(config: Settings):
    backend = resolve_v2_backend(config, component="foundation")
    if backend == "postgres":
        run_p1_1_migrations(config.POSTGRES_DSN)
        store = PostgresMasterDataStore(config.POSTGRES_DSN)
        service = MasterDataService(store)
        _ensure_default_demo_data(service)
        return store, service

    if config.SQLITE_DB_PATH.strip() and config.SQLITE_DB_PATH.strip() != ":memory:":
        store = SQLiteMasterDataStore(config.SQLITE_DB_PATH)
        service = MasterDataService(store)
        _ensure_default_demo_data(service)
        _reconcile_orphaned_brands(service, config.SQLITE_DB_PATH)
        return store, service

    store = InMemoryMasterDataStore()
    service = MasterDataService(store)
    _ensure_default_demo_data(service)
    return store, service
