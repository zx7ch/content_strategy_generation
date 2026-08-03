from datetime import datetime, timezone

import pytest

from app.content_research.contracts import (
    ADMISSION_REASON_CODES,
    CLAIM_EVIDENCE_STATES,
    DIRECTION_CATALOG_V1,
    DIRECTION_RESULT_STATES,
    RunPolicySnapshot,
    SamplePolicy,
    build_default_snapshot,
    evaluate_capability_preflight,
    policy_hash,
)
from app.content_research.service import _freeze_adapter_capabilities
from app.content_research.sources.base import ProviderCapability


def test_default_snapshot_has_one_contract_for_each_registered_direction():
    snapshot, policies, contracts = build_default_snapshot(
        snapshot_id="rps_test",
        workflow_run_id="run_1",
        brief_id="rb_1",
        plan_id="rp_1",
        run_as_of_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )
    assert snapshot.effective_policy_hash == policy_hash(snapshot.effective_policy)
    assert len(contracts) == len(policies) == 7
    assert {item.direction_id for item in contracts} == set(
        snapshot.effective_policy["direction_ids"]
    )
    assert (
        snapshot.effective_policy["provider_capabilities"]["xiaohongshu"]["collect_note_detail"][
            "status"
        ]
        == "unavailable"
    )
    assert all(item.detail_fetch_cap >= item.minimum_samples for item in policies)
    assert all(
        (item.comment_limit, item.comment_top_level_only, item.comment_reply_depth_limit)
        == (30, True, 0)
        for item in policies
    )
    assert all(
        item.required_note_fields != ("source_id", "source_url", "body") for item in contracts
    )
    assert {"case_level", "repeated_observation", "provisional", "insufficient_evidence"} == set(
        CLAIM_EVIDENCE_STATES
    )
    assert "formal_directional_result" in DIRECTION_RESULT_STATES
    assert "missing_blocking_field" in ADMISSION_REASON_CODES
    assert snapshot.validation_result["directions"]["product_marketing"]["status"] == "unavailable"
    assert (
        snapshot.validation_result["directions"]["ugc_community"]["missing_blocking_comment_fields"]
        == []
    )


def test_snapshot_freezes_template_only_direction_set_without_registry_inference():
    snapshot, policies, contracts = build_default_snapshot(
        snapshot_id="rps_lite",
        workflow_run_id="run_lite",
        brief_id="rb_lite",
        plan_id="rp_lite",
        direction_set_version="direction_set_v1",
        direction_ids=("product_marketing", "competitor_discovery", "content_performance"),
        direction_catalog=DIRECTION_CATALOG_V1,
        report_compose_mode="template_only",
    )
    assert snapshot.effective_policy["direction_set_version"] == "direction_set_v1"
    assert snapshot.effective_policy["direction_ids"] == [
        "product_marketing",
        "competitor_discovery",
        "content_performance",
    ]
    assert snapshot.effective_policy["direction_catalog_version"] == "direction_catalog_v1"
    assert snapshot.effective_policy["requested_direction_ids"] == [
        "product_marketing",
        "competitor_discovery",
        "content_performance",
    ]
    assert snapshot.effective_policy["report_compose_mode"] == "template_only"
    assert {item.direction_id for item in contracts} == set(
        snapshot.effective_policy["direction_ids"]
    )
    assert {item.direction_id for item in policies} == set(
        snapshot.effective_policy["direction_ids"]
    )


