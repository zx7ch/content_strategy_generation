"""CL-10: public-API gate for the packet-only formal research workflow."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.api.routes.router import app
from app.content_research.persistence_models import (
    DirectionalEvidencePacketRecord,
    ReportPublicationRecord,
    StageCheckpointRecord,
)
from app.content_research.presearch.service import PresearchService
from app.content_research.service import ContentResearchService, WorkflowRunManagerRuntime
from app.content_research.sources import SourceAdapterRegistry
from app.content_research.sources.base import ProviderCapability, SourceOperationResult
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.content_research.worker import ContentResearchDispatchWorker
from app.memory.thread_store import ThreadStore
from app.services.llm.failures import LLMProviderFailure
from app.services.llm.types import LLMResponse, TokenUsage

LITE_DIRECTIONS = [
    "product_marketing",
    "content_performance",
    "competitor_discovery",
]


class FakeLLM:
    def __init__(self) -> None:
        self.fail_marketing = False
        self.marketing_calls = 0

    async def generate(self, request):
        if request.task_type == "content_research.marketing_conclusion_analysis":
            self.marketing_calls += 1
            if self.fail_marketing:
                raise LLMProviderFailure(
                    "llm_model_unavailable",
                    "模型不可用",
                    True,
                    404,
                    provider="fake",
                    model="fake",
                )
            payload = json.loads(request.messages[-1].content)
            claim_ids = [item["claim_id"] for item in payload["claims"]]
            return LLMResponse(
                content=json.dumps(
                    {
                        "candidates": (
                            [
                                {
                                    "track": "need",
                                    "statement": "样本明确表达轻量透气需求",
                                    "supporting_claim_ids": claim_ids,
                                }
                            ]
                            if claim_ids
                            else []
                        )
                    },
                    ensure_ascii=False,
                ),
                provider="fake",
                model="fake",
                usage=TokenUsage(total_tokens=1),
                latency_ms=1,
            )
        user_prompt = request.messages[-1].content
        subject = "慢速调研" if "慢速调研" in user_prompt else "徒步短裤"
        core_object = "调研" if subject == "慢速调研" else "短裤"
        return LLMResponse(
            content=json.dumps({
                "subject_confirmation": subject,
                "competitor_tags": ["迪卡侬"],
                "research_directions": LITE_DIRECTIONS,
                "custom_research_question": "请给出下一步建议",
                "custom_competitor_input": "",
                "subject_structure": {
                    "schema_version": "content_research_subject_structure_v1",
                    "canonical_subject": subject,
                    "subject_type": "category",
                    "core_entities": [{"canonical_name": core_object, "raw_mentions": [core_object]}],
                    "research_intents": ["产品营销"],
                    "context_modifiers": [],
                    "synonym_groups": {core_object: ["户外短裤"]},
                    "ambiguities": [],
                    "resolution_state": "resolved",
                },
            }, ensure_ascii=False),
            provider="fake", model="fake", usage=TokenUsage(total_tokens=1), latency_ms=1,
        )


class CapableFakeAdapter:
    """Deterministic transport only; it never persists research artifacts."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.delay_seconds = 0.0
        self.discover_failure_reason: str | None = None
        self.detail_failure_reason: str | None = None
        self.authenticated = False

    def authentication_ready(self) -> bool:
        return self.authenticated

    def capabilities(self):
        note_fields = (
            "title", "content_text", "tags", "note_type", "metrics", "metrics_observed_at",
            "source_published_at", "ip_location", "media", "author", "competitor_names",
            "activity_signals", "keyword_patterns", "reference_window",
        )
        return (
            ProviderCapability("discover_candidates", "supported", ("title", "author", "metrics")),
            ProviderCapability("collect_note_detail", "supported", note_fields),
            ProviderCapability("collect_comments", "supported", ("comment_text", "source_published_at", "like_count", "reply_depth", "author", "parent_note_id")),
        )

    async def discover_candidates(self, request):
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        direction = str(request.context["direction_id"])
        self.calls.append(("discover", direction))
        if self.discover_failure_reason:
            return SourceOperationResult(
                provider="xiaohongshu",
                operation="discover_candidates",
                source_kind="search_result_minimal",
                status="failed",
                items=[],
                failure_reason=self.discover_failure_reason,
                retryable=self.discover_failure_reason in {"timeout", "transient_error"},
                completeness="unavailable",
            )
        return SourceOperationResult(
            provider="xiaohongshu", operation="discover_candidates", source_kind="search_result_minimal", status="completed",
            items=[
                {"provider": "xiaohongshu", "canonical_id": f"{direction}-note-{index}", "source_url": f"https://example.test/{direction}/{index}", "source_kind": "search_result_minimal", "author": f"note-author-{index}", "author_id": f"note-author-{index}", "metrics": {"likes": 100 - index}}
                for index in range(3)
            ],
        )

    async def collect_note_detail(self, request):
        direction = str(request.context["direction_id"])
        self.calls.append(("detail", direction))
        if self.detail_failure_reason:
            return SourceOperationResult(
                provider="xiaohongshu",
                operation="collect_note_detail",
                source_kind="note_detail",
                status="failed",
                items=[],
                failure_reason=self.detail_failure_reason,
                retryable=self.detail_failure_reason
                in {"timeout", "transient_error", "rate_limited"},
                completeness="unavailable",
            )
        index = request.note_id.rsplit("-", 1)[-1]
        return SourceOperationResult(
            provider="xiaohongshu", operation="collect_note_detail", source_kind="note_detail", status="completed",
            items=[{
                "provider": "xiaohongshu", "canonical_id": request.note_id, "source_url": request.note_url,
                "source_kind": "note_detail", "author": f"note-author-{index}", "author_id": f"note-author-{index}",
                "title": "迪卡侬 夏季徒步短裤产品营销观察",
                "content_text": "产品营销样本提到轻量透气的徒步短裤适合夏季通勤与户外。",
                "tags": ["徒步", "夏季"], "note_type": "image_text", "metrics": {"likes": 100, "comments": 20},
                "metrics_observed_at": "2026-07-01T00:00:00+00:00", "source_published_at": "2026-06-01T00:00:00+00:00",
                "ip_location": "上海", "media": {"count": 3}, "competitor_names": ["迪卡侬"],
                "activity_signals": ["launch_signal"], "keyword_patterns": ["徒步短裤"],
                "reference_window": {"non_overlapping": True, "comparable": True, "bias_disclosure": "same policy", "recent_eligible": 10, "reference_eligible": 10, "recent_keyword_count": 5, "reference_keyword_count": 2},
                "field_availability": {field: "present" for field in request.required_fields},
            }],
        )

    async def collect_comments(self, request):
        direction = str(request.context["direction_id"])
        self.calls.append(("comments", direction))
        return SourceOperationResult(
            provider="xiaohongshu", operation="collect_comments", source_kind="comment", status="completed",
            items=[{
                "provider": "xiaohongshu", "canonical_id": f"{direction}-comment-{index}", "source_kind": "comment",
                "comment_text": "我需要更透气的尺码，能不能提供？", "author": f"comment-author-{index % 5}", "author_id": f"comment-author-{index % 5}",
                "source_published_at": "2026-06-02T00:00:00+00:00", "like_count": index, "reply_depth": 0,
                "field_availability": {"comment_text": "present", "source_published_at": "present", "like_count": "present", "reply_depth": "present"},
            } for index in range(30)],
            completeness="complete",
        )


