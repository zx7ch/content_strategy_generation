"""Explicit, redacted QR-login session for the local XHS Spider runtime."""

from __future__ import annotations

import base64
import io
import logging
import os
import sys
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.xhs_credentials import XHSCredentialStore

logger = logging.getLogger(__name__)


@dataclass
class XHSQRLoginAttempt:
    attempt_id: str
    status: str = "pending"
    qr_image_data_url: str | None = None
    failure_code: str | None = None

    def public_projection(self) -> dict[str, str | None]:
        return {
            "attempt_id": self.attempt_id,
            "status": self.status,
            "qr_image_data_url": self.qr_image_data_url,
            "failure_code": self.failure_code,
        }


class XHSQRLoginSession:
    """Own one user-initiated QR login and its process-local upstream auth."""

    def __init__(self, auth_factory: Callable[[], Any] | None = None, credential_store: XHSCredentialStore | None = None) -> None:
        self._lock = threading.RLock()
        self._qr_ready = threading.Event()
        self._attempt: XHSQRLoginAttempt | None = None
        self._generation = 0
        self._login_context = threading.local()
        self._auth: Any | None = None
        self._auth_factory = auth_factory or self._upstream_qr_auth_factory
        self._credential_store = credential_store
        if credential_store is not None:
            cookie = credential_store.get_active()
            if cookie:
                try:
                    self._auth = self._auth_from_cookie(cookie)
                except Exception:
                    logger.exception("failed to restore persisted Xiaohongshu authentication")

    def get_auth(self) -> Any | None:
        with self._lock:
            return self._auth

    def start(self) -> dict[str, str | None]:
        with self._lock:
            if self._attempt is not None and self._attempt.status == "pending":
                return self._attempt.public_projection()
            self._generation += 1
            self._attempt = XHSQRLoginAttempt(attempt_id=f"xhsqr_{uuid.uuid4().hex}")
            generation = self._generation
            self._qr_ready.clear()
            try:
                # Preload the renderer on the request thread.  Importing PIL
                # lazily from the login daemon has proven unreliable under
                # the test/runtime thread boundary.
                import qrcode  # noqa: F401
            except Exception:
                self._attempt.status = "failed"
                self._attempt.failure_code = "qr_render_failed"
                return self._attempt.public_projection()
            threading.Thread(
                target=self._run_login,
                args=(self._attempt.attempt_id, generation),
                daemon=True,
                name="xhs-qr-login",
            ).start()

        self._qr_ready.wait(timeout=45)
        with self._lock:
            assert self._attempt is not None
            if self._attempt.qr_image_data_url is None and self._attempt.status == "pending":
                self._attempt.status = "failed"
                self._attempt.failure_code = "qr_unavailable"
            return self._attempt.public_projection()

    def status(self, attempt_id: str) -> dict[str, str | None] | None:
        with self._lock:
            if self._attempt is None or self._attempt.attempt_id != attempt_id:
                return None
            return self._attempt.public_projection()

    def current_status(self) -> dict[str, str | None] | None:
        """Return the redacted active attempt without exposing upstream auth."""
        with self._lock:
            return self._attempt.public_projection() if self._attempt is not None else None

    def _run_login(self, attempt_id: str, generation: int) -> None:
        self._login_context.generation = generation
        try:
            auth = self._auth_factory()
        except Exception:
            logger.exception("failed to render Xiaohongshu QR image")
            with self._lock:
                if self._is_current_attempt(attempt_id, generation):
                    self._attempt.status = "failed"
                    self._attempt.failure_code = "login_failed"
                    self._qr_ready.set()
            return
        with self._lock:
            if self._is_current_attempt(attempt_id, generation):
                self._auth = auth
                self._attempt.status = "authenticated"
                self._attempt.failure_code = None
                if self._credential_store is not None:
                    cookie = str(getattr(auth, "cookies", "")).strip()
                    if cookie:
                        self._credential_store.replace(cookie, "qr")
                self._qr_ready.set()

    def _publish_qr_url(self, url: str) -> None:
        try:
            import qrcode

            image = qrcode.make(url)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception:
            with self._lock:
                if self._is_current_generation():
                    self._attempt.status = "failed"
                    self._attempt.failure_code = "qr_render_failed"
                    self._qr_ready.set()
            return
        with self._lock:
            if self._is_current_generation():
                self._attempt.qr_image_data_url = data_url
                self._qr_ready.set()

    def _upstream_qr_auth_factory(self) -> Any:
        _prepare_upstream_runtime()
        from apis.xhs_pc_login_apis import XHSLoginApi
        from xhs_utils.xhs_pc import XHSPcAuth

        original_renderer = XHSLoginApi.show_qrcode_image
        XHSLoginApi.show_qrcode_image = staticmethod(self._publish_qr_url)
        try:
            return XHSPcAuth.from_qrcode_login(show_in_terminal=False)
        finally:
            XHSLoginApi.show_qrcode_image = original_renderer

    def set_manual_cookie(self, cookie: str) -> None:
        auth = self._auth_from_cookie(cookie)
        with self._lock:
            self._generation += 1
            if self._credential_store is not None:
                self._credential_store.replace(cookie, "manual_cookie")
            self._auth = auth
            self._supersede_pending_attempt()

    def clear_auth(self) -> None:
        with self._lock:
            self._generation += 1
            if self._credential_store is not None:
                self._credential_store.clear()
            self._auth = None
            self._supersede_pending_attempt()

    def mark_auth_stale(self, failure_code: str = "auth_required") -> None:
        with self._lock:
            self._generation += 1
            if self._credential_store is not None:
                self._credential_store.mark_stale(failure_code)
            self._auth = None
            self._supersede_pending_attempt()

    def _is_current_attempt(self, attempt_id: str, generation: int) -> bool:
        return (
            self._attempt is not None
            and self._attempt.attempt_id == attempt_id
            and self._attempt.status == "pending"
            and self._generation == generation
        )

    def _is_current_generation(self) -> bool:
        generation = getattr(self._login_context, "generation", None)
        return self._attempt is not None and (generation is None or self._generation == generation)

    def _supersede_pending_attempt(self) -> None:
        if self._attempt is not None and self._attempt.status == "pending":
            self._attempt.status = "failed"
            self._attempt.failure_code = "superseded"
            self._qr_ready.set()

    @staticmethod
    def _auth_from_cookie(cookie: str) -> Any:
        _prepare_upstream_runtime()
        from xhs_utils.xhs_pc import XHSPcAuth
        return XHSPcAuth.from_cookie(cookie)


def _prepare_upstream_runtime() -> None:
    """Make the bundled Spider import and Node signer available to QR login.

    QR authentication starts before the normal Spider client has made a
    provider call, so it must establish the same local runtime prerequisites
    itself instead of relying on that incidental initialization order.
    """
    submodule_path = Path(__file__).parent.parent / "ingest" / "xhs_spider"
    target = str(submodule_path)
    if target not in sys.path:
        sys.path.insert(0, target)
    node_modules = submodule_path / "node_modules"
    if not node_modules.exists():
        return
    node_target = str(node_modules)
    current = os.environ.get("NODE_PATH", "")
    if node_target not in current.split(os.pathsep):
        os.environ["NODE_PATH"] = node_target if not current else f"{node_target}{os.pathsep}{current}"
