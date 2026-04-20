"""Tests for app.config settings (P1-4)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_default_values() -> None:
    settings = Settings(_env_file=None)
    assert settings.LLM_PROVIDER == "anthropic"
    assert settings.XHS_SPIDER_MAX_AUTO_RETRIES == 3
    assert settings.SESSION_ALIVE_HOURS == 24
    assert settings.ALERT_JOB_SUCCESS_RATE_MIN == pytest.approx(0.99)
    assert settings.RAG_EMBEDDING_MODEL == "BAAI/bge-base-zh-v1.5"


def test_settings_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    chroma_dir = tmp_path / "chroma"
    chroma_dir.mkdir(parents=True, exist_ok=True)
    sqlite_parent = tmp_path / "db"
    sqlite_parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("XHS_SPIDER_MAX_RETRIES", "7")
    monkeypatch.setenv("XHS_SESSION_ALIVE_HOURS", "48")
    monkeypatch.setenv("XHS_CHROMA_PERSIST_DIR", str(chroma_dir))
    monkeypatch.setenv("XHS_SQLITE_DB_PATH", str(sqlite_parent / "xhs_agent.db"))

    settings = Settings(_env_file=None)
    assert settings.LLM_PROVIDER == "deepseek"
    assert settings.XHS_SPIDER_MAX_RETRIES == 7
    assert settings.SESSION_ALIVE_HOURS == 48
    assert settings.CHROMA_PERSIST_DIR == str(chroma_dir)


def test_settings_validation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUALITY_SCORE_THRESHOLD", "1.5")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_path_existence_validation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Missing storage paths should be created automatically for local development.
    chroma_dir = tmp_path / "chroma"
    chroma_dir.mkdir(parents=True, exist_ok=True)

    bad_sqlite_path = tmp_path / "missing_parent" / "xhs_agent.db"
    monkeypatch.setenv("CHROMA_PERSIST_DIR", str(chroma_dir))
    monkeypatch.setenv("SQLITE_DB_PATH", str(bad_sqlite_path))

    settings = Settings(_env_file=None)
    assert settings.SQLITE_DB_PATH == str(bad_sqlite_path)
    assert bad_sqlite_path.parent.exists()
