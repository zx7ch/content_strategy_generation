from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi.testclient import TestClient


def headers(workspace_id: str) -> dict[str, str]:
    return {
        "X-Workspace-Id": workspace_id,
        "X-User-Id": "acceptance-user",
    }


def create_workspace(client: TestClient, *, name: str, slug: str) -> dict[str, Any]:
    response = client.post(
        "/workspaces",
        json={
            "name": name,
            "slug": slug,
            "timezone": "Asia/Shanghai",
        },
    )
    assert response.status_code == 201
    return response.json()


def create_brand(client: TestClient, *, workspace_id: str, name: str = "Acme Outdoor") -> dict[str, Any]:
    response = client.post(
        "/brands",
        headers=headers(workspace_id),
        json={
            "name": name,
            "category": "outdoor",
            "stage": "growth",
            "target_audience": {"age_ranges": ["25-34"], "gender_skew": "female"},
        },
    )
    assert response.status_code == 201
    return response.json()


def create_channel(client: TestClient, *, workspace_id: str, brand_id: str, handle: str = "acme-xhs") -> dict[str, Any]:
    response = client.post(
        f"/brands/{brand_id}/channels",
        headers=headers(workspace_id),
        json={
            "platform": "xiaohongshu",
            "account_name": "Acme 小红书",
            "profile_url": f"https://www.xiaohongshu.com/user/profile/{handle}",
        },
    )
    assert response.status_code == 201
    return response.json()


