from __future__ import annotations

from app.services.xhs_credentials import XHSCredentialStore


def test_local_cookie_status_is_redacted_and_survives_store_recreation(tmp_path):
    path = str(tmp_path / "runtime.db")
    store = XHSCredentialStore(path)
    status = store.replace("a1=value; web_session=value", "manual_cookie")

    assert status.authenticated is True
    assert status.source == "manual_cookie"
    assert "value" not in repr(status)
    assert XHSCredentialStore(path).get_active() == "a1=value; web_session=value"


def test_invalid_replacement_does_not_replace_active_cookie(tmp_path):
    store = XHSCredentialStore(str(tmp_path / "runtime.db"))
    store.replace("a1=first; web_session=first", "manual_cookie")

    try:
        store.replace("  ", "manual_cookie")
    except ValueError as error:
        assert str(error) == "invalid_cookie"
    else:
        raise AssertionError("invalid cookie must fail")
    assert store.get_active() == "a1=first; web_session=first"


def test_auth_failure_marks_cookie_stale_without_exposing_or_deleting_it(tmp_path):
    store = XHSCredentialStore(str(tmp_path / "runtime.db"))
    store.replace("a1=first; web_session=first", "manual_cookie")

    status = store.mark_stale("auth_required")

    assert status.authenticated is False
    assert status.source == "manual_cookie"
    assert status.failure_code == "auth_required"
    assert store.get_active() is None
    assert "first" not in repr(status)
