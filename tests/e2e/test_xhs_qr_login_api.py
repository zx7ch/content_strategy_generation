from __future__ import annotations

import httpx
import pytest

from app.api.routes.router import app


class FakeQRSession:
    def start(self):
        return {"attempt_id": "xhsqr_1", "status": "pending", "qr_image_data_url": "data:image/png;base64,AA==", "failure_code": None}

    def status(self, attempt_id: str):
        if attempt_id != "xhsqr_1":
            return None
        return {"attempt_id": attempt_id, "status": "authenticated", "qr_image_data_url": "data:image/png;base64,AA==", "failure_code": None}

    def current_status(self):
        return self.status("xhsqr_1")


@pytest.mark.asyncio
async def test_qr_login_api_exposes_only_redacted_attempt_projection():
    original = getattr(app.state, "xhs_qr_login_session", None)
    app.state.xhs_qr_login_session = FakeQRSession()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            created = await client.post("/content-research/providers/xiaohongshu/login/qr")
            observed = await client.get("/content-research/providers/xiaohongshu/login/qr/xhsqr_1")
            restored = await client.get("/content-research/providers/xiaohongshu/login/qr")
    finally:
        if original is None:
            delattr(app.state, "xhs_qr_login_session")
        else:
            app.state.xhs_qr_login_session = original

    assert created.json() == {"attempt_id": "xhsqr_1", "status": "pending", "qr_image_data_url": "data:image/png;base64,AA==", "failure_code": None}
    assert observed.json()["status"] == "authenticated"
    assert restored.json() == observed.json()
