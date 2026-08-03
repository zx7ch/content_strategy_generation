from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.content_research.contracts import SamplePolicy, build_default_snapshot
from app.content_research.models import SubagentTaskRecord
from app.content_research.persistence_models import (
    ClaimAdmissionDecisionRecord,
    DirectionalEvidencePacketRecord,
    DirectionResultDecisionRecord,
    DirectionSourceProjectionRecord,
    StageCheckpointRecord,
    WeakSignalRecord,
)
from app.content_research.sources import SourceAdapterRegistry
from app.content_research.sources.base import SourceOperationResult
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.content_research.workflow.directional_pipeline import (
    DirectionalEvidencePipeline,
    OperationOutcomeUnknownError,
    QueryGroup,
    compile_query_groups,
)
from app.content_research.workflow.task_router import SubagentTaskRouter


def _build_frozen_pipeline_snapshot(
    *,
    snapshot_id,
    workflow_run_id,
    direction_id,
    subject,
    questions,
    competitors=(),
    groups=None,
    run_as_of_at=None,
):
    frozen_at = run_as_of_at or datetime(2026, 7, 30, tzinfo=timezone.utc)
    frozen_groups = groups or compile_query_groups(
        direction_id=direction_id,
        subject=subject,
        questions=list(questions),
        competitors=list(competitors),
        run_as_of_at=frozen_at,
    )
    snapshot, policies, contracts = build_default_snapshot(
        snapshot_id=snapshot_id,
        workflow_run_id=workflow_run_id,
        brief_id="rb",
        plan_id="rp",
        run_as_of_at=frozen_at,
        direction_ids=(direction_id,),
        confirmed_subject=subject,
        query_groups_by_direction={
            direction_id: tuple(
                {
                    "id": group.id,
                    "direction_id": group.direction_id,
                    "normalized_query": group.query,
                    "priority": group.priority,
                    "sort": group.sort,
                    "time_window": dict(
                        group.time_window
                        or {"end_at": frozen_at.isoformat()}
                    ),
                    "candidate_cap": group.candidate_limit,
                    "roles": list(group.roles),
                    "activation": group.activation,
                    "normalized_identity": group.normalized_identity,
                }
                for group in frozen_groups
            )
        },
    )
    return snapshot, policies, contracts, frozen_groups


@pytest.mark.asyncio
async def test_frozen_fallback_activates_once_without_repeating_primary_discovery(
    tmp_path,
):
    store = SQLiteContentResearchStore(str(tmp_path / "coverage-fallback.db"))
    frozen_at = datetime(2026, 8, 4, tzinfo=timezone.utc)
    primary = QueryGroup(
        id="qg-primary",
        direction_id="product_marketing",
        query="徒步短裤 产品营销",
        priority=0,
        candidate_limit=20,
        time_window={"end_at": frozen_at.isoformat()},
        roles=("core_intent",),
        activation="primary",
        normalized_identity="primary-identity",
    )
    fallback = QueryGroup(
        id="qg-fallback",
        direction_id="product_marketing",
        query="户外短裤 夏季",
        priority=2,
        candidate_limit=20,
        time_window={"end_at": frozen_at.isoformat()},
        roles=("coverage_fallback",),
        activation="coverage_fallback",
        normalized_identity="fallback-identity",
    )
    snapshot, policies, contracts, _ = _build_frozen_pipeline_snapshot(
        snapshot_id="rps-fallback",
        workflow_run_id="run-fallback",
        direction_id="product_marketing",
        subject="徒步短裤",
        questions=("产品营销",),
        groups=(primary, fallback),
        run_as_of_at=frozen_at,
    )
    store.save_run_policy_snapshot(snapshot)
    for policy in policies:
        store.save_sample_policy(policy)
    for frozen_contract in contracts:
        store.save_direction_contract(frozen_contract)
    contract = contracts[0]
    calls: list[str] = []

    def note(note_id: str, author_id: str) -> dict:
        return {
            "provider": "xiaohongshu",
            "canonical_id": note_id,
            "source_kind": "note_detail",
            "source_url": f"https://example.test/{note_id}",
            "author": author_id,
            "author_id": author_id,
            "title": "夏季徒步短裤实测",
            "content_text": "轻量透气的徒步短裤适合夏季户外。",
            "tags": ["徒步短裤"],
            "note_type": "image_text",
            "metrics": {"likes": 10},
            "metrics_observed_at": frozen_at.isoformat(),
            "source_published_at": "2026-08-01T00:00:00+00:00",
            "ip_location": "上海",
            "media": {"count": 1},
            "field_availability": {
                field: "present" for field in contract.required_note_fields
            },
        }

    async def discover(group):
        calls.append(group.id)
        if group.id == primary.id:
            return [note("note-primary", "author-primary")]
        return [
            note("note-fallback-1", "author-fallback-1"),
            note("note-fallback-2", "author-fallback-2"),
        ]

    kwargs = {
        "workflow_run_id": "run-fallback",
        "subagent_task_id": "sat-fallback",
        "direction_id": "product_marketing",
        "subject": "徒步短裤",
        "questions": ["产品营销"],
        "competitors": [],
        "author_cap": policies[0].author_cap,
        "minimum_samples": policies[0].minimum_samples,
        "minimum_independent_authors": policies[0].minimum_independent_authors,
        "detail_fetch_cap": policies[0].detail_fetch_cap,
        "snapshot_id": snapshot.id,
        "run_as_of_at": snapshot.run_as_of_at,
        "admission_contract": contract,
        "admission_policy": policies[0],
        "policy_snapshot": snapshot,
        "discover": discover,
    }
    first = await DirectionalEvidencePipeline(store).execute(**kwargs)
    operation_ids = [
        item.id
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.stage_name == "operation"
    ]
    second = await DirectionalEvidencePipeline(store).execute(**kwargs)

    assert calls == [primary.id, fallback.id]
    assert first.selection.status == "complete"
    assert second.selection == first.selection
    assert [
        item.id
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.stage_name == "operation"
    ] == operation_ids
    fallback_decisions = [
        item
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.stage_name == "fallback_decision"
    ]
    assert [item.payload["state"] for item in fallback_decisions] == [
        "activated",
        "not_needed",
    ]


@pytest.mark.asyncio
async def test_pipeline_replays_completed_checkpoints_without_recalling_adapter(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "direction.db"))
    pipeline = DirectionalEvidencePipeline(store)
    calls = 0

    async def discover(group):
        nonlocal calls
        calls += 1
        return [
            {
                "provider": "xiaohongshu",
                "canonical_id": "note-1",
                "canonical_source_id": "note-1",
                "author_id": "author-1",
                "source_kind": "note_detail",
                "title": "one",
                "content_text": "body",
                "field_availability": {"content_text": "present"},
            }
        ]

    kwargs = {
        "subagent_task_id": "sat-1",
        "direction_id": "product_marketing",
        "subject": "徒步短裤",
        "questions": ["卖点"],
        "competitors": [],
        "author_cap": 1,
        "discover": discover,
    }
    first = await pipeline.execute(**kwargs)
    second = await pipeline.execute(**kwargs)

    assert calls == 1
    assert not first.replayed_collect
    assert second.replayed_collect and second.replayed_selection and second.replayed_packet
    assert first.packet_ids == second.packet_ids
    checkpoints = store.list_typed_records(StageCheckpointRecord)
    assert {item.stage_name for item in checkpoints} == {"collect", "collect_page", "operation", "selection", "packet"}
    completed_stages = {
        item.stage_name: item
        for item in checkpoints
        if item.status == "completed" and item.stage_name in {"collect", "collect_page", "operation", "selection", "packet"}
    }
    assert set(completed_stages) == {"collect", "collect_page", "operation", "selection", "packet"}
    assert all(item.started_at is not None and item.finished_at is not None for item in completed_stages.values())
    assert all(item.finished_at >= item.started_at for item in completed_stages.values())
    assert len(store.list_typed_records(DirectionalEvidencePacketRecord)) == 1
    assert len(store.list_typed_records(DirectionSourceProjectionRecord)) == 1


@pytest.mark.asyncio
async def test_changed_projection_creates_another_immutable_packet_version(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "packet-version.db"))
    pipeline = DirectionalEvidencePipeline(store)

    def discover_for(title):
        async def discover(group):
            return [
                {
                    "provider": "xiaohongshu",
                    "canonical_id": "note-1",
                    "canonical_source_id": "note-1",
                    "author_id": "author-1",
                    "source_kind": "note_detail",
                    "title": title,
                    "content_text": "body",
                    "field_availability": {"content_text": "present"},
                }
            ]

        return discover

    await pipeline.execute(
        subagent_task_id="sat-v1",
        direction_id="product_marketing",
        subject="徒步短裤",
        questions=["卖点"],
        competitors=[],
        author_cap=1,
        discover=discover_for("old"),
    )
    await pipeline.execute(
        subagent_task_id="sat-v2",
        direction_id="product_marketing",
        subject="徒步短裤",
        questions=["卖点"],
        competitors=[],
        author_cap=1,
        discover=discover_for("new"),
    )

    packets = store.list_directional_evidence_packets("local_sat-v1", "product_marketing")
    assert len(packets) == 1
    assert packets[0].payload["field_projection"]["title"] == "old"
    second_run_packets = store.list_directional_evidence_packets(
        "local_sat-v2", "product_marketing"
    )
    assert {item.payload["field_projection"]["title"] for item in second_run_packets} == {"new"}


@pytest.mark.asyncio
async def test_search_candidate_is_not_persisted_as_detail_evidence(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "search-is-not-detail.db"))
    pipeline = DirectionalEvidencePipeline(store)

    async def discover(group):
        return [
            {
                "provider": "xiaohongshu",
                "canonical_id": "note-1",
                "source_kind": "search_result_minimal",
            }
        ]

    result = await pipeline.execute(
        subagent_task_id="sat-search",
        direction_id="product_marketing",
        subject="徒步短裤",
        questions=["卖点"],
        competitors=[],
        author_cap=1,
        discover=discover,
    )

    assert result.selection.status == "incomplete"
    assert result.packet_ids == ()
    assert store.list_directional_evidence_packets("local_sat-search", "product_marketing") == []


