from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from app.content_research.api_schemas import (
    ContentResearchBriefConfirmRequest,
    ContentResearchSubjectStructureConfirmationRequest,
    ContentResearchWorkflowActionRequest,
)
from app.content_research.persistence_models import StageCheckpointRecord
from app.content_research.presearch.service import PresearchService
from app.content_research.service import (
    ContentResearchService,
    ContentResearchStateConflictError,
    ContentResearchValidationError,
    WorkflowRunManagerRuntime,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.services.llm.failures import LLMProviderFailure
from app.services.llm.types import LLMResponse, TokenUsage


class FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.waiting = False

    async def start_presearch_run(self, *, thread_id: str, user_id: str, seed_text: str) -> str:
        self.calls.append({"thread_id": thread_id, "user_id": user_id, "seed_text": seed_text})
        return "run_1"

    async def mark_presearch_ready(self, workflow_run_id: str) -> None:
        self.calls.append({"workflow_run_id": workflow_run_id, "event": "presearch_ready"})

    async def wait_for_presearch_recovery(self, workflow_run_id: str, reason: dict) -> dict:
        self.waiting = True
        self.calls.append({"workflow_run_id": workflow_run_id, "event": "wait_for_presearch_recovery", "reason": reason})
        return {"workflow_run_id": workflow_run_id, "status": "waiting_user"}

    async def wait_for_subject_clarification(self, workflow_run_id: str, reason: dict) -> dict:
        self.waiting = True
        self.calls.append({
            "workflow_run_id": workflow_run_id,
            "event": "wait_for_subject_clarification",
            "reason": reason,
        })
        return {"workflow_run_id": workflow_run_id, "status": "waiting_user"}

    async def resume_subject_clarification(self, workflow_run_id: str) -> dict:
        self.waiting = False
        self.calls.append({
            "workflow_run_id": workflow_run_id,
            "event": "resume_subject_clarification",
        })
        return {"workflow_run_id": workflow_run_id, "status": "running"}

    async def restart_presearch_step(self, workflow_run_id: str) -> dict:
        self.waiting = False
        self.calls.append({"workflow_run_id": workflow_run_id, "event": "restart_presearch_step"})
        return {"workflow_run_id": workflow_run_id, "status": "running"}

    async def get_runtime_snapshot(self, workflow_run_id: str) -> dict:
        return {
            "run": {"run_id": workflow_run_id, "status": "waiting_user" if self.waiting else "running"},
            "steps": [{"step_name": "presearch", "status": "retrying", "attempt_count": 1, "max_attempts": 3}],
            "child_tasks": [],
        }

    async def list_events(self, workflow_run_id: str) -> list[dict]:
        return []


class FakeLLM:
    def __init__(self, content: str | None = None, *, delay: float = 0.0, fail: bool = False) -> None:
        self.content = content or json.dumps(
            {
                "subject_confirmation": "徒步短裤更可能是户外服饰品类，请确认。",
                "competitor_tags": ["迪卡侬", "凯乐石"],
                "research_directions": ["产品卖点表达", "用户评论痛点"],
                "custom_research_question": "关注夏季轻量户外",
                "custom_competitor_input": "",
                "subject_structure": {
                    "schema_version": "content_research_subject_structure_v1",
                    "canonical_subject": "短裤",
                    "subject_type": "category",
                    "core_entities": [
                        {
                            "canonical_name": "短裤",
                            "raw_mentions": ["短裤"],
                        }
                    ],
                    "research_intents": ["产品卖点"],
                    "context_modifiers": ["夏季轻量户外"],
                    "synonym_groups": {"短裤": ["户外短裤"]},
                    "ambiguities": [],
                    "resolution_state": "resolved",
                },
            },
            ensure_ascii=False,
        )
        self.delay = delay
        self.fail = fail
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("llm unavailable")
        return LLMResponse(
            content=self.content,
            provider="fake",
            model="fake-model",
            usage=TokenUsage(total_tokens=10),
            latency_ms=1,
        )


class AmbiguousThenClarifiedLLM(FakeLLM):
    def __init__(self) -> None:
        super().__init__()
        self.responses = [
            {
                "subject_confirmation": "‘苹果’可能指品牌或水果，请补充说明。",
                "competitor_tags": [],
                "research_directions": ["产品营销"],
                "custom_research_question": "",
                "custom_competitor_input": "",
                "subject_structure": {
                    "schema_version": "content_research_subject_structure_v1",
                    "canonical_subject": "苹果",
                    "subject_type": "ambiguous",
                    "core_entities": [
                        {"canonical_name": "Apple 品牌", "raw_mentions": ["苹果"]},
                        {"canonical_name": "苹果水果", "raw_mentions": ["苹果"]},
                    ],
                    "research_intents": ["年轻人偏好"],
                    "context_modifiers": [],
                    "synonym_groups": {},
                    "ambiguities": ["苹果指 Apple 品牌还是水果"],
                    "resolution_state": "needs_confirmation",
                },
            },
            {
                "subject_confirmation": "已确认调研 Apple 品牌在年轻人中的偏好。",
                "competitor_tags": ["华为", "小米"],
                "research_directions": ["产品营销"],
                "custom_research_question": "",
                "custom_competitor_input": "",
                "subject_structure": {
                    "schema_version": "content_research_subject_structure_v1",
                    "canonical_subject": "Apple 品牌",
                    "subject_type": "brand",
                    "core_entities": [
                        {"canonical_name": "Apple 品牌", "raw_mentions": ["苹果"]},
                    ],
                    "research_intents": ["年轻人偏好"],
                    "context_modifiers": [],
                    "synonym_groups": {"Apple 品牌": ["Apple"]},
                    "ambiguities": [],
                    "resolution_state": "resolved",
                },
            },
        ]

    async def generate(self, request):
        self.requests.append(request)
        response = self.responses.pop(0)
        return LLMResponse(
            content=json.dumps(response, ensure_ascii=False),
            provider="fake",
            model="fake-model",
            usage=TokenUsage(total_tokens=10),
            latency_ms=1,
        )


@pytest.fixture()
def store(tmp_path):
    return SQLiteContentResearchStore(str(tmp_path / "content_research.db"))


def _service(store, llm=None, *, first_timeout=0.05, hard_cutoff=0.1, runtime=None):
    return ContentResearchService(
        store=store,
        presearch=PresearchService(
            llm,
            first_feedback_timeout_seconds=first_timeout,
            hard_cutoff_seconds=hard_cutoff,
        ),
        workflow_runtime=runtime or FakeRuntime(),
    )


@pytest.mark.asyncio
async def test_presearch_success_creates_workflow_brief_trace_and_observation(store):
    service = _service(store, FakeLLM())

    response = await service.submit_presearch(
        seed_text="徒步短裤",
        user_note="关注夏季",
        thread_id="thread_1",
        user_id="user_1",
    )

    assert response.workflow_run_id == "run_1"
    assert response.attempt_id.startswith("att_")
    assert response.brief_id.startswith("rb_")
    assert response.status == "completed"
    assert response.fallback_used is False
    assert response.timeout_status == "none"
    assert response.competitor_tags == ["迪卡侬", "凯乐石"]
    assert service._presearch._llm.requests[0].response_format is None
    assert service._presearch._llm.requests[0].temperature == 1.0
    assert service._presearch._llm.requests[0].model_policy == "balanced"
    system_prompt = service._presearch._llm.requests[0].messages[0].content
    assert "只输出一个合法 JSON 对象" in system_prompt
    assert "不要输出 Markdown" in system_prompt
    assert "competitor_tags" in system_prompt
    assert "核心对象只保留可被调研的实体" in system_prompt
    assert "不要把包含意图或场景修饰的完整用户句子直接复制为核心对象" in system_prompt
    assert store.get_brief(response.brief_id).payload["attempt_id"] == response.attempt_id
    traces = store.list_traces_for_workflow(response.workflow_run_id)
    assert len(traces) == 1
    events = store.list_observation_events(traces[0].id)
    assert [event.event_name for event in events] == ["presearch_started", "presearch_completed"]

    with sqlite3.connect(store._db_path) as conn:
        evidence_rows = conn.execute("SELECT COUNT(*) FROM content_research_evidence_records").fetchone()[0]
    assert evidence_rows == 0


@pytest.mark.asyncio
async def test_presearch_returns_backend_validated_subject_structure(store):
    content = json.dumps(
        {
            "subject_confirmation": "夏季防晒穿搭",
            "subject_structure": {
                "schema_version": "content_research_subject_structure_v1",
                "canonical_subject": "防晒服饰",
                "subject_type": "category",
                "core_entities": [
                        {
                            "canonical_name": "防晒服饰",
                            "raw_mentions": ["防晒"],
                    }
                ],
                "research_intents": ["穿搭"],
                "context_modifiers": ["夏季"],
                "synonym_groups": {"防晒服饰": ["防晒衣", "防晒服"]},
                "ambiguities": [],
                "resolution_state": "resolved",
            },
            "competitor_tags": [],
            "research_directions": ["产品营销"],
            "custom_research_question": "",
            "custom_competitor_input": "",
        },
        ensure_ascii=False,
    )
    service = _service(store, FakeLLM(content=content))

    response = await service.submit_presearch(
        seed_text="夏季防晒穿搭",
        user_note=None,
        thread_id="thread_1",
        user_id="user_1",
    )

    assert response.subject_structure_state == "confirmed"
    assert response.subject_structure_reason_codes == []
    assert response.subject_structure["canonical_subject"] == "防晒服饰"
    assert response.subject_structure_hash
    brief = store.get_brief(response.brief_id)
    assert brief is not None
    assert brief.payload["subject_structure_hash"] == response.subject_structure_hash


@pytest.mark.asyncio
async def test_subject_clarification_reuses_run_without_model_recovery_or_spider(store):
    runtime = FakeRuntime()
    llm = AmbiguousThenClarifiedLLM()
    service = _service(store, llm, runtime=runtime)

    first = await service.submit_presearch(
        seed_text="苹果适合年轻人吗",
        user_note=None,
        thread_id="thread_1",
        user_id="user_1",
    )

    assert first.status == "subject_needs_confirmation"
    assert first.subject_structure_state == "needs_confirmation"
    assert runtime.calls[-1]["event"] == "wait_for_subject_clarification"
    assert all(call.get("event") != "wait_for_presearch_recovery" for call in runtime.calls)

    action = await service.run_workflow_action(
        workflow_run_id=first.workflow_run_id,
        request=ContentResearchWorkflowActionRequest(
            action="clarify_subject",
            payload={"clarification_text": "这里的苹果是 Apple 品牌，不是水果。"},
        ),
    )

    assert action.workflow_run_id == first.workflow_run_id
    assert action.result["attempt_id"] == first.attempt_id
    assert action.result["brief_id"] == first.brief_id
    assert action.result["status"] == "completed"
    assert action.result["subject_structure_state"] == "confirmed"
    assert action.result["subject_structure_hash"] != first.subject_structure_hash
    assert runtime.calls[-2]["event"] == "resume_subject_clarification"
    assert runtime.calls[-1]["event"] == "presearch_ready"
    assert all(call.get("event") != "restart_presearch_step" for call in runtime.calls)
    assert all(call.get("event") != "wait_for_presearch_recovery" for call in runtime.calls)
    assert len(llm.requests) == 2
    assert "Apple 品牌" in llm.requests[1].messages[-1].content

    with pytest.raises(ContentResearchValidationError, match="stale subject structure"):
        await service.confirm_brief(
            brief_id=first.brief_id,
            confirmation_request=ContentResearchBriefConfirmRequest(
                confirmed_subject="Apple 品牌",
                subject_structure_hash=str(first.subject_structure_hash),
                subject_type="brand",
                selected_competitors=[],
                    custom_competitors=[],
                    selected_directions=["product_marketing"],
                ),
        )

    checkpoints = [
        item
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.workflow_run_id == first.workflow_run_id
        and item.stage_name == "subject_structure"
    ]
    assert len(checkpoints) == 2
    assert [item.payload["state"] for item in checkpoints] == [
        "needs_confirmation",
        "confirmed",
    ]
    assert all(item.payload.get("raw_input") is None for item in checkpoints)
    assert all(item.stage_name != "operation" for item in store.list_typed_records(StageCheckpointRecord))


@pytest.mark.asyncio
async def test_structured_subject_confirmation_atomically_updates_real_runtime(store):
    runtime = WorkflowRunManagerRuntime(store._db_path)
    llm = AmbiguousThenClarifiedLLM()
    service = _service(store, llm, runtime=runtime)

    first = await service.submit_presearch(
        seed_text="苹果适合年轻人吗",
        user_note=None,
        thread_id="thread_structured_confirmation",
        user_id="user_1",
    )

    action = await service.run_workflow_action(
        workflow_run_id=first.workflow_run_id,
        request=ContentResearchWorkflowActionRequest(
            action="confirm_subject_structure",
            payload={
                "subject_structure_hash": first.subject_structure_hash,
                "core_object": "Apple 品牌",
                "research_intent": "年轻人偏好",
                "context_modifiers": "大学生，日常使用",
            },
        ),
    )

    result = action.result
    assert result["status"] == "completed"
    assert result["subject_structure_state"] == "confirmed"
    assert result["subject_structure"]["core_entities"] == [
        {"canonical_name": "Apple 品牌", "raw_mentions": ["Apple 品牌"]}
    ]
    assert result["subject_structure"]["research_intents"] == ["年轻人偏好"]
    assert result["subject_structure"]["context_modifiers"] == ["大学生", "日常使用"]
    persisted_brief = store.get_brief(first.brief_id)
    assert persisted_brief is not None
    assert persisted_brief.payload["subject_structure_user_confirmed_fields"] == [
        "core_entities[0]",
        "research_intents[0]",
        "context_modifiers",
    ]
    assert len(llm.requests) == 1

    confirmed_checkpoints = [
        item
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.workflow_run_id == first.workflow_run_id
        and item.stage_name == "subject_structure"
        and item.payload.get("state") == "confirmed"
    ]
    assert len(confirmed_checkpoints) == 1
    snapshot = await runtime.get_runtime_snapshot(first.workflow_run_id)
    presearch_step = next(step for step in snapshot["steps"] if step["step_name"] == "presearch")
    assert snapshot["run"]["status"] == "running"
    assert snapshot["run"]["current_step"] == "brief_confirm"
    assert presearch_step["status"] == "succeeded"

    with pytest.raises(ContentResearchValidationError, match="stale subject structure"):
        await service.run_workflow_action(
            workflow_run_id=first.workflow_run_id,
            request=ContentResearchWorkflowActionRequest(
                action="confirm_subject_structure",
                payload={
                    "subject_structure_hash": first.subject_structure_hash,
                    "core_object": "Apple 品牌",
                    "research_intent": "年轻人偏好",
                },
            ),
        )


@pytest.mark.asyncio
async def test_subject_confirmation_conflict_rolls_back_brief_checkpoint_and_runtime(store, monkeypatch):
    runtime = WorkflowRunManagerRuntime(store._db_path)
    service = _service(store, AmbiguousThenClarifiedLLM(), runtime=runtime)
    first = await service.submit_presearch(
        seed_text="苹果适合年轻人吗",
        user_note=None,
        thread_id="thread_subject_confirmation_conflict",
        user_id="user_1",
    )

    brief_before = store.get_brief(first.brief_id)
    checkpoints_before = store.list_typed_records(StageCheckpointRecord)
    snapshot_before = await runtime.get_runtime_snapshot(first.workflow_run_id)

    async def locked_confirmation_writer(conn, *, brief, checkpoint):
        await conn.execute(
            "UPDATE content_research_briefs SET status='draft' WHERE id=?",
            (brief.id,),
        )
        await conn.execute(
            """INSERT INTO content_research_stage_checkpoints
               (id, schema_version, workflow_run_id, subagent_task_id, stage_name,
                input_fingerprint, status, retry_count, payload_json, metadata_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'completed', 0, ?, '{}', CURRENT_TIMESTAMP)""",
            (
                "scp_lock_conflict",
                checkpoint.schema_version,
                checkpoint.workflow_run_id,
                checkpoint.subagent_task_id,
                checkpoint.stage_name,
                checkpoint.input_fingerprint,
                json.dumps(checkpoint.payload, ensure_ascii=False),
            ),
        )
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(
        service._dispatch,
        "persist_subject_structure_confirmation",
        locked_confirmation_writer,
        raising=False,
    )

    with pytest.raises(ContentResearchStateConflictError) as exc_info:
        await service.confirm_subject_structure(
            workflow_run_id=first.workflow_run_id,
            confirmation=ContentResearchSubjectStructureConfirmationRequest(
                subject_structure_hash=first.subject_structure_hash,
                core_object="Apple 品牌",
                research_intent="年轻人偏好",
                context_modifiers="大学生，日常使用",
            ),
        )

    assert exc_info.value.error_code == "CONTENT_RESEARCH_SUBJECT_CONFIRMATION_CONFLICT"
    assert store.get_brief(first.brief_id) == brief_before
    assert store.list_typed_records(StageCheckpointRecord) == checkpoints_before
    assert await runtime.get_runtime_snapshot(first.workflow_run_id) == snapshot_before


@pytest.mark.asyncio
async def test_subject_clarification_does_not_increment_real_runtime_attempt_count(store):
    runtime = WorkflowRunManagerRuntime(store._db_path)
    service = _service(store, AmbiguousThenClarifiedLLM(), runtime=runtime)

    first = await service.submit_presearch(
        seed_text="苹果适合年轻人吗",
        user_note=None,
        thread_id="thread_real_runtime_clarification",
        user_id="user_1",
    )
    waiting = await runtime.get_runtime_snapshot(first.workflow_run_id)
    waiting_step = next(
        step for step in waiting["steps"] if step["step_name"] == "presearch"
    )
    assert waiting["run"]["status"] == "waiting_user"
    assert waiting_step["status"] == "retrying"
    assert waiting_step["attempt_count"] == 0

    await service.run_workflow_action(
        workflow_run_id=first.workflow_run_id,
        request=ContentResearchWorkflowActionRequest(
            action="clarify_subject",
            payload={"clarification_text": "这里的苹果是 Apple 品牌，不是水果。"},
        ),
    )

    completed = await runtime.get_runtime_snapshot(first.workflow_run_id)
    completed_step = next(
        step for step in completed["steps"] if step["step_name"] == "presearch"
    )
    assert completed["run"]["status"] == "running"
    assert completed_step["status"] == "succeeded"
    assert completed_step["attempt_count"] == 0


@pytest.mark.asyncio
async def test_presearch_llm_failure_waits_for_model_configuration(store):
    service = _service(store, FakeLLM(fail=True))

    response = await service.submit_presearch(
        seed_text="Satisfy Running",
        user_note=None,
        thread_id="thread_1",
        user_id="user_1",
    )

    assert response.status == "waiting_model_config"
    assert response.fallback_used is False
    assert response.error_code == "llm_service_unavailable"


@pytest.mark.asyncio
async def test_presearch_malformed_llm_response_waits_for_configuration(store):
    llm = FakeLLM(content="not json")
    service = _service(store, llm)

    response = await service.submit_presearch(
        seed_text="露营灯",
        user_note=None,
        thread_id="thread_1",
        user_id="user_1",
    )

    assert response.status == "waiting_model_config"
    assert response.fallback_used is False
    assert response.error_code == "llm_structured_output_invalid"
    assert len(llm.requests) == 1


class FailingLLM:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def generate(self, _request):
        raise self.error


class FailOnceThenSucceedLLM(FakeLLM):
    def __init__(self) -> None:
        super().__init__()
        self.call_count = 0

    async def generate(self, request):
        self.call_count += 1
        if self.call_count == 1:
            raise LLMProviderFailure("llm_auth_invalid", "API Key 无效", True, 401,
                provider="openai_compatible", model="model-x", configuration_source="user")
        return await super().generate(request)


class DelayedProviderFailureLLM:
    async def generate(self, _request):
        await asyncio.sleep(0.02)
        raise LLMProviderFailure(
            "llm_auth_invalid",
            "API Key 无效",
            True,
            401,
            provider="openai_compatible",
            model="model-x",
            configuration_source="user",
        )


class FailThenDelayedProviderFailureLLM:
    def __init__(self) -> None:
        self.call_count = 0

    async def generate(self, _request):
        self.call_count += 1
        if self.call_count == 1:
            raise LLMProviderFailure(
                "llm_auth_invalid", "API Key 无效", True, 401,
                provider="openai_compatible", model="model-x", configuration_source="user",
            )
        await asyncio.sleep(0.02)
        raise LLMProviderFailure(
            "llm_auth_invalid", "API Key 无效", True, 401,
            provider="openai_compatible", model="model-x", configuration_source="user",
        )


class ControlledSuccessLLM(FakeLLM):
    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    async def generate(self, request):
        await self.release.wait()
        return await super().generate(request)


class BlockingReadyRuntime(WorkflowRunManagerRuntime):
    def __init__(self, db_path: str) -> None:
        super().__init__(db_path)
        self.ready_transition_started = asyncio.Event()
        self.allow_ready_transition = asyncio.Event()

    async def mark_presearch_ready(self, workflow_run_id: str) -> None:
        self.ready_transition_started.set()
        await self.allow_ready_transition.wait()
        await super().mark_presearch_ready(workflow_run_id)


@pytest.mark.asyncio
async def test_provider_failure_waits_for_configuration_without_completing_presearch(store):
    runtime = FakeRuntime()
    service = _service(store, FailingLLM(
        LLMProviderFailure("llm_account_unavailable", "模型账户不可用", True, 402)
    ), runtime=runtime)

    response = await service.submit_presearch(
        seed_text="夏季通勤短裤", user_note=None, thread_id="thread_1", workspace_id="ws_1", user_id="user_1",
    )

    assert response.status == "waiting_model_config"
    assert response.error_code == "llm_account_unavailable"
    assert runtime.calls[-1]["event"] == "wait_for_presearch_recovery"
    assert all(call.get("event") != "presearch_ready" for call in runtime.calls)


@pytest.mark.asyncio
async def test_submit_presearch_propagates_real_workspace_scope_to_llm(store):
    llm = FakeLLM()
    service = _service(store, llm)

    await service.submit_presearch(
        seed_text="夏季通勤短裤",
        user_note=None,
        thread_id="thread_1",
        workspace_id="workspace_from_router",
        user_id="user_1",
    )

    assert llm.requests[0].context is not None
    assert llm.requests[0].context.tenant_id == "workspace_from_router"
    assert llm.requests[0].context.user_id == "user_1"


@pytest.mark.asyncio
async def test_retry_presearch_reuses_attempt_brief_and_run(store):
    runtime = FakeRuntime()
    service = _service(store, FailOnceThenSucceedLLM(), runtime=runtime)
    first = await service.submit_presearch(
        seed_text="夏季通勤短裤", user_note=None, thread_id="thread_1", workspace_id="ws_1", user_id="user_1",
    )
    retried = await service.retry_presearch(first.workflow_run_id)

    assert retried.attempt_id == first.attempt_id
    assert retried.brief_id == first.brief_id
    assert retried.workflow_run_id == first.workflow_run_id
    assert retried.status == "completed"


@pytest.mark.asyncio
async def test_presearch_first_timeout_returns_fallback_then_hard_cutoff_updates_attempt(store):
    service = _service(store, FakeLLM(delay=0.2), first_timeout=0.01, hard_cutoff=0.03)

    response = await service.submit_presearch(
        seed_text="越野跑背心",
        user_note=None,
        thread_id="thread_1",
        user_id="user_1",
    )

    assert response.status == "fallback"
    assert response.timeout_status == "first_timeout"
    assert response.fallback_used is True

    await asyncio.sleep(0.06)
    final = service.get_presearch(response.attempt_id)
    assert final.status == "final_timeout"
    assert final.timeout_status == "final_timeout"

    trace = store.list_traces_for_workflow(response.workflow_run_id)[0]
    event_names = [event.event_name for event in store.list_observation_events(trace.id)]
    assert event_names == ["presearch_started", "presearch_first_timeout", "presearch_final_timeout"]


@pytest.mark.asyncio
async def test_late_provider_failure_after_first_timeout_atomically_waits_for_user(store):
    service = _service(
        store,
        DelayedProviderFailureLLM(),
        first_timeout=0.005,
        hard_cutoff=0.1,
        runtime=WorkflowRunManagerRuntime(store._db_path),
    )

    first = await service.submit_presearch(
        seed_text="夏季通勤短裤",
        user_note=None,
        thread_id="thread_late_failure",
        workspace_id="ws_1",
        user_id="user_1",
    )
    assert first.status == "fallback"
    assert first.timeout_status == "first_timeout"

    for _ in range(20):
        await asyncio.sleep(0.01)
        settled = service.get_presearch(first.attempt_id)
        snapshot = await service._workflow_runtime.get_runtime_snapshot(first.workflow_run_id)
        if settled.status == "waiting_model_config" and snapshot["run"]["status"] == "waiting_user":
            break
    else:
        pytest.fail("late provider failure did not converge to waiting_user")

    assert settled.error_code == "llm_auth_invalid"
    assert snapshot["steps"][0]["status"] == "retrying"


@pytest.mark.asyncio
async def test_retry_first_timeout_keeps_task_until_late_auth_failure_waits_for_user(store):
    llm = FailThenDelayedProviderFailureLLM()
    service = _service(
        store,
        llm,
        first_timeout=0.005,
        hard_cutoff=0.1,
        runtime=WorkflowRunManagerRuntime(store._db_path),
    )
    first = await service.submit_presearch(
        seed_text="夏季通勤短裤",
        user_note=None,
        thread_id="thread_retry_late_failure",
        workspace_id="ws_1",
        user_id="user_1",
    )
    assert first.status == "waiting_model_config"

    retried = await service.retry_presearch(first.workflow_run_id)
    assert retried.status == "fallback"
    assert retried.timeout_status == "first_timeout"

    for _ in range(20):
        await asyncio.sleep(0.01)
        settled = service.get_presearch(first.attempt_id)
        snapshot = await service._workflow_runtime.get_runtime_snapshot(first.workflow_run_id)
        if settled.status == "waiting_model_config" and snapshot["run"]["status"] == "waiting_user":
            break
    else:
        pytest.fail("retry late provider failure did not converge to waiting_user")

    assert llm.call_count == 2
    assert settled.error_code == "llm_auth_invalid"
    assert snapshot["steps"][0]["status"] == "retrying"
    assert not any(step["status"] == "succeeded" for step in snapshot["steps"] if step["step_name"] == "presearch")


@pytest.mark.asyncio
async def test_confirmation_waits_for_brief_and_runtime_presearch_to_settle(store):
    llm = ControlledSuccessLLM()
    runtime = BlockingReadyRuntime(store._db_path)
    service = _service(
        store,
        llm,
        first_timeout=0.005,
        hard_cutoff=0.1,
        runtime=runtime,
    )
    first = await service.submit_presearch(
        seed_text="夏季通勤短裤",
        user_note=None,
        thread_id="thread_confirmation_race",
        workspace_id="ws_1",
        user_id="user_1",
    )
    assert first.timeout_status == "first_timeout"

    confirmation = ContentResearchBriefConfirmRequest(
        confirmed_subject="夏季通勤短裤",
        subject_type="category",
        selected_competitors=[],
        custom_competitors=[],
        selected_directions=["product_marketing"],
    )
    with pytest.raises(ContentResearchValidationError, match="Presearch final outcome is not ready"):
        await service.confirm_brief(brief_id=first.brief_id, confirmation_request=confirmation)

    llm.release.set()
    await asyncio.wait_for(runtime.ready_transition_started.wait(), timeout=0.1)
    assert service.get_presearch(first.attempt_id).status == "completed"
    snapshot = await runtime.get_runtime_snapshot(first.workflow_run_id)
    presearch_step = next(step for step in snapshot["steps"] if step["step_name"] == "presearch")
    assert presearch_step["status"] == "running"
    with pytest.raises(ContentResearchValidationError, match="Presearch final outcome is not ready"):
        await service.confirm_brief(brief_id=first.brief_id, confirmation_request=confirmation)

    runtime.allow_ready_transition.set()
    for _ in range(20):
        await asyncio.sleep(0.005)
        snapshot = await runtime.get_runtime_snapshot(first.workflow_run_id)
        presearch_step = next(step for step in snapshot["steps"] if step["step_name"] == "presearch")
        if presearch_step["status"] == "succeeded":
            break
    else:
        pytest.fail("presearch runtime transition did not settle")

    summary = await service.confirm_brief(brief_id=first.brief_id, confirmation_request=confirmation)
    assert summary.brief.status == "ready"
    with sqlite3.connect(store._db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM content_research_dispatch_jobs").fetchone()[0] == 0

    dispatched = await service.run_workflow_action(
        workflow_run_id=first.workflow_run_id,
        request=ContentResearchWorkflowActionRequest(
            action="start_formal_research",
            payload={"provider": "xiaohongshu", "source_kind": "search_result", "limit": 20},
        ),
    )
    assert dispatched.status == "queued"
    with sqlite3.connect(store._db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM content_research_dispatch_jobs").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_start_formal_research_rejects_unconfirmed_first_timeout_without_dispatch(store):
    service = _service(
        store,
        FakeLLM(delay=0.2),
        first_timeout=0.005,
        hard_cutoff=0.1,
        runtime=WorkflowRunManagerRuntime(store._db_path),
    )
    first = await service.submit_presearch(
        seed_text="夏季通勤短裤",
        user_note=None,
        thread_id="thread_no_early_dispatch",
        workspace_id="ws_1",
        user_id="user_1",
    )
    assert first.timeout_status == "first_timeout"

    with pytest.raises(ContentResearchValidationError, match="Formal research is not ready to dispatch"):
        await service.run_workflow_action(
            workflow_run_id=first.workflow_run_id,
            request=ContentResearchWorkflowActionRequest(
                action="start_formal_research",
                payload={"provider": "xiaohongshu", "source_kind": "search_result", "limit": 20},
            ),
        )

    with sqlite3.connect(store._db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM content_research_dispatch_jobs").fetchone()[0] == 0
