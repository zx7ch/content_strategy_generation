from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.graph.workflow import create_workflow
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

    async def execute(self, session_id: str) -> StubStrategyResult:
        async with SessionManager(self.db_path) as manager:
            await manager.update_session(
                session_id,
                spider_notes=[
                    SpiderNote(note_id="n1", title="标题1", content="内容1", tags=["tag1"]),
                    SpiderNote(note_id="n2", title="标题2", content="内容2", tags=["tag2"]),
                ],
                content_strategy=ContentStrategy(
                    positioning="轻运动",
                    target_audience="城市女生",
                    content_pillars=["教程", "穿搭"],
                    key_messaging="真实可执行",
                    content_types=["图文"],
                    posting_strategy="晚间",
                    data_source_quality=0.68,
                ),
                platform_preference=PlatformPreference(
                    avg_title_length=14,
                    popular_tags=["教程", "穿搭"],
                    optimal_posting_times=["20:00"],
                    content_patterns=["中等长度文案"],
                ),
                quality_score=0.68,
                used_fallback=False,
                stage=SessionStage.STRATEGY,
            )
        return StubStrategyResult(success=True, message="ok")


class StubGenerationAgent:
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def execute(self, session_id: str) -> StubGenerationResult:
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


async def _create_session(db_path: str, session_id: str) -> None:
    async with SessionManager(db_path) as manager:
        await manager.create_session(session_id, "u1", "轻运动")


def _workflow_agents(db_path: str):
    return StubStrategyAgent(db_path), StubGenerationAgent(db_path)


@pytest.mark.asyncio
async def test_checkpoint_recovery_resumes_generation_and_matches_one_shot_result(tmp_path):
    interrupted_db = str(tmp_path / "checkpoint-interrupted.db")
    baseline_db = str(tmp_path / "checkpoint-baseline.db")
    session_id = str(uuid.uuid4())
    await _create_session(interrupted_db, session_id)
    await _create_session(baseline_db, session_id)

    interrupted_strategy_agent, interrupted_generation_agent = _workflow_agents(interrupted_db)
    baseline_strategy_agent, baseline_generation_agent = _workflow_agents(baseline_db)
    config = {"configurable": {"thread_id": "thread-checkpoint"}}

    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "langgraph-checkpoints.db")) as saver:
        interrupted_workflow = create_workflow(
            checkpointer=saver,
            interrupt_before=["generate_node"],
            session_manager=SessionManager(interrupted_db),
            strategy_agent=interrupted_strategy_agent,
            generation_agent=interrupted_generation_agent,
        )

        paused_state = await interrupted_workflow.ainvoke(
            {"session_id": session_id},
            config=config,
        )
        assert paused_state["stage"] == "strategy"
        assert paused_state["strategy_id"]
        assert paused_state["generated_note_ids"] == []

        resumed_state = await interrupted_workflow.ainvoke(None, config=config)

        baseline_workflow = create_workflow(
            session_manager=SessionManager(baseline_db),
            strategy_agent=baseline_strategy_agent,
            generation_agent=baseline_generation_agent,
        )
        baseline_state = await baseline_workflow.ainvoke({"session_id": session_id})

    comparable_keys = {
        "session_id",
        "stage",
        "lifecycle_state",
        "user_query",
        "spider_note_ids",
        "proposal_ids",
        "generated_note_ids",
        "quality_score",
        "used_fallback",
    }
    assert {key: resumed_state.get(key) for key in comparable_keys} == {
        key: baseline_state.get(key) for key in comparable_keys
    }
    assert resumed_state["stage"] == "completed"
    assert resumed_state["generated_note_ids"] == ["g1"]


@pytest.mark.asyncio
async def test_checkpoint_payload_stays_lightweight(tmp_path):
    db_path = str(tmp_path / "checkpoint-lightweight.db")
    session_id = str(uuid.uuid4())
    await _create_session(db_path, session_id)
    strategy_agent, generation_agent = _workflow_agents(db_path)
    config = {"configurable": {"thread_id": "thread-lightweight"}}

    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "langgraph-lightweight.db")) as saver:
        workflow = create_workflow(
            checkpointer=saver,
            interrupt_before=["generate_node"],
            session_manager=SessionManager(db_path),
            strategy_agent=strategy_agent,
            generation_agent=generation_agent,
        )

        await workflow.ainvoke({"session_id": session_id}, config=config)
        checkpoint_tuple = await saver.aget_tuple(config)

    assert checkpoint_tuple is not None
    checkpoint_json = json.dumps(checkpoint_tuple.checkpoint, ensure_ascii=False)
    assert "content_strategy" not in checkpoint_json
    assert "generated_notes" not in checkpoint_json
    assert "spider_notes" not in checkpoint_json
    assert len(checkpoint_json) < 5000


@pytest.mark.asyncio
async def test_checkpoint_recovery_resumes_after_strategy_interrupt_point(tmp_path):
    interrupted_db = str(tmp_path / "checkpoint-before-strategy.db")
    baseline_db = str(tmp_path / "checkpoint-before-strategy-baseline.db")
    session_id = str(uuid.uuid4())
    await _create_session(interrupted_db, session_id)
    await _create_session(baseline_db, session_id)

    interrupted_strategy_agent, interrupted_generation_agent = _workflow_agents(interrupted_db)
    baseline_strategy_agent, baseline_generation_agent = _workflow_agents(baseline_db)
    config = {"configurable": {"thread_id": "thread-before-strategy"}}

    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "langgraph-before-strategy.db")) as saver:
        interrupted_workflow = create_workflow(
            checkpointer=saver,
            interrupt_before=["strategy_node"],
            session_manager=SessionManager(interrupted_db),
            strategy_agent=interrupted_strategy_agent,
            generation_agent=interrupted_generation_agent,
        )

        paused_state = await interrupted_workflow.ainvoke({"session_id": session_id}, config=config)
        assert paused_state["stage"] == "init"
        assert paused_state["strategy_id"] is None

        resumed_state = await interrupted_workflow.ainvoke(None, config=config)

        baseline_workflow = create_workflow(
            session_manager=SessionManager(baseline_db),
            strategy_agent=baseline_strategy_agent,
            generation_agent=baseline_generation_agent,
        )
        baseline_state = await baseline_workflow.ainvoke({"session_id": session_id})

    assert resumed_state["stage"] == "completed"
    assert resumed_state["generated_note_ids"] == baseline_state["generated_note_ids"]
    assert resumed_state["strategy_id"]
    assert baseline_state["strategy_id"]
