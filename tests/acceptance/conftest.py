from __future__ import annotations

import json
import importlib.util
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings


def _has_llm_credentials() -> bool:
    provider = settings.LLM_PROVIDER.lower()
    key_map = {
        "anthropic": settings.ANTHROPIC_API_KEY,
        "deepseek": settings.DEEPSEEK_API_KEY,
        "minimax": settings.MINIMAX_API_KEY,
        "kimi": settings.KIMI_API_KEY,
        "openai": settings.OPENAI_API_KEY,
    }
    return bool((key_map.get(provider) or "").strip())


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "acceptance: real dependency acceptance tests")
    config.addinivalue_line("markers", "real_dependency: requires real external services")


@pytest.fixture(scope="session")
def acceptance_enabled() -> None:
    if os.getenv("ACCEPTANCE_RUN_REAL") != "1":
        pytest.skip("set ACCEPTANCE_RUN_REAL=1 to run real dependency acceptance tests")


@pytest.fixture(scope="session")
def acceptance_queries(acceptance_enabled: None) -> dict[str, str]:
    return {
        "primary": os.getenv("ACCEPTANCE_QUERY", "敏感肌护肤"),
        "fallback": os.getenv("ACCEPTANCE_FALLBACK_QUERY", "冷门手帐收纳方法"),
    }


@pytest.fixture(scope="session")
def spider_ready(acceptance_enabled: None) -> None:
    if not settings.XHS_SPIDER_COOKIES.strip():
        pytest.skip("XHS_SPIDER_COOKIES is required for acceptance spider tests")


@pytest.fixture(scope="session")
def llm_ready(acceptance_enabled: None) -> None:
    if not _has_llm_credentials():
        pytest.skip(f"credentials for LLM_PROVIDER={settings.LLM_PROVIDER!r} are required")


@pytest.fixture(scope="session")
def rag_ready(acceptance_enabled: None) -> None:
    if importlib.util.find_spec("sentence_transformers") is None:
        pytest.skip("sentence-transformers is required for acceptance rag tests")


@pytest.fixture
def acceptance_storage(tmp_path, monkeypatch):
    db_path = tmp_path / "acceptance.db"
    chroma_dir = tmp_path / "chroma"
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", str(db_path))
    monkeypatch.setattr(settings, "CHROMA_PERSIST_DIR", str(chroma_dir))
    monkeypatch.setattr(settings, "JOB_POLL_INTERVAL_MS", 50)
    monkeypatch.setattr(settings, "SSE_HEARTBEAT_SECONDS", 1)
    return {"db_path": str(db_path), "chroma_dir": str(chroma_dir), "root": tmp_path}


@pytest.fixture
def acceptance_artifact_dir(tmp_path) -> Path:
    path = tmp_path / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_acceptance_artifact(artifact_dir: Path, name: str, payload: dict[str, Any]) -> Path:
    target = artifact_dir / f"{name}.json"
    enriched = {
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **payload,
    }
    target.write_text(json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
