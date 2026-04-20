from __future__ import annotations

import time

from fastapi.testclient import TestClient
import pytest

from app.main import app
from tests.acceptance.conftest import write_acceptance_artifact
from tests.acceptance.v2_phase1_helpers import detect_second_batch_effect


def _headers(workspace_id: str) -> dict[str, str]:
    return {
        "X-Workspace-Id": workspace_id,
        "X-User-Id": "acceptance-user",
    }


@pytest.mark.acceptance
def test_v2_phase1_full_loop_acceptance(
    acceptance_storage,
    acceptance_artifact_dir,
) -> None:
    started = time.perf_counter()

    with TestClient(app) as client:
        default_workspace_response = client.get("/workspaces/default")
        assert default_workspace_response.status_code == 200

        workspace_response = client.post(
            "/workspaces",
            json={
                "name": "Phase1 Acceptance Workspace",
                "slug": "phase1-acceptance",
                "timezone": "Asia/Shanghai",
            },
        )
        assert workspace_response.status_code == 201
        workspace = workspace_response.json()

        workspace_brand_list_response = client.get(
            "/brands",
            headers=_headers(workspace["id"]),
        )
        assert workspace_brand_list_response.status_code == 200
        assert workspace_brand_list_response.json()["items"] == []

        brand_response = client.post(
            "/brands",
            headers=_headers(workspace["id"]),
            json={
                "name": "Acme Outdoor",
                "category": "outdoor",
                "stage": "growth",
                "target_audience": {"age_ranges": ["25-34"], "gender_skew": "female"},
            },
        )
        assert brand_response.status_code == 201
        brand = brand_response.json()

        brand_list_response = client.get(
            "/brands",
            headers=_headers(workspace["id"]),
        )
        assert brand_list_response.status_code == 200
        assert brand_list_response.json()["items"][0]["id"] == brand["id"]

        brand_read_response = client.get(
            f"/brands/{brand['id']}",
            headers=_headers(workspace["id"]),
        )
        assert brand_read_response.status_code == 200
        assert brand_read_response.json()["workspace_id"] == workspace["id"]

        channel_response = client.post(
            f"/brands/{brand['id']}/channels",
            headers=_headers(workspace["id"]),
            json={
                "platform": "xiaohongshu",
                "account_name": "Acme 小红书",
                "profile_url": "https://www.xiaohongshu.com/user/profile/acme-xhs",
            },
        )
        assert channel_response.status_code == 201
        channel = channel_response.json()

        channel_list_response = client.get(
            f"/brands/{brand['id']}/channels",
            headers=_headers(workspace["id"]),
        )
        assert channel_list_response.status_code == 200
        assert channel_list_response.json()["items"][0]["id"] == channel["id"]

        policy_response = client.put(
            f"/brands/{brand['id']}/policy-configs/active",
            headers=_headers(workspace["id"]),
            json={
                "policy_name": "baseline_rule_v1",
                "policy_version": "v1",
                "topic_type_targets": {
                    "targets": [
                        {"topic_type": "scenario", "min_ratio": 0.34, "max_ratio": 1.0, "priority_boost": 0.12},
                        {"topic_type": "problem", "min_ratio": 0.0, "max_ratio": 0.5, "priority_boost": 0.03},
                    ]
                },
            },
        )
        assert policy_response.status_code == 200
        policy_payload = policy_response.json()

        active_policy_response = client.get(
            f"/brands/{brand['id']}/policy-configs/active",
            headers=_headers(workspace["id"]),
        )
        assert active_policy_response.status_code == 200
        assert active_policy_response.json()["id"] == policy_payload["id"]

        snapshot_response = client.post(
            f"/brands/{brand['id']}/state-snapshots",
            headers=_headers(workspace["id"]),
            json={
                "state_version": "state_v1",
                "stage": "growth",
                "state_features": {"audience_focus": "urban commuting"},
                "source_version": "v1",
            },
        )
        assert snapshot_response.status_code == 201
        snapshot_payload = snapshot_response.json()

        snapshot_list_response = client.get(
            f"/brands/{brand['id']}/state-snapshots",
            headers=_headers(workspace["id"]),
        )
        assert snapshot_list_response.status_code == 200
        assert snapshot_list_response.json()["items"][0]["id"] == snapshot_payload["id"]

        source_sync_response = client.post(
            f"/brands/{brand['id']}/source-syncs",
            headers=_headers(workspace["id"]),
            json={
                "source_type": "xhs_extension_capture",
                "source_adapter": "extension_source_sync_adapter_v1",
                "channel_id": channel["id"],
                "capture_payload": {
                    "page_type": "search_result",
                    "captured_at": "2026-04-11T10:00:00+08:00",
                    "items": [
                        {
                            "note_id": "note-1",
                            "source_url": "https://www.xiaohongshu.com/explore/note-1",
                            "title": "通勤徒步鞋怎么选",
                            "visible_text_excerpt": "解决上下班和周末轻徒步切换问题",
                            "author_handle": "competitor-a",
                            "likes": 128,
                            "comments": 22,
                            "collects": 63,
                            "shares": 11,
                            "tags": ["通勤", "徒步"],
                        },
                        {
                            "note_id": "note-2",
                            "source_url": "https://www.xiaohongshu.com/explore/note-2",
                            "title": "尺码痛点怎么避坑",
                            "visible_text_excerpt": "买鞋最怕前掌挤脚和后跟磨脚",
                            "author_handle": "competitor-b",
                            "likes": 116,
                            "comments": 25,
                            "collects": 48,
                            "shares": 6,
                            "tags": ["避坑", "尺码"],
                        },
                        {
                            "note_id": "note-3",
                            "source_url": "https://www.xiaohongshu.com/explore/note-3",
                            "title": "周末轻徒步穿搭清单",
                            "visible_text_excerpt": "从鞋包到外套的一套轻量方案",
                            "author_handle": "competitor-c",
                            "likes": 104,
                            "comments": 14,
                            "collects": 41,
                            "shares": 5,
                            "tags": ["穿搭", "徒步"],
                        },
                    ],
                },
            },
        )
        assert source_sync_response.status_code == 202
        source_sync_payload = source_sync_response.json()
        assert source_sync_payload["entry_type"] == "source_sync"
        assert source_sync_payload["imported_item_count"] == 3

        topic_refresh_response = client.post(
            f"/brands/{brand['id']}/topic-pool/refresh",
            headers=_headers(workspace["id"]),
            json={"archive_threshold_days": 60},
        )
        assert topic_refresh_response.status_code == 202
        topic_refresh_payload = topic_refresh_response.json()
        assert topic_refresh_payload["generated_item_count"] >= 1
        assert topic_refresh_payload["total_candidate_count"] >= topic_refresh_payload["generated_item_count"]

        topic_pool_response = client.get(
            f"/brands/{brand['id']}/topic-pool",
            headers=_headers(workspace["id"]),
        )
        assert topic_pool_response.status_code == 200
        topic_pool_payload = topic_pool_response.json()
        assert topic_pool_payload["items"]
        assert topic_pool_payload["stats"]["total_candidate_count"] >= 1
        assert topic_pool_payload["brand"]["id"] == brand["id"]
        topic_pool_before_feedback = topic_pool_payload

        first_decision_response = client.post(
            f"/brands/{brand['id']}/decisions/run",
            headers=_headers(workspace["id"]),
            json={
                "requested_slot_count": 3,
                "objective": "topic_recommendation",
                "exploration_mode": "balanced",
            },
        )
        assert first_decision_response.status_code == 201
        first_decision_payload = first_decision_response.json()
        assert first_decision_payload["chosen_count"] == 3
        assert len({item["topic_pool_item_id"] for item in first_decision_payload["items"]}) == 3
        assert all(item["decision_event_id"] for item in first_decision_payload["items"])

        review_response = client.patch(
            f"/decision-batches/{first_decision_payload['batch_id']}/items/0",
            headers=_headers(workspace["id"]),
            json={
                "review_action": "edit_and_accept",
                "edited_title": "编辑后的 Phase 1 选题",
                "review_notes": "更贴近本周活动节奏",
            },
        )
        assert review_response.status_code == 200
        review_payload = review_response.json()
        assert review_payload["review_status"] == "edit_and_accept"
        assert review_payload["title"] == "编辑后的 Phase 1 选题"

        decision_get_response = client.get(
            f"/decision-batches/{first_decision_payload['batch_id']}",
            headers=_headers(workspace["id"]),
        )
        assert decision_get_response.status_code == 200
        decision_get_payload = decision_get_response.json()
        assert decision_get_payload["items"][0]["title"] == "编辑后的 Phase 1 选题"

        latest_decision_response = client.get(
            f"/brands/{brand['id']}/decision-batches/latest",
            headers=_headers(workspace["id"]),
        )
        assert latest_decision_response.status_code == 200
        assert latest_decision_response.json()["batch_id"] == first_decision_payload["batch_id"]

        publish_response = client.post(
            "/publish-records",
            headers=_headers(workspace["id"]),
            json={
                "brand_id": brand["id"],
                "channel_id": channel["id"],
                "topic_pool_item_id": first_decision_payload["items"][0]["topic_pool_item_id"],
                "decision_event_id": first_decision_payload["items"][0]["decision_event_id"],
                "decision_batch_id": first_decision_payload["batch_id"],
                "publish_status": "published",
                "published_at": "2026-04-10T09:30:00+08:00",
                "creative_variant": "v1",
            },
        )
        assert publish_response.status_code == 201
        publish_payload = publish_response.json()
        assert publish_payload["decision_event_id"] == first_decision_payload["items"][0]["decision_event_id"]
        assert publish_payload["decision_batch_id"] == first_decision_payload["batch_id"]

        publish_list_response = client.get(
            f"/brands/{brand['id']}/publish-records",
            headers=_headers(workspace["id"]),
        )
        assert publish_list_response.status_code == 200
        publish_list_payload = publish_list_response.json()
        assert publish_list_payload["items"][0]["decision_event_id"] == first_decision_payload["items"][0]["decision_event_id"]
        assert publish_list_payload["items"][0]["decision_source"] != "manual"

        performance_response = client.post(
            "/performance/import",
            headers=_headers(workspace["id"]),
            json={
                "publish_record_id": publish_payload["publish_record_id"],
                "observation_window_hours": 168,
                "snapshot_at": "2026-04-17T09:30:00+08:00",
                "reward_version": "reward_v1",
                "metrics": {
                    "impressions": 12000,
                    "clicks": 850,
                    "likes": 320,
                    "comments": 28,
                    "collects": 96,
                    "shares": 31,
                    "follows_gained": 12,
                    "conversion_proxy": {
                        "value": 0.08,
                        "type": "store_click_rate",
                        "source": "manual_import",
                    },
                },
            },
        )
        assert performance_response.status_code == 201
        performance_payload = performance_response.json()
        assert performance_payload["composite_reward"] > 0
        assert performance_payload["reward_version"] == "reward_v1"

        performance_list_response = client.get(
            f"/brands/{brand['id']}/performance-snapshots",
            headers=_headers(workspace["id"]),
        )
        assert performance_list_response.status_code == 200
        performance_list_payload = performance_list_response.json()
        assert performance_list_payload["items"][0]["publish_record_id"] == publish_payload["publish_record_id"]
        assert performance_list_payload["items"][0]["reward_version"] == "reward_v1"

        topic_pool_after_feedback_response = client.get(
            f"/brands/{brand['id']}/topic-pool",
            headers=_headers(workspace["id"]),
        )
        assert topic_pool_after_feedback_response.status_code == 200
        topic_pool_after_feedback = topic_pool_after_feedback_response.json()

        second_decision_response = client.post(
            f"/brands/{brand['id']}/decisions/run",
            headers=_headers(workspace["id"]),
            json={
                "requested_slot_count": 3,
                "objective": "topic_recommendation",
                "exploration_mode": "balanced",
            },
        )
        assert second_decision_response.status_code == 201
        second_decision_payload = second_decision_response.json()
        effect = detect_second_batch_effect(
            topic_pool_before=topic_pool_before_feedback,
            topic_pool_after=topic_pool_after_feedback,
            first_batch=first_decision_payload,
            second_batch=second_decision_payload,
        )
        assert effect["has_downstream_change"], effect

        evaluation_response = client.post(
            "/evaluation-runs",
            headers=_headers(workspace["id"]),
            json={
                "brand_id": brand["id"],
                "evaluation_type": "replay",
            },
        )
        assert evaluation_response.status_code == 201
        evaluation_payload = evaluation_response.json()
        assert evaluation_payload["summary"]["sample_count"] == 1
        assert "candidate_quality" in evaluation_payload["summary"]
        assert evaluation_payload["sample_count"] == 1

        evaluation_get_response = client.get(
            f"/evaluation-runs/{evaluation_payload['evaluation_run_id']}",
            headers=_headers(workspace["id"]),
        )
        assert evaluation_get_response.status_code == 200
        assert evaluation_get_response.json()["evaluation_run_id"] == evaluation_payload["evaluation_run_id"]

        latest_evaluation_response = client.get(
            f"/brands/{brand['id']}/evaluation-runs/latest",
            headers=_headers(workspace["id"]),
        )
        assert latest_evaluation_response.status_code == 200
        latest_evaluation_payload = latest_evaluation_response.json()
        assert latest_evaluation_payload["evaluation_run_id"] == evaluation_payload["evaluation_run_id"]

    latency_ms = int((time.perf_counter() - started) * 1000)
    write_acceptance_artifact(
        acceptance_artifact_dir,
        "v2_phase1_full_loop",
        {
            "default_workspace_id": default_workspace_response.json()["workspace_id"],
            "workspace_id": workspace["id"],
            "brand_id": brand["id"],
            "channel_id": channel["id"],
            "active_policy_config_id": policy_payload["id"],
            "state_snapshot_id": snapshot_payload["id"],
            "source_sync_run_id": source_sync_payload["ingestion_run_id"],
            "topic_pool_refresh_run_id": topic_refresh_payload["refresh_run_id"],
            "decision_batch_id": first_decision_payload["batch_id"],
            "second_decision_batch_id": second_decision_payload["batch_id"],
            "reviewed_decision_event_id": review_payload["decision_event_id"],
            "publish_record_id": publish_payload["publish_record_id"],
            "evaluation_run_id": evaluation_payload["evaluation_run_id"],
            "first_batch_chosen_count": first_decision_payload["chosen_count"],
            "second_batch_chosen_count": second_decision_payload["chosen_count"],
            "composite_reward": performance_payload["composite_reward"],
            "evaluation_sample_count": evaluation_payload["summary"]["sample_count"],
            "second_batch_effect": effect,
            "latency_ms": latency_ms,
            "db_path": acceptance_storage["db_path"],
            "chroma_dir": acceptance_storage["chroma_dir"],
        },
    )
