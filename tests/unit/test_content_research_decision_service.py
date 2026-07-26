from __future__ import annotations

import pytest

from app.content_research.api_schemas import HumanDecisionRequest
from app.content_research.decisions.service import ResearchDecisionService
from app.content_research.models import ResearchBriefRecord, TraceRecord, utcnow
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore


class FakeWorkflowRuntime:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def append_event(self, *, workflow_run_id: str, thread_id: str, event_type: str, payload: dict) -> None:
        self.events.append(
            {
                "workflow_run_id": workflow_run_id,
                "thread_id": thread_id,
                "event_type": event_type,
                "payload": payload,
            }
        )


@pytest.fixture()
def store(tmp_path):
    return SQLiteContentResearchStore(str(tmp_path / "content_research.db"))


@pytest.fixture()
def brief(store):
    brief = ResearchBriefRecord(
        id="rb_decision_1",
        workflow_run_id="wr_decision_1",
        thread_id="thread_decision_1",
        schema_version="content_research_brief_v1",
        status="ready",
        payload={"schema_version": "content_research_brief_v1", "subject_confirmation": "Satisfy Running"},
    )
    trace = TraceRecord(
        id="trc_decision_1",
        workflow_run_id=brief.workflow_run_id,
        thread_id=brief.thread_id,
        schema_version="content_research_trace_v1",
        status="running",
        started_at=utcnow(),
        payload={"schema_version": "content_research_trace_v1", "trace_type": "presearch"},
    )
    store.save_brief(brief)
    store.save_trace(trace)
    return brief


@pytest.mark.asyncio
async def test_brand_selected_creates_decision_observation_and_workflow_event(store, brief):
    runtime = FakeWorkflowRuntime()
    service = ResearchDecisionService(store=store, workflow_runtime=runtime)

    response = await service.submit_decision(
        brief=brief,
        target_type="brand_candidate",
        request=HumanDecisionRequest(
            target_id="brand_satisfy",
            decision_request_id="req_1",
            decision_status="selected",
            rationale="值得深入看内容打法",
        ),
        user_id="user_1",
    )

    assert response.decision_status == "selected"
    assert response.is_current is True
    assert response.history_count == 1
    assert response.advancement == {
        "next_step": "brand_content_deep_research",
        "resource_policy": "full_deep_research",
    }
    assert store.list_observation_events("trc_decision_1")[-1].event_name == "human_decision_submitted"
    assert runtime.events[-1]["event_type"] == "human_decision_submitted"
    assert runtime.events[-1]["payload"]["decision_id"] == response.decision_id


@pytest.mark.asyncio
async def test_idempotent_retry_returns_same_decision_without_duplicate_events(store, brief):
    runtime = FakeWorkflowRuntime()
    service = ResearchDecisionService(store=store, workflow_runtime=runtime)
    request = HumanDecisionRequest(
        target_id="brand_satisfy",
        decision_request_id="req_idempotent",
        decision_status="watchlist",
    )

    first = await service.submit_decision(brief=brief, target_type="brand_candidate", request=request, user_id="user_1")
    second = await service.submit_decision(brief=brief, target_type="brand_candidate", request=request, user_id="user_1")

    assert second.decision_id == first.decision_id
    assert second.idempotent_replay is True
    assert len(store.list_human_decisions_for_workflow(brief.workflow_run_id)) == 1
    assert len(store.list_observation_events("trc_decision_1")) == 1
    assert len(runtime.events) == 1


@pytest.mark.asyncio
async def test_changed_decision_appends_history_and_latest_is_current(store, brief):
    runtime = FakeWorkflowRuntime()
    service = ResearchDecisionService(store=store, workflow_runtime=runtime)

    first = await service.submit_decision(
        brief=brief,
        target_type="recommended_content",
        request=HumanDecisionRequest(
            target_id="content_1",
            decision_request_id="req_content_1",
            decision_status="watchlist",
        ),
        user_id="user_1",
    )
    second = await service.submit_decision(
        brief=brief,
        target_type="recommended_content",
        request=HumanDecisionRequest(
            target_id="content_1",
            decision_request_id="req_content_2",
            decision_status="rejected",
        ),
        user_id="user_1",
    )

    replay = service.list_decisions(brief.workflow_run_id)

    assert first.decision_id != second.decision_id
    assert second.history_count == 2
    assert [item.decision_id for item in replay.decisions] == [first.decision_id, second.decision_id]
    assert [item.decision_id for item in replay.current_decisions] == [second.decision_id]
    assert replay.decisions[0].is_current is False
    assert replay.decisions[1].is_current is True


@pytest.mark.asyncio
async def test_watchlist_brand_does_not_default_to_full_deep_research(store, brief):
    service = ResearchDecisionService(store=store, workflow_runtime=FakeWorkflowRuntime())

    response = await service.submit_decision(
        brief=brief,
        target_type="brand_candidate",
        request=HumanDecisionRequest(
            target_id="brand_satisfy",
            decision_request_id="req_watchlist",
            decision_status="watchlist",
        ),
        user_id="user_1",
    )

    assert response.advancement["resource_policy"] == "lightweight_or_deferred"
    assert response.advancement["resource_policy"] != "full_deep_research"


@pytest.mark.asyncio
async def test_invalid_status_fails_without_partial_persistence(store, brief):
    service = ResearchDecisionService(store=store, workflow_runtime=FakeWorkflowRuntime())

    with pytest.raises(ValueError, match="Unsupported decision_status"):
        await service.submit_decision(
            brief=brief,
            target_type="brand_candidate",
            request=HumanDecisionRequest(
                target_id="brand_satisfy",
                decision_request_id="req_bad",
                decision_status="maybe",
            ),
            user_id="user_1",
        )

    assert store.list_human_decisions_for_workflow(brief.workflow_run_id) == []
    assert store.list_observation_events("trc_decision_1") == []
