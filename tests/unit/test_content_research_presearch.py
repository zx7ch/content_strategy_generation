from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from app.content_research.api_schemas import ContentResearchWorkflowActionRequest
from app.content_research.lifecycle.coordinator import LifecyclePersistenceBusy
from app.content_research.lifecycle.models import ContentResearchState, LifecycleCommand
from app.content_research.presearch.service import PresearchService
from app.content_research.service import ContentResearchService, WorkflowRunManagerRuntime
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.thread_store import ThreadStore
from app.services.llm.types import LLMResponse, TokenUsage


def _presearch_payload(*, resolution_state: str = "resolved") -> str:
    ambiguities = ["苹果指 Apple 品牌还是水果"] if resolution_state != "resolved" else []
    return json.dumps(
        {
            "subject_confirmation": "已识别本轮需要调研的对象与方向。",
            "competitor_tags": ["迪卡侬", "凯乐石"],
            "research_directions": ["产品营销"],
            "custom_competitor_input": "",
            "subject_structure": {
                "schema_version": "content_research_subject_structure_v1",
                "canonical_subject": "夏季凉感T恤",
                "subject_type": "category",
                "core_entities": [
                    {
                        "canonical_name": "凉感T恤",
                        "raw_mentions": ["夏季凉感T恤"],
                    }
                ],
                "research_intents": ["凉感"],
                "context_modifiers": ["夏季"],
                "synonym_groups": {"凉感T恤": ["冰感T恤"]},
                "ambiguities": ambiguities,
                "resolution_state": resolution_state,
            },
        },
        ensure_ascii=False,
    )


class RecordingLLM:
    def __init__(self, *, content: str | None = None, error: Exception | None = None) -> None:
        self.content = content or _presearch_payload()
        self.error = error
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return LLMResponse(
            content=self.content,
            provider="fake",
            model="fake-model",
            usage=TokenUsage(total_tokens=10),
            latency_ms=1,
        )


class SequenceLLM(RecordingLLM):
    def __init__(self, contents: list[str]) -> None:
        super().__init__(content=contents[0])
        self.contents = list(contents)

    async def generate(self, request):
        self.requests.append(request)
        return LLMResponse(
            content=self.contents.pop(0),
            provider="fake",
            model="fake-model",
            usage=TokenUsage(total_tokens=10),
            latency_ms=1,
        )


class LockingOutcomeLLM(RecordingLLM):
    def __init__(self) -> None:
        super().__init__()
        self.db_path: str | None = None

    async def generate(self, request):
        response = await super().generate(request)
        assert self.db_path is not None
        blocker = sqlite3.connect(self.db_path, timeout=0)
        blocker.execute("BEGIN IMMEDIATE")

        async def release() -> None:
            await asyncio.sleep(2.5)
            blocker.commit()
            blocker.close()

        asyncio.create_task(release())
        return response


async def _owned_service(tmp_path, llm: RecordingLLM):
    db_path = str(tmp_path / "content-research.db")
    async with ThreadStore(db_path) as thread_store:
        thread = await thread_store.create_thread(
            title="夏季凉感T恤",
            workspace_id="ws-test",
            brand_id="brand-test",
        )
    return (
        ContentResearchService(
            store=SQLiteContentResearchStore(db_path),
            presearch=PresearchService(
                llm,
                first_feedback_timeout_seconds=0.05,
                hard_cutoff_seconds=0.1,
            ),
            workflow_runtime=WorkflowRunManagerRuntime(db_path),
        ),
        db_path,
        thread,
    )


