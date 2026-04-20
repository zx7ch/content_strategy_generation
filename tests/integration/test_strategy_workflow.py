"""Integration tests for StrategyAgent workflow (branch-complete for P2-2)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime

import pytest

from app.agents.content_strategy_agent import ContentStrategyAgent
from app.config import settings
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


def _strategy_json(positioning: str = "通用生活方式分享") -> str:
    return json.dumps(
        {
            "positioning": positioning,
            "target_audience": "大众",
            "content_pillars": ["经验"],
            "key_messaging": "实用优先",
            "content_types": ["图文"],
            "posting_strategy": "晚间",
        }
    )


class FakeSpider:
    def __init__(self, mapping=None, fail_queries=None):
        self.mapping = mapping or {}
        self.fail_queries = set(fail_queries or set())
        self.calls = []

    async def search_with_retry(self, query: str, num: int = 50):
        self.calls.append((query, num))
        if query in self.fail_queries:
            raise SpiderPermanentError(f"spider failed for {query}")
        if query not in self.mapping:
            raise SpiderPermanentError(f"query not found: {query}")
        return list(self.mapping[query])


class FakeRAG:
    def __init__(self, qualities):
        self.qualities = list(qualities)
        self.index_calls = []
        self.chunk_calls = []

    def chunk_posts(self, posts):
        self.chunk_calls.append([p.note_id for p in posts])
        return posts

    async def index_documents(self, session_id, posts, query):
        self.index_calls.append((session_id, [p.note_id for p in posts], query))
        if self.qualities:
            return self.qualities.pop(0)
        return QualityScore(score=0.0, total_notes=len(posts), filtered_count=len(posts), avg_similarity=0.0)


class FakeLLM:
    def __init__(self, outputs):
        self.outputs = list(outputs)

    async def chat(self, system: str, user: str, max_tokens: int = 1024, temperature: float = 0.7):
        del system, user, max_tokens, temperature
        if not self.outputs:
            return "{}"
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


async def _create_session(db_path: str, session_id: str, query: str) -> None:
    async with SessionManager(db_path) as manager:
        await manager.create_session(session_id, "u1", query)


def _build_agent(db_path: str, spider: FakeSpider, rag: FakeRAG, llm: FakeLLM) -> ContentStrategyAgent:
    return ContentStrategyAgent(
        session_manager=SessionManager(db_path),
        spider_client=spider,
        rag_service=rag,
        llm_client=llm,
    )


@pytest.mark.asyncio
async def test_strategy_workflow_session_not_found_returns_error(tmp_path):
    db_path = str(tmp_path / "strategy-missing.db")
    agent = _build_agent(
        db_path,
        spider=FakeSpider(mapping={}),
        rag=FakeRAG([]),
        llm=FakeLLM([]),
    )
    result = await agent.execute("missing-session")
    assert result.success is False
    assert result.error_code == "SESSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_strategy_workflow_spider_failure_sets_failed_and_cooldown(tmp_path):
    db_path = str(tmp_path / "strategy-spider-fail.db")
    session_id = str(uuid.uuid4())
    await _create_session(db_path, session_id, "护肤")

    agent = _build_agent(
        db_path,
        spider=FakeSpider(mapping={}, fail_queries={"护肤"}),
        rag=FakeRAG([]),
        llm=FakeLLM([]),
    )
    result = await agent.execute(session_id)
    assert result.success is False
    assert result.error_code == "SPIDER_SERVICE_UNAVAILABLE"

    async with SessionManager(db_path) as manager:
        session = await manager.get_session(session_id)
        assert session is not None
        assert session.stage.value == "failed"
        assert session.spider_cooldown_until is not None
        assert session.spider_cooldown_until > datetime.utcnow()


@pytest.mark.asyncio
async def test_strategy_workflow_insufficient_data_marks_failed_without_cooldown(tmp_path):
    db_path = str(tmp_path / "strategy-insufficient.db")
    session_id = str(uuid.uuid4())
    await _create_session(db_path, session_id, "空结果")

    agent = _build_agent(
        db_path,
        spider=FakeSpider(mapping={"空结果": []}),
        rag=FakeRAG([]),
        llm=FakeLLM([]),
    )
    result = await agent.execute(session_id)
    assert result.success is False
    assert result.error_code == "INSUFFICIENT_DATA"

    async with SessionManager(db_path) as manager:
        session = await manager.get_session(session_id)
        assert session is not None
        assert session.stage.value == "failed"
        assert session.spider_cooldown_until is None


@pytest.mark.asyncio
async def test_strategy_workflow_data_driven_end_to_end(tmp_path):
    db_path = str(tmp_path / "strategy-data-driven.db")
    session_id = str(uuid.uuid4())
    await _create_session(db_path, session_id, "护肤")

    spider = FakeSpider(mapping={"护肤": [_post(f"n{i}") for i in range(1, 12)]})
    rag = FakeRAG([QualityScore(score=0.66, total_notes=11, filtered_count=11, avg_similarity=0.6)])
    llm = FakeLLM([_strategy_json("功效护肤专家")])
    agent = _build_agent(db_path, spider=spider, rag=rag, llm=llm)

    result = await agent.execute(session_id)
    assert result.success is True
    assert result.used_fallback is False
    assert result.quality_score == pytest.approx(0.66)

    async with SessionManager(db_path) as manager:
        session = await manager.get_session(session_id)
        assert session is not None
        assert session.stage.value == "strategy"
        assert session.strategy_id is not None
        assert session.spider_note_ids is not None
        assert len(session.spider_note_ids) == 11


@pytest.mark.asyncio
async def test_strategy_workflow_low_quality_but_doc_count_at_cap_skips_expansion(tmp_path):
    db_path = str(tmp_path / "strategy-skip-expansion.db")
    session_id = str(uuid.uuid4())
    await _create_session(db_path, session_id, "咖啡")

    posts = [_post(f"a{i}") for i in range(1, settings.EXPANSION_DOC_COUNT_MAX + 1)]
    spider = FakeSpider(mapping={"咖啡": posts})
    rag = FakeRAG(
        [QualityScore(score=0.2, total_notes=settings.EXPANSION_DOC_COUNT_MAX, filtered_count=len(posts), avg_similarity=0.2)]
    )
    llm = FakeLLM([_strategy_json("通用策略")])
    agent = _build_agent(db_path, spider=spider, rag=rag, llm=llm)

    result = await agent.execute(session_id)
    assert result.success is True
    assert result.used_fallback is True
    assert spider.calls == [("咖啡", 50)]


@pytest.mark.asyncio
async def test_strategy_workflow_expansion_stops_when_new_unique_docs_too_few(tmp_path):
    db_path = str(tmp_path / "strategy-stop-unique.db")
    session_id = str(uuid.uuid4())
    await _create_session(db_path, session_id, "抹茶")

    spider = FakeSpider(
        mapping={
            "抹茶": [_post("s1"), _post("s2"), _post("s3"), _post("s4")],
            "抹茶教程": [_post("n1"), _post("n2")],
            "抹茶清单": [_post("n3"), _post("n4"), _post("n5")],
        }
    )
    rag = FakeRAG([QualityScore(score=0.2, total_notes=4, filtered_count=4, avg_similarity=0.2)])
    llm = FakeLLM(["抹茶教程\n抹茶清单", _strategy_json("fallback")])
    agent = _build_agent(db_path, spider=spider, rag=rag, llm=llm)

    result = await agent.execute(session_id)
    assert result.success is True
    assert result.used_fallback is True

    # hit stop condition after first expansion candidate (<3 new docs), so no index update and no next expansion
    assert spider.calls == [("抹茶", 50), ("抹茶教程", 30)]
    assert len(rag.index_calls) == 1

    async with SessionManager(db_path) as manager:
        session = await manager.get_session(session_id)
        assert session is not None
        assert session.expanded_queries == []


@pytest.mark.asyncio
async def test_strategy_workflow_expansion_stops_on_low_quality_gain(tmp_path):
    db_path = str(tmp_path / "strategy-stop-quality.db")
    session_id = str(uuid.uuid4())
    await _create_session(db_path, session_id, "奶茶")

    spider = FakeSpider(
        mapping={
            "奶茶": [_post("a1"), _post("a2"), _post("a3"), _post("a4")],
            "奶茶教程": [_post("a5"), _post("a6"), _post("a7")],
            "奶茶避坑": [_post("a8"), _post("a9"), _post("a10")],
        }
    )
    rag = FakeRAG(
        [
            QualityScore(score=0.20, total_notes=4, filtered_count=4, avg_similarity=0.2),
            QualityScore(score=0.22, total_notes=7, filtered_count=7, avg_similarity=0.22),
        ]
    )
    llm = FakeLLM(["奶茶教程\n奶茶避坑", _strategy_json("fallback")])
    agent = _build_agent(db_path, spider=spider, rag=rag, llm=llm)

    result = await agent.execute(session_id)
    assert result.success is True
    assert result.used_fallback is True
    # stop after first executed expansion due to quality_gain < threshold
    assert spider.calls == [("奶茶", 50), ("奶茶教程", 30)]

    async with SessionManager(db_path) as manager:
        session = await manager.get_session(session_id)
        assert session is not None
        assert session.expanded_queries == ["奶茶教程"]


@pytest.mark.asyncio
async def test_strategy_workflow_expansion_skips_failed_query_and_succeeds(tmp_path):
    db_path = str(tmp_path / "strategy-expand-skip-failed.db")
    session_id = str(uuid.uuid4())
    await _create_session(db_path, session_id, "咖啡")

    spider = FakeSpider(
        mapping={
            "咖啡": [_post("a1"), _post("a2"), _post("a3"), _post("a4")],
            "咖啡教程": [_post("a5"), _post("a6"), _post("a7")],
        },
        fail_queries={"咖啡清单"},
    )
    rag = FakeRAG(
        [
            QualityScore(score=0.20, total_notes=4, filtered_count=4, avg_similarity=0.2),
            QualityScore(score=0.40, total_notes=7, filtered_count=7, avg_similarity=0.4),
        ]
    )
    llm = FakeLLM(["咖啡清单\n咖啡教程", _strategy_json("数据驱动")])
    agent = _build_agent(db_path, spider=spider, rag=rag, llm=llm)

    result = await agent.execute(session_id)
    assert result.success is True
    assert result.used_fallback is False

    async with SessionManager(db_path) as manager:
        session = await manager.get_session(session_id)
        assert session is not None
        assert session.expanded_queries == ["咖啡教程"]


@pytest.mark.asyncio
async def test_strategy_workflow_expansion_stops_on_doc_count_cap(tmp_path):
    db_path = str(tmp_path / "strategy-stop-doc-cap.db")
    session_id = str(uuid.uuid4())
    await _create_session(db_path, session_id, "便当")

    seed_count = settings.EXPANSION_DOC_COUNT_MAX - 2
    spider = FakeSpider(
        mapping={
            "便当": [_post(f"s{i}") for i in range(seed_count)],
            "便当教程": [_post("x1"), _post("x2"), _post("x3")],
            "便当清单": [_post("x4"), _post("x5"), _post("x6")],
        }
    )
    rag = FakeRAG(
        [
            QualityScore(score=0.20, total_notes=seed_count, filtered_count=seed_count, avg_similarity=0.2),
            QualityScore(score=0.30, total_notes=settings.EXPANSION_DOC_COUNT_MAX, filtered_count=settings.EXPANSION_DOC_COUNT_MAX, avg_similarity=0.3),
        ]
    )
    llm = FakeLLM(["便当教程\n便当清单", _strategy_json("fallback")])
    agent = _build_agent(db_path, spider=spider, rag=rag, llm=llm)

    result = await agent.execute(session_id)
    assert result.success is True
    assert result.used_fallback is True
    # stop after first expansion because total_notes reaches cap
    assert spider.calls == [("便当", 50), ("便当教程", 30)]

    async with SessionManager(db_path) as manager:
        session = await manager.get_session(session_id)
        assert session is not None
        assert session.expanded_queries == ["便当教程"]


@pytest.mark.asyncio
async def test_strategy_workflow_expansion_generation_failure_falls_back_to_generic_without_expansion(tmp_path):
    db_path = str(tmp_path / "strategy-expand-llm-fail.db")
    session_id = str(uuid.uuid4())
    await _create_session(db_path, session_id, "酸奶")

    spider = FakeSpider(mapping={"酸奶": [_post("a1"), _post("a2"), _post("a3"), _post("a4")]})
    rag = FakeRAG([QualityScore(score=0.20, total_notes=4, filtered_count=4, avg_similarity=0.2)])
    llm = FakeLLM([RuntimeError("llm temporary failure"), _strategy_json("fallback")])
    agent = _build_agent(db_path, spider=spider, rag=rag, llm=llm)

    result = await agent.execute(session_id)
    assert result.success is True
    assert result.used_fallback is True
    assert spider.calls == [("酸奶", 50)]


@pytest.mark.asyncio
async def test_strategy_workflow_invalid_strategy_json_uses_default_strategy(tmp_path):
    db_path = str(tmp_path / "strategy-invalid-json.db")
    session_id = str(uuid.uuid4())
    await _create_session(db_path, session_id, "健身")

    spider = FakeSpider(mapping={"健身": [_post(f"n{i}") for i in range(1, 12)]})
    rag = FakeRAG([QualityScore(score=0.60, total_notes=11, filtered_count=11, avg_similarity=0.6)])
    llm = FakeLLM(["not a json response"])
    agent = _build_agent(db_path, spider=spider, rag=rag, llm=llm)

    result = await agent.execute(session_id)
    assert result.success is True
    assert result.content_strategy is not None
    assert result.content_strategy.positioning == "生活方式分享者"