@pytest.mark.asyncio
async def test_failed_discover_persists_typed_terminal_operation_outcome(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "failed-discover.db"))
    pipeline = DirectionalEvidencePipeline(store)

    async def discover(_group):
        return SourceOperationResult(
            provider="xiaohongshu",
            operation="discover_candidates",
            source_kind="search_result",
            status="failed",
            items=[],
            failure_reason="auth_required",
            retryable=False,
            completeness="unavailable",
        )

    await pipeline.execute(
        subagent_task_id="sat-failed-discover",
        direction_id="product_marketing",
        subject="徒步短裤",
        questions=["卖点"],
        competitors=[],
        author_cap=1,
        discover=discover,
    )

    operations = [
        item for item in store.list_typed_records(StageCheckpointRecord)
        if item.stage_name == "operation"
    ]
    assert len(operations) == 2  # failed first page terminates its query group
    terminal = [item for item in operations if item.status == "auth_required"]
    assert len(terminal) == 1
    assert all(item.finished_at is not None for item in terminal)
    assert all(item.payload["completion"]["failure_code"] == "auth_required" for item in terminal)
    assert all(item.payload["completion"]["recovery_action"] == "更新小红书登录态后继续。" for item in terminal)
    assert all(item.payload["completion"]["provider"] == "xiaohongshu" for item in terminal)
    assert all(item.payload["completion"]["provider_operation"] == "discover_candidates" for item in terminal)
    assert all(item.payload["completion"]["source_kind"] == "search_result" for item in terminal)
    assert all(item.payload["completion"]["item_count"] == 0 for item in terminal)
    assert all(item.payload["completion"]["completeness"] == "unavailable" for item in terminal)
    pages = [item for item in store.list_typed_records(StageCheckpointRecord) if item.stage_name == "collect_page"]
    assert all(item.payload["failure_reason"] == "auth_required" for item in pages)


@pytest.mark.asyncio
async def test_failed_detail_persists_its_provider_outcome(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "failed-detail.db"))
    pipeline = DirectionalEvidencePipeline(store)

    async def discover(_group):
        return [{
            "provider": "xiaohongshu", "canonical_id": "note-1",
            "source_kind": "search_result_minimal",
        }]

    async def collect_detail(_candidate):
        return SourceOperationResult(
            provider="xiaohongshu", operation="collect_note_detail", source_kind="note_detail",
            status="failed", items=[], failure_reason="parser_error", completeness="unavailable",
        )

    result = await pipeline.execute(
        subagent_task_id="sat-failed-detail", direction_id="product_marketing",
        subject="徒步短裤", questions=["卖点"], competitors=[], author_cap=1,
        discover=discover, collect_detail=collect_detail,
    )

    assert result.selection.status == "insufficient_evidence"
    assert result.blocking_failure_code == "parser_error"
    terminal = [
        item for item in store.list_typed_records(StageCheckpointRecord)
        if item.stage_name == "operation" and item.payload.get("operation") == "detail"
        and item.status == "failed"
    ]
    assert len(terminal) == 1
    assert terminal[0].payload["completion"]["failure_code"] == "parser_error"
    assert terminal[0].finished_at is not None


@pytest.mark.asyncio
async def test_content_performance_pipeline_persists_visible_formats_with_snapshots_and_replays(
    tmp_path,
):
    store = SQLiteContentResearchStore(str(tmp_path / "content-performance.db"))
    snapshot, policies, contracts, _groups = _build_frozen_pipeline_snapshot(
        snapshot_id="rps-performance",
        workflow_run_id="run-performance",
        direction_id="content_performance",
        subject="通勤",
        questions=("格式",),
    )
    store.save_run_policy_snapshot(snapshot)
    for item in policies:
        store.save_sample_policy(item)
    for item in contracts:
        store.save_direction_contract(item)
    contract = next(item for item in contracts if item.direction_id == "content_performance")
    policy = next(item for item in policies if item.direction_id == "content_performance")
    calls = 0

    async def discover(group):
        nonlocal calls
        calls += 1
        return [
            {
                "provider": "xiaohongshu",
                "canonical_id": f"note-{index}",
                "canonical_source_id": f"note-{index}",
                "source_kind": "note_detail",
                "author_id": f"author-{index}",
                "title": f"标题框架 {index}",
                "content_text": f"清单式正文 {index}",
                "note_type": "image_text",
                "source_published_at": "2026-07-17T00:00:00+00:00",
                "metrics": {"like_count": 100 + index},
                "metrics_observed_at": "2026-07-18T00:00:00+00:00",
                "media": {"cover_count": 1},
                "field_availability": {field: "present" for field in contract.required_note_fields},
            }
            for index in range(3)
        ]

    kwargs = {
        "workflow_run_id": "run-performance",
        "subagent_task_id": "sat-performance",
        "direction_id": "content_performance",
        "subject": "短裤",
        "questions": ["格式"],
        "competitors": [],
        "author_cap": policy.author_cap,
        "minimum_samples": policy.minimum_samples,
        "minimum_independent_authors": policy.minimum_independent_authors,
        "discover": discover,
        "admission_contract": contract,
        "admission_policy": policy,
        "policy_snapshot": snapshot,
    }
    await DirectionalEvidencePipeline(store).execute(**kwargs)
    await DirectionalEvidencePipeline(store).execute(**kwargs)

    candidates = store.list_claim_candidates("run-performance", "content_performance")
    assert calls == 1
    assert {item.claim_type for item in candidates} == {
        "observed_high_engagement_sample",
        "visible_content_format",
    }
    assert all(
        item.payload["scope"]["engagement_context"]["metrics_observed_at"] for item in candidates
    )
    assert (
        len(
            [
                item
                for item in store.list_typed_records(StageCheckpointRecord)
                if item.stage_name == "admission"
            ]
        )
        == 1
    )


@pytest.mark.asyncio
async def test_competitor_pipeline_requires_quoted_names_and_deduplicates_author_and_canonical_evidence(
    tmp_path,
):
    store = SQLiteContentResearchStore(str(tmp_path / "competitor-discovery.db"))
    snapshot, policies, contracts, _groups = _build_frozen_pipeline_snapshot(
        snapshot_id="rps-competitor",
        workflow_run_id="run-competitor",
        direction_id="competitor_discovery",
        subject="竞品A",
        questions=("竞品",),
    )
    store.save_run_policy_snapshot(snapshot)
    for item in policies:
        store.save_sample_policy(item)
    for item in contracts:
        store.save_direction_contract(item)
    contract = next(item for item in contracts if item.direction_id == "competitor_discovery")
    policy = next(item for item in policies if item.direction_id == "competitor_discovery")
    calls = 0

    async def discover(group):
        nonlocal calls
        calls += 1
        items = []
        for note_id, author in (
            ("note-1", "author-1"),
            ("note-1", "author-1"),
            ("note-2", "author-1"),
            ("note-3", "author-2"),
        ):
            items.append(
                {
                    "provider": "xiaohongshu",
                    "canonical_id": note_id,
                    "canonical_source_id": note_id,
                        "source_kind": "note_detail",
                        "author_id": author,
                        "author": author,
                    "title": f"{note_id} 标题",
                    "content_text": f"竞品A 的 {note_id} 使用场景",
                    "tags": ["竞品A"],
                    "competitor_names": ["竞品A"],
                    "metrics": {"like_count": 100},
                    "metrics_observed_at": "2026-07-18T00:00:00+00:00",
                    "field_availability": {
                        field: "present" for field in contract.required_note_fields
                    },
                }
            )
        return items

    kwargs = {
        "workflow_run_id": "run-competitor",
        "subagent_task_id": "sat-competitor",
        "direction_id": "competitor_discovery",
        "subject": "短裤",
        "questions": ["竞品"],
        "competitors": [],
        "author_cap": policy.author_cap,
        "minimum_samples": policy.minimum_samples,
        "minimum_independent_authors": policy.minimum_independent_authors,
        "discover": discover,
        "admission_contract": contract,
        "admission_policy": policy,
        "policy_snapshot": snapshot,
    }
    first = await DirectionalEvidencePipeline(store).execute(**kwargs)
    await DirectionalEvidencePipeline(store).execute(**kwargs)

    candidates = store.list_claim_candidates("run-competitor", "competitor_discovery")
    assert calls == 1
    assert first.selection.selected_source_count == 3
    assert first.selection.independent_source_count == 2
    assert {item.claim_type for item in candidates} == {
        "named_competitor",
        "visible_content_expression",
    }
    assert all(item.payload["scope"]["competitor_name"] in item.statement for item in candidates)
    assert (
        len(
            [
                item
                for item in store.list_typed_records(StageCheckpointRecord)
                if item.stage_name == "admission"
            ]
        )
        == 1
    )


@pytest.mark.asyncio
async def test_brand_activity_pipeline_excludes_future_notes_and_replays_admission(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "brand-activity.db"))
    snapshot, policies, contracts, _groups = _build_frozen_pipeline_snapshot(
        snapshot_id="rps-activity",
        workflow_run_id="run-activity",
        direction_id="brand_activity",
        subject="活动",
        questions=("上新",),
    )
    store.save_run_policy_snapshot(snapshot)
    for item in policies:
        store.save_sample_policy(item)
    for item in contracts:
        store.save_direction_contract(item)
    contract = next(item for item in contracts if item.direction_id == "brand_activity")
    policy = next(item for item in policies if item.direction_id == "brand_activity")
    calls = 0

    async def discover(group):
        nonlocal calls
        calls += 1
        return [
            {
                "canonical_id": f"note-{index}",
                "canonical_source_id": f"note-{index}",
                "source_kind": "note_detail",
                "author_id": f"a-{index}",
                "title": "夏日上新",
                "content_text": "夏日上新活动",
                "tags": ["上新"],
                "activity_signals": ["launch_signal"],
                "note_type": "image_text",
                "metrics": {"like_count": 10},
                "metrics_observed_at": "2026-07-18T00:00:00+00:00",
                "source_published_at": date,
                "field_availability": {field: "present" for field in contract.required_note_fields},
            }
            for index, date in enumerate(
                [
                    "2026-07-17T00:00:00+00:00",
                    "2026-07-17T00:00:00+00:00",
                    "2026-07-17T00:00:00+00:00",
                    "2099-01-01T00:00:00+00:00",
                ]
            )
        ]

    kwargs = {
        "workflow_run_id": "run-activity",
        "subagent_task_id": "sat-activity",
        "direction_id": "brand_activity",
        "subject": "短裤",
        "questions": ["上新"],
        "competitors": [],
        "author_cap": policy.author_cap,
        "minimum_samples": policy.minimum_samples,
        "minimum_independent_authors": policy.minimum_independent_authors,
        "discover": discover,
        "run_as_of_at": snapshot.run_as_of_at,
        "admission_contract": contract,
        "admission_policy": policy,
        "policy_snapshot": snapshot,
    }
    first = await DirectionalEvidencePipeline(store).execute(**kwargs)
    await DirectionalEvidencePipeline(store).execute(**kwargs)
    assert calls == 1
    assert first.selection.selected_source_count == 3
    assert store.list_claim_candidates("run-activity", "brand_activity")
    assert (
        len(
            [
                item
                for item in store.list_typed_records(StageCheckpointRecord)
                if item.stage_name == "admission"
            ]
        )
        == 1
    )


