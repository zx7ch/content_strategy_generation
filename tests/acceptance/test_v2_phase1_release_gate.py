from __future__ import annotations

import time
from dataclasses import replace

from fastapi.testclient import TestClient
import pytest

from app.main import app
from tests.acceptance.conftest import write_acceptance_artifact
from tests.acceptance.v2_phase1_helpers import (
    create_brand,
    create_channel,
    create_evaluation_run,
    create_publish_record_from_decision,
    create_workspace,
    detect_second_batch_effect,
    get_topic_pool,
    headers,
    import_performance,
    refresh_topic_pool,
    run_decision_batch,
    seed_policy_and_snapshot,
    seed_source_sync,
)


@pytest.mark.acceptance
def test_v2_phase1_second_batch_reflects_feedback_updates(
    acceptance_artifact_dir,
) -> None:
    started = time.perf_counter()

    with TestClient(app) as client:
        workspace = create_workspace(client, name="Feedback Loop Workspace", slug="feedback-loop")
        brand = create_brand(client, workspace_id=workspace["id"], name="Feedback Loop Brand")
        channel = create_channel(client, workspace_id=workspace["id"], brand_id=brand["id"], handle="feedback-loop")
        seed_policy_and_snapshot(client, workspace_id=workspace["id"], brand_id=brand["id"])
        seed_source_sync(client, workspace_id=workspace["id"], brand_id=brand["id"], channel_id=channel["id"])
        refresh_topic_pool(client, workspace_id=workspace["id"], brand_id=brand["id"])

        topic_pool_before = get_topic_pool(client, workspace_id=workspace["id"], brand_id=brand["id"])
        first_batch = run_decision_batch(client, workspace_id=workspace["id"], brand_id=brand["id"])
        publish = create_publish_record_from_decision(
            client,
            workspace_id=workspace["id"],
            brand_id=brand["id"],
            channel_id=channel["id"],
            decision_batch=first_batch,
        )
        performance = import_performance(
            client,
            workspace_id=workspace["id"],
            publish_record_id=publish["publish_record_id"],
        )

        topic_pool_after = get_topic_pool(client, workspace_id=workspace["id"], brand_id=brand["id"])
        second_batch = run_decision_batch(client, workspace_id=workspace["id"], brand_id=brand["id"])

    effect = detect_second_batch_effect(
        topic_pool_before=topic_pool_before,
        topic_pool_after=topic_pool_after,
        first_batch=first_batch,
        second_batch=second_batch,
    )

    assert performance["composite_reward"] > 0
    assert effect["has_downstream_change"], effect
    assert second_batch["chosen_count"] == 3

    write_acceptance_artifact(
        acceptance_artifact_dir,
        "v2_phase1_feedback_loop_effect",
        {
            "workspace_id": workspace["id"],
            "brand_id": brand["id"],
            "first_batch_id": first_batch["batch_id"],
            "second_batch_id": second_batch["batch_id"],
            "composite_reward": performance["composite_reward"],
            "second_batch_effect": effect,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        },
    )


@pytest.mark.acceptance
def test_v2_phase1_evaluation_fails_closed_on_missing_feedback_event(
    acceptance_artifact_dir,
) -> None:
    with TestClient(app) as client:
        workspace = create_workspace(client, name="Eval Missing Feedback", slug="eval-missing-feedback")
        brand = create_brand(client, workspace_id=workspace["id"], name="Eval Missing Feedback Brand")
        channel = create_channel(client, workspace_id=workspace["id"], brand_id=brand["id"], handle="eval-missing-feedback")
        seed_policy_and_snapshot(client, workspace_id=workspace["id"], brand_id=brand["id"])
        seed_source_sync(client, workspace_id=workspace["id"], brand_id=brand["id"], channel_id=channel["id"])
        refresh_topic_pool(client, workspace_id=workspace["id"], brand_id=brand["id"])
        batch = run_decision_batch(client, workspace_id=workspace["id"], brand_id=brand["id"])
        publish = create_publish_record_from_decision(
            client,
            workspace_id=workspace["id"],
            brand_id=brand["id"],
            channel_id=channel["id"],
            decision_batch=batch,
        )
        import_performance(
            client,
            workspace_id=workspace["id"],
            publish_record_id=publish["publish_record_id"],
        )
        client.app.state.v2_feedback_store._feedback_events.clear()  # type: ignore[attr-defined]

        evaluation = create_evaluation_run(client, workspace_id=workspace["id"], brand_id=brand["id"])

    assert evaluation.status_code == 422
    payload = evaluation.json()
    assert payload["error_code"] == "INVALID_FEEDBACK_PAYLOAD"
    assert "missing feedback_event" in payload["error_message"]

    write_acceptance_artifact(
        acceptance_artifact_dir,
        "v2_phase1_evaluation_fail_closed_missing_feedback",
        {
            "workspace_id": workspace["id"],
            "brand_id": brand["id"],
            "decision_batch_id": batch["batch_id"],
            "status_code": evaluation.status_code,
            "error_code": payload["error_code"],
            "error_message": payload["error_message"],
        },
    )


