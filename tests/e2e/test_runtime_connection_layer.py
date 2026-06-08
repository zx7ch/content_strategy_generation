"""E2E tests for local-first runtime connection contracts."""

from __future__ import annotations

import asyncio

import httpx
import pytest

import app.api.routes.router as router_module
from app.api.routes.router import app


@pytest.mark.asyncio
async def test_health_exposes_runtime_contract_and_features():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["service"]
    assert body["version"]
    assert body["api_contract"] == "local-runtime-v1"
    assert body["features"]["publish_candidate_artifacts"] is True
    assert body["features"]["embedding_prewarm"] is True


@pytest.mark.asyncio
async def test_runtime_prewarm_endpoint_starts_embedding_warmup(monkeypatch):
    original_task = router_module._embedding_prewarm_task
    original_status = dict(router_module._embedding_prewarm_status)
    router_module._embedding_prewarm_task = None
    router_module._embedding_prewarm_status = {"status": "idle"}

    async def fake_prewarm() -> None:
        router_module._embedding_prewarm_status = {"status": "ready", "message": "fake-ready"}

    monkeypatch.setattr(router_module, "_run_embedding_prewarm", fake_prewarm)

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/runtime/prewarm")
            assert response.status_code == 200
            await asyncio.sleep(0)
            status = await client.get("/runtime/prewarm")

        assert status.status_code == 200
        assert status.json()["embedding"]["status"] == "ready"
    finally:
        router_module._embedding_prewarm_task = original_task
        router_module._embedding_prewarm_status = original_status