@pytest.mark.asyncio
async def test_keyword_growth_pipeline_keeps_pattern_without_insufficient_baseline_growth_claim(
    tmp_path,
):
    store = SQLiteContentResearchStore(str(tmp_path / "keyword-growth.db"))
    snapshot, policies, contracts, _groups = _build_frozen_pipeline_snapshot(
        snapshot_id="rps-keyword",
        workflow_run_id="run-keyword",
        direction_id="keyword_growth",
        subject="轻量",
        questions=("关键词",),
    )
    store.save_run_policy_snapshot(snapshot)
    for item in policies:
        store.save_sample_policy(item)
    for item in contracts:
        store.save_direction_contract(item)
    contract = next(item for item in contracts if item.direction_id == "keyword_growth")
    policy = next(item for item in policies if item.direction_id == "keyword_growth")
    calls = 0

    async def discover(group):
        nonlocal calls
        calls += 1
        return [
            {
                "canonical_id": f"note-{index}",
                "canonical_source_id": f"note-{index}",
                "source_kind": "note_detail",
                "author_id": f"a-{index}",
                "title": "轻量通勤",
                "content_text": "轻量通勤装备",
                "tags": ["轻量"],
                "keyword_patterns": ["轻量"],
                "reference_window": {"non_overlapping": True, "comparable": False},
                "source_published_at": "2026-07-17T00:00:00+00:00",
                "metrics": {"like_count": 2},
                "metrics_observed_at": "2026-07-18T00:00:00+00:00",
                "field_availability": {field: "present" for field in contract.required_note_fields},
            }
            for index in range(3)
        ]

    kwargs = {
        "workflow_run_id": "run-keyword",
        "subagent_task_id": "sat-keyword",
        "direction_id": "keyword_growth",
        "subject": "短裤",
        "questions": ["关键词"],
        "competitors": [],
        "author_cap": policy.author_cap,
        "minimum_samples": policy.minimum_samples,
        "minimum_independent_authors": policy.minimum_independent_authors,
        "discover": discover,
        "admission_contract": contract,
        "admission_policy": policy,
        "policy_snapshot": snapshot,
    }
    await DirectionalEvidencePipeline(store).execute(**kwargs)
    await DirectionalEvidencePipeline(store).execute(**kwargs)
    claims = store.list_claim_candidates("run-keyword", "keyword_growth")
    assert calls == 1
    assert {item.claim_type for item in claims} == {"sampled_keyword_pattern"}
    assert (
        len(
            [
                item
                for item in store.list_typed_records(StageCheckpointRecord)
                if item.stage_name == "admission"
            ]
        )
        == 1
    )


@pytest.mark.asyncio
async def test_formal_router_uses_directional_execution_pipeline_not_legacy_agent(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "formal-router.db"))
    snapshot, policies, contracts, _groups = _build_frozen_pipeline_snapshot(
        snapshot_id="rps_1",
        workflow_run_id="run_1",
        direction_id="product_marketing",
        subject="短裤",
        questions=("卖点",),
    )
    store.save_run_policy_snapshot(snapshot)
    for policy in policies:
        store.save_sample_policy(policy)
    for contract in contracts:
        store.save_direction_contract(contract)

    class Adapter:
        async def discover_candidates(self, request):
            return SourceOperationResult(
                provider="xiaohongshu",
                operation="discover_candidates",
                source_kind="search_result_minimal",
                status="completed",
                items=[
                    {
                        "canonical_id": "note-1",
                        "source_kind": "search_result_minimal",
                        "source_url": "https://example/note-1",
                    }
                ],
            )

        async def collect_note_detail(self, request):
            return SourceOperationResult(
                provider="xiaohongshu",
                operation="collect_note_detail",
                source_kind="note_detail",
                status="completed",
                items=[
                    {
                        "canonical_id": request.note_id,
                        "source_kind": "note_detail",
                        "content_text": "body",
                        "field_availability": {"content_text": "present"},
                    }
                ],
            )

        async def collect_comments(self, request):
            raise AssertionError("product direction does not collect comments")

    task = SubagentTaskRecord(
        id="sat_1",
        workflow_run_id="run_1",
        thread_id="thread_1",
        schema_version="v1",
        status="queued",
        plan_id="rp_1",
        direction_id="product_marketing",
        payload={
            "schema_version": "content_research_subagent_task_v1",
            "agent_name": "DirectionalExecutionPipeline",
            "input_payload": {
                "confirmed_subject": "短裤",
                "competitors": [],
                "direction": {"id": "product_marketing", "questions": ["卖点"]},
            },
        },
    )
    store.save_subagent_task(task)
    router = SubagentTaskRouter(
        store=store, source_registry=SourceAdapterRegistry({"xiaohongshu": Adapter()})
    )
    terminal = await router.execute_task(task)

    assert terminal.status == "partial_completed"
    assert terminal.payload["output_payload"]["metadata"]["packet_ids"]
    assert store.list_directional_evidence_packets("run_1", "product_marketing")
    candidates = store.list_claim_candidates("run_1", "product_marketing")
    assert {item.claim_type for item in candidates} == {"product_value_expression"}
    assert all(item.payload["quote_refs"][0]["field_path"] == "content_text" for item in candidates)
    checkpoints = store.list_typed_records(StageCheckpointRecord)
    assert {item.stage_name for item in checkpoints} >= {"facts", "admission"}
    replayed = await router.execute_task(task)
    assert replayed.status == "partial_completed"
    assert (
        len(
            [
                item
                for item in store.list_typed_records(StageCheckpointRecord)
                if item.stage_name == "admission"
            ]
        )
        == 1
    )


@pytest.mark.asyncio
async def test_product_marketing_pipeline_does_not_count_rejected_material_toward_related_sample_threshold(
    tmp_path,
):
    store = SQLiteContentResearchStore(str(tmp_path / "query-subject-relevance.db"))
    groups = compile_query_groups(
        direction_id="product_marketing",
        subject="速干徒步短裤",
        questions=["卖点"],
        competitors=[],
    )
    snapshot, policies, contracts, groups = _build_frozen_pipeline_snapshot(
        snapshot_id="rps-query-subject",
        workflow_run_id="run-query-subject",
        direction_id="product_marketing",
        subject="速干徒步短裤",
        questions=("卖点",),
        groups=groups,
    )
    contract = next(item for item in contracts if item.direction_id == "product_marketing")
    policy = next(item for item in policies if item.direction_id == "product_marketing")
    store.save_run_policy_snapshot(snapshot)
    store.save_sample_policy(policy)
    store.save_direction_contract(contract)

    async def discover(group):
        common = {
            "provider": "xiaohongshu",
            "source_kind": "note_detail",
            "query_group_id": group.id,
            "metrics": {"like_count": 99999},
            "metrics_observed_at": "2026-07-30T00:00:00+00:00",
            "tags": [],
            "note_type": "normal",
            "field_availability": {
                field: "present" for field in contract.required_note_fields
            },
        }
        return [
            {
                **common,
                "canonical_id": "note-black-jargon",
                "canonical_source_id": "note-black-jargon",
                "author_id": "author-black",
                "title": "打工人必学的向上管理黑话",
                "content_text": "职场沟通里最重要的是颗粒度和闭环。",
            },
            {
                **common,
                "canonical_id": "note-shorts",
                "canonical_source_id": "note-shorts",
                "author_id": "author-shorts",
                "title": "夏日短裤怎么穿：轻量徒步搭配",
                "content_text": "这条短裤适合炎热天气的轻量徒步。",
            },
            {
                **common,
                "canonical_id": "note-shorts-2",
                "canonical_source_id": "note-shorts-2",
                "author_id": "author-shorts-2",
                "title": "速干短裤的周末徒步场景",
                "content_text": "运动短裤也能兼顾轻便和收纳。",
            },
        ]

    await DirectionalEvidencePipeline(store).execute(
        workflow_run_id="run-query-subject",
        subagent_task_id="sat-query-subject",
        direction_id="product_marketing",
        subject="速干徒步短裤",
        questions=["卖点"],
        competitors=[],
        author_cap=policy.author_cap,
        minimum_samples=policy.minimum_samples,
        minimum_independent_authors=policy.minimum_independent_authors,
        discover=discover,
        admission_contract=contract,
        admission_policy=policy,
        policy_snapshot=snapshot,
    )

    candidates = {
        item.id: item
        for item in store.list_claim_candidates(
            "run-query-subject", "product_marketing"
        )
    }
    decisions = store.list_typed_records(ClaimAdmissionDecisionRecord)
    black_decisions = [
        item
        for item in decisions
        if "黑话" in candidates[item.claim_candidate_id].statement
        or "职场沟通" in candidates[item.claim_candidate_id].statement
    ]
    valid_decisions = [
        item
        for item in decisions
        if "短裤" in candidates[item.claim_candidate_id].statement
    ]
    packets = store.list_directional_evidence_packets(
        "run-query-subject", "product_marketing"
    )

    assert packets
    assert all(
        packet.payload["retrieval_context"]["query_group_ids"]
        == [groups[0].id]
        for packet in packets
    )
    assert black_decisions
    assert all(item.decision == "rejected" for item in black_decisions)
    assert all(
        "query_subject_not_supported" in item.payload["reason_codes"]
        for item in black_decisions
    )
    assert valid_decisions
    assert all(item.decision == "downgraded" for item in valid_decisions)
    assert all(
        item.payload["reason_codes"] == ["sample_threshold_unmet"]
        for item in valid_decisions
    )


