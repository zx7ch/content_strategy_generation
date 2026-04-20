from __future__ import annotations

from contextlib import asynccontextmanager
from fastapi.testclient import TestClient
import pytest

from app.api.routes.router import app
from app.services.xhs_spider import SpiderSearchSortOption, XHSPost
from app.v2.discovery import DiscoveryService
from app.v2.discovery.query_expander import DiscoveryExpandedQuery, DiscoveryExpansionResult
from app.v2.foundation import InMemoryMasterDataStore, MasterDataService


class FakeSpiderClient:
    @staticmethod
    def get_hotspot_sort_options() -> tuple[SpiderSearchSortOption, ...]:
        return (
            SpiderSearchSortOption(key="likes", label="最多点赞", value=2),
            SpiderSearchSortOption(key="comments", label="最多评论", value=3),
            SpiderSearchSortOption(key="collections", label="最多收藏", value=4),
        )

    async def search_with_retry(self, query: str, num: int = 50, sort: int = 2) -> list[XHSPost]:
        if sort == 2:
            title = "点赞高的内容"
            likes, comments, collections = 120, 12, 30
        elif sort == 3:
            title = "评论高的内容"
            likes, comments, collections = 80, 48, 18
        else:
            title = "收藏高的内容"
            likes, comments, collections = 75, 10, 66
        return [
            XHSPost(
                note_id=f"{query}-{sort}",
                title=f"{query} {title}",
                title_is_explicit=True,
                content=f"{query} 的热点摘要",
                author="tester",
                tags=[],
                liked_count=likes,
                collected_count=collections,
                comment_count=comments,
                share_count=0,
                note_url=f"https://www.xiaohongshu.com/explore/{query}-{sort}",
                images=[],
            )
        ]


class FakeQueryExpander:
    async def expand_topic(self, topic: str) -> list[DiscoveryExpandedQuery]:
        return DiscoveryExpansionResult(
            queries=[
                DiscoveryExpandedQuery(category="core", query_text=topic),
                DiscoveryExpandedQuery(category="crowd", query_text=f"{topic} 小个子"),
                DiscoveryExpandedQuery(category="scenario", query_text=f"夏天{topic}"),
                DiscoveryExpandedQuery(category="problem", query_text=f"{topic}怎么选"),
                DiscoveryExpandedQuery(category="compare", query_text=f"{topic}平替"),
                DiscoveryExpandedQuery(category="decision", query_text=f"{topic}避坑"),
            ],
            source="llm",
        )


@pytest.fixture(autouse=True)
def reset_v2_discovery_app_state(tmp_path):
    master_store = InMemoryMasterDataStore()
    master_service = MasterDataService(master_store)
    discovery_service = DiscoveryService(
        database_path=tmp_path / "discovery.db",
        secret="test-secret",
        spider_client=FakeSpiderClient(),
        query_expander=FakeQueryExpander(),
    )
    app.state.v2_master_data_store = master_store
    app.state.v2_master_data_service = master_service
    app.state.v2_discovery_service = discovery_service
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    app.router.lifespan_context = _noop_lifespan
    yield
    app.router.lifespan_context = original_lifespan
    for attr in (
        "v2_master_data_service",
        "v2_master_data_store",
        "v2_discovery_service",
    ):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def _create_workspace(client: TestClient, *, name: str = "Acme", slug: str = "acme") -> dict:
    response = client.post(
        "/workspaces",
        json={"name": name, "slug": slug, "timezone": "Asia/Shanghai"},
    )
    assert response.status_code == 201
    return response.json()


def _workspace_headers(workspace_id: str) -> dict[str, str]:
    return {
        "X-Workspace-Id": workspace_id,
        "X-User-Id": "user-1",
    }


def _create_brand(client: TestClient, workspace_id: str, *, name: str = "Acme Outdoor") -> dict:
    response = client.post(
        "/brands",
        headers=_workspace_headers(workspace_id),
        json={"name": name, "category": "outdoor", "stage": "growth"},
    )
    assert response.status_code == 201
    return response.json()


