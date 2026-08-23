from __future__ import annotations

import json
from datetime import timedelta

import pytest

from app.content_research.models import ResearchBriefRecord
from app.content_research.observation.trace_service import (
    _duration_ms,
    _llm_recovery_projection,
    _logical_checkpoint_projection,
    _project_timing,
)
from app.content_research.persistence_models import StageCheckpointRecord
from app.content_research.presearch.service import PresearchService
from app.content_research.service import (
    ContentResearchNotFoundError,
    ContentResearchService,
    WorkflowRunManagerRuntime,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.workflow_store import WorkflowStore
from app.memory.thread_store import ThreadStore
from app.services.llm.pricing import UsageCost
from app.services.llm.types import LLMCallContext, LLMResponse, TokenUsage
from app.services.llm.usage_tracker import LLMUsageEventInput, LLMUsageTracker


class FakeLLM:
    async def generate(self, _request):
        return LLMResponse(
            content=json.dumps(
                {
                    "subject_confirmation": "徒步短裤更可能是户外服饰品类，请确认。",
                    "competitor_tags": ["迪卡侬", "凯乐石"],
                    "research_directions": ["产品营销", "用户评论痛点"],
                    "custom_competitor_input": "",
                    "subject_structure": {
                        "schema_version": "content_research_subject_structure_v1",
                        "canonical_subject": "徒步短裤",
                        "subject_type": "category",
                        "core_entities": [
                            {
                                "canonical_name": "徒步短裤",
                                "raw_mentions": ["徒步短裤"],
                            }
                        ],
                        "research_intents": ["产品营销"],
                        "context_modifiers": ["夏季"],
                        "synonym_groups": {"徒步短裤": ["户外短裤"]},
                        "ambiguities": [],
                        "resolution_state": "resolved",
                    },
                },
                ensure_ascii=False,
            ),
            provider="fake",
            model="fake-model",
            usage=TokenUsage(total_tokens=10),
            latency_ms=1,
        )


def test_llm_recovery_exposes_only_the_durable_failure_boundary():
    brief = ResearchBriefRecord(
        id="brief_recovery",
        workflow_run_id="run_recovery",
        thread_id="thread_recovery",
        schema_version="content_research_brief_v1",
        status="draft",
        payload={"configuration_source": "user", "model": "model-x"},
    )

    recovery = _llm_recovery_projection(
        run_status="waiting_user",
        current_stage="presearch",
        runtime_steps=[{"step_name": "presearch", "error_code": "llm_auth_invalid"}],
        workflow_events=[
            {
                "event_type": "run_waiting_user",
                "created_at": "2026-08-03T01:02:03+00:00",
                "payload_json": {
                    "step_name": "presearch",
                    "reason_code": "llm_auth_invalid",
                    "reason_message": "secret",
                    "recovery_required": True,
                },
            }
        ],
        brief=brief,
    )

    assert recovery == {
        "required": True,
        "required_since": "2026-08-03T01:02:03+00:00",
        "error_code": "llm_auth_invalid",
        "configuration_source": "user",
        "model": "model-x",
    }
    assert "secret" not in str(recovery)


def test_logical_checkpoint_projection_is_safe_and_newest_first(store):
    from app.content_research.models import utcnow

    now = utcnow()
    store.save_stage_checkpoint(
        StageCheckpointRecord(
            id="scp-plan-safe",
            schema_version="content_research_stage_checkpoint_v1",
            payload={
                "query_plan_hash": "a" * 64,
                "primary_group_count": 2,
                "fallback_group_count": 1,
                "complete_query": "徒步短裤 API_KEY=secret",
                "raw_subject": "private subject",
            },
            workflow_run_id="run-safe",
            subagent_task_id="sat-safe",
            stage_name="query_plan",
            input_fingerprint="plan-fingerprint",
            status="completed",
            started_at=now,
            finished_at=now,
        )
    )
    store.save_stage_checkpoint(
        StageCheckpointRecord(
            id="scp-coverage-safe",
            schema_version="content_research_stage_checkpoint_v1",
            payload={
                "satisfied": False,
                "reason_codes": ["minimum_relevant_samples_unmet"],
                "counts": {"discovered": 30, "admitted": 0},
                "note_id": "secret-note-id",
            },
            workflow_run_id="run-safe",
            subagent_task_id="sat-safe",
            stage_name="coverage_decision",
            input_fingerprint="coverage-fingerprint",
            status="completed",
            started_at=now,
            finished_at=now,
            created_at=now + timedelta(seconds=1),
        )
    )

    projection = _logical_checkpoint_projection(store, "run-safe")

    assert [item["stage"] for item in projection] == [
        "coverage_decision",
        "query_plan",
    ]
    assert projection[1]["query_plan_hash_short"] == "a" * 12
    assert projection[0]["counts"] == {"discovered": 30, "admitted": 0}
    serialized = json.dumps(projection, ensure_ascii=False)
    assert "complete_query" not in serialized
    assert "private subject" not in serialized
    assert "secret-note-id" not in serialized
    assert "API_KEY" not in serialized


def test_marketing_conclusion_checkpoint_projects_only_actionable_facts(store):
    store.save_stage_checkpoint(
        StageCheckpointRecord(
            id="scp-marketing-conclusion-safe",
            schema_version="content_research_stage_checkpoint_v1",
            payload={
                "tracks": {
                    "need": {
                        "state": "selected",
                        "supporting_note_count": 3,
                        "independent_author_count": 2,
                        "body_quote_note_count": 3,
                        "candidate_count": 4,
                        "statement": "private conclusion",
                        "note_id": "secret-note-id",
                        "author_id": "secret-author-id",
                    },
                    "value": {
                        "state": "directional",
                        "supporting_note_count": 2,
                        "independent_author_count": 1,
                        "reason_codes": [
                            "conclusion_note_count_unmet",
                            "conclusion_author_count_unmet",
                        ],
                    },
                    "message": {
                        "state": "no_single_primary_conclusion",
                        "supporting_note_count": 9,
                        "independent_author_count": 7,
                        "reason_codes": ["conclusion_support_tied"],
                    },
                },
                "candidate_count": 12,
                "policy_hash": "secret-policy-hash",
                "prompt": "secret prompt",
            },
            workflow_run_id="run-marketing-safe",
            subagent_task_id="marketing-conclusion:plan-safe",
            stage_name="marketing_conclusion",
            input_fingerprint="private-input-fingerprint",
            status="completed",
        )
    )

    checkpoint = _logical_checkpoint_projection(store, "run-marketing-safe")[0]

    assert checkpoint == {
        "stage": "marketing_conclusion",
        "status": "completed",
        "tracks": {
            "need": {
                "state": "selected",
                "supporting_note_count": 3,
                "independent_author_count": 2,
            },
            "value": {
                "state": "directional",
                "supporting_note_count": 2,
                "independent_author_count": 1,
                "reason_codes": [
                    "conclusion_note_count_unmet",
                    "conclusion_author_count_unmet",
                ],
            },
            "message": {
                "state": "no_single_primary_conclusion",
                "reason_codes": ["conclusion_support_tied"],
            },
        },
    }
    serialized = json.dumps(checkpoint, ensure_ascii=False)
    for forbidden in (
        "body_quote_note_count",
        "candidate_count",
        "statement",
        "note_id",
        "author_id",
        "policy_hash",
        "prompt",
        "private-input-fingerprint",
    ):
        assert forbidden not in serialized


def test_marketing_conclusion_checkpoint_projects_stable_recovery_only(store):
    store.save_stage_checkpoint(
        StageCheckpointRecord(
            id="scp-marketing-conclusion-unavailable",
            schema_version="content_research_stage_checkpoint_v1",
            payload={
                "reason_codes": ["marketing_analysis_unavailable", "secret-reason"],
                "recovery_action": "repair_model_configuration_and_resume",
                "provider_payload": {"error": "secret provider response"},
                "api_key": "secret-api-key",
            },
            workflow_run_id="run-marketing-unavailable",
            subagent_task_id="marketing-conclusion:plan-unavailable",
            stage_name="marketing_conclusion",
            input_fingerprint="private-input-fingerprint",
            status="waiting_user",
        )
    )

    checkpoint = _logical_checkpoint_projection(store, "run-marketing-unavailable")[0]

    assert checkpoint == {
        "stage": "marketing_conclusion",
        "status": "waiting_user",
        "reason_codes": ["marketing_analysis_unavailable"],
        "recovery_action": "repair_model_configuration_and_resume",
    }


def test_marketing_conclusion_checkpoint_projects_packet_replay_deltas_only(store):
    store.save_stage_checkpoint(
        StageCheckpointRecord(
            id="scp-marketing-conclusion-replay",
            schema_version="content_research_stage_checkpoint_v1",
            payload={
                "tracks": {},
                "replayed_from_persisted_packets": True,
                "provider_operation_count_delta": 0,
                "packet_count_delta": 0,
                "query": "secret query",
                "prompt": "secret prompt",
                "quote": "secret quote",
                "candidate_count": 3,
                "note_id": "secret-note-id",
                "author_id": "secret-author-id",
            },
            workflow_run_id="run-marketing-replay",
            subagent_task_id="marketing-conclusion:plan-replay",
            stage_name="marketing_conclusion",
            input_fingerprint="private-input-fingerprint",
            status="completed",
        )
    )

    checkpoint = _logical_checkpoint_projection(store, "run-marketing-replay")[0]

    assert checkpoint == {
        "stage": "marketing_conclusion",
        "status": "completed",
        "tracks": {},
        "replayed_from_persisted_packets": True,
        "provider_operation_count_delta": 0,
        "packet_count_delta": 0,
    }
    serialized = json.dumps(checkpoint, ensure_ascii=False)
    for forbidden in (
        "query",
        "prompt",
        "quote",
        "candidate_count",
        "note_id",
        "author_id",
        "private-input-fingerprint",
    ):
        assert forbidden not in serialized


def test_final_timeout_recovery_keeps_a_safe_reload_boundary():
    brief = ResearchBriefRecord(
        id="brief_timeout",
        workflow_run_id="run_timeout",
        thread_id="thread_timeout",
        schema_version="content_research_brief_v1",
        status="final_timeout",
        payload={"configuration_source": "system_default", "model": "model-x"},
    )

    recovery = _llm_recovery_projection(
        run_status="waiting_user",
        current_stage="presearch",
        runtime_steps=[{"step_name": "presearch", "error_code": "PRESEARCH_FINAL_TIMEOUT"}],
        workflow_events=[
            {
                "event_type": "run_waiting_user",
                "created_at": "2026-08-03T02:03:04+00:00",
                "payload_json": {
                    "step_name": "presearch",
                    "reason_code": "PRESEARCH_FINAL_TIMEOUT",
                    "reason_message": "private timeout detail",
                    "recovery_required": True,
                },
            }
        ],
        brief=brief,
    )

    assert recovery["required"] is True
    assert recovery["required_since"] == "2026-08-03T02:03:04+00:00"
    assert recovery["error_code"] is None
    assert "private timeout detail" not in str(recovery)


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "content_research.db")


