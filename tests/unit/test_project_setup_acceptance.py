from __future__ import annotations

from pathlib import Path
import subprocess

from app.config import Settings


ROOT = Path(__file__).resolve().parents[2]


def test_p0_1_gitmodules_configured_for_spider_submodule():
    content = (ROOT / ".gitmodules").read_text(encoding="utf-8")
    assert '[submodule "app/ingest/xhs_spider"]' in content
    assert "path = app/ingest/xhs_spider" in content
    assert "Spider_XHS.git" in content
    # locked to commit by default (no branch tracking)
    assert "branch =" not in content


def test_p0_2_env_example_contains_required_variables():
    content = (ROOT / ".env.example").read_text(encoding="utf-8")
    required = [
        "LLM_PROVIDER=",
        "XHS_SPIDER_COOKIES=",
        "QUALITY_SCORE_THRESHOLD=",
        "RAG_EMBEDDING_MODEL=",
        "REINDEX_MAX_ATTEMPTS=",
        "XHS_SESSION_ALIVE_HOURS=",
        "XHS_JOB_POLL_INTERVAL_MS=",
    ]
    for item in required:
        assert item in content


def test_p0_2_setup_env_script_bootstraps_venv_and_install():
    script = (ROOT / "setup_env.sh").read_text(encoding="utf-8")
    assert "python3 -m venv" in script
    assert "pip install -r" in script


def test_p0_3_gitignore_excludes_runtime_artifacts():
    content = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in ["data/", "__pycache__/", ".env"]:
        assert pattern in content


def test_p0_4_smoke_imports_cover_required_module_groups():
    content = (ROOT / "tests/unit/test_smoke_imports.py").read_text(encoding="utf-8")
    required_markers = [
        "test_import_app_config",
        "test_import_xhs_spider",
        "test_import_rag_service",
        "test_import_engagement_analyzer",
        "test_import_content_strategy_agent",
        "test_import_content_generation_agent",
        "test_import_session_state",
        "test_import_llm_client",
    ]
    for marker in required_markers:
        assert marker in content


def test_p0_5_config_file_loadable_from_example():
    # Pydantic settings should parse the example file without throwing.
    settings = Settings(_env_file=str(ROOT / ".env.example"))
    assert settings.LLM_PROVIDER


def test_p0_setup_scripts_have_valid_shell_syntax():
    subprocess.run(["bash", "-n", str(ROOT / "setup_env.sh")], check=True)
    subprocess.run(["bash", "-n", str(ROOT / "scripts/setup_env.sh")], check=True)
