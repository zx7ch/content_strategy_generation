"""Bootstrap helpers for selecting the V2 master-data backend."""

from __future__ import annotations

from app.config import Settings
from app.v2.db.runner import run_p1_1_migrations
from app.v2.foundation.models import WorkspaceRecord, utcnow
from app.v2.foundation.postgres_store import PostgresMasterDataStore
from app.v2.runtime import resolve_v2_backend
from app.v2.foundation.service import MasterDataService
from app.v2.foundation.store import InMemoryMasterDataStore

DEFAULT_WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_USER_ID = "operator"
_DEFAULT_WORKSPACE_NAME = "default"
_DEFAULT_WORKSPACE_SLUG = "default"


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


def build_master_data_runtime(config: Settings):
    backend = resolve_v2_backend(config, component="foundation")
    if backend == "postgres":
        run_p1_1_migrations(config.POSTGRES_DSN)
        store = PostgresMasterDataStore(config.POSTGRES_DSN)
        service = MasterDataService(store)
        _ensure_default_workspace(service)
        return store, service

    store = InMemoryMasterDataStore()
    service = MasterDataService(store)
    _ensure_default_workspace(service)
    return store, service