@pytest.mark.asyncio
async def test_formal_collection_fails_before_adapter_without_a_full_locked_query_plan(
    tmp_path,
):
    store = SQLiteContentResearchStore(str(tmp_path / "missing-locked-plan.db"))
    snapshot, policies, contracts = build_default_snapshot(
        snapshot_id="rps-missing-plan",
        workflow_run_id="run-missing-plan",
        brief_id="rb",
        plan_id="rp",
        direction_ids=("product_marketing",),
        confirmed_subject="短裤",
        query_group_ids_by_direction={"product_marketing": ("qg_only_id",)},
    )
    called = False

    async def discover(_group):
        nonlocal called
        called = True
        return []

    with pytest.raises(ValueError, match="full locked query plan"):
        await DirectionalEvidencePipeline(store).execute(
            workflow_run_id="run-missing-plan",
            subagent_task_id="sat-missing-plan",
            direction_id="product_marketing",
            subject="短裤",
            questions=["卖点"],
            competitors=[],
            author_cap=policies[0].author_cap,
            discover=discover,
            admission_contract=contracts[0],
            admission_policy=policies[0],
            policy_snapshot=snapshot,
        )

    assert called is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_fields_index,expected_decision,expected_eligible",
    [
        (None, "admitted", 3),
        (2, "downgraded", 2),
    ],
)
async def test_product_admission_counts_only_relevant_field_eligible_stable_authors(
    tmp_path,
    missing_fields_index,
    expected_decision,
    expected_eligible,
):
    store = SQLiteContentResearchStore(
        str(tmp_path / f"eligible-{missing_fields_index}.db")
    )
    groups = compile_query_groups(
        direction_id="product_marketing",
        subject="速干徒步短裤",
        questions=["卖点"],
        competitors=[],
    )
    snapshot, policies, contracts, groups = _build_frozen_pipeline_snapshot(
        snapshot_id="rps-eligible",
        workflow_run_id="run-eligible",
        direction_id="product_marketing",
        subject="速干徒步短裤",
        questions=("卖点",),
        groups=groups,
    )
    contract = contracts[0]
    policy = policies[0]
    store.save_run_policy_snapshot(snapshot)
    store.save_sample_policy(policy)
    store.save_direction_contract(contract)

    async def discover(group):
        return [
            {
                "provider": "xiaohongshu",
                "source_kind": "note_detail",
                "canonical_id": f"note-{index}",
                "canonical_source_id": f"note-{index}",
                "author_id": f"author-{index}",
                "author": "相同展示名",
                "title": f"短裤夏日卖点 {index}",
                "content_text": f"速干短裤适合轻量徒步 {index}",
                "tags": [],
                "note_type": "normal",
                "metrics": {"like_count": index},
                "metrics_observed_at": "2026-07-30T00:00:00+00:00",
                "field_availability": (
                    {}
                    if index == missing_fields_index
                    else {
                        field: "present"
                        for field in contract.required_note_fields
                    }
                ),
                "query_group_id": group.id,
            }
            for index in range(3)
        ]

    await DirectionalEvidencePipeline(store).execute(
        workflow_run_id="run-eligible",
        subagent_task_id="sat-eligible",
        direction_id="product_marketing",
        subject="速干徒步短裤",
        questions=["卖点"],
        competitors=[],
        author_cap=policy.author_cap,
        minimum_samples=policy.minimum_samples,
        minimum_independent_authors=policy.minimum_independent_authors,
        discover=discover,
        admission_contract=contract,
        admission_policy=policy,
        policy_snapshot=snapshot,
    )

    packets = store.list_directional_evidence_packets(
        "run-eligible", "product_marketing"
    )
    assert {packet.payload["field_projection"]["author_id"] for packet in packets} == {
        "author-0",
        "author-1",
        "author-2",
    }
    decisions = store.list_typed_records(ClaimAdmissionDecisionRecord)
    assert decisions
    assert {item.decision for item in decisions} == {expected_decision}
    assert {
        item.payload["computed_metrics"]["selected_source_count"]
        for item in decisions
    } == {3}
    assert {
        item.payload["computed_metrics"]["relevance_qualified_source_count"]
        for item in decisions
    } == {3}
    assert {
        item.payload["computed_metrics"]["eligible_source_count"]
        for item in decisions
    } == {expected_eligible}
    assert {
        item.payload["computed_metrics"]["independent_author_count"]
        for item in decisions
    } == {expected_eligible}
    admission_checkpoint = next(
        item
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.stage_name == "admission"
    )
    assert admission_checkpoint.payload["policy_snapshot_id"] == snapshot.id
    assert admission_checkpoint.payload["policy_snapshot_hash"] == (
        snapshot.effective_policy_hash
    )
    assert admission_checkpoint.payload["algorithm_version"] == (
        snapshot.effective_policy["admission_algorithm_version"]
    )
    assert admission_checkpoint.payload["relevance_contract"] == (
        contract.metadata["query_relevance"]
    )


@pytest.mark.asyncio
async def test_provider_author_names_are_conservative_fallback_identities_at_threshold(
    tmp_path,
):
    store = SQLiteContentResearchStore(str(tmp_path / "display-author-only.db"))
    snapshot, policies, contracts, groups = _build_frozen_pipeline_snapshot(
        snapshot_id="rps-display-author",
        workflow_run_id="run-display-author",
        direction_id="product_marketing",
        subject="速干徒步短裤",
        questions=("卖点",),
    )
    contract = contracts[0]
    policy = policies[0]
    store.save_run_policy_snapshot(snapshot)
    store.save_sample_policy(policy)
    store.save_direction_contract(contract)

    async def discover(group):
        return [
            {
                "provider": "xiaohongshu",
                "source_kind": "note_detail",
                "canonical_id": f"note-{index}",
                "canonical_source_id": f"note-{index}",
                "author": f"不同展示名-{index}",
                "title": f"短裤夏日卖点 {index}",
                "content_text": f"速干短裤适合轻量徒步 {index}",
                "tags": [],
                "note_type": "normal",
                "metrics": {"like_count": index},
                "metrics_observed_at": "2026-07-30T00:00:00+00:00",
                "field_availability": {
                    field: "present" for field in contract.required_note_fields
                },
                "query_group_id": group.id,
            }
            for index in range(3)
        ]

    await DirectionalEvidencePipeline(store).execute(
        workflow_run_id="run-display-author",
        subagent_task_id="sat-display-author",
        direction_id="product_marketing",
        subject="速干徒步短裤",
        questions=["卖点"],
        competitors=[],
        author_cap=policy.author_cap,
        minimum_samples=policy.minimum_samples,
        minimum_independent_authors=policy.minimum_independent_authors,
        discover=discover,
        admission_contract=contract,
        admission_policy=policy,
        policy_snapshot=snapshot,
    )

    decisions = store.list_typed_records(ClaimAdmissionDecisionRecord)
    assert decisions
    assert {item.decision for item in decisions} == {"admitted"}
    assert {
        item.payload["computed_metrics"]["eligible_source_count"]
        for item in decisions
    } == {3}
    assert {
        item.payload["computed_metrics"]["independent_author_count"]
        for item in decisions
    } == {3}
    assert {
        item.payload["computed_metrics"]["author_id"] for item in decisions
    } == {""}


@pytest.mark.asyncio
async def test_provider_author_name_fallback_collapses_normalized_duplicates(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "display-author-duplicates.db"))
    snapshot, policies, contracts, _groups = _build_frozen_pipeline_snapshot(
        snapshot_id="rps-display-author-duplicates",
        workflow_run_id="run-display-author-duplicates",
        direction_id="product_marketing",
        subject="速干徒步短裤",
        questions=("卖点",),
    )
    contract = contracts[0]
    policy = policies[0]
    store.save_run_policy_snapshot(snapshot)
    store.save_sample_policy(policy)
    store.save_direction_contract(contract)

    async def discover(group):
        return [
            {
                "provider": "xiaohongshu",
                "source_kind": "note_detail",
                "canonical_id": f"note-{index}",
                "canonical_source_id": f"note-{index}",
                "author": author,
                "title": f"短裤夏日卖点 {index}",
                "content_text": f"速干短裤适合轻量徒步 {index}",
                "tags": [],
                "note_type": "normal",
                "metrics": {"like_count": index},
                "metrics_observed_at": "2026-07-30T00:00:00+00:00",
                "field_availability": {
                    field: "present" for field in contract.required_note_fields
                },
                "query_group_id": group.id,
            }
            for index, author in enumerate(("同一作者", "  同一作者  ", "同一作者"))
        ]

    await DirectionalEvidencePipeline(store).execute(
        workflow_run_id="run-display-author-duplicates",
        subagent_task_id="sat-display-author-duplicates",
        direction_id="product_marketing",
        subject="速干徒步短裤",
        questions=["卖点"],
        competitors=[],
        author_cap=policy.author_cap,
        minimum_samples=policy.minimum_samples,
        minimum_independent_authors=policy.minimum_independent_authors,
        discover=discover,
        admission_contract=contract,
        admission_policy=policy,
        policy_snapshot=snapshot,
    )

    decisions = store.list_typed_records(ClaimAdmissionDecisionRecord)
    assert decisions
    assert {item.decision for item in decisions} == {"downgraded"}
    assert {
        item.payload["computed_metrics"]["eligible_source_count"]
        for item in decisions
    } == {3}
    assert {
        item.payload["computed_metrics"]["independent_author_count"]
        for item in decisions
    } == {1}


@pytest.mark.asyncio
async def test_packet_only_admission_replay_never_invokes_a_provider(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "packet-only-replay.db"))
    snapshot, policies, contracts, _groups = _build_frozen_pipeline_snapshot(
        snapshot_id="rps-packet-only-replay",
        workflow_run_id="run-packet-only-replay",
        direction_id="product_marketing",
        subject="速干徒步短裤",
        questions=("卖点",),
    )
    contract = contracts[0]
    policy = policies[0]
    store.save_run_policy_snapshot(snapshot)
    store.save_sample_policy(policy)
    store.save_direction_contract(contract)
    provider_calls = 0

    async def discover(group):
        nonlocal provider_calls
        provider_calls += 1
        return [
            {
                "provider": "xiaohongshu",
                "source_kind": "note_detail",
                "canonical_id": f"note-{index}",
                "canonical_source_id": f"note-{index}",
                "author": f"作者-{index}",
                "title": f"短裤夏日卖点 {index}",
                "content_text": f"速干短裤适合轻量徒步 {index}",
                "tags": [],
                "note_type": "normal",
                "metrics": {"like_count": index},
                "metrics_observed_at": "2026-07-30T00:00:00+00:00",
                "field_availability": {
                    field: "present" for field in contract.required_note_fields
                },
                "query_group_id": group.id,
            }
            for index in range(3)
        ]

    pipeline = DirectionalEvidencePipeline(store)
    initial = await pipeline.execute(
        workflow_run_id="run-packet-only-replay",
        subagent_task_id="sat-packet-only-replay",
        direction_id="product_marketing",
        subject="速干徒步短裤",
        questions=["卖点"],
        competitors=[],
        author_cap=policy.author_cap,
        minimum_samples=policy.minimum_samples,
        minimum_independent_authors=policy.minimum_independent_authors,
        discover=discover,
        policy_snapshot=snapshot,
    )
    calls_before_replay = provider_calls
    operations_before_replay = [
        item
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.stage_name == "operation"
    ]
    assert not store.list_typed_records(ClaimAdmissionDecisionRecord)

    replayed_packet_ids = pipeline.replay_admission_from_persisted_packets(
        workflow_run_id="run-packet-only-replay",
        subagent_task_id="sat-packet-only-replay",
        direction_id="product_marketing",
        contract=contract,
        policy=policy,
        snapshot=snapshot,
    )

    assert replayed_packet_ids == initial.packet_ids
    assert provider_calls == calls_before_replay
    assert [
        item.id
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.stage_name == "operation"
    ] == [item.id for item in operations_before_replay]
    decisions = store.list_typed_records(ClaimAdmissionDecisionRecord)
    assert decisions
    assert {item.decision for item in decisions} == {"admitted"}


