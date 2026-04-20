from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from experiments.xhs_extension_mvp.server.app import create_app
from experiments.xhs_extension_mvp.server.candidate_builder import NormalizedItem, build_candidates
from experiments.xhs_extension_mvp.server.query_expander import expand_topic
from experiments.xhs_extension_mvp.server.storage import MVPStorage


def test_expand_topic_returns_natural_queries_for_beauty() -> None:
    expansions = expand_topic("敏感肌护肤")

    assert [item.category for item in expansions] == ["core", "crowd", "scenario", "problem", "compare", "decision"]
    assert expansions[0].query_text == "敏感肌护肤"
    assert len({item.query_text for item in expansions}) == len(expansions)
    assert len(expansions) == 6
    assert "敏感肌护肤 新手入门" not in {item.query_text for item in expansions}
    assert "敏感肌护肤 日常场景" not in {item.query_text for item in expansions}
    assert any(item.query_text == "换季敏感肌护肤" for item in expansions)
    assert any(item.query_text == "敏感肌护肤怎么选" for item in expansions)


def test_expand_topic_returns_natural_queries_for_style() -> None:
    expansions = expand_topic("通勤穿搭")

    query_texts = [item.query_text for item in expansions]

    assert len(expansions) in {5, 6}
    assert query_texts[0] == "通勤穿搭"
    assert all("新手入门" not in query for query in query_texts)
    assert all("日常场景" not in query for query in query_texts)
    assert any(query in {"小个子通勤穿搭", "学生党通勤穿搭"} for query in query_texts)
    assert any(query.endswith("怎么搭") or query.endswith("怎么选") for query in query_texts)


def test_expand_topic_returns_specific_queries_for_generic_topic() -> None:
    expansions = expand_topic("租房收纳")

    query_texts = [item.query_text for item in expansions]

    assert len(expansions) >= 5
    assert all("新手入门" not in query for query in query_texts)
    assert all("日常场景" not in query for query in query_texts)
    assert any(query.endswith("技巧") or query.endswith("改造") or query.endswith("怎么选") for query in query_texts)


def test_expand_topic_avoids_duplicate_prefixes_and_intents() -> None:
    expansions = expand_topic("敏感肌护肤避坑")

    query_texts = [item.query_text for item in expansions]

    assert all("敏感肌敏感肌" not in query for query in query_texts)
    assert "敏感肌护肤避坑避坑" not in query_texts
    assert "敏感肌护肤避坑怎么选避坑" not in query_texts


def test_custom_queries_can_be_added_and_deduped(tmp_path) -> None:
    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    client = TestClient(app)

    task_id = client.post("/mvp/tasks", json={"topic": "敏感肌护肤"}).json()["task_id"]

    response = client.post(
        f"/mvp/tasks/{task_id}/queries",
        json={"text": "敏感肌修护精华推荐\n敏感肌护肤\n敏感肌修护精华推荐\n油皮敏感肌护肤"},
    )

    assert response.status_code == 200
    assert response.json()["created_count"] == 2
    assert response.json()["skipped_count"] == 2

    snapshot = client.get(f"/mvp/tasks/{task_id}").json()
    custom_queries = [query for query in snapshot["expanded_queries"] if query["category"] == "custom"]
    assert [query["query_text"] for query in custom_queries] == ["敏感肌修护精华推荐", "油皮敏感肌护肤"]
    auto_queries = [query for query in snapshot["expanded_queries"] if query["category"] not in {"core", "custom"}]
    assert len(auto_queries) == 5
    assert snapshot["collection_summary"]["capture_batch_count"] == 0
    assert snapshot["recommended_notes"] == []


