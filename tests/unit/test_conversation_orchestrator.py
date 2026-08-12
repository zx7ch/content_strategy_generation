"""Unit tests for T4/T8.2 ConversationOrchestrator."""

from __future__ import annotations

import json

import pytest

from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowConstraintType, WorkflowPhase, WorkflowRunStatus
from app.services.conversation_orchestrator import (
    ConstraintClassification,
    ConversationOrchestrator,
    IntentRouterV2,
    LLMStructuredConstraintClassifier,
)
from app.models.workflow import WorkflowArtifactType
from app.services.workflow_run_manager import WorkflowRunManager


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


class _FakeLLMClient:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[dict] = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class _SemanticIntentClassifier:
    def __init__(self, intent: str):
        self.intent = intent

    async def classify_intent(self, text: str, *, has_active_run: bool) -> str:
        return self.intent


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
async def test_late_constraint_ack_offers_rerun_instead_of_claiming_current_effect(ctx):
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
    run_id = started["command_result"]["run_id"]
    async with WorkflowStore(db_path) as store:
        assert store._conn is not None
        await store._conn.execute(
            "UPDATE workflow_runs SET phase=?, current_step=? WHERE run_id=?",
            (WorkflowPhase.REVIEW.value, "review.await_user_acceptance", run_id),
        )
        await store._conn.commit()
    updated = await thread_store.get_thread(thread["id"])
    assert updated is not None

    result = await orchestrator.handle_message(
        thread=updated,
        text="标题再强一点",
        user_id="user-1",
    )

    assert result["intent"] == "add_constraint"
    assert result["command_result"]["accepted"] is True
    assert result["command_result"]["can_affect_current_run"] is False
    assert result["command_result"]["suggested_action"] == "rerun_workflow"
    assert "不会改变这轮已生成结果" in result["assistant_reply"]


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


@pytest.mark.asyncio
async def test_llm_structured_classifier_calls_client_and_extracts_json_object():
    llm = _FakeLLMClient(
        '```json\n{"constraint_type":"style","scope":"run","confidence":0.88,'
        '"normalized":{"tone":"生活化"}}\n```'
    )
    classifier = LLMStructuredConstraintClassifier(llm_client=llm, fallback=None)

    result = await classifier.classify("语气更生活化一点")

    assert llm.calls
    assert llm.calls[0]["temperature"] == 0.0
    assert "Return only one JSON object" in llm.calls[0]["system"]
    assert result.constraint_type == WorkflowConstraintType.STYLE
    assert result.confidence == 0.88
    assert result.normalized == {"tone": "生活化"}


@pytest.mark.asyncio
async def test_revision_message_creates_patch_artifact_and_does_not_add_constraint(ctx):
    db_path, thread_store, thread = ctx
    orchestrator = ConversationOrchestrator(db_path=db_path, thread_store=thread_store)
    started = await orchestrator.handle_message(
        thread=thread,
        text="帮我生成两篇防晒衣笔记",
        user_id="user-1",
    )
    run_id = started["command_result"]["run_id"]
    async with WorkflowRunManager(db_path) as manager:
        first = await manager.attach_artifact(
            run_id=run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            payload={"note_id": "note-1", "title": "第一篇", "content": "正式", "tags": []},
        )
        second = await manager.attach_artifact(
            run_id=run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            payload={"note_id": "note-2", "title": "第二篇", "content": "正式", "tags": []},
        )
    await thread_store.append_artifact_result_message(
        thread_id=thread["id"],
        run_id=run_id,
        artifact_refs=[
            {
                "artifact_id": first.artifact_id,
                "artifact_type": "generated_note",
                "artifact_version": first.artifact_version,
            },
            {
                "artifact_id": second.artifact_id,
                "artifact_type": "generated_note",
                "artifact_version": second.artifact_version,
            },
        ],
    )

    updated = await thread_store.get_thread(thread["id"])
    result = await orchestrator.handle_message(
        thread=updated,
        text="把第 2 篇改生活化",
        user_id="user-1",
    )

    assert result["intent"] == "revise_artifact"
    assert result["command_result"]["accepted"] is True
    assert result["command_result"]["target_artifact_id"] == second.artifact_id
    async with WorkflowStore(db_path) as store:
        artifacts = await store.list_artifacts(run_id)
        constraints = await store.list_constraints(run_id)
    messages = await thread_store.get_thread_messages(thread["id"])
    artifact_messages = [message for message in messages if message["message_type"] == "artifact_result"]
    patch = next(artifact for artifact in artifacts if artifact.parent_artifact_id == second.artifact_id)
    assert patch.payload_mode.value == "patch"
    assert patch.payload_json["changed_fields"]["revision_instruction"] == "把第 2 篇改生活化"
    assert constraints == []
    revision_refs = json.loads(artifact_messages[-1]["artifact_refs_json"])
    assert revision_refs[0]["parent_artifact_id"] == second.artifact_id