@pytest.mark.asyncio
async def test_admission_checkpoint_and_decision_ids_change_with_threshold_inputs(
    tmp_path,
):
    store = SQLiteContentResearchStore(str(tmp_path / "snapshot-replay.db"))
    snapshot, policies, contracts, _groups = _build_frozen_pipeline_snapshot(
        snapshot_id="rps-replay-1",
        workflow_run_id="run-replay",
        direction_id="product_marketing",
        subject="速干徒步短裤",
        questions=("卖点",),
    )
    policy = policies[0]
    next_policy = replace(policy, minimum_samples=policy.minimum_samples + 1)
    contract = contracts[0]
    store.save_run_policy_snapshot(snapshot)
    store.save_sample_policy(policy)
    store.save_direction_contract(contract)

    async def discover(group):
        return [
            {
                "provider": "xiaohongshu",
                "source_kind": "note_detail",
                "canonical_id": f"note-{index}",
                "canonical_source_id": f"note-{index}",
                "author_id": f"author-{index}",
                "title": f"短裤夏日卖点 {index}",
                "content_text": f"速干短裤适合轻量徒步 {index}",
                "tags": [],
                "note_type": "normal",
                "metrics": {"like_count": index},
                "metrics_observed_at": "2026-07-30T00:00:00+00:00",
                "field_availability": {
                    field: "present" for field in contract.required_note_fields
                },
                "query_group_id": group.id,
            }
            for index in range(3)
        ]

    common = {
        "workflow_run_id": "run-replay",
        "subagent_task_id": "sat-replay",
        "direction_id": "product_marketing",
        "subject": "速干徒步短裤",
        "questions": ["卖点"],
        "competitors": [],
        "author_cap": policy.author_cap,
        "minimum_samples": policy.minimum_samples,
        "minimum_independent_authors": policy.minimum_independent_authors,
        "discover": discover,
        "admission_contract": contract,
        "admission_policy": policy,
    }
    await DirectionalEvidencePipeline(store).execute(
        **common, policy_snapshot=snapshot
    )
    await DirectionalEvidencePipeline(store).execute(
        **{
            **common,
            "minimum_samples": next_policy.minimum_samples,
            "admission_policy": next_policy,
        },
        policy_snapshot=snapshot,
    )

    checkpoints = [
        item
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.stage_name == "admission"
    ]
    decisions = store.list_typed_records(ClaimAdmissionDecisionRecord)
    assert len(checkpoints) == 2
    assert len({item.input_fingerprint for item in checkpoints}) == 2
    assert {
        item.payload["sample_policy"]["minimum_samples"]
        for item in checkpoints
    } == {
        policy.minimum_samples,
        next_policy.minimum_samples,
    }
    assert {item.policy_snapshot_id for item in decisions} == {snapshot.id}
    assert len({item.id for item in decisions}) == len(decisions)


@pytest.mark.asyncio
async def test_note_and_comment_packets_preserve_all_frozen_query_rank_hits_and_author_ids(
    tmp_path,
):
    store = SQLiteContentResearchStore(str(tmp_path / "full-hit-lineage.db"))

    async def discover(group):
        return [
            {
                "provider": "xiaohongshu",
                "source_kind": "search_result_minimal",
                "canonical_id": "note-shared",
                "canonical_source_id": "note-shared",
                "title": "短裤",
                "query_group_id": group.id,
            }
        ]

    async def collect_detail(_candidate):
        return {
            "provider": "xiaohongshu",
            "source_kind": "note_detail",
            "canonical_id": "note-shared",
            "canonical_source_id": "note-shared",
            "author_id": "note-author-id",
            "title": "短裤",
            "content_text": "短裤评论样本",
        }

    async def collect_comments(_candidate):
        return SourceOperationResult(
            "xiaohongshu",
            "collect_comments",
            "comment",
            "completed",
            [
                {
                    "canonical_id": "comment-1",
                    "source_kind": "comment",
                    "comment_text": "短裤需要更轻便",
                    "author_id": "comment-author-id",
                    "author": "评论展示名",
                    "source_published_at": "2026-07-29T00:00:00+00:00",
                    "like_count": 1,
                    "reply_depth": 0,
                    "field_availability": {
                        "comment_text": "present",
                        "source_published_at": "present",
                        "like_count": "present",
                        "reply_depth": "present",
                    },
                }
            ],
            completeness="complete",
        )

    run = await DirectionalEvidencePipeline(store).execute(
        workflow_run_id="run-lineage",
        subagent_task_id="sat-lineage",
        direction_id="comment_insight",
        subject="短裤",
        questions=["卖点", "使用场景"],
        competitors=[],
        author_cap=3,
        minimum_samples=1,
        minimum_independent_authors=1,
        discover=discover,
        collect_detail=collect_detail,
        collect_comments=collect_comments,
        required_comment_fields=(
            "comment_text",
            "source_published_at",
            "like_count",
            "reply_depth",
        ),
        comment_limit=1,
    )

    frozen_group_ids = tuple(
        sorted(
            group.id
            for group in compile_query_groups(
                direction_id="comment_insight",
                subject="短裤",
                questions=["卖点", "使用场景"],
                competitors=[],
            )
        )
    )
    assert len(frozen_group_ids) == 2
    note_packet = store.get_typed_record(
        DirectionalEvidencePacketRecord, run.packet_ids[0]
    )
    comment_packet = store.get_typed_record(
        DirectionalEvidencePacketRecord, run.comment_packet_ids[0]
    )
    assert note_packet is not None
    assert comment_packet is not None
    for packet in (note_packet, comment_packet):
        assert packet.payload["retrieval_context"]["query_group_ids"] == list(
            frozen_group_ids
        )
        assert packet.payload["retrieval_context"]["query_hits"] == [
            {"query_group_id": group_id, "rank": 1}
            for group_id in frozen_group_ids
        ]
    projections = {
        item.evidence_packet_id: item
        for item in store.list_typed_records(DirectionSourceProjectionRecord)
    }
    for packet in (note_packet, comment_packet):
        projection = projections[packet.id]
        assert projection.payload["query_group_ids"] == list(frozen_group_ids)
        assert projection.payload["query_hits"] == [
            {"query_group_id": group_id, "rank": 1}
            for group_id in frozen_group_ids
        ]
    assert note_packet.payload["field_projection"]["author_id"] == "note-author-id"
    assert (
        comment_packet.payload["field_projection"]["author_id"]
        == "comment-author-id"
    )


@pytest.mark.asyncio
async def test_completed_detail_checkpoint_reuses_detail_without_another_call(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "detail-replay.db"))
    calls = 0

    async def discover(group):
        return [{"canonical_id": "note-1", "source_kind": "search_result_minimal"}]

    async def detail(candidate):
        nonlocal calls
        calls += 1
        return {"canonical_id": "note-1", "source_kind": "note_detail", "content_text": "body"}

    pipeline = DirectionalEvidencePipeline(store)
    kwargs = {
        "subagent_task_id": "sat-detail",
        "direction_id": "product_marketing",
        "subject": "短裤",
        "questions": ["卖点"],
        "competitors": [],
        "author_cap": 1,
        "discover": discover,
        "collect_detail": detail,
    }
    first = await pipeline.execute(**kwargs)
    second = await pipeline.execute(**kwargs)

    assert calls == 1
    assert first.packet_ids == second.packet_ids
    assert {item.stage_name for item in store.list_typed_records(StageCheckpointRecord)} == {
        "collect",
        "collect_page",
        "operation",
        "selection",
        "selection_revision",
        "detail",
        "packet",
    }


@pytest.mark.asyncio
async def test_required_comment_contract_persists_parent_linkage_metadata_and_replays(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "comments.db"))
    pipeline = DirectionalEvidencePipeline(store)
    calls = 0

    async def discover(group):
        return [
            {
                "provider": "xiaohongshu",
                "canonical_id": "note-1",
                "source_kind": "note_detail",
                "author_id": "note-author",
                "content_text": "note body",
            }
        ]

    async def comments(_candidate):
        nonlocal calls
        calls += 1
        return SourceOperationResult(
            provider="xiaohongshu",
            operation="collect_comments",
            source_kind="comment",
            status="partial_completed",
            items=[
                {
                    "provider": "xiaohongshu",
                    "canonical_id": "comment-1",
                    "source_kind": "comment",
                    "content_text": "尺码偏小",
                    "author_id": "reader",
                    "field_availability": {"comment_text": "present", "parent_note_id": "present"},
                },
                {
                    "provider": "xiaohongshu",
                    "canonical_id": "comment-1",
                    "source_kind": "comment",
                    "content_text": "duplicate",
                    "author_id": "reader",
                },
            ],
            next_cursor="cursor-2",
            completeness="truncated_by_cap",
        )

    kwargs = {
        "subagent_task_id": "sat-comments",
        "direction_id": "ugc_community",
        "subject": "短裤",
        "questions": ["评论"],
        "competitors": [],
        "author_cap": 1,
        "discover": discover,
        "collect_comments": comments,
        "required_comment_fields": ("comment_text", "parent_note_id"),
    }
    first = await pipeline.execute(**kwargs)
    second = await pipeline.execute(**kwargs)

    assert calls == 2
    assert len(first.comment_packet_ids) == 1
    assert second.comment_packet_ids == first.comment_packet_ids
    comment_packet = store.get_typed_record(
        DirectionalEvidencePacketRecord, first.comment_packet_ids[0]
    )
    assert comment_packet is not None
    assert comment_packet.payload["retrieval_context"][
        "parent_note_canonical_source_id"
    ].startswith("cs_")
    assert (
        comment_packet.payload["retrieval_context"]["collection"]["completeness"]
        == "truncated_by_cap"
    )
    checkpoint = next(
        item
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.stage_name == "comments"
    )
    assert checkpoint.payload["parents"][0]["deduplicated_comment_count"] == 1
    assert checkpoint.payload["parents"][0]["deduplicated_author_count"] == 1


