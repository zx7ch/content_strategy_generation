from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest

from app.graph.nodes import error_node, generate_node, init_node, strategy_node
from app.memory.session_state import SessionManager
from app.models.session import (
    ContentStrategy,
    GeneratedNote,
    PlatformPreference,
    Proposal,
    SessionStage,
    SpiderNote,
)


@dataclass
class StubStrategyResult:
    success: bool
    message: str
    error_code: str | None = None


@dataclass
class StubGenerationResult:
    success: bool
    message: str
    error_code: str | None = None


class StubStrategyAgent:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.calls: list[str] = []

    async def execute(self, session_id: str) -> StubStrategyResult:
        self.calls.append(session_id)
        async with SessionManager(self.db_path) as manager:
            await manager.update_session(
                session_id,
                spider_notes=[SpiderNote(note_id="n1", title="t1", content="c1", tags=["tag"])],
                content_strategy=ContentStrategy(
                    positioning="轻运动",
                    target_audience="城市女生",
                    content_pillars=["教程", "穿搭"],
                    key_messaging="真实可执行",
                    content_types=["图文"],
                    posting_strategy="晚间",
                    data_source_quality=0.72,
                ),
                platform_preference=PlatformPreference(
                    avg_title_length=14,
                    popular_tags=["教程"],
                    optimal_posting_times=["20:00"],
                    content_patterns=["中等长度文案"],
                ),
                quality_score=0.72,
                used_fallback=False,
                stage=SessionStage.STRATEGY,
            )
        return StubStrategyResult(success=True, message="ok")


class StubGenerationAgent:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.calls: list[str] = []

    async def execute(self, session_id: str) -> StubGenerationResult:
        self.calls.append(session_id)
        async with SessionManager(self.db_path) as manager:
            await manager.update_session(
                session_id,
                proposals=[
                    Proposal(
                        proposal_id="p1",
                        angle="角度一",
                        hook="标题一",
                        outline="要点一\n要点二",
                        target_emotion="practical_value",
                        content_pillars=["教程"],
                        suggested_tags=["教程"],
                        score=0.9,
                    )
                ],
                generated_notes=[
                    GeneratedNote(
                        note_id="g1",
                        title="标题一",
                        content="正文一\n第二段",
                        tags=["#教程"],
                        cover_design_prompt="封面一",
                        suggested_update_time="2026-03-18 20:00",
                        similarity_check={"max_similarity": 0.1, "status": "safe"},
                        generation_params={"proposal_id": "p1", "temperature": 0.7, "slot_id": 0},
                    )
                ],
                similarity_report={"notes_generated": 1, "failed_count": 0},
                stage=SessionStage.COMPLETED,
            )
        return StubGenerationResult(success=True, message="ok")


async def _create_session(db_path: str, session_id: str, user_query: str = "护肤") -> None:
    async with SessionManager(db_path) as manager:
        await manager.create_session(session_id, "u1", user_query)


async def _seed_strategy_session(db_path: str, session_id: str) -> None:
    async with SessionManager(db_path) as manager:
        await manager.create_session(session_id, "u1", "轻运动")
        await manager.update_session(
            session_id,
            spider_notes=[SpiderNote(note_id="n1", title="t1", content="c1", tags=["tag"])],
            content_strategy=ContentStrategy(
                positioning="轻运动",
                target_audience="城市女生",
                content_pillars=["教程", "穿搭"],
                key_messaging="真实可执行",
                content_types=["图文"],
                posting_strategy="晚间",
                data_source_quality=0.66,
            ),
            platform_preference=PlatformPreference(
                avg_title_length=14,
                popular_tags=["教程"],
                optimal_posting_times=["20:00"],
                content_patterns=["中等长度文案"],
            ),
            quality_score=0.66,
            used_fallback=False,
            stage=SessionStage.STRATEGY,
        )


async def _seed_generation_session(db_path: str, session_id: str) -> None:
    await _seed_strategy_session(db_path, session_id)
    async with SessionManager(db_path) as manager:
        await manager.update_session(
            session_id,
            proposals=[
                Proposal(
                    proposal_id="p1",
                    angle="角度一",
                    hook="标题一",
                    outline="要点一\n要点二",
                    target_emotion="practical_value",
                    content_pillars=["教程"],
                    suggested_tags=["教程"],
                    score=0.9,
                )
            ],
            generated_notes=[
                GeneratedNote(
                    note_id="g1",
                    title="标题一",
                    content="正文一\n第二段",
                    tags=["#教程"],
                    cover_design_prompt="封面一",
                    suggested_update_time="2026-03-18 20:00",
                    similarity_check={"max_similarity": 0.1, "status": "safe"},
                    generation_params={"proposal_id": "p1", "temperature": 0.7, "slot_id": 0},
                )
            ],
            similarity_report={"notes_generated": 1, "failed_count": 0},
            stage=SessionStage.COMPLETED,
        )