def test_custom_query_can_be_deleted_but_generated_query_cannot(tmp_path) -> None:
    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    client = TestClient(app)

    task_id = client.post("/mvp/tasks", json={"topic": "通勤穿搭"}).json()["task_id"]
    add_response = client.post(f"/mvp/tasks/{task_id}/queries", json={"text": "通勤穿搭小个子"})
    custom_query_id = client.get(f"/mvp/tasks/{task_id}").json()["expanded_queries"][-1]["query_id"]

    assert add_response.status_code == 200

    delete_custom = client.delete(f"/mvp/tasks/{task_id}/queries/{custom_query_id}")
    assert delete_custom.status_code == 200
    assert delete_custom.json()["deleted"] is True

    snapshot = client.get(f"/mvp/tasks/{task_id}").json()
    generated_query_id = snapshot["expanded_queries"][0]["query_id"]
    delete_generated = client.delete(f"/mvp/tasks/{task_id}/queries/{generated_query_id}")
    assert delete_generated.status_code == 409


def test_build_candidates_prefers_frequent_high_signal_terms() -> None:
    items = [
        NormalizedItem(
            note_id="1",
            title="敏感肌修护 精简护肤",
            author="a",
            source_url="https://example.com/1",
            raw_href="https://example.com/1",
            xsec_token="",
            xsec_source="",
            debug_url_source="test",
            query_text="敏感肌护肤",
            excerpt="换季修护 屏障稳定",
            tags=["敏感肌", "修护"],
            likes=120,
            comments=12,
            collections=30,
        ),
        NormalizedItem(
            note_id="2",
            title="敏感肌修护 避坑清单",
            author="b",
            source_url="https://example.com/2",
            raw_href="https://example.com/2",
            xsec_token="",
            xsec_source="",
            debug_url_source="test",
            query_text="敏感肌 怎么选 避坑",
            excerpt="成分避坑 屏障修护",
            tags=["敏感肌", "避坑"],
            likes=90,
            comments=10,
            collections=20,
        ),
        NormalizedItem(
            note_id="3",
            title="通勤底妆 快速出门",
            author="c",
            source_url="https://example.com/3",
            raw_href="https://example.com/3",
            xsec_token="",
            xsec_source="",
            debug_url_source="test",
            query_text="通勤底妆",
            excerpt="五分钟通勤",
            tags=["通勤", "底妆"],
            likes=20,
            comments=2,
            collections=5,
        ),
    ]

    candidates = build_candidates("敏感肌护肤", items)

    assert len(candidates) >= 2
    assert any("敏感肌" in candidate.title or "修护" in candidate.title for candidate in candidates[:2])
    assert candidates[0].evidence_refs
    assert candidates[0].supporting_note_count >= 1
    assert candidates[0].query_coverage_count >= 1
    assert "推荐指数" in candidates[0].score_explanation or "推荐" in candidates[0].score_explanation


def test_capture_token_expiry_and_validation(tmp_path) -> None:
    storage = MVPStorage(tmp_path / "mvp.db", secret="secret")
    storage.init_db()
    task_id, _ = storage.create_task("通勤穿搭")

    token, _ = storage.create_capture_token(task_id, ttl_seconds=1)
    assert storage.validate_capture_token(token) == task_id

    expired_token, _ = storage.create_capture_token(task_id, ttl_seconds=-1)
    try:
        storage.validate_capture_token(expired_token)
    except Exception as exc:  # noqa: BLE001
        assert "expired" in str(exc).lower()
    else:
        raise AssertionError("Expected expired capture token to be rejected")