@pytest.mark.asyncio
async def test_ugc_community_admits_qualified_comment_sample_and_replays(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "ugc-admission.db"))
    groups = compile_query_groups(
        direction_id="ugc_community", subject="短裤", questions=["评论"], competitors=[]
    )
    snapshot, policies, contracts, groups = _build_frozen_pipeline_snapshot(
        snapshot_id="rps-ugc",
        workflow_run_id="run-ugc",
        direction_id="ugc_community",
        subject="短裤",
        questions=("评论",),
        groups=groups,
    )
    store.save_run_policy_snapshot(snapshot)
    for item in policies:
        store.save_sample_policy(item)
    for item in contracts:
        store.save_direction_contract(item)
    contract = next(item for item in contracts if item.direction_id == "ugc_community")
    policy = next(item for item in policies if item.direction_id == "ugc_community")
    calls = 0

    async def discover(group):
        return [
            {
                "provider": "xiaohongshu",
                "canonical_id": "note-1",
                "source_kind": "note_detail",
                "author_id": "owner",
                "title": "标题",
                "content_text": "正文",
                "source_published_at": "2026-07-17T00:00:00+00:00",
                "field_availability": {field: "present" for field in contract.required_note_fields},
            }
        ]

    async def comments(candidate):
        nonlocal calls
        calls += 1
        return SourceOperationResult(
            provider="xiaohongshu",
            operation="collect_comments",
            source_kind="comment",
            status="completed",
            completeness="complete",
            items=[
                {
                    "provider": "xiaohongshu",
                    "canonical_id": f"comment-{index}",
                    "source_kind": "comment",
                        "comment_text": f"短裤评论文本 {index}",
                    "author_id": f"reader-{index % 5}",
                    "source_published_at": "2026-07-17T00:00:00+00:00",
                    "like_count": 1,
                    "reply_depth": 0,
                    "field_availability": {
                        field: "present" for field in contract.required_comment_fields
                    },
                }
                for index in range(30)
            ],
        )

    kwargs = {
        "workflow_run_id": "run-ugc",
        "subagent_task_id": "sat-ugc",
        "direction_id": "ugc_community",
        "subject": "短裤",
        "questions": ["评论"],
        "competitors": [],
        "author_cap": policy.author_cap,
        "minimum_samples": 1,
        "minimum_independent_authors": 1,
        "discover": discover,
        "collect_comments": comments,
        "required_comment_fields": contract.required_comment_fields,
        "comment_limit": 30,
        "admission_contract": contract,
        "admission_policy": policy,
        "policy_snapshot": snapshot,
    }
    first = await DirectionalEvidencePipeline(store).execute(**kwargs)
    await DirectionalEvidencePipeline(store).execute(**kwargs)

    candidates = store.list_claim_candidates("run-ugc", "ugc_community")
    decisions = [
        item
        for item in store.list_typed_records(ClaimAdmissionDecisionRecord)
        if item.research_direction_id == "ugc_community"
    ]
    assert calls == 1
    assert len(first.comment_packet_ids) == 30
    assert candidates and all(item.payload["scope"]["reply_relation"] == 0 for item in candidates)
    assert all(
        item.payload["scope"]["collection"]["deduplicated_comment_count"] == 30
        for item in candidates
    )
    assert any(item.decision == "admitted" for item in decisions)
    assert (
        len(
            [
                item
                for item in store.list_typed_records(StageCheckpointRecord)
                if item.stage_name == "admission"
            ]
        )
        == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("comment_text", "claim_type"),
    [
        ("这个尺码怎么选？", "explicit_question"),
        ("这个设计太贵，不好用", "objection_or_failure"),
        ("希望增加口袋", "repeated_need_language"),
    ],
)
async def test_comment_insight_admits_qualified_comment_claims_and_replays_once(
    tmp_path, comment_text, claim_type
):
    store = SQLiteContentResearchStore(str(tmp_path / "comment-insight.db"))
    groups = compile_query_groups(
        direction_id="comment_insight", subject="短裤", questions=["需求"], competitors=[]
    )
    snapshot, policies, contracts, groups = _build_frozen_pipeline_snapshot(
        snapshot_id="rps-ci",
        workflow_run_id="run-ci",
        direction_id="comment_insight",
        subject="短裤",
        questions=("需求",),
        groups=groups,
    )
    store.save_run_policy_snapshot(snapshot)
    for item in policies:
        store.save_sample_policy(item)
    for item in contracts:
        store.save_direction_contract(item)
    contract = next(item for item in contracts if item.direction_id == "comment_insight")
    policy = next(item for item in policies if item.direction_id == "comment_insight")
    calls = 0

    async def discover(group):
        return [
            {
                "canonical_id": "note",
                "source_kind": "note_detail",
                "author_id": "owner",
                "title": "t",
                "content_text": "b",
                "field_availability": {field: "present" for field in contract.required_note_fields},
            }
        ]

    async def comments(candidate):
        nonlocal calls
        calls += 1
        return SourceOperationResult(
            provider="xiaohongshu",
            operation="collect_comments",
            source_kind="comment",
            status="completed",
            completeness="complete",
            items=[
                {
                    "canonical_id": f"c{i}",
                    "source_kind": "comment",
                        "comment_text": f"短裤{comment_text}",
                    "author_id": f"a{i % 5}",
                    "source_published_at": "2026-07-17T00:00:00+00:00",
                    "like_count": 1,
                    "reply_depth": 0,
                    "field_availability": {
                        field: "present" for field in contract.required_comment_fields
                    },
                }
                for i in range(30)
            ],
        )

    kwargs = {
        "workflow_run_id": "run-ci",
        "subagent_task_id": "sat-ci",
        "direction_id": "comment_insight",
        "subject": "短裤",
        "questions": ["需求"],
        "competitors": [],
        "author_cap": policy.author_cap,
        "minimum_samples": 1,
        "minimum_independent_authors": 1,
        "discover": discover,
        "collect_comments": comments,
        "required_comment_fields": contract.required_comment_fields,
        "comment_limit": 30,
        "admission_contract": contract,
        "admission_policy": policy,
        "policy_snapshot": snapshot,
    }
    await DirectionalEvidencePipeline(store).execute(**kwargs)
    await DirectionalEvidencePipeline(store).execute(**kwargs)
    candidates = store.list_claim_candidates("run-ci", "comment_insight")
    decisions = [
        item
        for item in store.list_typed_records(ClaimAdmissionDecisionRecord)
        if item.research_direction_id == "comment_insight"
    ]
    results = [
        item
        for item in store.list_typed_records(DirectionResultDecisionRecord)
        if item.research_direction_id == "comment_insight"
    ]
    assert calls == 1
    assert {item.claim_type for item in candidates} == {claim_type}
    assert decisions and all(item.decision == "admitted" for item in decisions)
    assert len(results) == 1
    assert results[0].payload["state"] == "formal_directional_result"
    assert set(results[0].payload["admitted_claim_ids"]) == {item.id for item in candidates}
    assert not store.list_typed_records(WeakSignalRecord)
    assert len([item for item in store.list_typed_records(StageCheckpointRecord) if item.stage_name == "admission"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("comment_count", "author_count", "reply_depth", "completeness"),
    [
        (29, 5, 0, "complete"),
        (30, 4, 0, "complete"),
        (30, 5, None, "complete"),
        (30, 5, 0, "partial"),
    ],
)
async def test_comment_insight_incomplete_comment_collection_never_becomes_formal(
    tmp_path, comment_count, author_count, reply_depth, completeness
):
    store = SQLiteContentResearchStore(str(tmp_path / "comment-insight-insufficient.db"))
    snapshot, policies, contracts, _groups = _build_frozen_pipeline_snapshot(
        snapshot_id="rps-ci-insufficient",
        workflow_run_id="run-ci-insufficient",
        direction_id="comment_insight",
        subject="短裤",
        questions=("需求",),
    )
    store.save_run_policy_snapshot(snapshot)
    for item in policies:
        store.save_sample_policy(item)
    for item in contracts:
        store.save_direction_contract(item)
    contract = next(item for item in contracts if item.direction_id == "comment_insight")
    policy = next(item for item in policies if item.direction_id == "comment_insight")

    async def discover(group):
        return [{
            "canonical_id": "note", "source_kind": "note_detail", "author_id": "owner",
            "title": "t", "content_text": "b",
            "field_availability": {field: "present" for field in contract.required_note_fields},
        }]

    async def comments(candidate):
        return SourceOperationResult(
            provider="xiaohongshu", operation="collect_comments", source_kind="comment",
            status="completed", completeness=completeness,
            items=[{
                "canonical_id": f"c{i}", "source_kind": "comment", "comment_text": "这个尺码怎么选？",
                "author_id": f"a{i % author_count}", "source_published_at": "2026-07-17T00:00:00+00:00",
                "like_count": 1, "reply_depth": reply_depth,
                "field_availability": {field: "present" for field in contract.required_comment_fields},
            } for i in range(comment_count)],
        )

    await DirectionalEvidencePipeline(store).execute(
        workflow_run_id="run-ci-insufficient", subagent_task_id="sat-ci-insufficient",
        direction_id="comment_insight", subject="短裤", questions=["需求"], competitors=[],
        author_cap=policy.author_cap, minimum_samples=1, minimum_independent_authors=1,
        discover=discover, collect_comments=comments,
        required_comment_fields=contract.required_comment_fields, comment_limit=30,
        admission_contract=contract, admission_policy=policy, policy_snapshot=snapshot,
    )

    assert not store.list_claim_candidates("run-ci-insufficient", "comment_insight")
    assert not [
        item for item in store.list_typed_records(ClaimAdmissionDecisionRecord)
        if item.research_direction_id == "comment_insight"
    ]
    results = [
        item for item in store.list_typed_records(DirectionResultDecisionRecord)
        if item.research_direction_id == "comment_insight"
    ]
    assert len(results) == 1
    assert results[0].payload["state"] == "insufficient_evidence"


@pytest.mark.asyncio
async def test_required_comment_failure_is_incomplete_and_note_only_direction_does_not_collect_comments(
    tmp_path,
):
    store = SQLiteContentResearchStore(str(tmp_path / "comment-failure.db"))
    pipeline = DirectionalEvidencePipeline(store)
    calls = 0

    async def discover(group):
        return [
            {
                "provider": "xiaohongshu",
                "canonical_id": "note-1",
                "source_kind": "note_detail",
                "content_text": "note body",
            }
        ]

    async def unavailable(_candidate):
        nonlocal calls
        calls += 1
        return SourceOperationResult(
            provider="xiaohongshu",
            operation="collect_comments",
            source_kind="comment",
            status="failed",
            items=[],
            failure_reason="unavailable",
            completeness="unavailable",
        )

    required = await pipeline.execute(
        subagent_task_id="sat-comment-failure",
        direction_id="ugc_community",
        subject="短裤",
        questions=["评论"],
        competitors=[],
        author_cap=1,
        discover=discover,
        collect_comments=unavailable,
        required_comment_fields=("comment_text", "parent_note_id"),
    )
    note_only = await pipeline.execute(
        subagent_task_id="sat-note-only",
        direction_id="product_marketing",
        subject="短裤",
        questions=["卖点"],
        competitors=[],
        author_cap=1,
        discover=discover,
        collect_comments=unavailable,
    )

    assert required.selection.status == "incomplete"
    assert required.comment_packet_ids == ()
    assert calls == 1
    terminal = [
        item for item in store.list_typed_records(StageCheckpointRecord)
        if item.stage_name == "operation" and item.payload.get("operation") == "comments"
        and item.status == "failed"
    ]
    assert len(terminal) == 1
    assert terminal[0].payload["completion"]["failure_code"] == "unavailable"
    assert note_only.selection.status == "incomplete"


@pytest.mark.asyncio
async def test_formal_router_uses_frozen_required_comment_contract(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "router-comments.db"))
    snapshot, policies, contracts, _groups = _build_frozen_pipeline_snapshot(
        snapshot_id="rps-comments",
        workflow_run_id="run-comments",
        direction_id="ugc_community",
        subject="短裤",
        questions=("评论",),
    )
    store.save_run_policy_snapshot(snapshot)
    for policy in policies:
        if policy.direction_id == "ugc_community":
            policy = SamplePolicy(
                id=policy.id,
                schema_version=policy.schema_version,
                direction_id=policy.direction_id,
                minimum_samples=policy.minimum_samples,
                minimum_independent_authors=policy.minimum_independent_authors,
                author_cap=policy.author_cap,
                metadata={
                    "detail_fetch_cap": 30,
                    "comment_limit": 2,
                    "comment_top_level_only": True,
                    "comment_reply_depth_limit": 0,
                },
            )
        store.save_sample_policy(policy)
    for contract in contracts:
        store.save_direction_contract(contract)

    class Adapter:
        comment_calls = 0
        comment_requests: list = []

        async def discover_candidates(self, _request):
            return SourceOperationResult(
                provider="xiaohongshu",
                operation="discover_candidates",
                source_kind="search_result_minimal",
                status="completed",
                items=[
                    {
                        "canonical_id": "note-1",
                        "source_kind": "search_result_minimal",
                        "source_url": "https://example/note-1",
                    }
                ],
            )

        async def collect_note_detail(self, request):
            return SourceOperationResult(
                provider="xiaohongshu",
                operation="collect_note_detail",
                source_kind="note_detail",
                status="completed",
                items=[
                    {
                        "canonical_id": request.note_id,
                        "source_kind": "note_detail",
                        "content_text": "body",
                    }
                ],
            )

        async def collect_comments(self, request):
            self.comment_calls += 1
            self.comment_requests.append(request)
            return SourceOperationResult(
                provider="xiaohongshu",
                operation="collect_comments",
                source_kind="comment",
                status="completed",
                items=[
                    {
                        "canonical_id": f"comment-{self.comment_calls}",
                        "source_kind": "comment",
                        "content_text": "尺码偏小",
                        "author_id": "reader",
                    }
                ],
                next_cursor="cursor-2" if self.comment_calls == 1 else None,
            )

    adapter = Adapter()
    task = SubagentTaskRecord(
        id="sat-comments-router",
        workflow_run_id="run-comments",
        thread_id="thread-comments",
        schema_version="v1",
        status="queued",
        plan_id="rp-comments",
        direction_id="ugc_community",
        payload={
            "schema_version": "content_research_subagent_task_v1",
            "input_payload": {
                "confirmed_subject": "短裤",
                "competitors": [],
                "direction": {"id": "ugc_community", "questions": ["评论"]},
            },
        },
    )
    store.save_subagent_task(task)
    terminal = await SubagentTaskRouter(
        store=store, source_registry=SourceAdapterRegistry({"xiaohongshu": adapter})
    ).execute_task(task)

    assert terminal.status == "partial_completed"
    assert adapter.comment_calls == 2
    assert [(item.limit, item.cursor, item.top_level_only, item.reply_depth_limit) for item in adapter.comment_requests] == [
        (2, None, True, 0),
        (1, "cursor-2", True, 0),
    ]
    packets = [
        item for item in store.list_directional_evidence_packets("run-comments", "ugc_community")
        if item.payload["retrieval_context"].get("source_kind") == "comment"
    ]
    collections = [item.payload["retrieval_context"]["collection"] for item in packets]
    assert all(item["sample_policy_id"] == "sp_rps-comments_ugc_community" for item in collections)
    assert all(item["target_comment_count"] == 2 for item in collections)
    assert all(item["top_level_only"] is True and item["reply_depth_limit"] == 0 for item in collections)
    comment_page = [item for item in store.list_typed_records(StageCheckpointRecord) if item.stage_name == "comments_page"]
    assert [(item.payload["page_limit"], item.payload["cursor"]) for item in comment_page] == [(2, None), (1, "cursor-2")]


