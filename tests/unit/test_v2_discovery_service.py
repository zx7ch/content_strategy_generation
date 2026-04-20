from __future__ import annotations

import pytest

from app.v2.discovery.service import (
    CURRENT_DISCOVERY_QUERY_GENERATION_VERSION,
    LEGACY_DISCOVERY_QUERY_GENERATION_VERSION,
    DiscoveryQueryExpansionError,
    DiscoveryService,
)
from app.v2.discovery.query_expander import DiscoveryExpandedQuery, DiscoveryExpansionResult


class FakeQueryExpander:
    async def expand_topic(self, topic: str):
        return DiscoveryExpansionResult(queries=[], source="llm")


class SuccessQueryExpander:
    async def expand_topic(self, topic: str):
        return DiscoveryExpansionResult(
            queries=[
                DiscoveryExpandedQuery(category="core", query_text=topic),
                DiscoveryExpandedQuery(category="problem", query_text=f"{topic}怎么选"),
            ],
            source="llm",
        )


@pytest.mark.asyncio
async def test_create_task_marks_query_generation_version_as_current(tmp_path) -> None:
    service = DiscoveryService(
        database_path=tmp_path / "discovery.db",
        secret="test-secret",
        query_expander=SuccessQueryExpander(),
    )

    result = await service.create_task(workspace_id="ws-1", brand_id="brand-1", topic="敏感肌护肤")

    assert result.query_generation_version == CURRENT_DISCOVERY_QUERY_GENERATION_VERSION
    assert result.query_generation_source == "llm"


@pytest.mark.asyncio
async def test_create_task_raises_when_query_expander_returns_no_queries(tmp_path) -> None:
    service = DiscoveryService(
        database_path=tmp_path / "discovery.db",
        secret="test-secret",
        query_expander=FakeQueryExpander(),
    )

    with pytest.raises(DiscoveryQueryExpansionError):
        await service.create_task(workspace_id="ws-1", brand_id="brand-1", topic="敏感肌护肤")


def test_get_task_workspace_treats_missing_version_as_legacy(tmp_path) -> None:
    service = DiscoveryService(
        database_path=tmp_path / "discovery.db",
        secret="test-secret",
    )
    task_id, _ = service._storage.create_task("通勤穿搭")
    with service._connect() as conn:
        conn.execute(
            """
            INSERT INTO v2_discovery_task_scope (task_id, workspace_id, brand_id, query_generation_version, created_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (task_id, "ws-1", "brand-1", ""),
        )
        conn.commit()

    result = service.get_task_workspace(workspace_id="ws-1", brand_id="brand-1", task_id=task_id)

    assert result.query_generation_version == LEGACY_DISCOVERY_QUERY_GENERATION_VERSION
    assert result.query_generation_source == "legacy"
