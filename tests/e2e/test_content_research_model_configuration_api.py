from __future__ import annotations

import httpx
import pytest

from app.api.routes.router import app
from app.services.llm.configuration_service import LiteLLMConfigurationService
from app.services.llm.configuration_store import SQLiteLLMConfigurationStore
from app.services.llm.types import LLMResponse, TokenUsage


class ProbeAdapter:
    async def generate(self, request, api_key, model, base_url=None):
        return LLMResponse(content='{"ok":true}', provider="openai_compatible", model=model, usage=TokenUsage(), latency_ms=1)


@pytest.fixture()
async def client(tmp_path):
    original = getattr(app.state, "llm_configuration_service", None)
    app.state.llm_configuration_service = LiteLLMConfigurationService(
        store=SQLiteLLMConfigurationStore(str(tmp_path / "config.db")), probe_adapter=ProbeAdapter(),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as value:
        yield value
    if original is None:
        delattr(app.state, "llm_configuration_service")
    else:
        app.state.llm_configuration_service = original


@pytest.mark.asyncio
async def test_workspace_scoped_configuration_api_redacts_key(client):
    headers = {"X-Workspace-Id": "ws_1", "X-User-Id": "user_1"}
    saved = await client.put("/content-research/llm-config", headers=headers, json={
        "base_url": "https://proxy.example/v1", "model": "model-x", "api_key": "secret-1234",
    })
    assert saved.status_code == 200
    assert "api_key" not in saved.json()
    assert saved.json()["api_key_suffix"] == "1234"
    other_user = await client.get("/content-research/llm-config", headers={"X-Workspace-Id": "ws_1", "X-User-Id": "user_2"})
    assert other_user.json()["source"] == "system_default"


@pytest.mark.asyncio
async def test_configuration_api_requires_principal_and_delete_restores_default(client):
    assert (await client.get("/content-research/llm-config")).status_code == 401
    headers = {"X-Workspace-Id": "ws_1", "X-User-Id": "user_1"}
    await client.put("/content-research/llm-config", headers=headers, json={
        "base_url": "https://proxy.example/v1", "model": "model-x", "api_key": "secret-1234",
    })
    deleted = await client.delete("/content-research/llm-config", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["source"] == "system_default"


@pytest.mark.asyncio
@pytest.mark.parametrize("path,method", [
    ("/content-research/llm-config/validate", "POST"),
    ("/content-research/llm-config", "PUT"),
])
async def test_configuration_validation_error_never_echoes_api_key(client, path, method):
    secret = "secret-that-must-never-be-returned"

    response = await client.request(
        method,
        path,
        headers={"X-Workspace-Id": "ws_1", "X-User-Id": "user_1"},
        json={"base_url": "https://proxy.example/v1", "api_key": secret},
    )

    assert response.status_code == 422
    assert secret not in response.text


@pytest.mark.asyncio
async def test_configuration_put_cors_preflight_accepts_real_origin(client):
    response = await client.options(
        "/content-research/llm-config",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type,x-workspace-id,x-user-id",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "PUT" in response.headers["access-control-allow-methods"]
