"""Unit tests for P2-2 ContentStrategyAgent acceptance criteria."""

from __future__ import annotations

import json
import uuid
from datetime import datetime

import pytest

from app.agents.content_strategy_agent import ContentStrategyAgent
from app.memory.session_state import SessionManager
from app.services.rag_service import QualityScore
from app.services.xhs_spider import SpiderPermanentError, XHSPost


def _post(note_id: str, liked: int = 100, collected: int = 50) -> XHSPost:
    return XHSPost(
        note_id=note_id,
        title=f"title-{note_id}",
        content=f"content-{note_id}",
        author="author",
        tags=["tag-a", "tag-b"],
        liked_count=liked,
        collected_count=collected,
        comment_count=10,
        share_count=2,
        note_url=f"https://xhs.com/{note_id}",
        images=["img1"],
    )


class FakeSpider:
    def __init__(self, mapping=None, fail_queries=None):
        self.mapping = mapping or {}
        self.fail_queries = set(fail_queries or set())
        self.calls = []

    async def search_with_retry(self, query: str, num: int = 50):
        self.calls.append((query, num))
        if query in self.fail_queries:
            raise SpiderPermanentError("spider failed")
        return list(self.mapping.get(query, []))


class FakeRAG:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []
        self.chunk_calls = []

    def chunk_posts(self, posts):
        self.chunk_calls.append([p.note_id for p in posts])
        return []

    async def index_documents(self, session_id, posts, query):
        self.calls.append((session_id, [p.note_id for p in posts], query))
        if self.results:
            return self.results.pop(0)
        return QualityScore(score=0.0, total_notes=len(posts), filtered_count=len(posts), avg_similarity=0.0)


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    async def chat(self, system: str, user: str, max_tokens: int = 1024, temperature: float = 0.7):
        del system, user, max_tokens, temperature
        if not self.responses:
            return "{}"
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_execute_returns_session_not_found():
    agent = ContentStrategyAgent(
        session_manager=SessionManager(":memory:"),
        spider_client=FakeSpider(),
        rag_service=FakeRAG([]),
        llm_client=FakeLLM([]),
    )
    result = await agent.execute("missing-session")
    assert result.success is False
    assert result.error_code == "SESSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_execute_spider_failure_sets_cooldown_and_failed(tmp_path):
    db_path = tmp_path / "strategy-agent.db"
    session_id = str(uuid.uuid4())

    async with SessionManager(str(db_path)) as manager:
        await manager.create_session(session_id, "u1", "matcha")

    agent = ContentStrategyAgent(
        session_manager=SessionManager(str(db_path)),
        spider_client=FakeSpider(mapping={}, fail_queries={"matcha"}),
        rag_service=FakeRAG([]),
        llm_client=FakeLLM([]),
    )

    result = await agent.execute(session_id)
    assert result.success is False
    assert result.error_code == "SPIDER_SERVICE_UNAVAILABLE"

    async with SessionManager(str(db_path)) as manager:
        session = await manager.get_session(session_id)
        assert session is not None
        assert session.lifecycle_state.value == "alive"
        assert session.stage.value == "failed"
        assert session.spider_cooldown_until is not None
        assert session.spider_cooldown_until > datetime.utcnow()


@pytest.mark.asyncio
async def test_execute_triggers_query_expansion_when_low_quality_and_low_doc_count(tmp_path):
    db_path = tmp_path / "strategy-expand.db"
    session_id = str(uuid.uuid4())
    query = "抹茶拿铁"

    async with SessionManager(str(db_path)) as manager:
        await manager.create_session(session_id, "u1", query)

    spider = FakeSpider(
        mapping={
            query: [_post("n1"), _post("n2"), _post("n3"), _post("n4"), _post("n5")],
            "抹茶自制": [_post("n6"), _post("n7"), _post("n8"), _post("n9")],
        }
    )
    rag = FakeRAG(
        results=[
            QualityScore(score=0.20, total_notes=5, filtered_count=5, avg_similarity=0.2),
            QualityScore(score=0.30, total_notes=9, filtered_count=9, avg_similarity=0.3),
        ]
    )
    llm = FakeLLM(
        responses=[
            "抹茶自制\n抹茶避坑",
            json.dumps(
                {
                    "positioning": "泛生活",
                    "target_audience": "大众",
                    "content_pillars": ["经验"],
                    "key_messaging": "可执行",
                    "content_types": ["图文"],
                    "posting_strategy": "晚间",
                }
            ),
        ]
    )

    agent = ContentStrategyAgent(
        session_manager=SessionManager(str(db_path)),
        spider_client=spider,
        rag_service=rag,
        llm_client=llm,
    )

    result = await agent.execute(session_id)
    assert result.success is True
    assert result.used_fallback is True
    assert any(call[0] == "抹茶自制" for call in spider.calls)

    async with SessionManager(str(db_path)) as manager:
        session = await manager.get_session(session_id)
        assert session is not None
        assert session.expanded_queries == ["抹茶自制"]