@pytest.fixture()
async def formal_client(tmp_path):
    original = getattr(app.state, "content_research_service", None)
    adapter = CapableFakeAdapter()
    analysis_llm = FakeLLM()
    adapter.analysis_llm = analysis_llm
    db_path = str(tmp_path / "cl10.db")
    dispatch_wake_event = asyncio.Event()
    app.state.content_research_service = ContentResearchService(
        store=SQLiteContentResearchStore(db_path),
        presearch=PresearchService(FakeLLM(), first_feedback_timeout_seconds=0.05, hard_cutoff_seconds=0.1),
        workflow_runtime=WorkflowRunManagerRuntime(db_path),
        source_registry=SourceAdapterRegistry({"xiaohongshu": adapter}),
        analysis_llm=analysis_llm,
        dispatch_wake_event=dispatch_wake_event,
    )
    dispatch_worker = ContentResearchDispatchWorker(
        store=app.state.content_research_service._store,
        service_factory=lambda: app.state.content_research_service,
        wake_event=dispatch_wake_event,
    )
    dispatch_stop_event = asyncio.Event()
    dispatch_task = asyncio.create_task(dispatch_worker.run_loop(stop_event=dispatch_stop_event))
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"X-Workspace-Id": "ws-1", "X-User-Id": "user-1"},
    )
    try:
        yield client, adapter, db_path
    finally:
        dispatch_stop_event.set()
        dispatch_wake_event.set()
        await dispatch_task
        await client.aclose()
        if original is None:
            delattr(app.state, "content_research_service")
        else:
            app.state.content_research_service = original