def test_create_task_response_includes_immediately_usable_capture_token(tmp_path) -> None:
    db_path = tmp_path / "mvp.db"
    app = create_app(database_path=db_path, secret="secret")
    client = TestClient(app)

    response = client.post("/mvp/tasks", json={"topic": "轻量化穿搭"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"]
    assert payload["token"]
    assert payload["expires_at"]

    storage = MVPStorage(db_path, secret="secret")
    assert storage.validate_capture_token(payload["token"]) == payload["task_id"]


def test_capture_endpoint_dedupes_by_note_id(tmp_path) -> None:
    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    client = TestClient(app)

    create_response = client.post("/mvp/tasks", json={"topic": "租房收纳"})
    assert create_response.status_code == 200
    create_payload = create_response.json()
    task_id = create_payload["task_id"]
    token = create_payload["token"]

    payload = {
        "token": token,
        "page_type": "search_result",
        "query_text": "租房收纳",
        "items": [
            {
                "source_url": "https://www.xiaohongshu.com/explore/abc123",
                "raw_href": "/explore/abc123?xsec_token=test-token&xsec_source=pc_search",
                "xsec_token": "test-token",
                "xsec_source": "pc_search",
                "debug_url_source": "test_payload",
                "page_type": "search_result",
                "query_text": "租房收纳",
                "note_id": "abc123",
                "title": "租房收纳思路",
                "author": "u1",
                "visible_text_excerpt": "小空间收纳",
                "tags": ["收纳", "租房"],
                "likes": 10,
                "comments": 2,
                "collections": 5,
                "cover_image_url": ""
            },
            {
                "source_url": "https://www.xiaohongshu.com/explore/abc123",
                "raw_href": "/explore/abc123?xsec_token=test-token&xsec_source=pc_search",
                "xsec_token": "test-token",
                "xsec_source": "pc_search",
                "debug_url_source": "test_payload",
                "page_type": "search_result",
                "query_text": "租房收纳",
                "note_id": "abc123",
                "title": "租房收纳思路更新版",
                "author": "u1",
                "visible_text_excerpt": "小空间收纳二次编辑",
                "tags": ["收纳", "租房"],
                "likes": 12,
                "comments": 3,
                "collections": 6,
                "cover_image_url": ""
            }
        ]
    }

    capture_response = client.post("/mvp/captures", json=payload)
    assert capture_response.status_code == 200
    assert capture_response.json()["imported_count"] == 1

    snapshot_response = client.get(f"/mvp/tasks/{task_id}")
    snapshot = snapshot_response.json()
    assert snapshot["imported_item_count"] == 1
    assert snapshot["collection_summary"]["capture_batch_count"] == 1
    assert snapshot["collection_summary"]["deduped_item_count"] == 1
    assert len(snapshot["candidates"]) >= 1
    assert snapshot["candidates"][0]["supporting_note_count"] >= 1
    assert snapshot["candidates"][0]["query_coverage_count"] >= 1
    assert snapshot["candidates"][0]["score_explanation"]
    assert snapshot["candidates"][0]["evidence_refs"][0]["xsec_token"] == "test-token"
    assert snapshot["candidates"][0]["evidence_refs"][0]["raw_href"].endswith("xsec_source=pc_search")
    assert len(snapshot["recommended_notes"]) == 1
    assert snapshot["recommended_notes"][0]["note_id"] == "abc123"
    assert snapshot["recommended_notes"][0]["query_coverage_count"] == 1
    assert snapshot["recommended_notes"][0]["score_reason"]
    assert snapshot["recommended_notes"][0]["why_recommended"]
    assert snapshot["recommended_notes"][0]["excerpt"]


def test_invalid_capture_token_returns_401(tmp_path) -> None:
    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    client = TestClient(app)

    response = client.post(
        "/mvp/captures",
        json={"token": "bad.token", "page_type": "search_result", "query_text": "", "items": []},
    )

    assert response.status_code == 401


def test_capture_endpoint_supports_extension_cors_preflight(tmp_path) -> None:
    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    client = TestClient(app)

    response = client.options(
        "/mvp/captures",
        headers={
            "Origin": "chrome-extension://test-extension-id",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_workspace_assets_disable_browser_caching(tmp_path) -> None:
    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    client = TestClient(app)

    index_response = client.get("/")
    script_response = client.get("/static/app.js")

    assert index_response.status_code == 200
    assert script_response.status_code == 200
    assert index_response.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"
    assert script_response.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"
    assert index_response.headers["pragma"] == "no-cache"
    assert script_response.headers["pragma"] == "no-cache"


def test_manual_seed_import_rebuilds_candidates(tmp_path) -> None:
    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    client = TestClient(app)

    create_response = client.post("/mvp/tasks", json={"topic": "通勤穿搭"})
    task_id = create_response.json()["task_id"]

    manual_response = client.post(
        f"/mvp/tasks/{task_id}/manual-seeds",
        json={"text": "通勤穿搭避坑\n上班族一周穿搭公式\nhttps://www.xiaohongshu.com/explore/demo123"},
    )
    assert manual_response.status_code == 200
    assert manual_response.json()["imported_count"] == 3

    snapshot_response = client.get(f"/mvp/tasks/{task_id}")
    snapshot = snapshot_response.json()
    assert snapshot["imported_item_count"] == 3
    assert snapshot["collection_summary"]["manual_seed_count"] == 3
    assert len(snapshot["candidates"]) >= 1
    assert snapshot["candidates"][0]["evidence_refs"]
    assert snapshot["candidates"][0]["supporting_note_count"] >= 1
    assert snapshot["candidates"][0]["query_coverage_count"] >= 1
    assert snapshot["candidates"][0]["score_explanation"]


def test_recommended_notes_accumulate_query_coverage(tmp_path) -> None:
    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    client = TestClient(app)

    create_payload = client.post("/mvp/tasks", json={"topic": "敏感肌修护"}).json()
    task_id = create_payload["task_id"]
    token = create_payload["token"]

    base_item = {
        "source_url": "https://www.xiaohongshu.com/explore/note123",
        "raw_href": "/explore/note123?xsec_token=q1&xsec_source=pc_search",
        "xsec_token": "q1",
        "xsec_source": "pc_search",
        "debug_url_source": "test_payload",
        "page_type": "search_result",
        "note_id": "note123",
        "title": "敏感肌修护避坑笔记",
        "author": "tester",
        "visible_text_excerpt": "换季修护第一步先看成分和肤感。",
        "tags": ["敏感肌", "修护"],
        "likes": 99,
        "comments": 11,
        "collections": 22,
        "cover_image_url": "",
    }

    response_one = client.post(
        "/mvp/captures",
        json={
            "token": token,
            "page_type": "search_result",
            "query_text": "敏感肌修护",
            "items": [{**base_item, "query_text": "敏感肌修护"}],
        },
    )
    response_two = client.post(
        "/mvp/captures",
        json={
            "token": token,
            "page_type": "search_result",
            "query_text": "敏感肌修护避坑",
            "items": [{**base_item, "query_text": "敏感肌修护避坑"}],
        },
    )

    assert response_one.status_code == 200
    assert response_two.status_code == 200
    snapshot = client.get(f"/mvp/tasks/{task_id}").json()
    assert snapshot["imported_item_count"] == 1
    assert len(snapshot["recommended_notes"]) == 1
    assert snapshot["recommended_notes"][0]["query_coverage_count"] == 2
    assert "覆盖 2 个搜索词" in snapshot["recommended_notes"][0]["score_reason"]


def test_mvp_logging_emits_capture_events(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "mvp.log"
    monkeypatch.setenv("XHS_EXTENSION_MVP_LOG_PATH", str(log_path))
    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    client = TestClient(app)

    create_response = client.post("/mvp/tasks", json={"topic": "敏感肌修护"})
    create_payload = create_response.json()
    task_id = create_payload["task_id"]
    token = create_payload["token"]

    capture_response = client.post(
        "/mvp/captures",
        json={
            "token": token,
            "page_type": "search_result",
            "query_text": "敏感肌修护",
            "items": [
                {
                    "source_url": "https://www.xiaohongshu.com/explore/log123",
                    "raw_href": "/explore/log123?xsec_token=log-token&xsec_source=pc_search",
                    "xsec_token": "log-token",
                    "xsec_source": "pc_search",
                    "debug_url_source": "test_payload",
                    "page_type": "search_result",
                    "query_text": "敏感肌修护",
                    "note_id": "log123",
                    "title": "敏感肌修护推荐",
                    "author": "logger",
                    "visible_text_excerpt": "换季修护思路",
                    "tags": ["敏感肌", "修护"],
                    "likes": 88,
                    "comments": 11,
                    "collections": 22,
                    "cover_image_url": ""
                }
            ]
        },
    )

    assert capture_response.status_code == 200
    contents = Path(log_path).read_text(encoding="utf-8")
    assert '"message": "Created MVP task"' in contents
    assert '"message": "Generated capture token"' in contents
    assert '"message": "Ingested capture payload"' in contents
    assert '"message": "Rebuilt deterministic candidates"' in contents
