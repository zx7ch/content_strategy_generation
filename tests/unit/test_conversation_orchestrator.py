"""Unit tests for T4 ConversationOrchestrator."""

from __future__ import annotations

import pytest

from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowConstraintType, WorkflowRunStatus
from app.services.conversation_orchestrator import (
    ConstraintClassification,
    ConversationOrchestrator,
    LLMStructuredConstraintClassifier,
)


class _FakeClassifier:
    def __init__(self, confidence: float = 0.9):
        self.confidence = confidence

    async def classify(self, text: str) -> ConstraintClassification:
        return ConstraintClassification(
            constraint_type=WorkflowConstraintType.STYLE,
            scope="run",
            confidence=self.confidence,
            normalized={"text": text, "fake": True},
        )


class _StructuredClassifier(LLMStructuredConstraintClassifier):
    async def _call_llm_structured_output(self, _text: str) -> dict:
        return {
            "constraint_type": "target_audience",
            "scope": "run",
            "confidence": 0.91,
            "normalized": {"age_range": "25-35", "gender": "female"},
        }


@pytest.fixture
async def ctx(tmp_path):
    db_path = str(tmp_path / "conversation.db")
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    thread = await thread_store.create_thread(title="T4")
    yield db_path, thread_store, thread
    await thread_store.close()


@pytest.mark.asyncio
async def test_generation_request_creates_active_run(ctx):
    db_path, thread_store, thread = ctx
    orchestrator = ConversationOrchestrator(db_path=db_path, thread_store=thread_store)

    result = await orchestrator.handle_message(
        thread=thread,
        text="帮我生成一组小红书防晒衣笔记",
        user_id="user-1",
    )

    assert result["intent"] == "start_workflow"
    assert result["command_result"]["action"] == "start_workflow"
    assert result["active_run_snapshot"]["run"]["status"] == "running"
    assert result["active_run_snapshot"]["run"]["current_step"] == "intake.capture_request"
    assert result["active_run_snapshot"]["steps"][0]["step_name"] == "intake.capture_request"
    assert len(result["active_run_snapshot"]["steps"]) >= 20
    updated = await thread_store.get_thread(thread["id"])
    assert updated is not None
    assert updated["active_run_id"] == result["command_result"]["run_id"]


@pytest.mark.asyncio
async def test_running_message_adds_constraint(ctx):
    db_path, thread_store, thread = ctx
    orchestrator = ConversationOrchestrator(
        db_path=db_path,
        thread_store=thread_store,
        constraint_classifier=_FakeClassifier(confidence=0.92),
    )
    started = await orchestrator.handle_message(
        thread=thread,
        text="帮我生成内容策略",
        user_id="user-1",
    )
    updated = await thread_store.get_thread(thread["id"])
    assert updated is not None

    result = await orchestrator.handle_message(
        thread=updated,
        text="风格更生活化一点",
        user_id="user-1",
    )

    assert result["intent"] == "add_constraint"
    assert result["command_result"]["accepted"] is True
    assert result["active_run_snapshot"]["run"]["constraint_version"] == 1
    async with WorkflowStore(db_path) as store:
        refreshed_run = await store.get_run(started["command_result"]["run_id"])
        constraints = await store.list_constraints(started["command_result"]["run_id"])
        events = await store.list_events(started["command_result"]["run_id"])
    assert refreshed_run is not None
    assert refreshed_run.constraint_version == 1
    assert len(constraints) == 1
    assert constraints[0].raw_text == "风格更生活化一点"
    assert "constraint_added" in [event.event_type for event in events]


@pytest.mark.asyncio
async def test_low_confidence_constraint_does_not_write_constraint(ctx):
    db_path, thread_store, thread = ctx
    orchestrator = ConversationOrchestrator(
        db_path=db_path,
        thread_store=thread_store,
        constraint_classifier=_FakeClassifier(confidence=0.2),
    )
    started = await orchestrator.handle_message(
        thread=thread,
        text="帮我生成内容策略",
        user_id="user-1",
    )
    updated = await thread_store.get_thread(thread["id"])
    assert updated is not None

    result = await orchestrator.handle_message(
        thread=updated,
        text="也许随便改一下吧",
        user_id="user-1",
    )

    assert result["intent"] == "add_constraint"
    assert result["command_result"]["accepted"] is False
    assert result["command_result"]["reason"] == "low_confidence"
    async with WorkflowStore(db_path) as store:
        constraints = await store.list_constraints(started["command_result"]["run_id"])
    assert constraints == []


@pytest.mark.asyncio
async def test_pause_resume_cancel_and_status_commands(ctx):
    db_path, thread_store, thread = ctx
    orchestrator = ConversationOrchestrator(db_path=db_path, thread_store=thread_store)
    start = await orchestrator.handle_message(
        thread=thread,
        text="帮我生成内容策略",
        user_id="user-1",
    )
    run_id = start["command_result"]["run_id"]

    thread = await thread_store.get_thread(thread["id"])
    paused = await orchestrator.handle_message(thread=thread, text="暂停一下", user_id="user-1")
    assert paused["command_result"]["action"] == "pause_run"
    assert paused["active_run_snapshot"]["run"]["status"] == WorkflowRunStatus.PAUSING.value

    async with WorkflowStore(db_path) as store:
        assert store._conn is not None
        await store._conn.execute("UPDATE workflow_runs SET status='paused' WHERE run_id=?", (run_id,))
        await store._conn.commit()

    resumed = await orchestrator.handle_message(thread=thread, text="继续", user_id="user-1")
    assert resumed["command_result"]["action"] == "resume_run"
    assert resumed["active_run_snapshot"]["run"]["status"] == WorkflowRunStatus.RUNNING.value

    status = await orchestrator.handle_message(thread=thread, text="现在进度怎么样？", user_id="user-1")
    assert status["command_result"]["action"] == "ask_status"
    assert "当前任务状态" in status["assistant_reply"]

    cancelled = await orchestrator.handle_message(thread=thread, text="取消任务", user_id="user-1")
    assert cancelled["command_result"]["action"] == "cancel_run"
    assert cancelled["active_run_snapshot"]["run"]["status"] == WorkflowRunStatus.CANCELLING.value


@pytest.mark.asyncio
async def test_llm_structured_classifier_adapter_parses_reserved_schema():
    classifier = _StructuredClassifier(llm_client=object())

    result = await classifier.classify("目标用户改为25-35岁女性")

    assert result.constraint_type == WorkflowConstraintType.TARGET_AUDIENCE
    assert result.scope == "run"
    assert result.confidence == 0.91
    assert result.normalized == {"age_range": "25-35", "gender": "female"}