@pytest.mark.asyncio
async def test_pipeline_limits_detail_attempts_to_frozen_cap_and_persists_all_thresholds(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "detail-cap.db"))
    pipeline = DirectionalEvidencePipeline(store)
    calls: list[str] = []

    async def discover(group):
        return [
            {"canonical_id": "note-1", "source_kind": "search_result_minimal", "relevance": 3},
            {"canonical_id": "note-2", "source_kind": "search_result_minimal", "relevance": 2},
            {"canonical_id": "note-3", "source_kind": "search_result_minimal", "relevance": 1},
        ]

    async def detail(candidate):
        calls.append(candidate["canonical_id"])
        return {
            "canonical_id": candidate["canonical_id"],
            "source_kind": "note_detail",
            "content_text": "body",
            "author_id": candidate["canonical_id"],
        }

    result = await pipeline.execute(
        subagent_task_id="sat-detail-cap",
        direction_id="product_marketing",
        subject="短裤",
        questions=["卖点"],
        competitors=[],
        author_cap=1,
        minimum_samples=3,
        minimum_independent_authors=3,
        detail_fetch_cap=2,
        discover=discover,
        collect_detail=detail,
    )

    assert calls == ["note-1", "note-2"]
    assert result.selection.status == "incomplete"
    selection_checkpoint = next(
        item
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.stage_name == "selection"
    )
    assert selection_checkpoint.payload["selection_policy"] == {
        "snapshot_id": None,
        "author_cap": 1,
        "minimum_samples": 3,
        "minimum_independent_authors": 3,
        "detail_fetch_cap": 2,
        "run_as_of_at": None,
    }


@pytest.mark.asyncio
async def test_detail_failure_backfills_in_frozen_order_and_appends_selection_revisions(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "detail-backfill.db"))
    pipeline = DirectionalEvidencePipeline(store)
    calls: list[str] = []

    async def discover(group):
        return [
            {"canonical_id": "note-1", "source_kind": "search_result_minimal", "relevance": 3},
            {"canonical_id": "note-2", "source_kind": "search_result_minimal", "relevance": 2},
            {"canonical_id": "note-3", "source_kind": "search_result_minimal", "relevance": 1},
        ]

    async def detail(candidate):
        calls.append(candidate["canonical_id"])
        if candidate["canonical_id"] == "note-1":
            return SourceOperationResult(
                provider="xiaohongshu",
                operation="collect_note_detail",
                source_kind="note_detail",
                status="failed",
                items=[],
                failure_reason="note_unavailable",
                completeness="unavailable",
                retryable=False,
            )
        return {
            "canonical_id": candidate["canonical_id"],
            "source_kind": "note_detail",
            "content_text": "body",
            "author_id": candidate["canonical_id"],
        }

    result = await pipeline.execute(
        subagent_task_id="sat-backfill",
        direction_id="product_marketing",
        subject="短裤",
        questions=["卖点"],
        competitors=[],
        author_cap=1,
        minimum_samples=2,
        minimum_independent_authors=2,
        detail_fetch_cap=3,
        discover=discover,
        collect_detail=detail,
    )

    assert calls == ["note-1", "note-2", "note-3"]
    assert result.selection.status == "complete"
    assert len(result.packet_ids) == 2
    revisions = sorted(
        (
            item
            for item in store.list_typed_records(StageCheckpointRecord)
            if item.stage_name == "selection_revision"
        ),
        key=lambda item: item.payload["revision"],
    )
    assert [item.payload["trigger"]["candidate_id"] for item in revisions] == calls
    assert "blocking_field_unavailable" in revisions[0].payload["trigger"]["reasons"]
    assert all(item.id != revisions[0].id for item in revisions[1:])
    unavailable_operation = next(
        item
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.stage_name == "operation"
        and (item.payload.get("completion") or {}).get("failure_code")
        == "note_unavailable"
    )
    assert unavailable_operation.status == "failed"
    assert unavailable_operation.payload["completion"]["retryable"] is False
    assert unavailable_operation.payload["completion"]["recovery_action"] is None


@pytest.mark.asyncio
async def test_detail_auth_failure_stops_before_calling_later_candidates(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "detail-auth-stop.db"))
    pipeline = DirectionalEvidencePipeline(store)
    calls: list[str] = []

    async def discover(_group):
        return [
            {"canonical_id": "note-1", "source_kind": "search_result_minimal"},
            {"canonical_id": "note-2", "source_kind": "search_result_minimal"},
        ]

    async def detail(candidate):
        calls.append(candidate["canonical_id"])
        return SourceOperationResult(
            provider="xiaohongshu",
            operation="collect_note_detail",
            source_kind="note_detail",
            status="failed",
            items=[],
            failure_reason="auth_required",
            completeness="unavailable",
            retryable=False,
        )

    result = await pipeline.execute(
        subagent_task_id="sat-auth-stop",
        direction_id="product_marketing",
        subject="短裤",
        questions=["卖点"],
        competitors=[],
        author_cap=2,
        minimum_samples=2,
        minimum_independent_authors=2,
        detail_fetch_cap=2,
        discover=discover,
        collect_detail=detail,
    )

    assert calls == ["note-1"]
    assert result.blocking_failure_code == "auth_required"


@pytest.mark.asyncio
async def test_detail_backfill_interruption_after_external_call_requires_confirmation(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "detail-resume-revision.db"))
    pipeline = DirectionalEvidencePipeline(store)
    calls: list[str] = []
    interrupted = True

    async def discover(group):
        return [
            {"canonical_id": "note-1", "source_kind": "search_result_minimal", "relevance": 2},
            {"canonical_id": "note-2", "source_kind": "search_result_minimal", "relevance": 1},
        ]

    async def detail(candidate):
        nonlocal interrupted
        calls.append(candidate["canonical_id"])
        if candidate["canonical_id"] == "note-1":
            return None
        if interrupted:
            interrupted = False
            raise RuntimeError("interrupted after first revision")
        return {
            "canonical_id": "note-2",
            "source_kind": "note_detail",
            "content_text": "body",
            "author_id": "author-2",
        }

    kwargs = {
        "subagent_task_id": "sat-backfill-resume",
        "direction_id": "product_marketing",
        "subject": "短裤",
        "questions": ["卖点"],
        "competitors": [],
        "author_cap": 1,
        "minimum_samples": 1,
        "minimum_independent_authors": 1,
        "detail_fetch_cap": 2,
        "discover": discover,
        "collect_detail": detail,
    }
    with pytest.raises(RuntimeError, match="interrupted"):
        await pipeline.execute(**kwargs)
    with pytest.raises(OperationOutcomeUnknownError, match="pending confirmation"):
        await pipeline.execute(**kwargs)

    assert calls == ["note-1", "note-2"]
    operation_records = [
        item
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.stage_name == "operation" and item.payload["operation"] == "detail"
    ]
    assert {item.status for item in operation_records} >= {"completed", "running", "outcome_unknown"}


@pytest.mark.asyncio
async def test_discover_interruption_after_provider_return_never_retries_without_confirmation(tmp_path, monkeypatch):
    store = SQLiteContentResearchStore(str(tmp_path / "discover-inflight.db"))
    pipeline = DirectionalEvidencePipeline(store)
    calls = 0

    async def discover(_group):
        nonlocal calls
        calls += 1
        assert any(
            item.stage_name == "operation"
            and item.payload["operation"] == "discover"
            and item.status == "running"
            for item in store.list_typed_records(StageCheckpointRecord)
        )
        return [{"canonical_id": "note-1", "source_kind": "note_detail", "content_text": "body"}]

    original_save = pipeline._save_checkpoint

    def crash_before_collect_artifact(task_id, stage, fingerprint, payload):
        if stage == "collect":
            raise RuntimeError("process interrupted after provider return")
        original_save(task_id, stage, fingerprint, payload)

    monkeypatch.setattr(pipeline, "_save_checkpoint", crash_before_collect_artifact)
    kwargs = {
        "workflow_run_id": "run-discover-inflight",
        "subagent_task_id": "sat-discover-inflight",
        "direction_id": "product_marketing",
        "subject": "短裤",
        "questions": ["卖点"],
        "competitors": [],
        "author_cap": 1,
        "discover": discover,
    }
    with pytest.raises(RuntimeError, match="interrupted"):
        await pipeline.execute(**kwargs)

    await DirectionalEvidencePipeline(store).execute(**kwargs)

    assert calls == 1
    lifecycle = [
        item for item in store.list_typed_records(StageCheckpointRecord)
        if item.stage_name == "operation" and item.payload["operation"] == "discover"
    ]
    assert {item.status for item in lifecycle} == {"running", "completed"}