@pytest.mark.asyncio
async def test_presearch_success_uses_the_owned_lifecycle_and_returns_only_brief(tmp_path):
    llm = RecordingLLM()
    service, db_path, thread = await _owned_service(tmp_path, llm)

    response = await service.submit_presearch(
        command_id="submit-presearch-success",
        seed_text="夏季凉感T恤",
        user_note="关注通勤场景",
        thread_id=thread["id"],
        user_id="user-test",
        workspace_id="ws-test",
    )

    assert response.status == "completed"
    assert response.run.state == "brief_confirmation_required"
    assert response.run.state_revision == 2
    assert response.run.brief_id == response.brief_id
    assert response.subject_structure["core_entities"][0]["canonical_name"] == "凉感T恤"
    assert llm.requests[0].temperature == 1.0
    assert llm.requests[0].model_policy == "balanced"
    assert "只输出一个合法 JSON 对象" in llm.requests[0].messages[0].content

    with sqlite3.connect(db_path) as connection:
        run = connection.execute(
            "SELECT content_research_state, state_revision, status FROM workflow_runs WHERE run_id=?",
            (response.workflow_run_id,),
        ).fetchone()
        brief_status = connection.execute(
            "SELECT status FROM content_research_briefs WHERE id=?",
            (response.brief_id,),
        ).fetchone()[0]
        active_run_id = connection.execute(
            "SELECT active_run_id FROM creator_threads WHERE id=?",
            (thread["id"],),
        ).fetchone()[0]
        assert connection.execute(
            "SELECT COUNT(*) FROM content_research_scope_contracts WHERE workflow_run_id=?",
            (response.workflow_run_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM content_research_dispatch_jobs WHERE workflow_run_id=?",
            (response.workflow_run_id,),
        ).fetchone()[0] == 0

    assert run == ("brief_confirmation_required", 2, "waiting_user")
    assert brief_status == "draft"
    assert active_run_id == response.workflow_run_id


@pytest.mark.asyncio
async def test_ambiguous_model_interpretation_does_not_create_a_user_confirmation_stage(
    tmp_path,
):
    ambiguous = json.loads(_presearch_payload(resolution_state="needs_confirmation"))
    ambiguous["subject_structure"].update(
        {
            "canonical_subject": "苹果",
            "core_entities": [
                {"canonical_name": "苹果", "raw_mentions": ["苹果"]}
            ],
            "research_intents": ["适合"],
            "context_modifiers": ["年轻人"],
            "synonym_groups": {},
        }
    )
    llm = RecordingLLM(content=json.dumps(ambiguous, ensure_ascii=False))
    service, _db_path, thread = await _owned_service(tmp_path, llm)

    response = await service.submit_presearch(
        command_id="submit-presearch-ambiguous",
        seed_text="苹果适合年轻人吗",
        user_note=None,
        thread_id=thread["id"],
        user_id="user-test",
        workspace_id="ws-test",
    )

    assert response.status == "completed"
    assert response.run.state == "brief_confirmation_required"
    assert "confirm_subject_structure" not in response.run.allowed_actions
    assert response.subject_structure["resolution_state"] == "needs_confirmation"
    assert response.subject_structure_analysis_state == "needs_confirmation"
    assert response.subject_structure_analysis_reason_codes == (
        "unresolved_ambiguity",
    )


@pytest.mark.asyncio
async def test_complete_input_core_keeps_its_analysis_diagnosis_without_old_stage(
    tmp_path,
):
    invalid = json.loads(_presearch_payload())
    invalid["subject_structure"]["core_entities"] = [
        {
            "canonical_name": "夏季凉感T恤",
            "raw_mentions": ["夏季凉感T恤"],
        }
    ]
    invalid["subject_structure"]["synonym_groups"] = {
        "夏季凉感T恤": ["冰感T恤"]
    }
    llm = RecordingLLM(content=json.dumps(invalid, ensure_ascii=False))
    service, _db_path, thread = await _owned_service(tmp_path, llm)

    response = await service.submit_presearch(
        command_id="submit-complete-input-core",
        seed_text="夏季凉感T恤",
        user_note=None,
        thread_id=thread["id"],
        user_id="user-test",
        workspace_id="ws-test",
    )

    assert response.status == "completed"
    assert response.run.state == "brief_confirmation_required"
    assert response.subject_structure_analysis_state == "needs_confirmation"
    assert response.subject_structure_analysis_reason_codes == (
        "core_entity_is_complete_input",
    )
    assert "confirm_subject_structure" not in response.run.allowed_actions


@pytest.mark.asyncio
async def test_invalid_system_structure_gets_one_reason_directed_repair_attempt(tmp_path):
    invalid = json.loads(_presearch_payload())
    invalid["subject_structure"]["core_entities"] = [
        {
            "canonical_name": "夏季凉感T恤",
            "raw_mentions": ["夏季凉感T恤"],
        }
    ]
    invalid["subject_structure"]["synonym_groups"] = {
        "夏季凉感T恤": ["冰感T恤"]
    }
    repaired = json.loads(_presearch_payload())
    repaired["subject_structure"]["core_entities"] = [
        {"canonical_name": "T恤", "raw_mentions": ["T恤"]}
    ]
    repaired["subject_structure"]["synonym_groups"] = {"T恤": ["短袖"]}
    llm = SequenceLLM(
        [
            json.dumps(invalid, ensure_ascii=False),
            json.dumps(repaired, ensure_ascii=False),
        ]
    )
    service, _db_path, thread = await _owned_service(tmp_path, llm)

    response = await service.submit_presearch(
        command_id="submit-repaired-structure",
        seed_text="夏季凉感T恤",
        user_note=None,
        thread_id=thread["id"],
        user_id="user-test",
        workspace_id="ws-test",
    )

    assert len(llm.requests) == 2
    assert "core_entity_is_complete_input" in llm.requests[1].messages[-1].content
    assert response.subject_structure_analysis_state == "confirmed"
    assert response.subject_structure_analysis_reason_codes == ()
    assert response.subject_structure["core_entities"][0]["canonical_name"] == "T恤"


@pytest.mark.asyncio
async def test_llm_failure_converges_run_and_trace_to_recovery_required(tmp_path):
    llm = RecordingLLM(error=RuntimeError("provider secret detail"))
    service, db_path, thread = await _owned_service(tmp_path, llm)

    response = await service.submit_presearch(
        command_id="submit-presearch-llm-failure",
        seed_text="夏季凉感T恤",
        user_note=None,
        thread_id=thread["id"],
        user_id="user-test",
        workspace_id="ws-test",
    )
    trace = await service.get_workflow_trace(response.workflow_run_id)

    assert response.status == "waiting_model_config"
    assert response.run.state == "recovery_required"
    assert response.run.state_revision == 2
    assert response.run.reason_code == "llm_service_unavailable"
    assert response.run.allowed_actions == ["retry_presearch", "cancel"]
    assert trace.state == "recovery_required"
    assert trace.state_revision == 2
    assert trace.current_stage == "presearch"
    assert trace.run_status == "waiting_user"
    assert [item["event"] for item in trace.state_transitions] == [
        "submit_research_subject",
        "fail",
    ]
    assert "provider secret detail" not in json.dumps(
        trace.model_dump(mode="json"), ensure_ascii=False
    )

    with sqlite3.connect(db_path) as connection:
        run = connection.execute(
            "SELECT content_research_state, status, error_code FROM workflow_runs WHERE run_id=?",
            (response.workflow_run_id,),
        ).fetchone()
    assert run == ("recovery_required", "waiting_user", "llm_service_unavailable")


@pytest.mark.asyncio
async def test_malformed_model_output_is_a_recoverable_lifecycle_failure(tmp_path):
    service, _db_path, thread = await _owned_service(
        tmp_path,
        RecordingLLM(content="not json"),
    )

    response = await service.submit_presearch(
        command_id="submit-presearch-malformed",
        seed_text="露营灯",
        user_note=None,
        thread_id=thread["id"],
        user_id="user-test",
        workspace_id="ws-test",
    )

    assert response.error_code == "llm_structured_output_invalid"
    assert response.run.state == "recovery_required"
    assert response.run.error is not None
    assert response.run.error["stage"] == "presearch"


@pytest.mark.asyncio
async def test_user_revision_reuses_run_and_brief_with_monotonic_revision(tmp_path):
    llm = RecordingLLM()
    service, _db_path, thread = await _owned_service(tmp_path, llm)
    first = await service.submit_presearch(
        command_id="submit-presearch-before-revision",
        seed_text="夏季凉感T恤",
        user_note=None,
        thread_id=thread["id"],
        user_id="user-test",
        workspace_id="ws-test",
    )

    revised = await service.run_workflow_action(
        workflow_run_id=first.workflow_run_id,
        request=ContentResearchWorkflowActionRequest(
            command_id="revise-subject-once",
            expected_state="brief_confirmation_required",
            expected_revision=2,
            action="revise_subject",
            payload={"clarification_text": "这里重点是通勤场景，不是户外运动。"},
        ),
    )

    result = revised.result
    assert result["workflow_run_id"] == first.workflow_run_id
    assert result["brief_id"] == first.brief_id
    assert result["run"]["state"] == "brief_confirmation_required"
    assert result["run"]["state_revision"] == 4
    assert "通勤场景" in llm.requests[-1].messages[-1].content
    brief = service._store.get_brief(first.brief_id)
    assert brief is not None
    assert brief.payload["subject_clarifications"] == [
        "这里重点是通勤场景，不是户外运动。"
    ]
    transitions = await service._lifecycle.list_transitions(first.workflow_run_id)
    assert [item["event"] for item in transitions] == [
        "submit_research_subject",
        "presearch_completed",
        "revise_subject",
        "presearch_completed",
    ]


@pytest.mark.asyncio
async def test_presearch_retry_recovers_the_same_run_after_model_repair(tmp_path):
    llm = RecordingLLM(error=RuntimeError("temporary provider failure"))
    service, _db_path, thread = await _owned_service(tmp_path, llm)
    failed = await service.submit_presearch(
        command_id="submit-presearch-before-retry",
        seed_text="夏季凉感T恤",
        user_note=None,
        thread_id=thread["id"],
        user_id="user-test",
        workspace_id="ws-test",
    )
    llm.error = None

    recovered = await service.retry_presearch(
        failed.workflow_run_id,
        command_id="retry-presearch-once",
        expected_state=ContentResearchState.RECOVERY_REQUIRED,
        expected_revision=2,
    )

    assert recovered.workflow_run_id == failed.workflow_run_id
    assert recovered.brief_id == failed.brief_id
    assert recovered.run.state == "brief_confirmation_required"
    assert recovered.run.state_revision == 4
    assert recovered.run.reason_code is None


@pytest.mark.asyncio
async def test_duplicate_submit_command_returns_one_run_and_one_bounded_repair(tmp_path):
    llm = RecordingLLM()
    service, db_path, thread = await _owned_service(tmp_path, llm)

    request = dict(
        command_id="stable-browser-submit-command",
        seed_text="夏季凉感T恤",
        user_note=None,
        thread_id=thread["id"],
        user_id="user-test",
        workspace_id="ws-test",
    )
    first, duplicate = await __import__("asyncio").gather(
        service.submit_presearch(**request),
        service.submit_presearch(**request),
    )

    assert first.workflow_run_id == duplicate.workflow_run_id
    assert first.brief_id == duplicate.brief_id
    assert first.run.state_revision == duplicate.run.state_revision == 2
    assert len(llm.requests) == 2
    assert llm.requests[1].task_type == "content_research.presearch.repair"
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM content_research_briefs"
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_stale_public_action_is_rejected_before_presearch_or_brief_changes(tmp_path):
    llm = RecordingLLM()
    service, db_path, thread = await _owned_service(tmp_path, llm)
    first = await service.submit_presearch(
        command_id="submit-before-stale-action",
        seed_text="夏季凉感T恤",
        user_note=None,
        thread_id=thread["id"],
        user_id="user-test",
        workspace_id="ws-test",
    )

    with pytest.raises(Exception, match="revision"):
        await service.run_workflow_action(
            workflow_run_id=first.workflow_run_id,
            request=ContentResearchWorkflowActionRequest(
                command_id="stale-revise",
                expected_state="brief_confirmation_required",
                expected_revision=1,
                action="revise_subject",
                payload={"clarification_text": "关注通勤"},
            ),
        )

    initial_request_count = len(llm.requests)
    assert initial_request_count == 2
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT state_revision FROM workflow_runs WHERE run_id=?",
            (first.workflow_run_id,),
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM content_research_state_transitions WHERE run_id=?",
            (first.workflow_run_id,),
        ).fetchone()[0] == 2

    with pytest.raises(Exception, match="expected_state"):
        await service.run_workflow_action(
            workflow_run_id=first.workflow_run_id,
            request=ContentResearchWorkflowActionRequest(
                command_id="wrong-state-revise",
                expected_state="presearch_running",
                expected_revision=2,
                action="revise_subject",
                payload={"clarification_text": "关注通勤"},
            ),
        )
    assert len(llm.requests) == initial_request_count


