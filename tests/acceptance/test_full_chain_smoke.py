from __future__ import annotations

import time

from fastapi.testclient import TestClient
import pytest

from app.main import app
from tests.acceptance.conftest import write_acceptance_artifact


def _wait_for_job(client: TestClient, job_id: str, timeout: float = 180.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = client.get(f"/jobs/{job_id}").json()
        if payload["status"] in {"succeeded", "failed"}:
            return payload
        time.sleep(1.0)
    raise AssertionError(f"job {job_id} did not finish in time")


@pytest.mark.acceptance
@pytest.mark.real_dependency
def test_full_chain_smoke(
    spider_ready: None,
    llm_ready: None,
    acceptance_enabled: None,
    acceptance_queries: dict[str, str],
    acceptance_storage,
    acceptance_artifact_dir,
):
    started = time.perf_counter()
    with TestClient(app) as client:
        create = client.post(
            "/sessions",
            json={
                "user_id": "acceptance-user",
                "user_query": acceptance_queries["primary"],
                "platform": "xiaohongshu",
                "mode": "editing",
            },
        )
        assert create.status_code == 201
        session_id = create.json()["session_id"]

        strategy = client.post(
            f"/sessions/{session_id}/strategy",
            headers={"Idempotency-Key": f"acc-strategy-{session_id}"},
        )
        assert strategy.status_code == 202
        strategy_job = _wait_for_job(client, strategy.json()["job_id"])
        assert strategy_job["status"] == "succeeded"

        generate = client.post(
            f"/sessions/{session_id}/generate",
            json={"topic": acceptance_queries["primary"], "output_language": "zh-CN"},
            headers={"Idempotency-Key": f"acc-generate-{session_id}"},
        )
        assert generate.status_code == 202
        generate_job = _wait_for_job(client, generate.json()["job_id"])
        assert generate_job["status"] in {"succeeded", "failed"}

        session_payload = client.get(f"/sessions/{session_id}").json()
        assert session_payload["session_id"] == session_id
        assert session_payload["stage"] in {"completed", "failed"}

        invalid_last_event = client.get(
            f"/sessions/{session_id}/events",
            headers={"Last-Event-ID": "invalid"},
        )
        assert invalid_last_event.status_code == 400
        assert invalid_last_event.json()["error_code"] == "INVALID_LAST_EVENT_ID"

        with client.stream(
            "GET",
            f"/sessions/{session_id}/events",
            headers={"Last-Event-ID": "0"},
        ) as response:
            assert response.status_code == 200
            chunks = response.iter_text()
            first_chunk = next(chunks)
            assert "event:" in first_chunk

    latency_ms = int((time.perf_counter() - started) * 1000)
    write_acceptance_artifact(
        acceptance_artifact_dir,
        "full_chain_smoke",
        {
            "session_id": session_id,
            "query": acceptance_queries["primary"],
            "strategy_job_status": strategy_job["status"],
            "generation_job_status": generate_job["status"],
            "session_stage": session_payload["stage"],
            "latency_ms": latency_ms,
            "db_path": acceptance_storage["db_path"],
            "chroma_dir": acceptance_storage["chroma_dir"],
        },
    )