def test_snapshot_rejects_wrong_hash_and_naive_time():
    with pytest.raises(ValueError, match="effective_policy_hash"):
        RunPolicySnapshot(
            id="rps_1",
            workflow_run_id="run",
            research_brief_id="rb",
            research_plan_id="rp",
            schema_version="v1",
            effective_policy={"a": 1},
            effective_policy_hash="wrong",
            run_as_of_at=datetime.now(timezone.utc),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        build_default_snapshot(
            snapshot_id="rps_1",
            workflow_run_id="run",
            brief_id="rb",
            plan_id="rp",
            run_as_of_at=datetime(2026, 1, 1),
        )


def test_sample_policy_rejects_detail_cap_below_minimum_samples():
    with pytest.raises(ValueError, match="detail_fetch_cap"):
        SamplePolicy(
            id="sp_1",
            schema_version="v1",
            direction_id="product_marketing",
            minimum_samples=3,
            minimum_independent_authors=2,
            author_cap=2,
            metadata={"detail_fetch_cap": 2},
        )


def test_sample_policy_freezes_and_validates_comment_collection_policy():
    policy = SamplePolicy(
        id="sp_comments",
        schema_version="v1",
        direction_id="ugc_community",
        minimum_samples=30,
        minimum_independent_authors=5,
        author_cap=3,
        metadata={
            "detail_fetch_cap": 30,
            "comment_limit": 12,
            "comment_top_level_only": True,
            "comment_reply_depth_limit": 0,
        },
    )

    assert (
        policy.comment_limit,
        policy.comment_top_level_only,
        policy.comment_reply_depth_limit,
    ) == (12, True, 0)
    with pytest.raises(ValueError, match="comment_limit"):
        SamplePolicy(
            id="sp_bad_limit",
            schema_version="v1",
            direction_id="ugc_community",
            minimum_samples=30,
            minimum_independent_authors=5,
            author_cap=3,
            metadata={"detail_fetch_cap": 30, "comment_limit": 0},
        )
    with pytest.raises(ValueError, match="top-level"):
        SamplePolicy(
            id="sp_bad_depth",
            schema_version="v1",
            direction_id="ugc_community",
            minimum_samples=30,
            minimum_independent_authors=5,
            author_cap=3,
            metadata={
                "detail_fetch_cap": 30,
                "comment_top_level_only": True,
                "comment_reply_depth_limit": 1,
            },
        )


def test_capability_preflight_requires_all_blocking_note_and_comment_fields():
    full_capabilities = {
        "xiaohongshu": {
            "collect_note_detail": {
                "status": "supported",
                "fields": [
                    "title",
                    "content_text",
                    "tags",
                    "note_type",
                    "metrics",
                    "metrics_observed_at",
                    "source_published_at",
                    "ip_location",
                    "media",
                    "author",
                ],
            },
            "collect_comments": {
                "status": "supported",
                "fields": [
                    "comment_text",
                    "source_published_at",
                    "like_count",
                    "reply_depth",
                    "parent_note_id",
                    "author",
                ],
            },
        }
    }
    snapshot, _policies, contracts = build_default_snapshot(
        snapshot_id="rps_full",
        workflow_run_id="run",
        brief_id="rb",
        plan_id="rp",
        provider_capabilities=full_capabilities,
    )
    preflight = evaluate_capability_preflight(
        contracts=contracts, provider_capabilities=full_capabilities
    )

    assert snapshot.validation_result == preflight
    assert all(item["formal_eligible"] for item in preflight["directions"].values())

    missing_comment = evaluate_capability_preflight(
        contracts=contracts,
        provider_capabilities={
            "xiaohongshu": {
                **full_capabilities["xiaohongshu"],
                "collect_comments": {"status": "supported", "fields": ["comment_text"]},
            }
        },
    )
    ugc = missing_comment["directions"]["ugc_community"]
    assert ugc["status"] == "incomplete"
    assert ugc["reason_codes"] == ["missing_comment_field"]


def test_adapter_capabilities_are_frozen_once_into_snapshot_shape():
    class Adapter:
        def capabilities(self):
            return (
                ProviderCapability(
                    "collect_note_detail", "supported", ("title", "content_text"), {"max_limit": 1}
                ),
            )

    class Registry:
        def get(self, provider):
            assert provider == "xiaohongshu"
            return Adapter()

    frozen = _freeze_adapter_capabilities(Registry())

    assert frozen == {
        "xiaohongshu": {
            "adapter_version": "Adapter",
            "collect_note_detail": {
                "status": "supported",
                "fields": ["title", "content_text"],
                "max_limit": 1,
                "failure_retryability": {},
            },
        },
    }


def test_frozen_relevance_and_query_plan_are_canonical_across_input_order():
    run_as_of = datetime(2026, 7, 30, tzinfo=timezone.utc)
    first_groups = {
        "product_marketing": (
            {
                "id": "qg_second",
                "direction_id": "product_marketing",
                "normalized_query": "速干短裤 使用场景",
                "priority": 2,
                "sort": "likes",
                "time_window": {"end_at": run_as_of.isoformat()},
                "candidate_cap": 20,
            },
            {
                "id": "qg_first",
                "direction_id": "product_marketing",
                "normalized_query": "速干短裤 卖点",
                "priority": 1,
                "sort": "likes",
                "time_window": {"end_at": run_as_of.isoformat()},
                "candidate_cap": 20,
            },
        )
    }
    second_groups = {"product_marketing": tuple(reversed(first_groups["product_marketing"]))}

    first, _policies, first_contracts = build_default_snapshot(
        snapshot_id="rps_order",
        workflow_run_id="run_order",
        brief_id="rb_order",
        plan_id="rp_order",
        run_as_of_at=run_as_of,
        direction_ids=("product_marketing",),
        confirmed_subject="速干徒步短裤",
        custom_research_question="关注夏季轻量",
        query_groups_by_direction=first_groups,
    )
    second, _policies, second_contracts = build_default_snapshot(
        snapshot_id="rps_order",
        workflow_run_id="run_order",
        brief_id="rb_order",
        plan_id="rp_order",
        run_as_of_at=run_as_of,
        direction_ids=("product_marketing",),
        confirmed_subject="速干徒步短裤",
        custom_research_question="关注夏季轻量",
        query_groups_by_direction=second_groups,
    )

    assert first.effective_policy == second.effective_policy
    assert first.effective_policy_hash == second.effective_policy_hash
    frozen = first_contracts[0].metadata["query_relevance"]
    assert frozen["query_group_ids"] == ["qg_first", "qg_second"]
    assert frozen["claim_quote_fields"]["message_angle"] == [
        "content_text",
        "title",
    ]
    direction_plan = first.effective_policy["locked_query_plan"]["directions"]["product_marketing"]
    locked = first.effective_policy["locked_query_plan"]
    assert locked["schema_version"] == "content_research_locked_query_plan_v2"
    assert locked["query_compiler_version"] == "content_research_query_compiler_v2"
    assert locked["primary_query_group_cap"] == 2
    assert locked["coverage_fallback_query_group_cap"] == 1
    assert locked["candidate_cap_per_group"] == 20
    assert locked["custom_research_question"] == "关注夏季轻量"
    assert [item["id"] for item in direction_plan["query_groups"]] == [
        "qg_first",
        "qg_second",
    ]
    assert all(item["activation"] == "primary" for item in direction_plan["query_groups"])
    assert all(item["roles"] for item in direction_plan["query_groups"])
    assert all(item["normalized_identity"] for item in direction_plan["query_groups"])
    assert (
        first_contracts[0].metadata["query_relevance"]
        == second_contracts[0].metadata["query_relevance"]
    )