@pytest.mark.asyncio
async def test_execute_stops_expansion_when_quality_gain_too_low(tmp_path):
    db_path = tmp_path / "strategy-stop-gain.db"
    session_id = str(uuid.uuid4())
    query = "咖啡"

    async with SessionManager(str(db_path)) as manager:
        await manager.create_session(session_id, "u1", query)

    spider = FakeSpider(
        mapping={
            query: [_post("a1"), _post("a2"), _post("a3"), _post("a4")],
            "咖啡教程": [_post("a5"), _post("a6"), _post("a7")],
            "咖啡清单": [_post("a8"), _post("a9"), _post("a10")],
        }
    )
    rag = FakeRAG(
        results=[
            QualityScore(score=0.20, total_notes=4, filtered_count=4, avg_similarity=0.2),
            QualityScore(score=0.22, total_notes=7, filtered_count=7, avg_similarity=0.22),
        ]
    )
    llm = FakeLLM(
        responses=[
            "咖啡教程\n咖啡清单",
            json.dumps(
                {
                    "positioning": "泛生活",
                    "target_audience": "大众",
                    "content_pillars": ["经验"],
                    "key_messaging": "可执行",
                    "content_types": ["图文"],
                    "posting_strategy": "晚间",
                }
            ),
        ]
    )

    agent = ContentStrategyAgent(
        session_manager=SessionManager(str(db_path)),
        spider_client=spider,
        rag_service=rag,
        llm_client=llm,
    )
    result = await agent.execute(session_id)

    assert result.success is True
    # stop condition on quality gain should stop before executing second expansion query
    expanded_calls = [call for call in spider.calls if call[0] != query]
    assert len(expanded_calls) == 1


@pytest.mark.asyncio
async def test_execute_uses_data_driven_strategy_when_quality_high(tmp_path):
    db_path = tmp_path / "strategy-high-quality.db"
    session_id = str(uuid.uuid4())
    query = "护肤"

    async with SessionManager(str(db_path)) as manager:
        await manager.create_session(session_id, "u1", query)

    spider = FakeSpider(mapping={query: [_post("b1"), _post("b2"), _post("b3"), _post("b4"), _post("b5"), _post("b6"), _post("b7"), _post("b8"), _post("b9"), _post("b10"), _post("b11")]})
    rag = FakeRAG(
        results=[QualityScore(score=0.72, total_notes=11, filtered_count=11, avg_similarity=0.7)]
    )
    llm = FakeLLM(
        responses=[
            json.dumps(
                {
                    "positioning": "专业护肤顾问",
                    "target_audience": "油敏肌用户",
                    "content_pillars": ["成分", "实测"],
                    "key_messaging": "温和有效",
                    "content_types": ["图文", "短视频"],
                    "posting_strategy": "20:00",
                }
            )
        ]
    )

    agent = ContentStrategyAgent(
        session_manager=SessionManager(str(db_path)),
        spider_client=spider,
        rag_service=rag,
        llm_client=llm,
    )
    result = await agent.execute(session_id)

    assert result.success is True
    assert result.used_fallback is False
    assert result.content_strategy is not None
    assert result.content_strategy.positioning == "专业护肤顾问"
    assert result.quality_score == pytest.approx(0.72)

    async with SessionManager(str(db_path)) as manager:
        session = await manager.get_session(session_id)
        assert session is not None
        assert session.spider_note_ids is not None
        assert len(session.spider_note_ids) >= 10
        assert session.strategy_id is not None


@pytest.mark.asyncio
async def test_execute_calls_chunk_and_index_with_session_and_query(tmp_path):
    db_path = tmp_path / "strategy-chunk-index.db"
    session_id = str(uuid.uuid4())
    query = "穿搭"

    async with SessionManager(str(db_path)) as manager:
        await manager.create_session(session_id, "u1", query)

    spider = FakeSpider(mapping={query: [_post("c1"), _post("c2"), _post("c3"), _post("c4"), _post("c5"), _post("c6"), _post("c7"), _post("c8"), _post("c9"), _post("c10"), _post("c11")]})
    rag = FakeRAG(
        results=[QualityScore(score=0.50, total_notes=11, filtered_count=11, avg_similarity=0.5)]
    )
    llm = FakeLLM(
        responses=[
            json.dumps(
                {
                    "positioning": "穿搭博主",
                    "target_audience": "年轻女性",
                    "content_pillars": ["通勤", "平价"],
                    "key_messaging": "可复制",
                    "content_types": ["图文"],
                    "posting_strategy": "19:00",
                }
            )
        ]
    )

    agent = ContentStrategyAgent(
        session_manager=SessionManager(str(db_path)),
        spider_client=spider,
        rag_service=rag,
        llm_client=llm,
    )

    result = await agent.execute(session_id)
    assert result.success is True
    assert rag.chunk_calls
    assert rag.calls[0][0] == session_id
    assert rag.calls[0][2] == query