@pytest.mark.asyncio
async def test_formal_workflow_public_api_e2e_is_packet_only_safe_and_replayable(formal_client):
    client, adapter, db_path = formal_client
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    creator_thread = await thread_store.create_thread(title="CL10 report")
    created = await client.post("/content-research/presearch", json={"seed_text": "徒步短裤", "thread_id": creator_thread["id"]})
    assert created.status_code == 201, created.text
    workflow = created.json()
    confirmed = await client.post(
        f"/content-research/briefs/{workflow['brief_id']}/confirm",
        json={
            "confirmed_subject": "徒步短裤",
            "subject_structure_hash": workflow["subject_structure_hash"],
            "subject_type": "category",
            "selected_competitors": ["迪卡侬"],
            "custom_competitors": [],
            "selected_directions": ["product_marketing"],
            "custom_research_question": "请给出下一步建议",
            "primary_marketing_goal": "content_seeding",
            "subject_structure_confirmation": {
                "core_object": "短裤",
                "research_intent": "产品营销",
                "context_modifiers": [],
            },
        },
    )
    assert confirmed.status_code == 200, confirmed.text

    action = await client.post(
        f"/content-research/workflows/{workflow['workflow_run_id']}/actions",
        json={"action": "start_formal_research", "payload": {"provider": "xiaohongshu", "source_kind": "search_result", "limit": 20}},
    )
    assert action.status_code == 200, action.text
    assert action.json()["status"] in {"queued", "running"}
    report_messages = []
    for _ in range(100):
        summary = (await client.get(f"/content-research/workflows/{workflow['workflow_run_id']}")).json()
        timeline_messages = await thread_store.get_thread_messages(creator_thread["id"])
        report_messages = [item for item in timeline_messages if item["message_type"] == "artifact_result"]
        if {item["status"] for item in summary["subagent_tasks"]} <= {"completed", "partial_completed"} and report_messages:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("formal research did not publish after asynchronous dispatch")
    assert len(report_messages) == 1

    assert {item["direction_id"] for item in summary["subagent_tasks"]} == {
        "product_marketing"
    }
    assert {item["status"] for item in summary["subagent_tasks"]} <= {"completed", "partial_completed"}

    report = await client.get(f"/content-research/workflows/{workflow['workflow_run_id']}/lite-report")
    assert report.status_code == 200, report.text
    report_payload = report.json()
    assert report_payload["publication"]["state"] == "complete_verified_report"
    assert all(
        ref["quote"] and ref["field_path"] and ref["source_text_hash"] and ref["source_url"]
        for group in report_payload["citations"] for ref in group["evidence_refs"]
    )
    published_snapshot = app.state.content_research_service._store.list_result_snapshots_for_workflow(
        workflow["workflow_run_id"]
    )[-1]
    governed_conclusions = published_snapshot.metadata["governed_snapshot"][
        "marketing_conclusions"
    ]
    selected = next(item for item in governed_conclusions if item["state"] == "selected")
    assert selected["track"] == "need"
    assert selected["statement"] == "样本明确表达轻量透气需求"
    assert selected["supporting_claim_ids"]
    assert selected["additional_qualified_count"] == 0
    need = report_payload["sections"]["marketing_conclusions"]["need"]
    assert need["state"] == "selected"
    assert need["statement"] == "样本明确表达轻量透气需求"
    assert need["additional_qualified_count"] == 0

    governance = await client.get(f"/content-research/workflows/{workflow['workflow_run_id']}/governance")
    assert governance.status_code == 200, governance.text
    governance_payload = governance.json()
    action_hypotheses = [item for item in governance_payload["aggregate_claims"] if item["aggregate_type"] == "action_hypothesis"]
    assert action_hypotheses
    assert all(item["request_origin"] == "user_requested_next_steps" for item in action_hypotheses)

    trace = await client.get(f"/content-research/workflows/{workflow['workflow_run_id']}/trace")
    assert trace.status_code == 200, trace.text
    assert "raw_payload" not in trace.text and "access_token" not in trace.text
    marketing_checkpoint = next(
        item
        for item in trace.json()["logical_checkpoints"]
        if item["stage"] == "marketing_conclusion"
    )
    assert marketing_checkpoint == {
        "stage": "marketing_conclusion",
        "status": "completed",
        "tracks": {
            "need": {
                "state": "selected",
                "supporting_note_count": 3,
                "independent_author_count": 3,
            },
            "value": {
                "state": "insufficient_evidence",
                "reason_codes": ["conclusion_no_qualified_candidate"],
            },
            "message": {
                "state": "insufficient_evidence",
                "reason_codes": ["conclusion_no_qualified_candidate"],
            },
        },
    }
    assert not any(
        forbidden in json.dumps(marketing_checkpoint, ensure_ascii=False)
        for forbidden in (
            "candidate_count",
            "statement",
            "quote",
            "note_id",
            "author_id",
            "policy_hash",
            "prompt",
        )
    )

    store = app.state.content_research_service._store
    snapshots_before_packet_replay = store.list_result_snapshots_for_workflow(
        workflow["workflow_run_id"]
    )
    publications_before_packet_replay = [
        item
        for item in store.list_typed_records(ReportPublicationRecord)
        if item.workflow_run_id == workflow["workflow_run_id"]
    ]
    conclusion_calls_before_packet_replay = adapter.analysis_llm.marketing_calls
    adapter_calls_before_packet_replay = list(adapter.calls)
    operation_ids_before_packet_replay = {
        item.id
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.workflow_run_id == workflow["workflow_run_id"]
        and item.stage_name == "operation"
    }
    packet_ids_before_packet_replay = {
        item.id
        for item in store.list_typed_records(DirectionalEvidencePacketRecord)
        if item.workflow_run_id == workflow["workflow_run_id"]
    }
    await app.state.content_research_service.replay_downstream_from_persisted_packets(
        workflow["workflow_run_id"]
    )
    replay_messages = await thread_store.get_thread_messages(creator_thread["id"])
    assert len(
        [item for item in replay_messages if item["message_type"] == "artifact_result"]
    ) == 1
    assert store.list_result_snapshots_for_workflow(
        workflow["workflow_run_id"]
    ) == snapshots_before_packet_replay
    assert [
        item
        for item in store.list_typed_records(ReportPublicationRecord)
        if item.workflow_run_id == workflow["workflow_run_id"]
    ] == publications_before_packet_replay
    assert adapter.analysis_llm.marketing_calls == conclusion_calls_before_packet_replay
    assert adapter.calls == adapter_calls_before_packet_replay
    assert {
        item.id
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.workflow_run_id == workflow["workflow_run_id"]
        and item.stage_name == "operation"
    } == operation_ids_before_packet_replay
    assert {
        item.id
        for item in store.list_typed_records(DirectionalEvidencePacketRecord)
        if item.workflow_run_id == workflow["workflow_run_id"]
    } == packet_ids_before_packet_replay

    calls_before_replay = list(adapter.calls)
    replay = await client.post(
        f"/content-research/workflows/{workflow['workflow_run_id']}/actions",
        json={"action": "retry_formal_research", "payload": {"provider": "xiaohongshu", "source_kind": "search_result", "limit": 20}},
    )
    assert replay.status_code == 422, replay.text
    assert adapter.calls == calls_before_replay
    rerun = (await client.get(f"/content-research/workflows/{workflow['workflow_run_id']}/lite-report")).json()
    assert rerun["citations"] == report_payload["citations"]
    await thread_store.close()