@pytest.fixture()
def store(db_path):
    return SQLiteContentResearchStore(db_path)


@pytest.fixture()
def service(db_path, store):
    return ContentResearchService(
        store=store,
        presearch=PresearchService(
            FakeLLM(), first_feedback_timeout_seconds=0.05, hard_cutoff_seconds=0.1
        ),
        workflow_runtime=WorkflowRunManagerRuntime(db_path),
    )




async def _unconfirmed_workflow(service):
    async with ThreadStore(service._store._db_path) as thread_store:
        thread = await thread_store.create_thread(
            title="Trace 预检索",
            workspace_id="ws-trace",
            brand_id="brand-trace",
        )
    return await service.submit_presearch(
        command_id="trace-unconfirmed-presearch",
        seed_text="徒步短裤",
        user_note="关注异常边界",
        thread_id=thread["id"],
        user_id="user-confirm-boundary",
        workspace_id="ws-trace",
    )


async def _step_timings(db_path: str, workflow_run_id: str) -> dict[str, dict]:
    async with WorkflowStore(db_path) as workflow_store:
        steps = await workflow_store.list_steps(workflow_run_id)
    return {step.step_name: step.timing_json or {} for step in steps}


def _assert_no_open_execution_span(timing: dict) -> None:
    assert all(span.get("finished_at") for span in timing.get("execution_spans", []))


