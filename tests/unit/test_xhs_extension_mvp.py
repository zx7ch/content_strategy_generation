from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from experiments.xhs_extension_mvp.server.app import create_app
from experiments.xhs_extension_mvp.server.candidate_builder import NormalizedItem, build_candidates
from experiments.xhs_extension_mvp.server.llm_client import MVPLLMConfigError
from experiments.xhs_extension_mvp.server.llm_note_recommender import (
    LLMRecommendedNoteAnalyzer,
    RecommendedNotesAnalysis,
    LLMRecommendedNotesFailure,
)
from experiments.xhs_extension_mvp.server.llm_query_expander import LLMExpansionFailure, LLMQueryExpander
from experiments.xhs_extension_mvp.server.models import RecommendedNote, RecommendedNotesDiagnostics
from experiments.xhs_extension_mvp.server.query_expander import expand_topic
from experiments.xhs_extension_mvp.server.storage import MVPStorage
from app.services.xhs_spider import XHSNoteDetail, XHSUserProfile


class FakeLLMExpander:
    def __init__(self, expansions=None, error=None):
        self._expansions = expansions or []
        self._error = error

    def expand_topic(self, topic: str):
        if self._error:
            raise self._error
        return self._expansions


class FakeLLMClient:
    def __init__(self, response=None, error=None):
        self._response = response or ""
        self._error = error
        self.calls = []

    def chat(self, **_kwargs):
        self.calls.append(_kwargs)
        if self._error:
            raise self._error
        return self._response


class FakeRecommendedNoteAnalyzer:
    def __init__(self, notes=None, error=None, call_counter=None):
        self._notes = notes or []
        self._error = error
        self._call_counter = call_counter

    def analyze(self, topic: str, items, **_kwargs):
        if self._call_counter is not None:
            self._call_counter["count"] = self._call_counter.get("count", 0) + 1
        if self._error:
            raise self._error
        return RecommendedNotesAnalysis(
            notes=self._notes,
            diagnostics=RecommendedNotesDiagnostics(
                total_note_count=len(items),
                hard_filter_pass_count=len(items),
                llm_recommended_count=len(self._notes),
                analysis_source="llm",
            ),
        )


class FakeSpiderClient:
    def __init__(self, details=None, users=None):
        self.details = details or {}
        self.users = users or {}

    def fetch_note_detail(self, note_url: str):
        value = self.details.get(note_url)
        if isinstance(value, Exception):
            raise value
        return value

    def fetch_user_profile(self, user_id: str):
        value = self.users.get(user_id)
        if isinstance(value, Exception):
            raise value
        return value


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


