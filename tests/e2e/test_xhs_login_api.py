from __future__ import annotations

import httpx
import pytest

from app.api.routes.router import app
from app.services.xhs_credentials import XHSCredentialStore


class FakeSession:
    def __init__(self, store): self.store = store
    def set_manual_cookie(self, cookie): self.store.replace(cookie, "manual_cookie")
    def clear_auth(self): self.store.clear()


@pytest.mark.asyncio
async def test_manual_xhs_login_is_redacted_and_persists_locally(tmp_path):
    secret = "a1=very-secret; web_session=also-secret"
    store = XHSCredentialStore(str(tmp_path / "runtime.db"))
    original_store = getattr(app.state, "xhs_credential_store", None)
    original_session = getattr(app.state, "xhs_qr_login_session", None)
    app.state.xhs_credential_store, app.state.xhs_qr_login_session = store, FakeSession(store)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            before = await client.get("/content-research/providers/xiaohongshu/login")
            saved = await client.put("/content-research/providers/xiaohongshu/login", json={"cookie": secret})
            cleared = await client.delete("/content-research/providers/xiaohongshu/login")
    finally:
        app.state.xhs_credential_store, app.state.xhs_qr_login_session = original_store, original_session
    assert before.json()["authenticated"] is False
    assert saved.json()["authenticated"] is True
    assert saved.json()["source"] == "manual_cookie"
    assert secret not in saved.text
    assert cleared.json()["authenticated"] is False