async def _record_usage(db_path: str, workflow_run_id: str) -> None:
    async with LLMUsageTracker(db_path) as tracker:
        await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(
                    session_id="thread-trace-unit",
                    job_id=workflow_run_id,
                    step_id="presearch",
                    step_name="presearch",
                    agent_name="PresearchAgent",
                ),
                provider="fake",
                model="fake-model",
                model_policy="test",
                usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
                cost=UsageCost(input_cost=0.01, output_cost=0.02, total_cost=0.03),
                latency_ms=123,
                status="success",
            )
        )
        await tracker.record(
            LLMUsageEventInput(
                context=LLMCallContext(
                    session_id="thread-trace-unit",
                    job_id=workflow_run_id,
                    step_id="source_collect_minimal",
                    step_name="source_collect_minimal",
                    agent_name="CommentInsightAgent",
                ),
                provider="fake",
                model="fake-model",
                model_policy="test",
                usage=TokenUsage(prompt_tokens=5, completion_tokens=0, total_tokens=5),
                cost=UsageCost(total_cost=0.0),
                latency_ms=50,
                status="error",
                error_message="temporary failure",
            )
        )






def test_trace_timing_marks_legacy_step_estimated_without_precision_invention():
    timing = _project_timing(
        {
            "status": "succeeded",
            "started_at": "2026-08-03 01:00:00",
            "completed_at": "2026-08-03 01:00:03",
        },
        as_of="2026-08-03T01:00:10.123456+00:00",
    )

    assert timing == {
        "execution_started_at": "2026-08-03T01:00:00+00:00",
        "execution_finished_at": "2026-08-03T01:00:03+00:00",
        "active_duration_ms": 3000,
        "timing_source": "estimated",
    }


