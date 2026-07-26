"""CL-10: public-API gate for the packet-only formal research workflow."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.api.routes.router import app
from app.content_research.presearch.service import PresearchService
from app.content_research.reporting.composer import ResearchReportComposer
from app.content_research.service import ContentResearchService, WorkflowRunManagerRuntime
from app.content_research.sources import SourceAdapterRegistry
from app.content_research.sources.base import ProviderCapability, SourceOperationResult
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.content_research.worker import ContentResearchDispatchWorker
from app.memory.thread_store import ThreadStore
from app.services.llm.types import LLMResponse, TokenUsage

ALL_DIRECTIONS = [
    "product_marketing", "content_performance", "competitor_discovery",
    "ugc_community", "comment_insight", "brand_activity", "keyword_growth",
]


class FakeLLM:
    async def generate(self, _request):
        return LLMResponse(
            content=json.dumps({
                "subject_confirmation": "徒步短裤",
                "competitor_tags": ["迪卡侬"],
                "research_directions": ALL_DIRECTIONS,
                "custom_research_question": "请给出下一步建议",
                "custom_competitor_input": "",
            }, ensure_ascii=False),
            provider="fake", model="fake", usage=TokenUsage(total_tokens=1), latency_ms=1,
        )


class CapableFakeAdapter:
    """Deterministic transport only; it never persists research artifacts."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.delay_seconds = 0.0
        self.discover_failure_reason: str | None = None

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
        index = request.note_id.rsplit("-", 1)[-1]
        return SourceOperationResult(
            provider="xiaohongshu", operation="collect_note_detail", source_kind="note_detail", status="completed",
            items=[{
                "provider": "xiaohongshu", "canonical_id": request.note_id, "source_url": request.note_url,
                "source_kind": "note_detail", "author": f"note-author-{index}", "author_id": f"note-author-{index}",
                "title": "迪卡侬 夏季徒步短裤上新", "content_text": "轻量透气的徒步短裤适合夏季通勤与户外。",
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
    db_path = str(tmp_path / "cl10.db")
    app.state.content_research_service = ContentResearchService(
        store=SQLiteContentResearchStore(db_path),
        presearch=PresearchService(FakeLLM(), first_feedback_timeout_seconds=0.05, hard_cutoff_seconds=0.1),
        workflow_runtime=WorkflowRunManagerRuntime(db_path),
        source_registry=SourceAdapterRegistry({"xiaohongshu": adapter}),
    )
    dispatch_worker = ContentResearchDispatchWorker(
        store=app.state.content_research_service._store,
        service_factory=lambda: app.state.content_research_service,
        recovery_scan_seconds=0.005,
    )
    dispatch_stop_event = asyncio.Event()
    dispatch_task = asyncio.create_task(dispatch_worker.run_loop(stop_event=dispatch_stop_event))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    try:
        yield client, adapter, db_path
    finally:
        dispatch_stop_event.set()
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
        json={"confirmed_subject": "徒步短裤", "subject_type": "category", "selected_competitors": ["迪卡侬"], "custom_competitors": [], "selected_directions": ALL_DIRECTIONS, "custom_research_question": "请给出下一步建议"},
    )
    assert confirmed.status_code == 200, confirmed.text

    action = await client.post(
        f"/content-research/workflows/{workflow['workflow_run_id']}/actions",
        json={"action": "start_formal_research", "payload": {"provider": "xiaohongshu", "source_kind": "search_result", "limit": 20}},
    )
    assert action.status_code == 200, action.text
    assert action.json()["status"] == "queued"
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
    await thread_store.close()

    assert {item["direction_id"] for item in summary["subagent_tasks"]} == set(ALL_DIRECTIONS)
    assert {item["status"] for item in summary["subagent_tasks"]} <= {"completed", "partial_completed"}

    report = await client.get(f"/content-research/workflows/{workflow['workflow_run_id']}/report")
    assert report.status_code == 200, report.text
    report_payload = report.json()
    assert report_payload["publication_state"] in {"partial_verified_report", "evidence_only_report"}
    assert all("evidence_bundle" not in card for card in report_payload["claim_cards"])
    assert all("claim_candidate_id" in signal for signal in report_payload["weak_signals"])
    assert all(
        ref["quote"] and ref["field_path"] and isinstance(ref["text_start"], int)
        and isinstance(ref["text_end"], int) and ref["source_text_hash"] and ref["source_url"]
        for group in report_payload["citation_groups"] for ref in group["evidence_refs"]
    )
    persisted_snapshot = next(
        item
        for item in app.state.content_research_service._store.list_result_snapshots_for_workflow(workflow["workflow_run_id"])
        if item.id == report_payload["publication"]["governed_snapshot_id"]
    )
    report_draft = ResearchReportComposer().compose(persisted_snapshot)
    assert report_draft.sections[0].citation_anchors
    assert report_draft.sections[0].citation_anchors[0].citation_group_id == report_payload["citation_groups"][0]["citation_group_id"]

    governance = await client.get(f"/content-research/workflows/{workflow['workflow_run_id']}/governance")
    assert governance.status_code == 200, governance.text
    governance_payload = governance.json()
    action_hypotheses = [item for item in governance_payload["aggregate_claims"] if item["aggregate_type"] == "action_hypothesis"]
    assert action_hypotheses
    assert all(item["request_origin"] == "user_requested_next_steps" for item in action_hypotheses)

    trace = await client.get(f"/content-research/workflows/{workflow['workflow_run_id']}/trace")
    assert trace.status_code == 200, trace.text
    assert "raw_payload" not in trace.text and "access_token" not in trace.text

    calls_before_replay = list(adapter.calls)
    replay = await client.post(
        f"/content-research/workflows/{workflow['workflow_run_id']}/actions",
        json={"action": "retry_formal_research", "payload": {"provider": "xiaohongshu", "source_kind": "search_result", "limit": 20}},
    )
    assert replay.status_code == 200, replay.text
    assert adapter.calls == calls_before_replay
    rerun = (await client.get(f"/content-research/workflows/{workflow['workflow_run_id']}/report")).json()
    assert rerun["citation_groups"] == report_payload["citation_groups"]


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
    assert "auth_required" in (await client.get(f"/content-research/workflows/{workflow['workflow_run_id']}/trace")).text
    failed_messages = await thread_store.get_thread_messages(creator_thread["id"])
    assert not [item for item in failed_messages if item["message_type"] == "artifact_result"]

    adapter.discover_failure_reason = None
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
    report = await client.get(f"/content-research/workflows/{workflow['workflow_run_id']}/report")
    assert report.status_code == 200, report.text
    assert report.json()["citation_total"] > 0
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
    report = await client.get(f"/content-research/workflows/{workflow['workflow_run_id']}/report")
    await thread_store.close()

    assert report.status_code == 200, report.text
    payload = report.json()
    assert payload["release"] == {
        "direction_set_version": "formal_v1",
        "direction_ids": ["product_marketing"],
    }
    assert payload["run_direction_states"] == [{
        "direction": "product_marketing",
        "state": "formal_directional_result",
        "reason_codes": [],
        "recovery_actions": [],
    }]
    assert payload["citation_total"] > 0


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
    assert action.json()["status"] == "queued"
    trace = await asyncio.wait_for(
        client.get(f"/content-research/workflows/{workflow_run_id}/trace"), timeout=0.05
    )
    assert trace.status_code == 200, trace.text
    await thread_store.close()