@pytest.mark.asyncio
async def test_detail_and_comment_operations_are_running_before_their_callables(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "operation-precall.db"))

    async def discover(_group):
        return [{"canonical_id": "note-detail", "source_kind": "search_result_minimal"}]

    async def detail(_candidate):
        assert any(
            item.stage_name == "operation" and item.payload["operation"] == "detail" and item.status == "running"
            for item in store.list_typed_records(StageCheckpointRecord)
        )
        return {"canonical_id": "note-detail", "source_kind": "note_detail", "content_text": "body"}

    await DirectionalEvidencePipeline(store).execute(
        workflow_run_id="run-detail-precall",
        subagent_task_id="sat-detail-precall",
        direction_id="product_marketing",
        subject="短裤",
        questions=["卖点"],
        competitors=[],
        author_cap=1,
        discover=discover,
        collect_detail=detail,
    )

    async def note_discover(_group):
        return [{"canonical_id": "note-comments", "source_kind": "note_detail", "content_text": "body"}]

    async def comments(_candidate):
        assert any(
            item.stage_name == "operation" and item.payload["operation"] == "comments" and item.status == "running"
            for item in store.list_typed_records(StageCheckpointRecord)
        )
        return SourceOperationResult(
            provider="xiaohongshu",
            operation="collect_comments",
            source_kind="comment",
            status="completed",
            items=[{"canonical_id": "comment-1", "source_kind": "comment", "comment_text": "需要口袋", "author_id": "reader", "reply_depth": 0}],
        )

    await DirectionalEvidencePipeline(store).execute(
        workflow_run_id="run-comments-precall",
        subagent_task_id="sat-comments-precall",
        direction_id="comment_insight",
        subject="短裤",
        questions=["需求"],
        competitors=[],
        author_cap=1,
        discover=note_discover,
        collect_comments=comments,
        required_comment_fields=("comment_text", "parent_note_id", "reply_depth"),
    )


@pytest.mark.asyncio
async def test_completed_comment_operation_reuses_durable_parent_artifact_after_stage_interruption(tmp_path, monkeypatch):
    store = SQLiteContentResearchStore(str(tmp_path / "comments-operation-replay.db"))
    calls = 0

    async def discover(_group):
        return [{"canonical_id": "note-comments", "source_kind": "note_detail", "content_text": "body"}]

    async def comments(_candidate):
        nonlocal calls
        calls += 1
        return SourceOperationResult(
            provider="xiaohongshu",
            operation="collect_comments",
            source_kind="comment",
            status="completed",
            items=[{"canonical_id": "comment-1", "source_kind": "comment", "comment_text": "需要口袋", "author_id": "reader", "reply_depth": 0}],
        )

    kwargs = {
        "workflow_run_id": "run-comments-operation-replay",
        "subagent_task_id": "sat-comments-operation-replay",
        "direction_id": "comment_insight",
        "subject": "短裤",
        "questions": ["需求"],
        "competitors": [],
        "author_cap": 1,
        "discover": discover,
        "collect_comments": comments,
        "required_comment_fields": ("comment_text", "parent_note_id", "reply_depth"),
    }
    interrupted = DirectionalEvidencePipeline(store)
    original_save = interrupted._save_checkpoint

    def crash_before_comments_stage(task_id, stage, fingerprint, payload):
        if stage == "comments":
            raise RuntimeError("process interrupted after comment packet persistence")
        original_save(task_id, stage, fingerprint, payload)

    monkeypatch.setattr(interrupted, "_save_checkpoint", crash_before_comments_stage)
    with pytest.raises(RuntimeError, match="comment packet"):
        await interrupted.execute(**kwargs)

    replayed = await DirectionalEvidencePipeline(store).execute(**kwargs)
    assert calls == 1
    assert len(replayed.comment_packet_ids) == 1


@pytest.mark.asyncio
async def test_router_returns_recoverable_outcome_unknown_payload(tmp_path, monkeypatch):
    store = SQLiteContentResearchStore(str(tmp_path / "router-outcome-unknown.db"))
    task = SubagentTaskRecord(
        id="sat-outcome-unknown",
        workflow_run_id="run-outcome-unknown",
        thread_id="thread-outcome-unknown",
        schema_version="v1",
        status="queued",
        plan_id="rp-outcome-unknown",
        direction_id="product_marketing",
        payload={
            "schema_version": "content_research_subagent_task_v1",
            "input_payload": {"direction": {"id": "product_marketing"}},
        },
    )
    store.save_subagent_task(task)
    router = SubagentTaskRouter(store=store)

    async def raise_outcome_unknown(**_kwargs):
        raise OperationOutcomeUnknownError(operation="discover", operation_fingerprint="operation-fp")

    monkeypatch.setattr(router, "_execute_direction_pipeline", raise_outcome_unknown)
    terminal = await router.execute_task(task)

    assert terminal.status == "outcome_unknown"
    assert terminal.payload["output_payload"]["failure_reason"] == "collection_outcome_pending_confirmation"
    assert terminal.payload["output_payload"]["metadata"]["recovery_action"] == "confirm_collection_outcome_before_retry"


@pytest.mark.asyncio
async def test_search_pages_follow_cursor_and_persist_complete_page_provenance(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "search-pages.db"))
    cursors: list[str | None] = []

    async def discover(group):
        cursors.append(group.cursor)
        if group.cursor is None:
            return SourceOperationResult("x", "discover_candidates", "search_result_minimal", "completed", [
                {"canonical_id": "note-1", "source_kind": "note_detail"},
                {"canonical_id": "note-2", "source_kind": "note_detail"},
            ], next_cursor="cursor-2", completeness="partial")
        return SourceOperationResult("x", "discover_candidates", "search_result_minimal", "completed", [
            {"canonical_id": "note-3", "source_kind": "note_detail"},
        ], completeness="complete")

    await DirectionalEvidencePipeline(store).execute(
        subagent_task_id="sat-search-pages", direction_id="product_marketing",
        subject="短裤", questions=["卖点"], competitors=[], author_cap=3,
        candidate_limit_per_query=4, discover=discover,
    )

    assert cursors == [None, "cursor-2"]
    page_records = [item for item in store.list_typed_records(StageCheckpointRecord) if item.stage_name == "collect_page"]
    assert [item.payload["cursor"] for item in page_records] == [None, "cursor-2"]
    assert page_records[-1].payload["actual_count"] == 3
    assert page_records[-1].payload["completeness"] == "complete"


@pytest.mark.asyncio
async def test_search_cap_stops_before_next_cursor_and_discloses_truncation(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "search-cap.db"))
    cursors: list[str | None] = []

    async def discover(group):
        cursors.append(group.cursor)
        return SourceOperationResult("x", "discover_candidates", "search_result_minimal", "completed", [
            {"canonical_id": f"note-{index}", "source_kind": "note_detail"}
            for index in range(3)
        ], next_cursor="cursor-2", completeness="partial")

    await DirectionalEvidencePipeline(store).execute(
        subagent_task_id="sat-search-cap", direction_id="product_marketing",
        subject="短裤", questions=["卖点"], competitors=[], author_cap=3,
        candidate_limit_per_query=3, discover=discover,
    )

    assert cursors == [None]
    collect = next(item for item in store.list_typed_records(StageCheckpointRecord) if item.stage_name == "collect")
    assert collect.payload["pagination"] == [{
        "query_group_id": collect.payload["query_groups"][0]["id"],
        "actual_count": 3,
        "target_count": 3,
        "sort": "likes",
        "last_cursor": "cursor-2",
        "completeness": "truncated_by_cap",
    }]


@pytest.mark.asyncio
async def test_comment_pages_resume_from_cursor_and_direction_cap_stops_other_parents(tmp_path, monkeypatch):
    store = SQLiteContentResearchStore(str(tmp_path / "comment-pages.db"))
    calls: list[tuple[str, str | None, int]] = []

    async def discover(_group):
        return [
            {"canonical_id": "note-1", "source_kind": "note_detail", "author_id": "one", "content_text": "body"},
            {"canonical_id": "note-2", "source_kind": "note_detail", "author_id": "two", "content_text": "body"},
        ]

    async def comments(candidate):
        calls.append((candidate["canonical_id"], candidate.get("_collection_cursor"), candidate["_collection_limit"]))
        if candidate.get("_collection_cursor") is None:
            return SourceOperationResult("x", "collect_comments", "comment", "partial_completed", [
                {"canonical_id": "comment-1", "source_kind": "comment", "comment_text": "a", "author_id": "a", "reply_depth": 0},
                {"canonical_id": "comment-2", "source_kind": "comment", "comment_text": "b", "author_id": "b", "reply_depth": 0},
            ], next_cursor="cursor-2", completeness="partial")
        return SourceOperationResult("x", "collect_comments", "comment", "partial_completed", [
            {"canonical_id": "comment-3", "source_kind": "comment", "comment_text": "c", "author_id": "c", "reply_depth": 0},
            {"canonical_id": "comment-4", "source_kind": "comment", "comment_text": "d", "author_id": "d", "reply_depth": 0},
        ], next_cursor="cursor-3", completeness="partial")

    kwargs = {
        "subagent_task_id": "sat-comment-pages", "direction_id": "comment_insight",
        "subject": "短裤", "questions": ["需求"], "competitors": [], "author_cap": 2,
        "discover": discover, "collect_comments": comments,
        "required_comment_fields": ("comment_text", "parent_note_id", "reply_depth"), "comment_limit": 3,
    }
    pipeline = DirectionalEvidencePipeline(store)
    original_complete = pipeline._complete_operation

    def crash_after_first_page(task_id, operation, operation_fingerprint, **kwargs):
        if operation == "comments":
            raise RuntimeError("interrupted after comment page checkpoint")
        original_complete(task_id, operation, operation_fingerprint, **kwargs)

    monkeypatch.setattr(pipeline, "_complete_operation", crash_after_first_page)
    with pytest.raises(RuntimeError, match="comment page"):
        await pipeline.execute(**kwargs)

    replay = await DirectionalEvidencePipeline(store).execute(**kwargs)
    assert calls == [("note-1", None, 3), ("note-1", "cursor-2", 1)]
    assert len(replay.comment_packet_ids) == 3
    comments_checkpoint = next(item for item in store.list_typed_records(StageCheckpointRecord) if item.stage_name == "comments")
    assert comments_checkpoint.payload["parents"][0]["completeness"] == "truncated_by_cap"