def test_trace_timing_projects_open_queue_only_record_as_recorded_at_server_as_of():
    timing = _project_timing(
        {
            "status": "pending",
            "timing_json": {
                "queued_at": "2026-08-03T01:00:00.000001+00:00",
                "queue_spans": [
                    {
                        "started_at": "2026-08-03T01:00:00.000001+00:00",
                        "finished_at": None,
                    }
                ],
            },
        },
        as_of="2026-08-03T01:00:01.500001+00:00",
    )

    assert timing == {
        "queued_at": "2026-08-03T01:00:00.000001+00:00",
        "queue_duration_ms": 1500,
        "timing_source": "recorded",
    }


def test_terminal_trace_duration_ignores_later_metadata_timestamps():
    duration_ms = _duration_ms(
        traces=[],
        observation_events=[],
        workflow_events=[{"created_at": "2026-08-03T01:00:05+00:00"}],
        run={
            "status": "succeeded",
            "started_at": "2026-08-03T01:00:00+00:00",
            "completed_at": "2026-08-03T01:00:01+00:00",
            "updated_at": "2026-08-03T01:00:06+00:00",
        },
    )

    assert duration_ms == 1_000


@pytest.mark.parametrize(
    ("status", "stale_key"),
    [
        ("succeeded", "waiting_started_at"),
        ("running", "retry_backoff_started_at"),
    ],
)
def test_trace_timing_does_not_project_stale_inactive_boundaries(status, stale_key):
    timing = _project_timing(
        {
            "status": status,
            "timing_json": {
                "queued_at": "2026-08-03T01:00:00.000001+00:00",
                "execution_spans": [
                    {
                        "started_at": "2026-08-03T01:00:00.100001+00:00",
                        "finished_at": "2026-08-03T01:00:00.900001+00:00",
                    }
                ],
                stale_key: "2026-08-03T01:00:00.900001+00:00",
            },
        },
        as_of="2026-08-03T01:00:10.000001+00:00",
    )

    assert timing["timing_source"] == "recorded"
    assert stale_key not in timing








@pytest.mark.asyncio
async def test_trace_missing_workflow_raises_content_research_not_found(service):
    with pytest.raises(ContentResearchNotFoundError):
        await service.get_workflow_trace("run_missing")