def test_v2_discovery_task_routes_create_read_and_manage_queries() -> None:
    with TestClient(app) as client:
        workspace = _create_workspace(client)
        brand = _create_brand(client, workspace["id"])
        headers = _workspace_headers(workspace["id"])

        created = client.post(
            f"/brands/{brand['id']}/discovery/tasks",
            headers=headers,
            json={"topic": "轻量户外"},
        )
        assert created.status_code == 201
        created_payload = created.json()
        assert created_payload["task_id"]
        assert created_payload["token"]
        assert created_payload["query_generation_version"] == "llm_v1"
        assert created_payload["query_generation_source"] == "llm"
        assert created_payload["expanded_queries"][0]["query_text"] == "轻量户外"
        assert [item["category"] for item in created_payload["expanded_queries"]] == [
            "core",
            "crowd",
            "scenario",
            "problem",
            "compare",
            "decision",
        ]
        assert created_payload["expanded_queries"][1]["query_text"] == "轻量户外 小个子"

        task_id = created_payload["task_id"]
        fetched = client.get(f"/brands/{brand['id']}/discovery/tasks/{task_id}", headers=headers)
        assert fetched.status_code == 200
        assert fetched.json()["task_id"] == task_id
        assert fetched.json()["query_generation_source"] == "llm"

        added = client.post(
            f"/brands/{brand['id']}/discovery/tasks/{task_id}/queries",
            headers=headers,
            json={"text": "轻量户外 徒步穿搭"},
        )
        assert added.status_code == 200
        custom_queries = [item for item in added.json()["expanded_queries"] if item["category"] == "custom"]
        assert [item["query_text"] for item in custom_queries] == ["轻量户外 徒步穿搭"]

        query_id = custom_queries[0]["query_id"]
        deleted = client.delete(
            f"/brands/{brand['id']}/discovery/tasks/{task_id}/queries/{query_id}",
            headers=headers,
        )
        assert deleted.status_code == 200
        assert all(item["query_id"] != query_id for item in deleted.json()["expanded_queries"])


def test_v2_discovery_hotspot_refresh_runs_inside_main_app_backend() -> None:
    with TestClient(app) as client:
        workspace = _create_workspace(client)
        brand = _create_brand(client, workspace["id"])
        headers = _workspace_headers(workspace["id"])

        created = client.post(
            f"/brands/{brand['id']}/discovery/tasks",
            headers=headers,
            json={"topic": "敏感肌修护"},
        )
        task_id = created.json()["task_id"]

        refreshed = client.post(
            f"/brands/{brand['id']}/discovery/tasks/{task_id}/hotspots/refresh",
            headers=headers,
        )

    assert refreshed.status_code == 200
    payload = refreshed.json()
    assert payload["hotspot_status"] == "ready"
    assert {item["metric"] for item in payload["hotspots"]} == {"likes", "comments", "collections"}
    assert payload["hotspots"][0]["items"][0]["title"].startswith("敏感肌修护")


def test_v2_discovery_routes_reject_cross_brand_scope() -> None:
    with TestClient(app) as client:
        workspace = _create_workspace(client)
        headers = _workspace_headers(workspace["id"])
        brand_a = _create_brand(client, workspace["id"], name="Brand A")
        brand_b = _create_brand(client, workspace["id"], name="Brand B")

        created = client.post(
            f"/brands/{brand_a['id']}/discovery/tasks",
            headers=headers,
            json={"topic": "办公室微运动"},
        )
        task_id = created.json()["task_id"]

        forbidden = client.get(f"/brands/{brand_b['id']}/discovery/tasks/{task_id}", headers=headers)

    assert forbidden.status_code == 403
    payload = forbidden.json()
    assert payload["error_code"] == "WORKSPACE_SCOPE_MISMATCH"