def seed_policy_and_snapshot(client: TestClient, *, workspace_id: str, brand_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    policy = client.put(
        f"/brands/{brand_id}/policy-configs/active",
        headers=headers(workspace_id),
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
    assert policy.status_code == 200
    snapshot = client.post(
        f"/brands/{brand_id}/state-snapshots",
        headers=headers(workspace_id),
        json={
            "state_version": "state_v1",
            "stage": "growth",
            "state_features": {"audience_focus": "urban commuting"},
            "source_version": "v1",
        },
    )
    assert snapshot.status_code == 201
    return policy.json(), snapshot.json()


def seed_source_sync(client: TestClient, *, workspace_id: str, brand_id: str, channel_id: str) -> dict[str, Any]:
    response = client.post(
        f"/brands/{brand_id}/source-syncs",
        headers=headers(workspace_id),
        json={
            "source_type": "xhs_extension_capture",
            "source_adapter": "extension_source_sync_adapter_v1",
            "channel_id": channel_id,
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
    assert response.status_code == 202
    return response.json()


def refresh_topic_pool(client: TestClient, *, workspace_id: str, brand_id: str) -> dict[str, Any]:
    response = client.post(
        f"/brands/{brand_id}/topic-pool/refresh",
        headers=headers(workspace_id),
        json={"archive_threshold_days": 60},
    )
    assert response.status_code == 202
    return response.json()


def get_topic_pool(client: TestClient, *, workspace_id: str, brand_id: str) -> dict[str, Any]:
    response = client.get(
        f"/brands/{brand_id}/topic-pool",
        headers=headers(workspace_id),
    )
    assert response.status_code == 200
    return response.json()


def run_decision_batch(client: TestClient, *, workspace_id: str, brand_id: str, requested_slot_count: int = 3) -> dict[str, Any]:
    response = client.post(
        f"/brands/{brand_id}/decisions/run",
        headers=headers(workspace_id),
        json={
            "requested_slot_count": requested_slot_count,
            "objective": "topic_recommendation",
            "exploration_mode": "balanced",
        },
    )
    assert response.status_code == 201
    return response.json()


def create_publish_record_from_decision(
    client: TestClient,
    *,
    workspace_id: str,
    brand_id: str,
    channel_id: str,
    decision_batch: dict[str, Any],
    item_index: int = 0,
) -> dict[str, Any]:
    item = decision_batch["items"][item_index]
    response = client.post(
        "/publish-records",
        headers=headers(workspace_id),
        json={
            "brand_id": brand_id,
            "channel_id": channel_id,
            "topic_pool_item_id": item["topic_pool_item_id"],
            "decision_event_id": item["decision_event_id"],
            "decision_batch_id": decision_batch["batch_id"],
            "publish_status": "published",
            "published_at": "2026-04-10T09:30:00+08:00",
            "creative_variant": "v1",
        },
    )
    assert response.status_code == 201
    return response.json()


def import_performance(
    client: TestClient,
    *,
    workspace_id: str,
    publish_record_id: str,
    snapshot_at: str = "2026-04-17T09:30:00+08:00",
) -> dict[str, Any]:
    response = client.post(
        "/performance/import",
        headers=headers(workspace_id),
        json={
            "publish_record_id": publish_record_id,
            "observation_window_hours": 168,
            "snapshot_at": snapshot_at,
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
    assert response.status_code == 201
    return response.json()


def create_evaluation_run(client: TestClient, *, workspace_id: str, brand_id: str):
    return client.post(
        "/evaluation-runs",
        headers=headers(workspace_id),
        json={"brand_id": brand_id, "evaluation_type": "replay"},
    )


def summarize_topic_pool(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        item_id = str(item.get("id") or "")
        if not item_id:
            continue
        summary[item_id] = {
            "item_id": item_id,
            "topic_id": str(item.get("topic_id") or ""),
            "title": str(item.get("title") or ""),
            "status": str(item.get("status") or ""),
            "final_score": float(item.get("final_score") or 0.0),
            "rank": index,
        }
    return summary


def summarize_batch(batch: dict[str, Any]) -> dict[str, Any]:
    items = batch.get("items") if isinstance(batch.get("items"), list) else []
    ordered_topic_pool_item_ids = [
        str(item.get("topic_pool_item_id") or "")
        for item in items
        if str(item.get("topic_pool_item_id") or "")
    ]
    return {
        "batch_id": str(batch.get("batch_id") or ""),
        "chosen_count": int(batch.get("chosen_count") or 0),
        "ordered_topic_pool_item_ids": ordered_topic_pool_item_ids,
        "item_ids": set(ordered_topic_pool_item_ids),
    }


def detect_second_batch_effect(
    *,
    topic_pool_before: dict[str, Any],
    topic_pool_after: dict[str, Any],
    first_batch: dict[str, Any],
    second_batch: dict[str, Any],
) -> dict[str, Any]:
    before_items = summarize_topic_pool(topic_pool_before.get("items", []))
    after_items = summarize_topic_pool(topic_pool_after.get("items", []))
    first_summary = summarize_batch(first_batch)
    second_summary = summarize_batch(second_batch)

    common_item_ids = sorted(set(before_items) & set(after_items))
    changed_scores = [
        {
            "item_id": item_id,
            "before": before_items[item_id]["final_score"],
            "after": after_items[item_id]["final_score"],
        }
        for item_id in common_item_ids
        if before_items[item_id]["final_score"] != after_items[item_id]["final_score"]
    ]
    changed_ranks = [
        {
            "item_id": item_id,
            "before_rank": before_items[item_id]["rank"],
            "after_rank": after_items[item_id]["rank"],
        }
        for item_id in common_item_ids
        if before_items[item_id]["rank"] != after_items[item_id]["rank"]
    ]
    eligibility_removed = sorted(first_summary["item_ids"] - second_summary["item_ids"])
    eligibility_added = sorted(second_summary["item_ids"] - first_summary["item_ids"])
    archived_items = [
        item_id
        for item_id in common_item_ids
        if before_items[item_id]["status"] != after_items[item_id]["status"]
        and "archived" in {before_items[item_id]["status"], after_items[item_id]["status"]}
    ]

    change_flags = {
        "score_changed": bool(changed_scores),
        "ranking_changed": first_summary["ordered_topic_pool_item_ids"] != second_summary["ordered_topic_pool_item_ids"]
        or bool(changed_ranks),
        "eligibility_changed": bool(eligibility_removed or eligibility_added),
        "archive_state_changed": bool(archived_items),
    }
    return {
        "first_batch_id": first_summary["batch_id"],
        "second_batch_id": second_summary["batch_id"],
        "first_batch_item_ids": first_summary["ordered_topic_pool_item_ids"],
        "second_batch_item_ids": second_summary["ordered_topic_pool_item_ids"],
        "changed_scores": changed_scores,
        "changed_ranks": changed_ranks,
        "eligibility_removed": eligibility_removed,
        "eligibility_added": eligibility_added,
        "archived_items": archived_items,
        "change_flags": change_flags,
        "has_downstream_change": any(change_flags.values()),
    }


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