@pytest.mark.asyncio
async def test_presearch_outcome_persistence_exhaustion_converges_to_recovery(tmp_path, monkeypatch):
    llm = RecordingLLM()
    service, _db_path, thread = await _owned_service(tmp_path, llm)
    original_apply = service._lifecycle.apply
    injected = False

    async def fail_first_outcome(command):
        nonlocal injected
        if command.kind == "presearch_completed" and not injected:
            injected = True
            raise LifecyclePersistenceBusy("LOCAL_PERSISTENCE_BUSY after 3 attempts")
        return await original_apply(command)

    monkeypatch.setattr(service._lifecycle, "apply", fail_first_outcome)
    response = await service.submit_presearch(
        command_id="submit-with-persistence-contention",
        seed_text="夏季凉感T恤",
        user_note=None,
        thread_id=thread["id"],
        user_id="user-test",
        workspace_id="ws-test",
    )
    trace = await service.get_workflow_trace(response.workflow_run_id)

    assert response.run.state == "recovery_required"
    assert response.run.reason_code == "LOCAL_PERSISTENCE_BUSY"
    assert response.run.error["automatic_attempts"] == 3
    assert trace.run_status == "waiting_user"
    assert trace.current_stage == "presearch"


@pytest.mark.asyncio
async def test_busy_outcome_and_busy_authority_read_still_schedule_reconciliation(tmp_path, monkeypatch):
    service, _db_path, thread = await _owned_service(tmp_path, RecordingLLM())
    original_apply = service._lifecycle.apply
    original_load = service._lifecycle.load
    outcome_failed = False
    read_failed = False

    async def fail_outcome_once(command):
        nonlocal outcome_failed
        if command.kind == "presearch_completed" and not outcome_failed:
            outcome_failed = True
            raise LifecyclePersistenceBusy("LOCAL_PERSISTENCE_BUSY after 3 attempts")
        return await original_apply(command)

    async def fail_authority_read_once(run_id):
        nonlocal read_failed
        if not read_failed:
            read_failed = True
            raise LifecyclePersistenceBusy("LOCAL_PERSISTENCE_BUSY after 3 attempts")
        return await original_load(run_id)

    monkeypatch.setattr(service._lifecycle, "apply", fail_outcome_once)
    monkeypatch.setattr(service._lifecycle, "load", fail_authority_read_once)

    with pytest.raises(LifecyclePersistenceBusy):
        await service.submit_presearch(
            command_id="submit-with-busy-authority-read",
            seed_text="夏季凉感T恤",
            user_note=None,
            thread_id=thread["id"],
            user_id="user-test",
            workspace_id="ws-test",
        )

    tasks = list(service._lifecycle_reconciliation_tasks)
    assert len(tasks) == 1
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=3)
    run_id = service._stable_id("run", "submit-with-busy-authority-read")
    projection = await original_load(run_id)
    assert projection.state is ContentResearchState.RECOVERY_REQUIRED
    assert projection.reason_code == "LOCAL_PERSISTENCE_BUSY"


