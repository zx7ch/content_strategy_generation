"""Release guard for the removed Creator workflow forwarding endpoint."""

from __future__ import annotations

import httpx
import pytest

from app.api.routes.router import app


@pytest.mark.asyncio
async def test_removed_workflow_forwarding_endpoint_is_not_reachable() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/threads/thread-old-forwarder/workflow",
            json={"user_query": "must use the canonical message command"},
        )

    assert response.status_code == 404
