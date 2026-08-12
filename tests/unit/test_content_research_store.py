from __future__ import annotations

from dataclasses import replace

import pytest

from app.content_research.models import (
    ObservationEventRecord,
    ResearchBriefRecord,
    ResearchDirectionRecord,
    ResearchPlanRecord,
    SubagentTaskRecord,
    TraceRecord,
    utcnow,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore


def _payload(kind: str) -> dict:
    return {"schema_version": "content_research_p0_v1", "kind": kind}


@pytest.fixture()
def store(tmp_path):
    return SQLiteContentResearchStore(str(tmp_path / "content_research.db"))


def test_sqlite_store_creates_and_reads_all_p0_records(store):
    now = utcnow()
    brief = ResearchBriefRecord(
        id="rb_1",
        workflow_run_id="wr_1",
        thread_id="thread_1",
        schema_version="content_research_brief_v1",
        status="draft",
        payload=_payload("brief"),
    )
    plan = ResearchPlanRecord(
        id="rp_1",
        brief_id=brief.id,
        workflow_run_id=brief.workflow_run_id,
        thread_id=brief.thread_id,
        schema_version="content_research_plan_v1",
        status="draft",
        payload=_payload("plan"),
    )
    direction = ResearchDirectionRecord(
        id="rd_1",
        plan_id=plan.id,
        workflow_run_id=brief.workflow_run_id,
        thread_id=brief.thread_id,
        schema_version="content_research_direction_v1",
        status="proposed",
        priority=10,
        payload=_payload("direction"),
    )
    task = SubagentTaskRecord(
        id="sat_1",
        workflow_run_id=brief.workflow_run_id,
        thread_id=brief.thread_id,
        schema_version="content_research_subagent_task_v1",
        status="queued",
        plan_id=plan.id,
        direction_id=direction.id,
        payload=_payload("task"),
    )
    trace = TraceRecord(
        id="trc_1",
        workflow_run_id=brief.workflow_run_id,
        thread_id=brief.thread_id,
        schema_version="content_research_trace_v1",
        status="running",
        started_at=now,
        payload=_payload("trace"),
    )
    event = ObservationEventRecord(
        id="obs_1",
        trace_id=trace.id,
        workflow_run_id=brief.workflow_run_id,
        thread_id=brief.thread_id,
        schema_version="content_research_observation_event_v1",
        status="recorded",
        sequence_no=1,
        event_type="task_started",
        event_name="presearch_started",
        timestamp=now,
        payload=_payload("observation"),
    )

    store.save_brief(brief)
    store.save_plan(plan)
    store.save_direction(direction)
    store.save_subagent_task(task)
    store.save_trace(trace)
    store.append_observation_event(event)

    assert store.get_brief(brief.id) == brief
    assert store.get_plan(plan.id) == plan
    assert store.list_plans_for_brief(brief.id) == [plan]
    assert store.list_directions_for_plan(plan.id) == [direction]
    assert store.get_subagent_task(task.id) == task
    assert store.list_subagent_tasks_for_workflow(brief.workflow_run_id) == [task]
    assert store.get_trace(trace.id) == trace
    assert store.list_traces_for_workflow(brief.workflow_run_id) == [trace]
    assert store.list_observation_events(trace.id) == [event]


def test_observation_events_are_append_only(store):
    now = utcnow()
    event = ObservationEventRecord(
        id="obs_duplicate",
        trace_id="trc_1",
        workflow_run_id="wr_1",
        thread_id="thread_1",
        schema_version="content_research_observation_event_v1",
        status="recorded",
        sequence_no=1,
        event_type="task_started",
        event_name="presearch_started",
        timestamp=now,
        payload=_payload("observation"),
    )

    store.append_observation_event(event)

    with pytest.raises(ValueError, match="append-only"):
        store.append_observation_event(replace(event, event_name="mutated"))


def test_payload_schema_version_is_required(store):
    brief = ResearchBriefRecord(
        id="rb_invalid",
        workflow_run_id="wr_1",
        thread_id="thread_1",
        schema_version="content_research_brief_v1",
        status="draft",
        payload={"subject": "missing schema version"},
    )

    with pytest.raises(ValueError, match="schema_version"):
        store.save_brief(brief)


def test_mutable_records_can_be_updated_by_id(store):
    brief = ResearchBriefRecord(
        id="rb_1",
        workflow_run_id="wr_1",
        thread_id="thread_1",
        schema_version="content_research_brief_v1",
        status="draft",
        payload=_payload("brief"),
    )
    updated = replace(brief, status="ready", payload={**brief.payload, "confirmed": True})

    store.save_brief(brief)
    store.save_brief(updated)

    assert store.get_brief(brief.id) == updated
