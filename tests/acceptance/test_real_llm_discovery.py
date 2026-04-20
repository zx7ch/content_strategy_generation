from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.acceptance.conftest import write_acceptance_artifact


def _workspace_headers(workspace_id: str) -> dict[str, str]:
    return {
        "X-Workspace-Id": workspace_id,
        "X-User-Id": "acceptance-user",
    }


@pytest.mark.acceptance
@pytest.mark.real_dependency
def test_real_llm_discovery_query_expansion(
    llm_ready: None,
    acceptance_queries: dict[str, str],
    acceptance_storage,
    acceptance_artifact_dir,
):
    provider = ""
    model = ""
    with TestClient(create_app()) as client:
        workspace = client.post(
            "/workspaces",
            json={"name": "Acceptance Workspace", "slug": "acceptance-workspace", "timezone": "Asia/Shanghai"},
        )
        assert workspace.status_code == 201
        workspace_payload = workspace.json()
        headers = _workspace_headers(workspace_payload["id"])

        brand = client.post(
            "/brands",
            headers=headers,
            json={"name": "Acceptance Brand", "category": "beauty", "stage": "growth"},
        )
        assert brand.status_code == 201
        brand_payload = brand.json()

        started = time.perf_counter()
        created = client.post(
            f"/brands/{brand_payload['id']}/discovery/tasks",
            headers=headers,
            json={"topic": acceptance_queries["primary"]},
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        llm_client = getattr(getattr(client.app.state.v2_discovery_service, "_query_expander", None), "_llm", None)
        provider = getattr(getattr(llm_client, "provider", None), "value", "")
        model = getattr(llm_client, "model", "")

    assert created.status_code == 201
    payload = created.json()
    assert payload["task_id"]
    assert payload["token"]
    assert payload["query_generation_version"] == "llm_v1"
    assert payload["query_generation_source"] == "llm"
    assert 1 <= len(payload["expanded_queries"]) <= 6
    assert payload["expanded_queries"][0]["category"] == "core"
    assert payload["expanded_queries"][0]["query_text"]
    assert all(item["query_text"].strip() for item in payload["expanded_queries"])
    assert len({item["query_text"] for item in payload["expanded_queries"]}) == len(payload["expanded_queries"])
    assert {
        item["category"] for item in payload["expanded_queries"]
    }.issubset({"core", "crowd", "scenario", "problem", "compare", "decision"})

    write_acceptance_artifact(
        acceptance_artifact_dir,
        "real_llm_discovery",
        {
            "provider": provider,
            "model": model,
            "query": acceptance_queries["primary"],
            "latency_ms": latency_ms,
            "query_generation_version": payload["query_generation_version"],
            "query_generation_source": payload["query_generation_source"],
            "expanded_queries": payload["expanded_queries"],
        },
    )