@pytest.mark.acceptance
def test_v2_phase1_evaluation_fails_closed_on_missing_state_snapshot_lineage(
    acceptance_artifact_dir,
) -> None:
    with TestClient(app) as client:
        workspace = create_workspace(client, name="Eval Missing Snapshot", slug="eval-missing-snapshot")
        brand = create_brand(client, workspace_id=workspace["id"], name="Eval Missing Snapshot Brand")
        channel = create_channel(client, workspace_id=workspace["id"], brand_id=brand["id"], handle="eval-missing-snapshot")
        _policy, snapshot = seed_policy_and_snapshot(client, workspace_id=workspace["id"], brand_id=brand["id"])
        seed_source_sync(client, workspace_id=workspace["id"], brand_id=brand["id"], channel_id=channel["id"])
        refresh_topic_pool(client, workspace_id=workspace["id"], brand_id=brand["id"])
        batch = run_decision_batch(client, workspace_id=workspace["id"], brand_id=brand["id"])
        publish = create_publish_record_from_decision(
            client,
            workspace_id=workspace["id"],
            brand_id=brand["id"],
            channel_id=channel["id"],
            decision_batch=batch,
        )
        import_performance(client, workspace_id=workspace["id"], publish_record_id=publish["publish_record_id"])

        decision_store = client.app.state.v2_decision_store
        master_store = client.app.state.v2_master_data_store
        event_id = batch["items"][0]["decision_event_id"]
        event = decision_store.get_decision_event(event_id)
        assert event is not None
        decision_store.save_decision_event(replace(event, brand_state_snapshot_id="missing-snapshot-id"))
        master_store._snapshots.pop(snapshot["id"], None)  # type: ignore[attr-defined]

        evaluation = create_evaluation_run(client, workspace_id=workspace["id"], brand_id=brand["id"])

    assert evaluation.status_code == 422
    payload = evaluation.json()
    assert payload["error_code"] == "INVALID_FEEDBACK_PAYLOAD"
    assert "missing state_snapshot" in payload["error_message"]

    write_acceptance_artifact(
        acceptance_artifact_dir,
        "v2_phase1_evaluation_fail_closed_missing_snapshot",
        {
            "workspace_id": workspace["id"],
            "brand_id": brand["id"],
            "decision_event_id": event_id,
            "removed_snapshot_id": snapshot["id"],
            "status_code": evaluation.status_code,
            "error_code": payload["error_code"],
            "error_message": payload["error_message"],
        },
    )


@pytest.mark.acceptance
def test_v2_phase1_workspace_scope_isolation_holds_across_v2_routes(
    acceptance_artifact_dir,
) -> None:
    with TestClient(app) as client:
        workspace_a = create_workspace(client, name="Scope A", slug="scope-a")
        workspace_b = create_workspace(client, name="Scope B", slug="scope-b")
        brand = create_brand(client, workspace_id=workspace_a["id"], name="Scope Brand")
        channel = create_channel(client, workspace_id=workspace_a["id"], brand_id=brand["id"], handle="scope-brand")
        seed_policy_and_snapshot(client, workspace_id=workspace_a["id"], brand_id=brand["id"])
        seed_source_sync(client, workspace_id=workspace_a["id"], brand_id=brand["id"], channel_id=channel["id"])
        refresh_topic_pool(client, workspace_id=workspace_a["id"], brand_id=brand["id"])
        batch = run_decision_batch(client, workspace_id=workspace_a["id"], brand_id=brand["id"])
        publish = create_publish_record_from_decision(
            client,
            workspace_id=workspace_a["id"],
            brand_id=brand["id"],
            channel_id=channel["id"],
            decision_batch=batch,
        )
        import_performance(client, workspace_id=workspace_a["id"], publish_record_id=publish["publish_record_id"])
        evaluation = create_evaluation_run(client, workspace_id=workspace_a["id"], brand_id=brand["id"])
        assert evaluation.status_code == 201

        responses = {
            "topic_pool": client.get(f"/brands/{brand['id']}/topic-pool", headers=headers(workspace_b["id"])),
            "latest_batch": client.get(
                f"/brands/{brand['id']}/decision-batches/latest",
                headers=headers(workspace_b["id"]),
            ),
            "publish_records": client.get(
                f"/brands/{brand['id']}/publish-records",
                headers=headers(workspace_b["id"]),
            ),
            "performance": client.get(
                f"/brands/{brand['id']}/performance-snapshots",
                headers=headers(workspace_b["id"]),
            ),
            "evaluation_latest": client.get(
                f"/brands/{brand['id']}/evaluation-runs/latest",
                headers=headers(workspace_b["id"]),
            ),
        }

    for response in responses.values():
        assert response.status_code == 403
        assert response.json()["error_code"] == "WORKSPACE_SCOPE_MISMATCH"

    write_acceptance_artifact(
        acceptance_artifact_dir,
        "v2_phase1_workspace_scope_isolation",
        {
            "workspace_a_id": workspace_a["id"],
            "workspace_b_id": workspace_b["id"],
            "brand_id": brand["id"],
            "checked_routes": sorted(responses.keys()),
        },
    )