@pytest.mark.asyncio
async def test_init_node_projects_lightweight_state_without_large_payloads(tmp_path):
    db_path = str(tmp_path / "nodes-init.db")
    session_id = str(uuid.uuid4())
    await _seed_strategy_session(db_path, session_id)

    result = await init_node({"session_id": session_id}, session_manager=SessionManager(db_path))

    assert result["session_id"] == session_id
    assert result["stage"] == "strategy"
    assert result["user_query"] == "轻运动"
    assert result["spider_note_ids"] == ["n1"]
    assert result["strategy_id"]
    assert "content_strategy" not in result
    assert "generated_notes" not in result


@pytest.mark.asyncio
async def test_init_node_returns_failed_state_for_missing_session(tmp_path):
    db_path = str(tmp_path / "nodes-missing.db")

    result = await init_node({"session_id": "missing"}, session_manager=SessionManager(db_path))

    assert result["stage"] == "failed"
    assert result["error_code"] == "SESSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_strategy_node_is_idempotent_when_strategy_already_exists(tmp_path):
    db_path = str(tmp_path / "nodes-strategy-idempotent.db")
    session_id = str(uuid.uuid4())
    await _seed_strategy_session(db_path, session_id)
    agent = StubStrategyAgent(db_path)

    result = await strategy_node(
        {"session_id": session_id},
        session_manager=SessionManager(db_path),
        strategy_agent=agent,
    )

    assert result["stage"] == "strategy"
    assert result["strategy_id"]
    assert result["quality_score"] == pytest.approx(0.66)
    assert agent.calls == []


@pytest.mark.asyncio
async def test_strategy_node_executes_agent_and_reloads_session_state(tmp_path):
    db_path = str(tmp_path / "nodes-strategy-run.db")
    session_id = str(uuid.uuid4())
    await _create_session(db_path, session_id, "轻运动")
    agent = StubStrategyAgent(db_path)

    result = await strategy_node(
        {"session_id": session_id},
        session_manager=SessionManager(db_path),
        strategy_agent=agent,
    )

    assert agent.calls == [session_id]
    assert result["stage"] == "strategy"
    assert result["strategy_id"]
    assert result["spider_note_ids"] == ["n1"]
    assert result["quality_score"] == pytest.approx(0.72)


@pytest.mark.asyncio
async def test_generate_node_is_idempotent_when_notes_already_exist(tmp_path):
    db_path = str(tmp_path / "nodes-generate-idempotent.db")
    session_id = str(uuid.uuid4())
    await _seed_generation_session(db_path, session_id)
    agent = StubGenerationAgent(db_path)

    result = await generate_node(
        {"session_id": session_id},
        session_manager=SessionManager(db_path),
        generation_agent=agent,
    )

    assert result["stage"] == "completed"
    assert result["generated_note_ids"] == ["g1"]
    assert agent.calls == []


@pytest.mark.asyncio
async def test_generate_node_executes_agent_and_returns_projected_refs(tmp_path):
    db_path = str(tmp_path / "nodes-generate-run.db")
    session_id = str(uuid.uuid4())
    await _seed_strategy_session(db_path, session_id)
    agent = StubGenerationAgent(db_path)

    result = await generate_node(
        {"session_id": session_id},
        session_manager=SessionManager(db_path),
        generation_agent=agent,
    )

    assert agent.calls == [session_id]
    assert result["stage"] == "completed"
    assert result["proposal_ids"] == ["p1"]
    assert result["generated_note_ids"] == ["g1"]


@pytest.mark.asyncio
async def test_error_node_persists_unified_session_error_and_failed_state(tmp_path):
    db_path = str(tmp_path / "nodes-error.db")
    session_id = str(uuid.uuid4())
    await _seed_strategy_session(db_path, session_id)

    result = await error_node(
        {
            "session_id": session_id,
            "stage": "generation",
            "error_code": "WORKFLOW_ERROR",
            "error_message": "node crashed",
        },
        session_manager=SessionManager(db_path),
    )

    assert result["stage"] == "failed"
    assert result["error_code"] == "WORKFLOW_ERROR"
    assert result["error_message"] == "node crashed"

    async with SessionManager(db_path) as manager:
        session = await manager.get_session(session_id)
        assert session is not None
        assert session.stage == SessionStage.FAILED
        assert session.error is not None
        assert session.error.code == "WORKFLOW_ERROR"
        assert session.error.stage == SessionStage.GENERATION
