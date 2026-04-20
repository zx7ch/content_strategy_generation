from __future__ import annotations

from app.config import Settings


def test_v2_settings_defaults_do_not_break_legacy_runtime() -> None:
    settings = Settings(_env_file=None)

    assert settings.POSTGRES_DSN == ""
    assert settings.V2_AUTH_ENABLED is False
    assert settings.V2_AUTH_HEADER == "Authorization"
    assert settings.V2_WORKSPACE_HEADER == "X-Workspace-Id"
    assert settings.V2_USER_HEADER == "X-User-Id"
    assert settings.SQLITE_DB_PATH


def test_v2_settings_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://user:pass@localhost:5432/xhs")
    monkeypatch.setenv("XHS_V2_AUTH_ENABLED", "true")
    monkeypatch.setenv("XHS_V2_AUTH_TOKEN", "secret-token")
    monkeypatch.setenv("XHS_V2_WORKSPACE_HEADER", "X-Test-Workspace")
    monkeypatch.setenv("XHS_V2_USER_HEADER", "X-Test-User")

    settings = Settings(_env_file=None)

    assert settings.POSTGRES_DSN == "postgresql://user:pass@localhost:5432/xhs"
    assert settings.V2_AUTH_ENABLED is True
    assert settings.V2_AUTH_TOKEN == "secret-token"
    assert settings.V2_WORKSPACE_HEADER == "X-Test-Workspace"
    assert settings.V2_USER_HEADER == "X-Test-User"