@pytest.mark.asyncio
async def test_regenerate_artifact_returns_accepted_dispatch(ctx):
    db_path, thread_store, thread = ctx
    orchestrator = ConversationOrchestrator(db_path=db_path, thread_store=thread_store)
    start = await orchestrator.handle_message(thread=thread, text="帮我生成内容策略", user_id="user-1")
    updated = await thread_store.get_thread(thread["id"])

    result = await orchestrator.handle_message(thread=updated, text="重新生成一版", user_id="user-1")

    assert result["intent"] == "regenerate_artifact"
    assert result["command_result"] == {
        "action": "regenerate_artifact",
        "accepted": True,
        "run_id": start["command_result"]["run_id"],
        "dispatch": "workflow_command",
    }


@pytest.mark.asyncio
async def test_fake_semantic_intent_classifier_drives_add_constraint_and_revision():
    add_router = IntentRouterV2(semantic_classifier=_SemanticIntentClassifier("add_constraint"))
    revision_router = IntentRouterV2(semantic_classifier=_SemanticIntentClassifier("revise_artifact"))

    assert await add_router.classify("目标用户更年轻", has_active_run=True) == "add_constraint"
    assert await revision_router.classify("这条内容换个说法", has_active_run=True) == "revise_artifact"


@pytest.mark.asyncio
async def test_rerun_message_creates_new_run_and_records_parent_checkpoint(ctx):
    db_path, thread_store, thread = ctx
    orchestrator = ConversationOrchestrator(db_path=db_path, thread_store=thread_store)
    first = await orchestrator.handle_message(
        thread=thread,
        text="帮我生成防晒衣笔记",
        user_id="user-1",
    )
    old_run_id = first["command_result"]["run_id"]
    updated = await thread_store.get_thread(thread["id"])

    result = await orchestrator.handle_message(
        thread=updated,
        text="不要防晒衣了，改成徒步鞋",
        user_id="user-1",
    )

    assert result["intent"] == "rerun_workflow"
    assert result["command_result"]["action"] == "rerun_workflow"
    assert result["command_result"]["accepted"] is True
    assert result["command_result"]["parent_run_id"] == old_run_id
    assert result["command_result"]["run_id"] != old_run_id
    assert "新" in result["assistant_reply"]

    refreshed = await thread_store.get_thread(thread["id"])
    assert refreshed is not None
    assert refreshed["active_run_id"] == result["command_result"]["run_id"]

    async with WorkflowStore(db_path) as store:
        old_run = await store.get_run(old_run_id)
        new_run = await store.get_run(result["command_result"]["run_id"])
        new_steps = await store.list_steps(result["command_result"]["run_id"])
        old_constraints = await store.list_constraints(old_run_id)
    assert old_run is not None
    assert new_run is not None
    assert len(new_steps) >= 20
    assert new_steps[0].step_name == "intake.capture_request"
    assert new_steps[0].checkpoint_json == {
        "run_type": "rerun",
        "parent_run_id": old_run_id,
        "rerun_request": "不要防晒衣了，改成徒步鞋",
    }
    assert old_constraints == []


@pytest.mark.asyncio
async def test_intent_router_detects_rerun_before_generic_constraint():
    router = IntentRouterV2()

    assert await router.classify("不要防晒衣了，改成徒步鞋", has_active_run=True) == "rerun_workflow"
    assert await router.classify("换个主题，做徒步鞋", has_active_run=True) == "rerun_workflow"
