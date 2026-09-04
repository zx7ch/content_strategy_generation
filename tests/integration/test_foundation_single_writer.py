from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.runtime_write_coordinator import RuntimeWriteCoordinator
from app.v2.foundation.models import BrandRecord, WorkspaceRecord
from app.v2.foundation.mutations import foundation_mutation_handlers
from app.v2.foundation.sqlite_store import SQLiteMasterDataStore


def test_foundation_records_share_runtime_writer(tmp_path: Path) -> None:
    database = tmp_path / "foundation.sqlite"
    SQLiteMasterDataStore(str(database))

    async def exercise() -> None:
        writer = RuntimeWriteCoordinator(database, handlers=foundation_mutation_handlers())
        await writer.start()
        store = SQLiteMasterDataStore(str(database), writer=writer)
        workspace = WorkspaceRecord(id="workspace-owned", name="Owned", slug="owned")
        brand = BrandRecord(
            id="brand-owned",
            workspace_id=workspace.id,
            name="Brand",
            category="apparel",
            stage="growth",
            target_audience={"segment": "commuter"},
            brand_voice={"tone": "clear"},
            goals={"growth": True},
        )
        await store.save_workspace_async(workspace)
        await store.save_brand_async(brand)

        assert store.get_workspace(workspace.id) == workspace
        assert store.get_brand(brand.id) == brand
        await writer.close()

    asyncio.run(exercise())