def test_create_task_uses_llm_expansion_when_available(tmp_path, monkeypatch) -> None:
    expansions = [
        type("Expansion", (), {"category": "core", "query_text": "轻量徒步防晒衣"}),
        type("Expansion", (), {"category": "crowd", "query_text": "小个子轻量徒步防晒衣"}),
        type("Expansion", (), {"category": "scenario", "query_text": "夏天轻量徒步防晒衣"}),
        type("Expansion", (), {"category": "problem", "query_text": "轻量徒步防晒衣怎么选"}),
        type("Expansion", (), {"category": "compare", "query_text": "轻量徒步防晒衣对比"}),
        type("Expansion", (), {"category": "decision", "query_text": "轻量徒步防晒衣避坑"}),
    ]
    monkeypatch.setattr(
        "experiments.xhs_extension_mvp.server.storage.LLMQueryExpander",
        lambda: FakeLLMExpander(expansions=expansions),
    )
    monkeypatch.setattr(
        "experiments.xhs_extension_mvp.server.storage.load_llm_config",
        lambda: type("Config", (), {"model": "gpt-test", "base_url": ""})(),
    )

    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    client = TestClient(app)

    response = client.post("/mvp/tasks", json={"topic": "轻量徒步防晒衣"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["query_generation_source"] == "llm"
    assert payload["query_generation_notice"] is None
    assert [item["category"] for item in payload["expanded_queries"]] == [
        "core", "crowd", "scenario", "problem", "compare", "decision"
    ]
    assert payload["expanded_queries"][1]["query_text"] == "小个子轻量徒步防晒衣"

    snapshot = client.get(f"/mvp/tasks/{payload['task_id']}").json()
    assert snapshot["query_generation_source"] == "llm"
    assert snapshot["query_generation_notice"] is None


def test_create_task_falls_back_when_openai_api_key_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "experiments.xhs_extension_mvp.server.storage.load_llm_config",
        lambda: (_ for _ in ()).throw(MVPLLMConfigError("OPENAI_API_KEY is not configured")),
    )

    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    client = TestClient(app)

    response = client.post("/mvp/tasks", json={"topic": "敏感肌护肤"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["query_generation_source"] == "fallback_rule"
    assert payload["query_generation_notice"] == "AI 拓展词未启用，已使用规则生成。"
    assert any(item["category"] == "scenario" for item in payload["expanded_queries"])


def test_create_task_falls_back_when_llm_returns_invalid_payload(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "experiments.xhs_extension_mvp.server.storage.load_llm_config",
        lambda: type("Config", (), {"model": "gpt-test", "base_url": ""})(),
    )
    monkeypatch.setattr(
        "experiments.xhs_extension_mvp.server.storage.LLMQueryExpander",
        lambda: FakeLLMExpander(error=LLMExpansionFailure("invalid_json", "no JSON object found in LLM response")),
    )

    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    client = TestClient(app)

    response = client.post("/mvp/tasks", json={"topic": "租房收纳"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["query_generation_source"] == "fallback_rule"
    assert payload["query_generation_notice"] == "AI 拓展词暂时不可用，已自动降级为规则生成。"
    assert len(payload["expanded_queries"]) >= 5


def test_create_task_falls_back_when_llm_request_fails_and_logs_reason(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "mvp.log"
    monkeypatch.setenv("XHS_EXTENSION_MVP_LOG_PATH", str(log_path))
    monkeypatch.setattr(
        "experiments.xhs_extension_mvp.server.storage.load_llm_config",
        lambda: type("Config", (), {"model": "gpt-test", "base_url": ""})(),
    )
    monkeypatch.setattr(
        "experiments.xhs_extension_mvp.server.storage.LLMQueryExpander",
        lambda: FakeLLMExpander(error=LLMExpansionFailure("request_failed", "upstream 500")),
    )

    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    client = TestClient(app)

    response = client.post("/mvp/tasks", json={"topic": "通勤穿搭"})

    assert response.status_code == 200
    assert response.json()["query_generation_source"] == "fallback_rule"
    log_contents = Path(log_path).read_text(encoding="utf-8")
    assert '"event_name": "mvp_query_expansion_llm_failed"' in log_contents
    assert '"failure_stage": "request_failed"' in log_contents
    assert '"provider": "openai_compatible"' in log_contents
    assert '"event_name": "mvp_query_expansion_fallback_used"' in log_contents


def test_llm_query_expander_accepts_wrapped_json_response() -> None:
    expander = LLMQueryExpander(
        llm_client=FakeLLMClient(
            response="""
            下面是结果：
            {"queries":[
              {"category":"core","query_text":"敏感肌护肤"},
              {"category":"crowd","query_text":"油皮敏感肌护肤"},
              {"category":"scenario","query_text":"换季敏感肌护肤"},
              {"category":"problem","query_text":"敏感肌护肤怎么选"},
              {"category":"compare","query_text":"敏感肌护肤对比"},
              {"category":"decision","query_text":"敏感肌护肤避坑"}
            ]}
            """
        )
    )

    expansions = expander.expand_topic("敏感肌护肤")

    assert [item.category for item in expansions] == ["core", "crowd", "scenario", "problem", "compare", "decision"]
    assert expansions[1].query_text == "油皮敏感肌护肤"


def test_llm_query_expander_rejects_incomplete_or_invalid_categories() -> None:
    expander = LLMQueryExpander(
        llm_client=FakeLLMClient(
            response="""
            {"queries":[
              {"category":"core","query_text":"租房收纳"},
              {"category":"crowd","query_text":"新手租房收纳"},
              {"category":"invalid","query_text":"乱写一条"}
            ]}
            """
        )
    )

    try:
        expander.expand_topic("租房收纳")
    except LLMExpansionFailure as exc:
        assert exc.stage == "sanitize_empty"
    else:
        raise AssertionError("Expected incomplete LLM result to fail sanitization")


def test_llm_recommended_note_analyzer_filters_hard_sell_notes_from_prompt() -> None:
    client = FakeLLMClient(
        response="""
        {"results":[
          {
            "note_id":"note-real",
            "excluded":false,
            "score":89,
            "worth_checking_reason":"这条内容把具体使用场景和选购判断讲清楚了，适合品牌团队拆解人群切口与表达顺序。",
            "score_reason":"人群与决策需求表达清楚，适合复盘标题和结构。"
          }
        ]}
        """
    )
    analyzer = LLMRecommendedNoteAnalyzer(
        llm_client=client,
        spider_client=FakeSpiderClient(
            details={
                "https://example.com/ad": XHSNoteDetail(
                    note_id="note-ad",
                    note_url="https://example.com/ad",
                    user_id="user-ad",
                    title="官方旗舰店到手价直播间专拍",
                    desc="直播间专拍更便宜",
                    tags=["防晒衣"],
                    upload_time="2026-05-06 09:00:00",
                ),
                "https://example.com/real": XHSNoteDetail(
                    note_id="note-real",
                    note_url="https://example.com/real",
                    user_id="user-real",
                    title="小个子徒步防晒衣怎么选",
                    desc="把重量、透气和收纳体积都做了实际对比。",
                    tags=["徒步", "防晒衣"],
                    upload_time="2026-05-06 08:00:00",
                ),
            },
            users={
                "user-ad": XHSUserProfile(user_id="user-ad", fans=3000),
                "user-real": XHSUserProfile(user_id="user-real", fans=1800),
            },
        ),
    )

    analysis = analyzer.analyze(
        "轻量徒步防晒衣",
        [
            NormalizedItem(
                note_id="note-ad",
                title="官方旗舰店到手价直播间专拍",
                author="某某旗舰店",
                source_url="https://example.com/ad",
                raw_href="",
                xsec_token="",
                xsec_source="",
                debug_url_source="",
                query_text="轻量徒步防晒衣",
                excerpt="直播间专拍更便宜",
                tags=["防晒衣"],
                likes=10,
                comments=1,
                collections=0,
                item_id="item-ad",
            ),
            NormalizedItem(
                note_id="note-real",
                title="小个子徒步防晒衣怎么选",
                author="普通用户A",
                source_url="https://example.com/real",
                raw_href="",
                xsec_token="",
                xsec_source="",
                debug_url_source="",
                query_text="轻量徒步防晒衣",
                excerpt="把重量、透气和收纳体积都做了实际对比。",
                tags=["徒步", "防晒衣"],
                likes=88,
                comments=12,
                collections=23,
                item_id="item-real",
            ),
        ],
        query_hits_by_item_id={"item-real": {"轻量徒步防晒衣"}},
    )

    assert len(analysis.notes) == 1
    assert analysis.notes[0].note_id == "note-real"
    assert analysis.diagnostics.hard_filter_pass_count == 1
    assert "官方旗舰店到手价直播间专拍" not in client.calls[0]["user"]


def test_llm_recommended_note_analyzer_rejects_made_up_heat_metrics() -> None:
    analyzer = LLMRecommendedNoteAnalyzer(
        llm_client=FakeLLMClient(
            response="""
            {"results":[
              {
                "note_id":"note-real",
                "excluded":false,
                "score":92,
                "worth_checking_reason":"这是近3天新晋热榜内容，曝光和粉丝转化都很强。",
                "score_reason":"近3天热榜表现突出。"
              }
            ]}
            """
        ),
        spider_client=FakeSpiderClient(
            details={
                "https://example.com/real": XHSNoteDetail(
                    note_id="note-real",
                    note_url="https://example.com/real",
                    user_id="user-real",
                    title="敏感肌修护避坑",
                    desc="把成分、肤感和泛红风险都做了总结。",
                    tags=["敏感肌", "修护"],
                    upload_time="2026-05-06 09:30:00",
                )
            },
            users={"user-real": XHSUserProfile(user_id="user-real", fans=2300)},
        ),
    )

    try:
        analyzer.analyze(
            "敏感肌修护",
            [
                NormalizedItem(
                    note_id="note-real",
                    title="敏感肌修护避坑",
                    author="普通用户B",
                    source_url="https://example.com/real",
                    raw_href="",
                    xsec_token="",
                    xsec_source="",
                    debug_url_source="",
                    query_text="敏感肌修护",
                    excerpt="把成分、肤感和泛红风险都做了总结。",
                    tags=["敏感肌", "修护"],
                    likes=120,
                    comments=15,
                    collections=30,
                    item_id="item-real",
                )
            ],
            query_hits_by_item_id={"item-real": {"敏感肌修护"}},
        )
    except LLMRecommendedNotesFailure as exc:
        assert exc.stage == "sanitize_empty"
    else:
        raise AssertionError("Expected invented heat metrics to be rejected")


def test_llm_recommended_note_analyzer_filters_stale_and_head_accounts_before_prompt() -> None:
    client = FakeLLMClient(
        response="""
        {"results":[
          {
            "note_id":"note-keep",
            "excluded":false,
            "score":87,
            "worth_checking_reason":"这条内容把真实使用场景和决策顺序讲清楚了，适合复盘品牌内容里的人群切口和表达方式。",
            "score_reason":"场景与决策信息都比较完整。"
          }
        ]}
        """
    )
    analyzer = LLMRecommendedNoteAnalyzer(
        llm_client=client,
        spider_client=FakeSpiderClient(
            details={
                "https://example.com/old": XHSNoteDetail(
                    note_id="note-old",
                    note_url="https://example.com/old",
                    user_id="user-old",
                    title="旧爆款",
                    desc="一条老内容",
                    tags=["通勤"],
                    upload_time="2026-03-20 08:00:00",
                ),
                "https://example.com/head": XHSNoteDetail(
                    note_id="note-head",
                    note_url="https://example.com/head",
                    user_id="user-head",
                    title="头部博主爆款",
                    desc="大号日常分享",
                    tags=["通勤"],
                    upload_time="2026-05-06 08:00:00",
                ),
                "https://example.com/keep": XHSNoteDetail(
                    note_id="note-keep",
                    note_url="https://example.com/keep",
                    user_id="user-keep",
                    title="上班族通勤穿搭怎么搭",
                    desc="从一周搭配公式和通勤场景拆内容结构。",
                    tags=["通勤", "穿搭"],
                    upload_time="2026-05-06 07:30:00",
                ),
            },
            users={
                "user-old": XHSUserProfile(user_id="user-old", fans=2500),
                "user-head": XHSUserProfile(user_id="user-head", fans=2_500_000),
                "user-keep": XHSUserProfile(user_id="user-keep", fans=6800),
            },
        ),
    )

    analysis = analyzer.analyze(
        "通勤穿搭",
        [
            NormalizedItem(
                note_id="note-old",
                title="旧爆款",
                author="author-old",
                source_url="https://example.com/old",
                raw_href="",
                xsec_token="",
                xsec_source="",
                debug_url_source="",
                query_text="通勤穿搭",
                excerpt="一条老内容",
                tags=["通勤"],
                likes=500,
                comments=30,
                collections=100,
                item_id="item-old",
            ),
            NormalizedItem(
                note_id="note-head",
                title="头部博主爆款",
                author="author-head",
                source_url="https://example.com/head",
                raw_href="",
                xsec_token="",
                xsec_source="",
                debug_url_source="",
                query_text="通勤穿搭",
                excerpt="大号日常分享",
                tags=["通勤"],
                likes=900,
                comments=60,
                collections=200,
                item_id="item-head",
            ),
            NormalizedItem(
                note_id="note-keep",
                title="上班族通勤穿搭怎么搭",
                author="author-keep",
                source_url="https://example.com/keep",
                raw_href="",
                xsec_token="",
                xsec_source="",
                debug_url_source="",
                query_text="通勤穿搭",
                excerpt="从一周搭配公式和通勤场景拆内容结构。",
                tags=["通勤", "穿搭"],
                likes=150,
                comments=18,
                collections=40,
                item_id="item-keep",
            ),
        ],
        query_hits_by_item_id={"item-keep": {"通勤穿搭"}},
    )

    assert len(analysis.notes) == 1
    assert analysis.notes[0].note_id == "note-keep"
    assert analysis.diagnostics.hard_filter_pass_count == 1
    payload = client.calls[0]["user"]
    assert "note-old" not in payload
    assert "note-head" not in payload


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
    task_id, _, _, _ = storage.create_task("通勤穿搭")

    token, _ = storage.create_capture_token(task_id, ttl_seconds=1)
    assert storage.validate_capture_token(token) == task_id

    expired_token, _ = storage.create_capture_token(task_id, ttl_seconds=-1)
    try:
        storage.validate_capture_token(expired_token)
    except Exception as exc:  # noqa: BLE001
        assert "expired" in str(exc).lower()
    else:
        raise AssertionError("Expected expired capture token to be rejected")


def test_create_task_sets_runtime_token_without_exposing_it_to_workbench(tmp_path) -> None:
    db_path = tmp_path / "mvp.db"
    app = create_app(database_path=db_path, secret="secret")
    client = TestClient(app)

    response = client.post("/mvp/tasks", json={"topic": "轻量化穿搭"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"]
    assert "token" not in payload
    assert "expires_at" not in payload

    active = client.get("/api/extension/active-task").json()["active_task"]
    assert active["task_id"] == payload["task_id"]
    assert active["capture_token"]

    storage = MVPStorage(db_path, secret="secret")
    assert storage.validate_capture_token(active["capture_token"]) == payload["task_id"]


def test_create_task_sets_active_task_for_extension_runtime(tmp_path) -> None:
    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    client = TestClient(app)

    before_create = client.get("/api/extension/active-task")
    assert before_create.status_code == 200
    assert before_create.json()["active_task"] is None
    assert before_create.json()["error_summary"]["code"] == "no_active_task"

    create_response = client.post("/mvp/tasks", json={"topic": "轻量徒步防晒衣"})
    assert create_response.status_code == 200
    created = create_response.json()

    active_response = client.get("/api/extension/active-task")
    assert active_response.status_code == 200
    active_payload = active_response.json()
    active_task = active_payload["active_task"]
    assert active_task["task_id"] == created["task_id"]
    assert active_task["capture_token"]
    assert active_task["topic"] == "轻量徒步防晒衣"
    assert active_task["status"] == "active"
    assert active_task["snapshot_version"] == 0
    assert active_task["capture_count"] == 0
    assert active_task["candidate_count"] == 0
    assert active_payload["error_summary"] is None

    health_response = client.get("/api/extension/health")
    assert health_response.status_code == 200
    assert health_response.json()["status"] == "ok"
    assert health_response.json()["active_task_available"] is True


def test_activate_task_switches_single_active_task_and_rejects_missing_task(tmp_path) -> None:
    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    client = TestClient(app)

    first = client.post("/mvp/tasks", json={"topic": "敏感肌修护"}).json()
    second = client.post("/mvp/tasks", json={"topic": "通勤穿搭"}).json()

    activate_first = client.post(f"/api/tasks/{first['task_id']}/activate")
    assert activate_first.status_code == 200
    assert activate_first.json()["active_task"]["task_id"] == first["task_id"]

    activate_second = client.post(f"/api/tasks/{second['task_id']}/activate")
    assert activate_second.status_code == 200
    assert activate_second.json()["active_task"]["task_id"] == second["task_id"]

    active = client.get("/api/extension/active-task").json()["active_task"]
    assert active["task_id"] == second["task_id"]

    missing = client.post("/api/tasks/missing-task/activate")
    assert missing.status_code == 404


def test_active_search_context_records_query_and_activates_task(tmp_path) -> None:
    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    client = TestClient(app)

    first = client.post("/mvp/tasks", json={"topic": "敏感肌修护"}).json()
    second = client.post("/mvp/tasks", json={"topic": "通勤穿搭"}).json()

    response = client.post(
        f"/api/tasks/{first['task_id']}/active-search-context",
        json={
            "query": "敏感肌修护避坑",
            "source": "expanded_query",
            "opened_at": "2026-05-05T10:04:00+08:00",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_task"]["task_id"] == first["task_id"]
    assert payload["active_task"]["task_id"] != second["task_id"]
    assert payload["active_search_context"] == {
        "task_id": first["task_id"],
        "query": "敏感肌修护避坑",
        "source": "expanded_query",
        "opened_at": "2026-05-05T10:04:00+08:00",
    }


def test_extension_capture_uses_header_token_and_is_idempotent(tmp_path) -> None:
    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    client = TestClient(app)

    created = client.post("/mvp/tasks", json={"topic": "租房收纳"}).json()
    task_id = created["task_id"]
    token = client.get("/api/extension/active-task").json()["active_task"]["capture_token"]
    payload = {
        "task_id": task_id,
        "request_id": "req-test-1",
        "tab_id": 7,
        "page_url": "https://www.xiaohongshu.com/search_result?keyword=租房收纳",
        "page_type": "search_result",
        "query_text": "租房收纳",
        "visible_items": [
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
                "cover_image_url": "",
            }
        ],
    }

    missing_token = client.post("/api/extension/capture", json=payload)
    assert missing_token.status_code == 401

    response = client.post("/api/extension/capture", json=payload, headers={"X-Capture-Token": token})
    assert response.status_code == 200
    result = response.json()
    assert result["task_id"] == task_id
    assert result["request_id"] == "req-test-1"
    assert result["snapshot_version"] == 1
    assert result["captured_count"] == 1
    assert result["new_count"] == 1
    assert result["duplicate_count"] == 0
    assert result["status"] == "accepted"

    retry = client.post("/api/extension/capture", json=payload, headers={"X-Capture-Token": token})
    assert retry.status_code == 200
    assert retry.json() == result

    active_task = client.get("/api/extension/active-task").json()["active_task"]
    assert active_task["snapshot_version"] == 1
    assert active_task["capture_count"] == 1
    assert active_task["candidate_count"] >= 1

    lightweight = client.get(f"/api/tasks/{task_id}/snapshot")
    assert lightweight.status_code == 200
    lightweight_payload = lightweight.json()
    assert lightweight_payload["task_id"] == task_id
    assert lightweight_payload["snapshot_version"] == 1
    assert lightweight_payload["capture_count"] == 1
    assert lightweight_payload["candidate_count"] >= 1
    assert lightweight_payload["updated_at"]

    directions = client.get(f"/api/tasks/{task_id}/candidate-directions")
    assert directions.status_code == 200
    directions_payload = directions.json()
    assert directions_payload["task_id"] == task_id
    assert directions_payload["snapshot_version"] == 1
    assert directions_payload["capture_count"] == 1
    assert directions_payload["candidate_count"] >= 1
    assert directions_payload["updated_at"]
    assert len(directions_payload["candidates"]) >= 1


def test_phase6_acceptance_flow_create_capture_snapshot_and_refresh(tmp_path) -> None:
    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    client = TestClient(app)

    created = client.post("/mvp/tasks", json={"topic": "轻量徒步防晒衣"}).json()
    task_id = created["task_id"]

    assert "token" not in created
    assert "expires_at" not in created

    active_payload = client.get("/api/extension/active-task").json()
    active_task = active_payload["active_task"]
    assert active_task["task_id"] == task_id
    assert active_task["topic"] == "轻量徒步防晒衣"
    assert active_task["capture_token"]
    assert active_payload["error_summary"] is None

    capture_payload = {
        "task_id": task_id,
        "request_id": "phase6-acceptance-1",
        "tab_id": 42,
        "page_url": "https://www.xiaohongshu.com/search_result?keyword=轻量徒步防晒衣",
        "page_type": "search_result",
        "query_text": "轻量徒步防晒衣",
        "visible_items": [
            {
                "source_url": "https://www.xiaohongshu.com/explore/phase6-a",
                "raw_href": "/explore/phase6-a?xsec_token=phase6-token-a&xsec_source=pc_search",
                "xsec_token": "phase6-token-a",
                "xsec_source": "pc_search",
                "debug_url_source": "test_payload",
                "page_type": "search_result",
                "query_text": "轻量徒步防晒衣",
                "note_id": "phase6-a",
                "title": "轻量徒步防晒衣怎么选",
                "author": "tester-a",
                "visible_text_excerpt": "看透气、防晒指数和收纳体积。",
                "tags": ["徒步", "防晒衣"],
                "likes": 120,
                "comments": 9,
                "collections": 34,
                "cover_image_url": "",
            },
            {
                "source_url": "https://www.xiaohongshu.com/explore/phase6-b",
                "raw_href": "/explore/phase6-b?xsec_token=phase6-token-b&xsec_source=pc_search",
                "xsec_token": "phase6-token-b",
                "xsec_source": "pc_search",
                "debug_url_source": "test_payload",
                "page_type": "search_result",
                "query_text": "轻量徒步防晒衣",
                "note_id": "phase6-b",
                "title": "户外防晒衣轻量收纳实测",
                "author": "tester-b",
                "visible_text_excerpt": "小个子徒步也能轻松收纳。",
                "tags": ["户外", "收纳"],
                "likes": 86,
                "comments": 5,
                "collections": 21,
                "cover_image_url": "",
            },
        ],
    }

    capture = client.post(
        "/api/extension/capture",
        headers={"X-Capture-Token": active_task["capture_token"]},
        json=capture_payload,
    )
    assert capture.status_code == 200
    capture_result = capture.json()
    assert capture_result["status"] == "accepted"
    assert capture_result["snapshot_version"] == 1
    assert capture_result["captured_count"] == 2
    assert capture_result["new_count"] == 2
    assert capture_result["duplicate_count"] == 0

    deterministic_retry = client.post(
        "/api/extension/capture",
        headers={"X-Capture-Token": active_task["capture_token"]},
        json=capture_payload,
    )
    assert deterministic_retry.status_code == 200
    assert deterministic_retry.json() == capture_result

    snapshot = client.get(f"/api/tasks/{task_id}/snapshot").json()
    assert snapshot["snapshot_version"] == 1
    assert snapshot["capture_count"] == 1
    assert snapshot["candidate_count"] >= 1
    assert snapshot["updated_at"]

    refreshed = client.get(f"/api/tasks/{task_id}/candidate-directions").json()
    assert refreshed["task_id"] == task_id
    assert refreshed["snapshot_version"] == snapshot["snapshot_version"]
    assert refreshed["capture_count"] == snapshot["capture_count"]
    assert refreshed["candidate_count"] == snapshot["candidate_count"]
    assert refreshed["candidates"]
    assert refreshed["recommended_notes"]


def test_extension_runtime_files_wire_active_task_background() -> None:
    root = Path("experiments/xhs_extension_mvp/extension")
    manifest = json.loads((root / "manifest.json").read_text())
    background = (root / "src/background.js").read_text()
    popup = (root / "src/popup.js").read_text()
    popup_html = (root / "src/popup.html").read_text()
    content = (root / "src/content.js").read_text()

    assert manifest["background"]["service_worker"] == "src/background.js"
    assert "https://*.xiaohongshu.com/*" in manifest["host_permissions"]
    assert "ACTIVE_TASK_REQUEST" in background
    assert "ACTIVE_TASK_RESYNC" in background
    assert "CAPTURE_VISIBLE_PAGE" in background
    assert "X-Capture-Token" in background
    assert "ACTIVE_TASK_RESYNC" in popup
    assert "CAPTURE_VISIBLE_PAGE" not in popup
    assert "capture-token" not in popup_html
    assert "ACTIVE_TASK_REQUEST" in content


def test_popup_is_status_panel_with_advanced_connection_settings() -> None:
    root = Path("experiments/xhs_extension_mvp/extension")
    popup_html = (root / "src/popup.html").read_text()
    popup = (root / "src/popup.js").read_text()
    background = (root / "src/background.js").read_text()

    assert "Runtime Status" in popup_html
    assert "Advanced connection settings" in popup_html
    assert popup_html.index("Advanced connection settings") < popup_html.index("server-url")
    assert "capture-token" not in popup_html.lower()
    assert "token" not in popup_html.lower()
    assert 'id="page-state"' in popup_html
    assert 'id="visible-count"' in popup_html
    assert 'id="open-workbench-button"' in popup_html
    assert "Fallback Capture Current Page" not in popup_html

    assert "PAGE_STATUS_REQUEST" in background
    assert "PAGE_STATUS_REQUEST" in popup
    assert "openWorkbench" in popup
    assert "No active task detected. Please open the workbench and create a task first." in popup
    assert "Not connected to local service. Please start the MVP server first" in popup
    assert "This page is not supported for capture" in popup
    assert "fallback capture" not in popup.lower()
    assert "fallback capture" not in background.lower()


def test_content_script_injects_page_capture_panel() -> None:
    content = Path("experiments/xhs_extension_mvp/extension/src/content.js").read_text()

    assert 'const PANEL_ID = "xhs-mvp-capture-panel"' in content
    assert "__XHS_MVP_CAPTURE_PANEL_BOOTED__" in content
    assert "xhs-mvp-capture-panel:mount" in content
    assert "bootCapturePanel();" in content
    assert "mountCapturePanelWhenReady" in content
    assert "startPanelPersistenceObserver" in content
    assert "new MutationObserver" in content
    assert "PING_CAPTURE_PANEL" in content
    assert "MOUNT_CAPTURE_PANEL" in content
    assert "2147483647" in content
    assert "XHS 采集助手" in content
    assert "当前页可见笔记" in content
    assert "采集当前页" in content
    assert "CAPTURE_VISIBLE_PAGE" in content
    assert "captureInFlight" in content
    assert "pendingCaptureVisibleIds" in content
    assert 'payload: beforeCapture.payload' in content
    assert "本次提交中" in content
    assert "你可以继续滚动，计数会继续更新" in content
    assert "正在同步任务并提交本次可见" in content
    assert "lastCapturedVisibleIds" in content
    assert "window.setInterval(refreshVisibleCount, 1500)" in content
    assert "getDisplayItems" in content
    assert 'debug_url_source !== "search_page_context"' in content
    assert "is-collapsed" in content
    assert "已采集 ${result.captured_count} 条，新增 ${result.new_count} 条，重复 ${result.duplicate_count} 条。" in content
    assert 'id="xhs-mvp-retry-btn"' in content
    assert 'id="xhs-mvp-open-workbench-btn"' not in content
    assert "OPEN_WORKBENCH" not in content
    assert "打开工作台" not in content
    assert "未连接本地服务" in content
    assert "未检测到任务" in content
    assert "当前页面不支持采集" in content
    assert content.index("mountCapturePanelWhenReady();") < content.index("initializeActiveTaskContext();")
    assert "button.disabled = Boolean(disabled || captureInFlight)" in content
    assert "button.disabled = Boolean(disabled || !activeTaskContext" not in content


def test_extension_runtime_failure_handling_contracts() -> None:
    root = Path("experiments/xhs_extension_mvp/extension")
    background = (root / "src/background.js").read_text()
    popup = (root / "src/popup.js").read_text()
    content = (root / "src/content.js").read_text()

    for code in [
        "server_unavailable",
        "no_active_task",
        "unsupported_page",
        "content_script_unavailable",
        "capture_already_running",
        "capture_token_invalid",
        "capture_task_mismatch",
        "capture_submit_failed",
    ]:
        assert code in background

    assert "createRuntimeError" in background
    assert "error?.code" in background
    assert "extractPageWithRecovery" in background
    assert "message.payload" in background
    assert "suppliedPagePayload" in background
    assert "isMissingContentScriptError" in background
    assert "chrome.tabs.onUpdated.addListener" in background
    assert "chrome.tabs.onActivated.addListener" in background
    assert "PING_CAPTURE_PANEL" in background
    assert "MOUNT_CAPTURE_PANEL" in background
    assert "isSupportedXhsUrl" in background
    assert "getServerUrlCandidates" in background
    assert "http://localhost:8010" in background
    assert "uniqueServerUrls" in background
    assert background.count("extractPageWithRecovery(tabId)") >= 2
    assert "await ensureContentScript(tabId)" in background
    assert "if (response.status === 401)" in background
    assert "if (response.status === 403)" in background
    assert "runtimeState.activeCapturesByTab.has(tabId)" in background
    assert "runtimeState.activeCapturesByTab.delete(tabId)" in background
    assert 'case "OPEN_WORKBENCH"' not in background
    assert "Use fallback capture" not in background

    assert "unsupported_page" in popup
    assert "The extension tried to recover automatically" in popup
    assert "Use fallback capture" not in popup

    assert "重试连接/同步" in content
    assert "打开工作台" not in content
    assert "capture_token_invalid" in content
    assert "capture_task_mismatch" in content
    assert "capture_submit_failed" in content
    assert "页面采集助手暂未就绪" in content


def test_phase6_acceptance_ui_runtime_contract_is_new_flow_only() -> None:
    root = Path("experiments/xhs_extension_mvp")
    manifest = json.loads((root / "extension/manifest.json").read_text())
    background = (root / "extension/src/background.js").read_text()
    content = (root / "extension/src/content.js").read_text()
    popup_html = (root / "extension/src/popup.html").read_text()
    popup = (root / "extension/src/popup.js").read_text()
    web_html = (root / "web/index.html").read_text()
    web_app = (root / "web/app.js").read_text()
    server_app = (root / "server/app.py").read_text()
    readme = (root / "README.md").read_text()

    assert manifest["background"]["service_worker"] == "src/background.js"
    assert "https://*.xiaohongshu.com/*" in manifest["host_permissions"]
    assert manifest["content_scripts"][0]["matches"] == ["https://*.xiaohongshu.com/*"]
    assert manifest["content_scripts"][0]["js"] == ["src/content.js"]

    assert "/api/extension/capture" in background
    assert "X-Capture-Token" in background
    assert "ACTIVE_TASK_RESYNC" in background
    assert "CAPTURE_VISIBLE_PAGE" in background
    assert "OPEN_WORKBENCH" not in background
    assert "extractPageWithRecovery" in background
    assert "chrome.tabs.onUpdated.addListener" in background

    assert 'const PANEL_ID = "xhs-mvp-capture-panel"' in content
    assert "startPanelPersistenceObserver" in content
    assert "MOUNT_CAPTURE_PANEL" in content
    assert "采集当前页" in content
    assert "重试连接/同步" in content
    assert "打开工作台" not in content
    assert "CAPTURE_VISIBLE_PAGE" in content

    assert "Runtime Status" in popup_html
    assert "Capture from the Xiaohongshu page panel" in popup_html
    assert "CAPTURE_VISIBLE_PAGE" not in popup
    assert "capture-token" not in popup_html.lower()
    assert "Fallback Capture" not in popup_html

    assert "window.setInterval(pollTaskSnapshotVersion, 2000)" in web_app
    assert "fetch(`/api/tasks/${state.taskId}/snapshot`)" in web_app
    assert "fetch(`/api/tasks/${state.taskId}/candidate-directions`)" in web_app
    assert 'id="task-snapshot-status"' in web_html
    assert "先做硬性筛选，再结合 LLM 分析产出更值得优先查看的笔记" in web_html
    assert "refresh-task-button" not in web_html
    assert "capture-token-output" not in web_html
    assert "generate-token-button" not in web_html
    assert "手动刷新" not in web_app

    assert "/mvp/captures" not in server_app
    assert "/capture-token" not in server_app
    assert "Capture Token" not in readme
    assert "Fallback Capture" not in readme


def test_workbench_auto_refreshes_snapshot_versions() -> None:
    web_app = Path("experiments/xhs_extension_mvp/web/app.js").read_text()
    web_html = Path("experiments/xhs_extension_mvp/web/index.html").read_text()

    assert 'id="task-snapshot-status"' in web_html
    assert "创建任务后会自动监听采集结果变化" in web_html
    assert "snapshotVersion" in web_app
    assert "startTaskSnapshotPolling();" in web_app
    assert "window.setInterval(pollTaskSnapshotVersion, 2000)" in web_app
    assert "fetch(`/api/tasks/${state.taskId}/snapshot`)" in web_app
    assert "fetch(`/api/tasks/${state.taskId}/candidate-directions`)" in web_app
    assert "检测到新采集，候选方向已自动更新。" in web_app
    assert "已采集 ${formatCount(snapshot.capture_count)} 次，候选方向 ${formatCount(snapshot.candidate_count)} 个" in web_app
    assert "capture-token-output" not in web_html
    assert "generate-token-button" not in web_html
    assert "refresh-task-button" not in web_html
    assert "requestCaptureTokenWithRetry" not in web_app
    assert "navigator.clipboard" not in web_app


def test_legacy_manual_token_routes_and_ui_are_removed(tmp_path) -> None:
    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    client = TestClient(app)
    server_app = Path("experiments/xhs_extension_mvp/server/app.py").read_text()
    server_models = Path("experiments/xhs_extension_mvp/server/models.py").read_text()
    web_app = Path("experiments/xhs_extension_mvp/web/app.js").read_text()
    web_html = Path("experiments/xhs_extension_mvp/web/index.html").read_text()
    popup_html = Path("experiments/xhs_extension_mvp/extension/src/popup.html").read_text()

    assert client.post("/mvp/captures", json={}).status_code == 404
    assert client.post("/mvp/tasks/task-id/capture-token").status_code == 404
    assert "/mvp/captures" not in server_app
    assert "/capture-token" not in server_app
    assert "class CaptureTokenResponse" not in server_models
    assert "class CaptureAcceptedResponse" not in server_models
    assert "class CaptureRequest" not in server_models
    assert "capture-token-output" not in web_html
    assert "generate-token-button" not in web_html
    assert "refresh-task-button" not in web_html
    assert "requestCaptureTokenWithRetry" not in web_app
    assert "Fallback Capture Current Page" not in popup_html


def test_capture_endpoint_dedupes_by_note_id(tmp_path) -> None:
    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    client = TestClient(app)

    create_response = client.post("/mvp/tasks", json={"topic": "租房收纳"})
    assert create_response.status_code == 200
    create_payload = create_response.json()
    task_id = create_payload["task_id"]
    token = client.get("/api/extension/active-task").json()["active_task"]["capture_token"]

    payload = {
        "task_id": task_id,
        "request_id": "dedupe-1",
        "tab_id": 1,
        "page_url": "https://www.xiaohongshu.com/search_result?keyword=租房收纳",
        "page_type": "search_result",
        "query_text": "租房收纳",
        "visible_items": [
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

    capture_response = client.post("/api/extension/capture", json=payload, headers={"X-Capture-Token": token})
    assert capture_response.status_code == 200
    assert capture_response.json()["new_count"] == 1

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
    assert "recommended_notes_diagnostics" in snapshot
    assert snapshot["recommended_notes"][0]["score_reason"]
    assert snapshot["recommended_notes"][0]["why_recommended"]
    assert snapshot["recommended_notes"][0]["excerpt"]


def test_invalid_extension_capture_token_returns_401(tmp_path) -> None:
    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    client = TestClient(app)

    response = client.post(
        "/api/extension/capture",
        headers={"X-Capture-Token": "bad.token"},
        json={"task_id": "task", "request_id": "bad", "page_type": "search_result", "query_text": "", "visible_items": []},
    )

    assert response.status_code == 401


def test_capture_endpoint_supports_extension_cors_preflight(tmp_path) -> None:
    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    client = TestClient(app)

    response = client.options(
        "/api/extension/capture",
        headers={
            "Origin": "chrome-extension://test-extension-id",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-capture-token",
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
    token = client.get("/api/extension/active-task").json()["active_task"]["capture_token"]

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
        "/api/extension/capture",
        headers={"X-Capture-Token": token},
        json={
            "task_id": task_id,
            "request_id": "coverage-1",
            "tab_id": 1,
            "page_url": "https://www.xiaohongshu.com/search_result?keyword=敏感肌修护",
            "page_type": "search_result",
            "query_text": "敏感肌修护",
            "visible_items": [{**base_item, "query_text": "敏感肌修护"}],
        },
    )
    response_two = client.post(
        "/api/extension/capture",
        headers={"X-Capture-Token": token},
        json={
            "task_id": task_id,
            "request_id": "coverage-2",
            "tab_id": 1,
            "page_url": "https://www.xiaohongshu.com/search_result?keyword=敏感肌修护避坑",
            "page_type": "search_result",
            "query_text": "敏感肌修护避坑",
            "visible_items": [{**base_item, "query_text": "敏感肌修护避坑"}],
        },
    )

    assert response_one.status_code == 200
    assert response_two.status_code == 200
    snapshot = client.get(f"/mvp/tasks/{task_id}").json()
    assert snapshot["imported_item_count"] == 1
    assert len(snapshot["recommended_notes"]) == 1
    assert snapshot["recommended_notes"][0]["query_coverage_count"] == 2
    assert "覆盖 2 个搜索词" in snapshot["recommended_notes"][0]["score_reason"]


def test_snapshot_uses_cached_llm_recommended_notes_until_snapshot_changes(tmp_path, monkeypatch) -> None:
    call_counter = {"count": 0}
    monkeypatch.setattr(
        "experiments.xhs_extension_mvp.server.storage.LLMQueryExpander",
        lambda: FakeLLMExpander(
            expansions=[
                type("Expansion", (), {"category": "core", "query_text": "轻量徒步防晒衣"}),
                type("Expansion", (), {"category": "crowd", "query_text": "小个子轻量徒步防晒衣"}),
                type("Expansion", (), {"category": "scenario", "query_text": "夏天轻量徒步防晒衣"}),
                type("Expansion", (), {"category": "problem", "query_text": "轻量徒步防晒衣怎么选"}),
                type("Expansion", (), {"category": "compare", "query_text": "轻量徒步防晒衣对比"}),
                type("Expansion", (), {"category": "decision", "query_text": "轻量徒步防晒衣避坑"}),
            ]
        ),
    )
    monkeypatch.setattr(
        "experiments.xhs_extension_mvp.server.storage.load_llm_config",
        lambda: type("Config", (), {"model": "gpt-test", "base_url": ""})(),
    )
    monkeypatch.setattr(
        "experiments.xhs_extension_mvp.server.storage.LLMRecommendedNoteAnalyzer",
        lambda: FakeRecommendedNoteAnalyzer(
            notes=[
                RecommendedNote(
                    note_id="note123",
                    title="轻量徒步防晒衣避坑",
                    source_url="https://www.xiaohongshu.com/explore/note123",
                    author="tester",
                    excerpt="从重量、透气和收纳体积来拆决策点。",
                    score=91.0,
                    score_reason="人群与决策信号都比较集中。",
                    why_recommended="这条内容不是泛泛晒图，而是把轻量、防晒和徒步场景的决策依据拆清楚了，适合品牌团队复盘选题和表达顺序。",
                    likes=99,
                    comments=11,
                    collections=22,
                    query_coverage_count=1,
                )
            ],
            call_counter=call_counter,
        ),
    )

    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    client = TestClient(app)
    create_payload = client.post("/mvp/tasks", json={"topic": "轻量徒步防晒衣"}).json()
    task_id = create_payload["task_id"]
    token = client.get("/api/extension/active-task").json()["active_task"]["capture_token"]

    client.post(
        "/api/extension/capture",
        headers={"X-Capture-Token": token},
        json={
            "task_id": task_id,
            "request_id": "cache-1",
            "tab_id": 1,
            "page_url": "https://www.xiaohongshu.com/search_result?keyword=轻量徒步防晒衣",
            "page_type": "search_result",
            "query_text": "轻量徒步防晒衣",
            "visible_items": [
                {
                    "source_url": "https://www.xiaohongshu.com/explore/note123",
                    "raw_href": "/explore/note123?xsec_token=q1&xsec_source=pc_search",
                    "xsec_token": "q1",
                    "xsec_source": "pc_search",
                    "debug_url_source": "test_payload",
                    "page_type": "search_result",
                    "query_text": "轻量徒步防晒衣",
                    "note_id": "note123",
                    "title": "轻量徒步防晒衣避坑",
                    "author": "tester",
                    "visible_text_excerpt": "从重量、透气和收纳体积来拆决策点。",
                    "tags": ["徒步", "防晒衣"],
                    "likes": 99,
                    "comments": 11,
                    "collections": 22,
                    "cover_image_url": "",
                }
            ],
        },
    )

    first = client.get(f"/mvp/tasks/{task_id}").json()
    second = client.get(f"/mvp/tasks/{task_id}").json()

    assert first["recommended_notes"][0]["why_recommended"].startswith("这条内容不是泛泛晒图")
    assert second["recommended_notes"][0]["why_recommended"] == first["recommended_notes"][0]["why_recommended"]
    assert first["recommended_notes_diagnostics"]["hard_filter_pass_count"] == 1
    assert first["recommended_notes_diagnostics"]["llm_recommended_count"] == 1
    assert call_counter["count"] == 1


def test_snapshot_falls_back_when_llm_recommended_note_analysis_fails(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "mvp.log"
    monkeypatch.setenv("XHS_EXTENSION_MVP_LOG_PATH", str(log_path))
    monkeypatch.setattr(
        "experiments.xhs_extension_mvp.server.storage.LLMQueryExpander",
        lambda: FakeLLMExpander(
            expansions=[
                type("Expansion", (), {"category": "core", "query_text": "敏感肌修护"}),
                type("Expansion", (), {"category": "crowd", "query_text": "油皮敏感肌修护"}),
                type("Expansion", (), {"category": "scenario", "query_text": "换季敏感肌修护"}),
                type("Expansion", (), {"category": "problem", "query_text": "敏感肌修护怎么选"}),
                type("Expansion", (), {"category": "compare", "query_text": "敏感肌修护对比"}),
                type("Expansion", (), {"category": "decision", "query_text": "敏感肌修护避坑"}),
            ]
        ),
    )
    monkeypatch.setattr(
        "experiments.xhs_extension_mvp.server.storage.load_llm_config",
        lambda: type("Config", (), {"model": "gpt-test", "base_url": ""})(),
    )
    monkeypatch.setattr(
        "experiments.xhs_extension_mvp.server.storage.LLMRecommendedNoteAnalyzer",
        lambda: FakeRecommendedNoteAnalyzer(error=LLMRecommendedNotesFailure("request_failed", "upstream timeout")),
    )

    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    client = TestClient(app)
    create_payload = client.post("/mvp/tasks", json={"topic": "敏感肌修护"}).json()
    task_id = create_payload["task_id"]
    token = client.get("/api/extension/active-task").json()["active_task"]["capture_token"]

    client.post(
        "/api/extension/capture",
        headers={"X-Capture-Token": token},
        json={
            "task_id": task_id,
            "request_id": "fallback-1",
            "tab_id": 1,
            "page_url": "https://www.xiaohongshu.com/search_result?keyword=敏感肌修护",
            "page_type": "search_result",
            "query_text": "敏感肌修护",
            "visible_items": [
                {
                    "source_url": "https://www.xiaohongshu.com/explore/note123",
                    "raw_href": "/explore/note123?xsec_token=q1&xsec_source=pc_search",
                    "xsec_token": "q1",
                    "xsec_source": "pc_search",
                    "debug_url_source": "test_payload",
                    "page_type": "search_result",
                    "query_text": "敏感肌修护",
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
            ],
        },
    )

    snapshot = client.get(f"/mvp/tasks/{task_id}").json()

    assert snapshot["recommended_notes"]
    assert snapshot["recommended_notes"][0]["why_recommended"]
    assert snapshot["recommended_notes_diagnostics"]["analysis_source"] == "fallback_rule"
    log_contents = Path(log_path).read_text(encoding="utf-8")
    assert '"event_name": "mvp_recommended_notes_llm_failed"' in log_contents
    assert '"failure_stage": "request_failed"' in log_contents
    assert '"event_name": "mvp_recommended_notes_fallback_used"' in log_contents


def test_mvp_logging_emits_capture_events(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "mvp.log"
    monkeypatch.setenv("XHS_EXTENSION_MVP_LOG_PATH", str(log_path))
    app = create_app(database_path=tmp_path / "mvp.db", secret="secret")
    client = TestClient(app)

    create_response = client.post("/mvp/tasks", json={"topic": "敏感肌修护"})
    create_payload = create_response.json()
    task_id = create_payload["task_id"]
    token = client.get("/api/extension/active-task").json()["active_task"]["capture_token"]

    capture_response = client.post(
        "/api/extension/capture",
        headers={"X-Capture-Token": token},
        json={
            "task_id": task_id,
            "request_id": "log-1",
            "tab_id": 1,
            "page_url": "https://www.xiaohongshu.com/search_result?keyword=敏感肌修护",
            "page_type": "search_result",
            "query_text": "敏感肌修护",
            "visible_items": [
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
