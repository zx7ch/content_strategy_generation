from __future__ import annotations

import time

from app.services.xhs_qr_auth import XHSQRLoginAttempt, XHSQRLoginSession, _prepare_upstream_runtime
from app.services.xhs_spider import XHSSpiderClient


def test_start_returns_redacted_qr_projection_and_reuses_pending_attempt() -> None:
    session: XHSQRLoginSession

    def factory():
        with session._lock:
            assert session._attempt is not None
            session._attempt.qr_image_data_url = "data:image/png;base64,redacted"
            session._qr_ready.set()
        time.sleep(0.05)
        return object()

    session = XHSQRLoginSession(auth_factory=factory)
    first = session.start()
    second = session.start()

    assert first["status"] == "pending"
    assert first["attempt_id"] == second["attempt_id"]
    assert first["qr_image_data_url"].startswith("data:image/png;base64,")
    assert "qr-secret" not in str(first)


def test_qr_renderer_returns_a_redacted_image_data_url() -> None:
    session = XHSQRLoginSession(auth_factory=lambda: object())
    session._attempt = XHSQRLoginAttempt(attempt_id="xhsqr_test")

    session._publish_qr_url("https://example.test/qr-secret")

    projection = session.status("xhsqr_test")
    assert projection is not None
    assert projection["qr_image_data_url"].startswith("data:image/png;base64,")
    assert "qr-secret" not in str(projection)


def test_authenticated_qr_auth_is_used_before_cookie_fallback(monkeypatch) -> None:
    auth = object()
    session = XHSQRLoginSession(auth_factory=lambda: auth)
    session._auth = auth
    client = XHSSpiderClient(cookies="stale-cookie", auth_provider=session.get_auth)
    received = []

    class Api:
        def __init__(self, upstream_auth):
            received.append(upstream_auth)

        def bootstrap(self):
            return self

    monkeypatch.setattr(client, "_submodule_path", client._submodule_path)
    import sys
    from types import ModuleType

    api_module = ModuleType("apis.xhs_pc_apis")
    api_module.XHS_Apis = Api
    auth_module = ModuleType("xhs_utils.xhs_pc")
    auth_module.XHSPcAuth = object
    monkeypatch.setitem(sys.modules, "apis.xhs_pc_apis", api_module)
    monkeypatch.setitem(sys.modules, "xhs_utils.xhs_pc", auth_module)

    client._get_api()

    assert received == [auth]


def test_qr_login_prepares_bundled_spider_import_and_node_signer(monkeypatch, tmp_path) -> None:
    import app.services.xhs_qr_auth as qr_auth

    bundled = tmp_path / "app" / "ingest" / "xhs_spider"
    (bundled / "node_modules").mkdir(parents=True)
    monkeypatch.setattr(qr_auth, "Path", lambda _value: tmp_path / "app" / "services" / "xhs_qr_auth.py")
    monkeypatch.setattr(qr_auth.sys, "path", [item for item in qr_auth.sys.path if item != str(bundled)])
    monkeypatch.delenv("NODE_PATH", raising=False)

    _prepare_upstream_runtime()

    assert qr_auth.sys.path[0] == str(bundled)
    assert qr_auth.os.environ["NODE_PATH"] == str(bundled / "node_modules")