@pytest.mark.asyncio
async def test_busy_beyond_request_retry_budgets_converges_after_lock_release(tmp_path):
    llm = LockingOutcomeLLM()
    service, db_path, thread = await _owned_service(tmp_path, llm)
    llm.db_path = db_path

    with pytest.raises(LifecyclePersistenceBusy):
        await service.submit_presearch(
            command_id="submit-with-long-sqlite-lock",
            seed_text="夏季凉感T恤",
            user_note=None,
            thread_id=thread["id"],
            user_id="user-test",
            workspace_id="ws-test",
        )

    tasks = list(service._lifecycle_reconciliation_tasks)
    assert len(tasks) == 1
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=6)
    run_id = service._stable_id("run", "submit-with-long-sqlite-lock")
    projection = await service._lifecycle.load(run_id)
    assert projection.state is ContentResearchState.RECOVERY_REQUIRED
    assert projection.reason_code == "LOCAL_PERSISTENCE_BUSY"


@pytest.mark.asyncio
async def test_accepted_presearch_without_brief_has_truthful_read_and_trace_projection(tmp_path):
    service, db_path, thread = await _owned_service(tmp_path, RecordingLLM())
    run_id = "run-accepted-before-llm-outcome"
    await service._lifecycle.apply(LifecycleCommand(
        command_id="accepted-before-llm-outcome",
        run_id=run_id,
        expected_state=None,
        expected_revision=0,
        kind="submit_research_subject",
        payload={
            "thread_id": thread["id"],
            "user_id": "user-test",
            "workspace_id": "ws-test",
            "seed_text": "夏季凉感T恤",
        },
    ))

    summary = await service.get_workflow_summary(run_id)
    trace = await service.get_workflow_trace(run_id)

    assert summary.run.state == "presearch_running"
    assert summary.brief is None
    assert trace.state == "presearch_running"
    assert trace.current_stage == "presearch"
    assert trace.run_status == "running"

    cancelled = await service.run_workflow_action(
        workflow_run_id=run_id,
        request=ContentResearchWorkflowActionRequest(
            command_id="cancel-accepted-presearch",
            expected_state="presearch_running",
            expected_revision=1,
            action="cancel",
        ),
    )
    assert cancelled.result["run"]["state"] == "cancelled_or_failed"
    with sqlite3.connect(db_path) as connection:
        statuses = {
            row[0]
            for row in connection.execute(
                "SELECT status FROM workflow_steps WHERE run_id=?", (run_id,)
            )
        }
    assert statuses == {"cancelled"}