@pytest.mark.asyncio
async def test_marketing_analysis_unavailable_waits_and_resumes_without_collection_delta(
    formal_client,
):
    client, adapter, db_path = formal_client
    adapter.analysis_llm.fail_marketing = True
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    creator_thread = await thread_store.create_thread(title="marketing model recovery")
    created = await client.post(
        "/content-research/presearch",
        json={"seed_text": "徒步短裤", "thread_id": creator_thread["id"]},
    )
    workflow = created.json()
    confirmed = await client.post(
        f"/content-research/briefs/{workflow['brief_id']}/confirm",
        json={
            "confirmed_subject": "徒步短裤",
            "subject_type": "category",
            "selected_competitors": [],
            "custom_competitors": [],
            "selected_directions": ["product_marketing"],
            "custom_research_question": "",
            "primary_marketing_goal": "content_seeding",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    started = await client.post(
        f"/content-research/workflows/{workflow['workflow_run_id']}/actions",
        json={
            "action": "start_formal_research",
            "payload": {"provider": "xiaohongshu", "limit": 20},
        },
    )
    assert started.status_code == 200, started.text

    for _ in range(150):
        trace = (
            await client.get(
                f"/content-research/workflows/{workflow['workflow_run_id']}/trace"
            )
        ).json()
        if trace["run_status"] == "waiting_user":
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("marketing analysis failure did not reach waiting_user")

    public_checkpoint = next(
        item
        for item in trace["logical_checkpoints"]
        if item["stage"] == "marketing_conclusion"
    )
    assert public_checkpoint == {
        "stage": "marketing_conclusion",
        "status": "waiting_user",
        "reason_codes": ["marketing_analysis_unavailable"],
        "recovery_action": "repair_model_configuration_and_resume",
    }

    store = SQLiteContentResearchStore(db_path)
    checkpoints = [
        item
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.workflow_run_id == workflow["workflow_run_id"]
        and item.stage_name == "marketing_conclusion"
    ]
    assert len(checkpoints) == 1
    assert checkpoints[0].status == "waiting_user"
    assert checkpoints[0].payload == {
        "schema_version": "content_research_marketing_conclusion_checkpoint_v1",
        "reason_codes": ["marketing_analysis_unavailable"],
        "recovery_action": "repair_model_configuration_and_resume",
    }
    assert not any(
        key in json.dumps(checkpoints[0].payload, ensure_ascii=False)
        for key in ("statement", "quote", "claim_id", "candidate_id")
    )
    messages = await thread_store.get_thread_messages(creator_thread["id"])
    assert not [item for item in messages if item["message_type"] == "artifact_result"]
    operation_ids_before = {
        item.id
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.workflow_run_id == workflow["workflow_run_id"]
        and item.stage_name == "operation"
    }
    packet_ids_before = {
        item.id
        for item in store.list_typed_records(DirectionalEvidencePacketRecord)
        if item.workflow_run_id == workflow["workflow_run_id"]
    }
    adapter_calls_before = list(adapter.calls)

    adapter.analysis_llm.fail_marketing = False
    retried = await client.post(
        f"/content-research/workflows/{workflow['workflow_run_id']}/actions",
        json={
            "action": "retry_formal_research",
            "payload": {"provider": "xiaohongshu", "limit": 20},
        },
    )
    assert retried.status_code == 200, retried.text
    for _ in range(150):
        report = await client.get(
            f"/content-research/workflows/{workflow['workflow_run_id']}/lite-report"
        )
        if report.status_code == 200:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("marketing analysis recovery did not publish")

    assert adapter.analysis_llm.marketing_calls == 2
    assert adapter.calls == adapter_calls_before
    assert {
        item.id
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.workflow_run_id == workflow["workflow_run_id"]
        and item.stage_name == "operation"
    } == operation_ids_before
    assert {
        item.id
        for item in store.list_typed_records(DirectionalEvidencePacketRecord)
        if item.workflow_run_id == workflow["workflow_run_id"]
    } == packet_ids_before
    assert len(
        [
            item
            for item in store.list_typed_records(StageCheckpointRecord)
            if item.workflow_run_id == workflow["workflow_run_id"]
            and item.stage_name == "marketing_conclusion"
        ]
    ) == 1
    await thread_store.close()


@pytest.mark.asyncio
async def test_auth_required_retry_requeues_the_same_run_once_and_publishes_once(formal_client):
    """A user retry resumes a recoverable failed run; it never creates a sibling report."""
    client, adapter, db_path = formal_client
    adapter.discover_failure_reason = "auth_required"
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    creator_thread = await thread_store.create_thread(title="auth retry report")
    created = await client.post(
        "/content-research/presearch",
        json={"seed_text": "徒步短裤", "thread_id": creator_thread["id"]},
    )
    workflow = created.json()
    confirmed = await client.post(
        f"/content-research/briefs/{workflow['brief_id']}/confirm",
        json={
            "confirmed_subject": "徒步短裤",
            "subject_type": "category",
            "selected_competitors": [],
            "custom_competitors": [],
            "selected_directions": ["product_marketing"],
            "custom_research_question": "",
            "primary_marketing_goal": "content_seeding",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    start = await client.post(
        f"/content-research/workflows/{workflow['workflow_run_id']}/actions",
        json={"action": "start_formal_research", "payload": {"provider": "xiaohongshu", "limit": 20}},
    )
    assert start.status_code == 200, start.text

    for _ in range(100):
        summary = (await client.get(f"/content-research/workflows/{workflow['workflow_run_id']}")).json()
        if summary["subagent_tasks"][0]["status"] in {"failed", "partial_completed"}:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("auth_required was not recorded as a recoverable formal task")
    for _ in range(100):
        trace_after_failure = (
            await client.get(f"/content-research/workflows/{workflow['workflow_run_id']}/trace")
        ).json()
        if trace_after_failure["run_status"] == "waiting_user":
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("recoverable formal failure did not settle the parent run for user recovery")
    assert trace_after_failure["run_status"] == "waiting_user"
    assert next(
        step for step in trace_after_failure["runtime_steps"]
        if step["step_name"] == "formal_research"
    )["status"] == "retrying"
    assert "auth_required" in str(trace_after_failure)
    failed_messages = await thread_store.get_thread_messages(creator_thread["id"])
    assert not [item for item in failed_messages if item["message_type"] == "artifact_result"]

    calls_before_unauthenticated_retry = list(adapter.calls)
    unauthenticated_retry = await client.post(
        f"/content-research/workflows/{workflow['workflow_run_id']}/actions",
        json={
            "action": "retry_formal_research",
            "payload": {"provider": "xiaohongshu", "limit": 20},
        },
    )
    assert unauthenticated_retry.status_code == 422
    assert adapter.calls == calls_before_unauthenticated_retry
    trace_before_authentication = (
        await client.get(
            f"/content-research/workflows/{workflow['workflow_run_id']}/trace"
        )
    ).json()
    assert trace_before_authentication["runtime_child_tasks"][0]["attempt_count"] == 0

    adapter.discover_failure_reason = None
    adapter.authenticated = True
    retry = await client.post(
        f"/content-research/workflows/{workflow['workflow_run_id']}/actions",
        json={"action": "retry_formal_research", "payload": {"provider": "xiaohongshu", "limit": 20}},
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["workflow_run_id"] == workflow["workflow_run_id"]

    for _ in range(150):
        summary = (await client.get(f"/content-research/workflows/{workflow['workflow_run_id']}")).json()
        messages = await thread_store.get_thread_messages(creator_thread["id"])
        reports = [item for item in messages if item["message_type"] == "artifact_result"]
        if summary["subagent_tasks"][0]["status"] in {"completed", "partial_completed"} and reports:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("same-run auth retry did not publish")
    assert len(reports) == 1
    recovered_trace = (
        await client.get(
            f"/content-research/workflows/{workflow['workflow_run_id']}/trace"
        )
    ).json()
    recovered_child = recovered_trace["runtime_child_tasks"][0]
    assert recovered_child["retry_counters"] == {
        "specialist_user_recovery": {"used": 1, "limit": 2},
        "workflow_child_attempt": {"used": 2, "limit": 3},
    }
    report = await client.get(f"/content-research/workflows/{workflow['workflow_run_id']}/lite-report")
    assert report.status_code == 200, report.text
    assert report.json()["citations"]
    await thread_store.close()


@pytest.mark.asyncio
async def test_third_same_run_recovery_is_rejected_without_replaying_provider(formal_client):
    client, adapter, db_path = formal_client
    adapter.discover_failure_reason = "transient_error"
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    creator_thread = await thread_store.create_thread(title="recovery budget")
    created = await client.post(
        "/content-research/presearch",
        json={"seed_text": "徒步短裤", "thread_id": creator_thread["id"]},
    )
    workflow = created.json()
    confirmed = await client.post(
        f"/content-research/briefs/{workflow['brief_id']}/confirm",
        json={
            "confirmed_subject": "徒步短裤",
            "subject_type": "category",
            "selected_competitors": [],
            "custom_competitors": [],
            "selected_directions": ["product_marketing"],
            "custom_research_question": "",
            "primary_marketing_goal": "content_seeding",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    started = await client.post(
        f"/content-research/workflows/{workflow['workflow_run_id']}/actions",
        json={
            "action": "start_formal_research",
            "payload": {"provider": "xiaohongshu", "limit": 20},
        },
    )
    assert started.status_code == 200, started.text

    async def wait_for_recovery_count(expected: int) -> dict:
        for _ in range(150):
            trace = (
                await client.get(
                    f"/content-research/workflows/{workflow['workflow_run_id']}/trace"
                )
            ).json()
            if (
                trace["run_status"] == "waiting_user"
                and trace["runtime_child_tasks"][0]["attempt_count"] == expected
            ):
                return trace
            await asyncio.sleep(0.01)
        pytest.fail(
            f"workflow did not settle after recovery {expected}: "
            f"status={trace.get('run_status')} child={trace.get('runtime_child_tasks')} "
            f"operations={trace.get('provider_operations')}"
        )

    await wait_for_recovery_count(0)
    for expected in (1, 2):
        retry = await client.post(
            f"/content-research/workflows/{workflow['workflow_run_id']}/actions",
            json={
                "action": "retry_formal_research",
                "payload": {"provider": "xiaohongshu", "limit": 20},
            },
        )
        assert retry.status_code == 200, retry.text
        await wait_for_recovery_count(expected)

    provider_calls_before_rejection = list(adapter.calls)
    rejected = await client.post(
        f"/content-research/workflows/{workflow['workflow_run_id']}/actions",
        json={
            "action": "retry_formal_research",
            "payload": {"provider": "xiaohongshu", "limit": 20},
        },
    )

    assert rejected.status_code == 422
    assert "recovery budget is exhausted" in rejected.text
    await asyncio.sleep(0.05)
    assert adapter.calls == provider_calls_before_rejection
    exhausted_trace = await wait_for_recovery_count(2)
    assert exhausted_trace["runtime_child_tasks"][0]["retry_counters"] == {
        "specialist_user_recovery": {"used": 2, "limit": 2},
        "workflow_child_attempt": {"used": 3, "limit": 3},
    }
    await thread_store.close()


@pytest.mark.asyncio
async def test_detail_recovery_replays_failed_detail_without_repeating_discovery(formal_client):
    client, adapter, db_path = formal_client
    adapter.detail_failure_reason = "transient_error"
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    creator_thread = await thread_store.create_thread(title="detail recovery")
    created = await client.post(
        "/content-research/presearch",
        json={"seed_text": "徒步短裤", "thread_id": creator_thread["id"]},
    )
    workflow = created.json()
    confirmed = await client.post(
        f"/content-research/briefs/{workflow['brief_id']}/confirm",
        json={
            "confirmed_subject": "徒步短裤",
            "subject_type": "category",
            "selected_competitors": [],
            "custom_competitors": [],
            "selected_directions": ["product_marketing"],
            "custom_research_question": "",
            "primary_marketing_goal": "content_seeding",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    await client.post(
        f"/content-research/workflows/{workflow['workflow_run_id']}/actions",
        json={
            "action": "start_formal_research",
            "payload": {"provider": "xiaohongshu", "limit": 20},
        },
    )
    for _ in range(150):
        trace = (
            await client.get(
                f"/content-research/workflows/{workflow['workflow_run_id']}/trace"
            )
        ).json()
        if trace["run_status"] == "waiting_user":
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("detail failure did not reach recovery boundary")

    discovery_calls_before_retry = adapter.calls.count(
        ("discover", "product_marketing")
    )
    assert discovery_calls_before_retry > 0
    assert adapter.calls.count(("detail", "product_marketing")) == 1
    adapter.detail_failure_reason = None
    retried = await client.post(
        f"/content-research/workflows/{workflow['workflow_run_id']}/actions",
        json={
            "action": "retry_formal_research",
            "payload": {"provider": "xiaohongshu", "limit": 20},
        },
    )
    assert retried.status_code == 200, retried.text
    for _ in range(150):
        report = await client.get(
            f"/content-research/workflows/{workflow['workflow_run_id']}/lite-report"
        )
        if report.status_code == 200:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("detail recovery did not publish")

    assert (
        adapter.calls.count(("discover", "product_marketing"))
        == discovery_calls_before_retry
    )
    assert adapter.calls.count(("detail", "product_marketing")) == 4
    await thread_store.close()


@pytest.mark.asyncio
async def test_permanent_detail_failure_fails_run_without_publishing_report(formal_client):
    client, adapter, db_path = formal_client
    adapter.detail_failure_reason = "provider_permanent_error"
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    creator_thread = await thread_store.create_thread(title="permanent failure")
    created = await client.post(
        "/content-research/presearch",
        json={"seed_text": "徒步短裤", "thread_id": creator_thread["id"]},
    )
    workflow = created.json()
    confirmed = await client.post(
        f"/content-research/briefs/{workflow['brief_id']}/confirm",
        json={
            "confirmed_subject": "徒步短裤",
            "subject_type": "category",
            "selected_competitors": [],
            "custom_competitors": [],
            "selected_directions": ["product_marketing"],
            "custom_research_question": "",
            "primary_marketing_goal": "content_seeding",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    await client.post(
        f"/content-research/workflows/{workflow['workflow_run_id']}/actions",
        json={
            "action": "start_formal_research",
            "payload": {"provider": "xiaohongshu", "limit": 20},
        },
    )
    for _ in range(150):
        trace = (
            await client.get(
                f"/content-research/workflows/{workflow['workflow_run_id']}/trace"
            )
        ).json()
        if trace["run_status"] == "failed":
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("permanent provider failure did not fail the workflow")

    report = await client.get(
        f"/content-research/workflows/{workflow['workflow_run_id']}/lite-report"
    )
    assert report.status_code == 404
    await thread_store.close()


@pytest.mark.asyncio
async def test_single_direction_workflow_publishes_a_cited_report_with_frozen_scope(formal_client):
    """Gate 2 route: no report fixture may stand in for a real workflow run."""
    client, _adapter, db_path = formal_client
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    creator_thread = await thread_store.create_thread(title="single direction report")
    created = await client.post(
        "/content-research/presearch",
        json={"seed_text": "徒步短裤", "thread_id": creator_thread["id"]},
    )
    workflow = created.json()
    confirmed = await client.post(
        f"/content-research/briefs/{workflow['brief_id']}/confirm",
        json={
            "confirmed_subject": "徒步短裤",
            "subject_type": "category",
            "selected_competitors": [],
            "custom_competitors": [],
            "selected_directions": ["product_marketing"],
            "custom_research_question": "",
            "primary_marketing_goal": "content_seeding",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    action = await client.post(
        f"/content-research/workflows/{workflow['workflow_run_id']}/actions",
        json={"action": "start_formal_research", "payload": {"provider": "xiaohongshu", "limit": 20}},
    )
    assert action.status_code == 200, action.text

    for _ in range(100):
        summary = (await client.get(f"/content-research/workflows/{workflow['workflow_run_id']}")).json()
        messages = await thread_store.get_thread_messages(creator_thread["id"])
        if summary["subagent_tasks"][0]["status"] in {"completed", "partial_completed"} and any(
            item["message_type"] == "artifact_result" for item in messages
        ):
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("single-direction formal research did not publish")
    report = await client.get(f"/content-research/workflows/{workflow['workflow_run_id']}/lite-report")
    await thread_store.close()

    assert report.status_code == 200, report.text
    payload = report.json()
    assert payload["frozen_scope"] == {
        "direction_set_version": "formal_v1",
        "direction_ids": ["product_marketing"],
        "report_compose_mode": "template_only",
    }
    direction_states = {item["direction"]: item for item in payload["run_direction_states"]}
    assert direction_states["product_marketing"] == {
        "direction": "product_marketing",
        "state": "formal_directional_result",
        "reason_code": None,
        "recovery_action": None,
    }
    assert {
        direction for direction, state in direction_states.items()
        if state["state"] == "not_requested"
    } == {"competitor_discovery", "content_performance"}
    assert payload["citations"]


@pytest.mark.asyncio
async def test_formal_action_returns_while_slow_collection_keeps_trace_readable(formal_client):
    client, adapter, db_path = formal_client
    adapter.delay_seconds = 0.2
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    creator_thread = await thread_store.create_thread(title="slow dispatch")
    created = await client.post(
        "/content-research/presearch",
        json={"seed_text": "慢速调研", "thread_id": creator_thread["id"]},
    )
    confirmed = await client.post(
        f"/content-research/briefs/{created.json()['brief_id']}/confirm",
        json={
            "confirmed_subject": "慢速调研",
            "subject_type": "category",
            "selected_competitors": [],
            "custom_competitors": [],
            "selected_directions": ["product_marketing"],
            "custom_research_question": "",
            "primary_marketing_goal": "content_seeding",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    workflow_run_id = created.json()["workflow_run_id"]
    action = await asyncio.wait_for(
        client.post(
            f"/content-research/workflows/{workflow_run_id}/actions",
            json={"action": "start_formal_research", "payload": {"provider": "xiaohongshu", "limit": 20}},
        ),
        timeout=0.05,
    )
    assert action.json()["status"] in {"queued", "running"}
    trace = await asyncio.wait_for(
        client.get(f"/content-research/workflows/{workflow_run_id}/trace"),
        timeout=0.15,
    )
    assert trace.status_code == 200, trace.text
    await thread_store.close()
