from __future__ import annotations

import threading
import time

from app.services.xhs_qr_auth import XHSQRLoginAttempt, XHSQRLoginSession, _prepare_upstream_runtime
from app.services.xhs_spider import XHSSpiderClient
from app.services.xhs_credentials import XHSCredentialStore


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


def test_qr_success_persists_cookie_without_exposing_it(tmp_path) -> None:
    class Auth:
        cookies = "a1=qr-secret; web_session=qr-secret"

    store = XHSCredentialStore(str(tmp_path / "runtime.db"))
    session = XHSQRLoginSession(auth_factory=Auth, credential_store=store)
    session.start()

    assert store.get_active() == Auth.cookies
    assert "qr-secret" not in str(store.get_status())


def test_manual_cookie_supersedes_a_pending_qr_without_losing_the_new_login(monkeypatch, tmp_path) -> None:
    qr_started = threading.Event()
    allow_qr_completion = threading.Event()

    class QRAuth:
        cookies = "a1=qr-secret; web_session=qr-secret"

    store = XHSCredentialStore(str(tmp_path / "runtime.db"))
    session: XHSQRLoginSession

    def factory():
        session._publish_qr_url("https://example.test/qr")
        qr_started.set()
        assert allow_qr_completion.wait(timeout=1)
        return QRAuth()

    session = XHSQRLoginSession(auth_factory=factory, credential_store=store)
    monkeypatch.setattr(session, "_auth_from_cookie", lambda cookie: {"cookie": cookie})

    assert session.start()["status"] == "pending"
    assert qr_started.wait(timeout=1)
    session.set_manual_cookie("a1=manual; web_session=manual")
    allow_qr_completion.set()

    for _ in range(20):
        if store.get_status().source == "manual_cookie":
            break
        time.sleep(0.01)
    assert store.get_active() == "a1=manual; web_session=manual"
    assert session.get_auth() == {"cookie": "a1=manual; web_session=manual"}


async def test_spider_auth_failure_marks_the_persisted_login_stale(tmp_path) -> None:
    store = XHSCredentialStore(str(tmp_path / "runtime.db"))
    store.replace("a1=first; web_session=first", "manual_cookie")
    invalidated = []
    client = XHSSpiderClient(on_auth_failure=lambda: (invalidated.append(True), store.mark_stale("auth_required")))

    async def rejected_search(*_args, **_kwargs):
        return False, "unauthorized", []

    client.search = rejected_search  # type: ignore[method-assign]
    try:
        await client.search_with_retry_result("测试", num=1)
    except Exception:
        pass
    else:
        raise AssertionError("authentication failure must be returned to the caller")

    assert invalidated == [True]
    assert store.get_status().failure_code == "auth_required"


def test_spider_keeps_auth_failure_typed_when_status_persistence_is_unavailable() -> None:
    def persistence_failure() -> None:
        raise RuntimeError("database unavailable")

    client = XHSSpiderClient(on_auth_failure=persistence_failure)

    error = client._classify_error("unauthorized")

    assert error.__class__.__name__ == "SpiderPermanentError"
    assert "Auth error" in str(error)
