from __future__ import annotations

import json
from datetime import timedelta

import pytest

from app.content_research.api_schemas import ContentResearchBriefConfirmRequest
from app.content_research.execution_decision_identity import build_execution_decision_identity
from app.content_research.models import ResearchBriefRecord
from app.content_research.observation.trace_service import (
    _duration_ms,
    _llm_recovery_projection,
    _logical_checkpoint_projection,
    _project_timing,
)
from app.content_research.persistence_models import StageCheckpointRecord
from app.content_research.presearch.service import PresearchService
from app.content_research.scope_contract import (
    CoverageSnapshot,
    ScopeConstraint,
    ScopeQueryGroupInput,
    build_scope_contract,
)
from app.content_research.service import (
    ContentResearchNotFoundError,
    ContentResearchService,
    WorkflowRunManagerRuntime,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.workflow_store import WorkflowStore
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
                    "custom_research_question": "",
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


async def _confirmed_workflow(service):
    presearch = await service.submit_presearch(
        seed_text="徒步短裤",
        user_note="关注夏季",
        thread_id="thread-trace-unit",
        user_id="user-trace-unit",
    )
    await service.confirm_brief(
        brief_id=presearch.brief_id,
        confirmation_request=ContentResearchBriefConfirmRequest(
            confirmed_subject="徒步短裤",
            subject_type="category",
            selected_competitors=["迪卡侬"],
            custom_competitors=["凯乐石"],
            selected_directions=["product_marketing", "content_performance"],
            custom_research_question="关注轻量速干",
            primary_marketing_goal="content_seeding",
        ),
    )
    return presearch


async def _unconfirmed_workflow(service):
    return await service.submit_presearch(
        seed_text="徒步短裤",
        user_note="关注异常边界",
        thread_id="thread-confirm-boundary",
        user_id="user-confirm-boundary",
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


@pytest.mark.asyncio
async def test_trace_aggregates_runtime_observations_and_usage(db_path, service):
    presearch = await _confirmed_workflow(service)
    await _record_usage(db_path, presearch.workflow_run_id)

    trace = await service.get_workflow_trace(presearch.workflow_run_id)

    assert trace.workflow_run_id == presearch.workflow_run_id
    assert trace.thread_id == "thread-trace-unit"
    assert trace.current_stage == "formal_research"
    assert trace.run_status == "running"
    assert trace.recoverable is True
    assert trace.duration_ms >= 0
    assert trace.error_count >= 1
    assert trace.retry_count >= 1
    assert [item["event_name"] for item in trace.observation_events] == [
        "presearch_started",
        "presearch_completed",
    ]
    assert "child_tasks_created" in [event["event_type"] for event in trace.workflow_events]
    assert [step["step_name"] for step in trace.runtime_steps] == [
        "presearch",
        "brief_confirm",
        "plan_build",
        "formal_research",
    ]
    assert len(trace.runtime_child_tasks) == 2
    assert trace.usage_summary == {}
    assert trace.usage_steps == []
    assert trace.usage_events == []


@pytest.mark.asyncio
async def test_trace_returns_zero_usage_defaults_when_no_usage_rows(service):
    presearch = await _confirmed_workflow(service)

    trace = await service.get_workflow_trace(presearch.workflow_run_id)

    assert trace.usage_summary == {}
    assert trace.usage_steps == []
    assert trace.usage_events == []


@pytest.mark.asyncio
async def test_trace_projects_execution_identity_and_ordered_safe_facts(service, store):
    presearch = await _unconfirmed_workflow(service)
    contract = build_scope_contract(
        workflow_run_id=presearch.workflow_run_id,
        research_plan_id="rp_trace_identity",
        version=1,
        constraints=(
            ScopeConstraint("core_object", "核心对象", "徒步短裤", "required"),
            ScopeConstraint("season", "季节", "夏季", "required"),
        ),
        query_groups=(ScopeQueryGroupInput("夏季 徒步短裤", "夏季 徒步短裤"),),
    )
    store.save_scope_contract(contract)
    snapshot = CoverageSnapshot(
        id="scv_trace_identity",
        workflow_run_id=presearch.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        state="awaiting_scope_decision",
        constraint_counts={},
        unmet_constraint_ids=("season",),
    )
    store.save_coverage_snapshot(snapshot)
    unit, _created = store.resolve_coverage_to_execution_unit_atomically(
        snapshot=snapshot,
        decision={
            "resolution": "expand_required_constraint",
            "constraint_id": "season",
            "supplementary_queries": ("夏季 徒步短裤 防晒",),
        },
    )
    attempt = store.claim_execution_unit(execution_unit_id=unit.id, owner="worker-secret")
    assert attempt is not None and attempt.lease_token is not None
    assert store.record_provider_request(
        execution_unit_id=unit.id,
        attempt_no=attempt.attempt_no,
        lease_token=attempt.lease_token,
        payload={
            "provider": "xiaohongshu",
            "provider_operation": "discover_candidates",
            "query": "must-not-reach-trace",
            "cookie": "must-not-reach-trace",
        },
    )
    assert store.record_provider_outcome(
        execution_unit_id=unit.id,
        attempt_no=attempt.attempt_no,
        lease_token=attempt.lease_token,
        provider_state="completed",
        payload={
            "provider": "xiaohongshu",
            "provider_operation": "discover_candidates",
            "result_status": "completed",
            "raw_response": "must-not-reach-trace",
        },
    )

    trace = await service.get_workflow_trace(presearch.workflow_run_id)

    expected_identity = build_execution_decision_identity(
        coverage_snapshot_id=snapshot.id,
        source_scope_contract_id=contract.id,
        resulting_scope_contract_id=contract.id,
        resolution="expand_required_constraint",
        target_constraint_id="season",
        supplementary_queries=("夏季 徒步短裤 防晒",),
    )
    assert trace.model_dump(mode="json")["execution_units"] == [
        {
            "id": unit.id,
            "state": "running",
            "recovery_state": "replayable",
            "identity_schema": "execution_decision_identity_v1",
            "identity_state": "canonical",
            "identity_json": expected_identity.payload,
            "facts": [
                {
                    "attempt_no": 0,
                    "sequence_no": 1,
                    "kind": "decision_accepted",
                    "payload": {"decision": expected_identity.payload},
                },
                {
                    "attempt_no": 0,
                    "sequence_no": 2,
                    "kind": "attempt_claimed",
                    "payload": {},
                },
                {
                    "attempt_no": 0,
                    "sequence_no": 3,
                    "kind": "provider_request_recorded",
                    "payload": {
                        "provider": "xiaohongshu",
                        "provider_operation": "discover_candidates",
                    },
                },
                {
                    "attempt_no": 0,
                    "sequence_no": 4,
                    "kind": "provider_outcome_recorded",
                    "payload": {
                        "provider": "xiaohongshu",
                        "provider_operation": "discover_candidates",
                        "result_status": "completed",
                    },
                },
            ],
        }
    ]
    assert "must-not-reach-trace" not in trace.model_dump_json()
    assert "worker-secret" not in trace.model_dump_json()


@pytest.mark.asyncio
async def test_trace_projects_recorded_timing_without_exposing_runtime_json(service):
    presearch = await _confirmed_workflow(service)

    trace = await service.get_workflow_trace(presearch.workflow_run_id)

    formal_step = trace.runtime_steps[-1]
    timing = formal_step["timing"]
    assert timing["timing_source"] == "recorded"
    assert timing["queued_at"].endswith("+00:00")
    assert timing["execution_started_at"].endswith("+00:00")
    assert timing["active_duration_ms"] >= 0
    assert timing["queue_duration_ms"] >= 0
    assert "timing_json" not in formal_step


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
async def test_confirm_validation_failure_aborts_brief_execution_span(db_path, service):
    presearch = await _unconfirmed_workflow(service)

    with pytest.raises(ValueError, match="Unknown research directions"):
        await service.confirm_brief(
            brief_id=presearch.brief_id,
            confirmation_request=ContentResearchBriefConfirmRequest(
                confirmed_subject="徒步短裤",
                subject_type="category",
                selected_directions=["not_in_lite_catalog"],
                primary_marketing_goal="content_seeding",
            ),
        )

    timings = await _step_timings(db_path, presearch.workflow_run_id)
    _assert_no_open_execution_span(timings["brief_confirm"])
    assert timings["plan_build"].get("execution_spans") in (None, [])


@pytest.mark.asyncio
async def test_plan_build_failure_aborts_plan_without_overlapping_brief(
    db_path, service, monkeypatch
):
    presearch = await _unconfirmed_workflow(service)

    def fail_plan_build(**_kwargs):
        raise RuntimeError("plan build failed")

    monkeypatch.setattr(service._plan_builder, "build", fail_plan_build)
    with pytest.raises(RuntimeError, match="plan build failed"):
        await service.confirm_brief(
            brief_id=presearch.brief_id,
            confirmation_request=ContentResearchBriefConfirmRequest(
                confirmed_subject="徒步短裤",
                subject_type="category",
                selected_directions=["product_marketing"],
                primary_marketing_goal="content_seeding",
            ),
        )

    timings = await _step_timings(db_path, presearch.workflow_run_id)
    _assert_no_open_execution_span(timings["brief_confirm"])
    _assert_no_open_execution_span(timings["plan_build"])
    brief_span = timings["brief_confirm"]["execution_spans"][-1]
    plan_span = timings["plan_build"]["execution_spans"][-1]
    assert brief_span["finished_at"] <= plan_span["started_at"]


@pytest.mark.asyncio
async def test_confirmation_persistence_failure_aborts_plan_execution_span(
    db_path, service, monkeypatch
):
    presearch = await _unconfirmed_workflow(service)

    async def fail_persistence(*_args, **_kwargs):
        raise RuntimeError("confirmation persistence failed")

    monkeypatch.setattr(service._dispatch, "persist_confirmation", fail_persistence)
    with pytest.raises(RuntimeError, match="confirmation persistence failed"):
        await service.confirm_brief(
            brief_id=presearch.brief_id,
            confirmation_request=ContentResearchBriefConfirmRequest(
                confirmed_subject="徒步短裤",
                subject_type="category",
                selected_directions=["product_marketing"],
                primary_marketing_goal="content_seeding",
            ),
        )

    timings = await _step_timings(db_path, presearch.workflow_run_id)
    _assert_no_open_execution_span(timings["brief_confirm"])
    _assert_no_open_execution_span(timings["plan_build"])


@pytest.mark.asyncio
async def test_trace_missing_workflow_raises_content_research_not_found(service):
    with pytest.raises(ContentResearchNotFoundError):
        await service.get_workflow_trace("run_missing")


@pytest.mark.asyncio
async def test_trace_projects_durable_provider_failure_without_raw_request(service):
    presearch = await _confirmed_workflow(service)
    service._store.save_stage_checkpoint(
        StageCheckpointRecord(
            id="scp-provider-failure",
            schema_version="content_research_stage_checkpoint_v1",
            workflow_run_id=presearch.workflow_run_id,
            subagent_task_id="sat-provider",
            stage_name="operation",
            input_fingerprint="fingerprint-1",
            status="auth_required",
            payload={
                "workflow_run_id": presearch.workflow_run_id,
                "operation": "discover",
                "operation_fingerprint": "fingerprint-1",
                "operation_state": "auth_required",
                "request": {"query": "徒步短裤"},
                "completion": {
                    "failure_code": "auth_required",
                    "failure_reason": "auth_required",
                    "retryable": False,
                    "recovery_action": "更新小红书登录态后继续。",
                },
            },
        )
    )

    trace = await service.get_workflow_trace(presearch.workflow_run_id)

    assert len(trace.provider_operations) == 1
    operation = trace.provider_operations[0]
    assert operation.pop("operation_id").startswith("op_")
    assert operation == {
        "operation_fingerprint": "fingerprint-1",
        "operation": "discover",
        "provider": None,
        "provider_operation": None,
        "source_kind": None,
        "result_status": None,
        "status": "auth_required",
        "started_at": None,
        "finished_at": None,
        "failure_code": "auth_required",
        "retryable": False,
    }


@pytest.mark.asyncio
async def test_trace_projects_provider_access_rejection_with_safe_browser_session_recovery(service):
    presearch = await _confirmed_workflow(service)
    service._store.save_stage_checkpoint(
        StageCheckpointRecord(
            id="scp-provider-access-rejected",
            schema_version="content_research_stage_checkpoint_v1",
            workflow_run_id=presearch.workflow_run_id,
            subagent_task_id="sat-provider",
            stage_name="operation",
            input_fingerprint="fingerprint-detail-1",
            status="failed",
            payload={
                "workflow_run_id": presearch.workflow_run_id,
                "operation": "collect_note_detail",
                "operation_fingerprint": "fingerprint-detail-1",
                "operation_state": "failed",
                "request": {
                    "note_id": "note-1",
                    "xsec_token": "must-not-reach-trace",
                    "cookie": "must-not-reach-trace",
                },
                "completion": {
                    "provider": "xiaohongshu",
                    "provider_operation": "collect_note_detail",
                    "source_kind": "note_detail",
                    "result_status": "failed",
                    "item_count": 0,
                    "completeness": "unavailable",
                    "cookie_status": "valid",
                    "failure_code": "provider_access_rejected",
                    "failure_reason": "provider_access_rejected",
                    "retryable": False,
                    "recovery_action": "笔记详情请求的浏览器安全上下文不兼容；请启用或更新兼容的浏览器会话详情采集提供者后重新发起调研。",
                },
            },
        )
    )

    trace = await service.get_workflow_trace(presearch.workflow_run_id)

    assert len(trace.provider_operations) == 1
    operation = trace.provider_operations[0]
    assert operation.pop("operation_id").startswith("op_")
    assert operation == {
        "operation_fingerprint": "fingerprint-detail-1",
        "operation": "collect_note_detail",
        "provider": "xiaohongshu",
        "provider_operation": "collect_note_detail",
        "source_kind": "note_detail",
        "result_status": "failed",
        "status": "failed",
        "started_at": None,
        "finished_at": None,
        "failure_code": "provider_access_rejected",
        "retryable": False,
    }
    assert trace.external_api_summary == {
        "call_count": 1,
        "completed_count": 0,
        "failed_count": 1,
        "by_provider": {"xiaohongshu": 1},
        "by_operation": {"collect_note_detail": 1},
    }
    assert "must-not-reach-trace" not in trace.model_dump_json()


@pytest.mark.asyncio
async def test_direction_evidence_uses_post_detail_selection_for_coverage_and_counts(service):
    presearch = await _confirmed_workflow(service)
    base_payload = {
        "workflow_run_id": presearch.workflow_run_id,
        "direction_id": "product_marketing",
    }
    service._store.save_stage_checkpoint(
        StageCheckpointRecord(
            id="scp-selection-before-detail",
            schema_version="content_research_stage_checkpoint_v1",
            workflow_run_id=presearch.workflow_run_id,
            subagent_task_id="sat-product",
            stage_name="selection",
            input_fingerprint="selection-fingerprint",
            status="completed",
            payload={
                **base_payload,
                "selection": {
                    "status": "complete",
                    "selected_source_count": 3,
                    "eligible_source_count": 3,
                    "independent_source_count": 3,
                    "coverage_unmet_query_group_ids": [],
                    "decisions": [],
                },
            },
        )
    )
    service._store.save_stage_checkpoint(
        StageCheckpointRecord(
            id="scp-detail-final-selection",
            schema_version="content_research_stage_checkpoint_v1",
            workflow_run_id=presearch.workflow_run_id,
            subagent_task_id="sat-product",
            stage_name="detail",
            input_fingerprint="selection-fingerprint",
            status="completed",
            payload={
                **base_payload,
                "selection": {
                    "status": "incomplete",
                    "selected_source_count": 2,
                    "eligible_source_count": 2,
                    "independent_source_count": 2,
                    "coverage_unmet_query_group_ids": ["qg_missing"],
                    "decisions": [],
                },
            },
        )
    )
    service._store.save_stage_checkpoint(
        StageCheckpointRecord(
            id="scp-packet-final-selection",
            schema_version="content_research_stage_checkpoint_v1",
            workflow_run_id=presearch.workflow_run_id,
            subagent_task_id="sat-product",
            stage_name="packet",
            input_fingerprint="selection-fingerprint",
            status="completed",
            payload={**base_payload, "status": "incomplete", "packet_ids": []},
        )
    )

    evidence = service.get_direction_evidence(
        workflow_run_id=presearch.workflow_run_id,
        direction_id="product_marketing",
    )

    assert evidence.status == "incomplete"
    assert evidence.counts["selected_source_count"] == 2
    assert evidence.coverage_unmet_query_group_ids == ["qg_missing"]
