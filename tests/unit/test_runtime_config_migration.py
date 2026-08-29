from __future__ import annotations

import importlib.util
import json
import multiprocessing
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _load_runtime_helpers():
    source = Path("runtime_main.py").read_text(encoding="utf-8")
    start = source.index("    def _load_env_file")
    end = source.index("    # ── User data lives outside")
    helper_source = "\n".join(line[4:] for line in source[start:end].splitlines())
    namespace = {"os": os, "sys": sys, "time": __import__("time")}
    exec(helper_source, namespace)
    return namespace


def test_append_missing_template_keys_preserves_existing_values(tmp_path):
    helpers = _load_runtime_helpers()
    user_config = tmp_path / "config.env"
    template_config = tmp_path / "template.env"
    user_config.write_text("ANTHROPIC_API_KEY=real-key\nOLD_SETTING=keep\n", encoding="utf-8")
    template_config.write_text(
        "ANTHROPIC_API_KEY=\nNEW_REQUIRED_SETTING=\nNEW_DEFAULTED_SETTING=true\n",
        encoding="utf-8",
    )

    missing = helpers["_append_missing_template_keys"](str(user_config), str(template_config))

    content = user_config.read_text(encoding="utf-8")
    assert missing == ["NEW_REQUIRED_SETTING", "NEW_DEFAULTED_SETTING"]
    assert "ANTHROPIC_API_KEY=real-key" in content
    assert "OLD_SETTING=keep" in content
    assert "NEW_REQUIRED_SETTING=" in content
    assert "NEW_DEFAULTED_SETTING=true" in content
    assert list(tmp_path.glob("config.env.bak-*"))


def test_append_missing_template_keys_noops_when_config_is_current(tmp_path):
    helpers = _load_runtime_helpers()
    user_config = tmp_path / "config.env"
    template_config = tmp_path / "template.env"
    user_config.write_text("ANTHROPIC_API_KEY=real-key\nNEW_SETTING=custom\n", encoding="utf-8")
    template_config.write_text("ANTHROPIC_API_KEY=\nNEW_SETTING=default\n", encoding="utf-8")

    missing = helpers["_append_missing_template_keys"](str(user_config), str(template_config))

    assert missing == []
    assert user_config.read_text(encoding="utf-8") == "ANTHROPIC_API_KEY=real-key\nNEW_SETTING=custom\n"
    assert not list(tmp_path.glob("config.env.bak-*"))


def test_runtime_template_does_not_define_user_storage_or_credentials():
    template = Path("runtime.config.env").read_text(encoding="utf-8")

    assert "LOG_LEVEL=INFO" in template
    assert "APP_ENV=" not in template
    assert "SQLITE_DB_PATH=" not in template
    assert "CHROMA_PERSIST_DIR=" not in template
    assert "XHS_SPIDER_COOKIES=" not in template
    assert "ANTHROPIC_API_KEY=" not in template
    assert "OPENAI_API_KEY=" not in template


def _run_frozen_runtime(monkeypatch, runtime_folder: Path, home: Path) -> Path:
    """Execute the frozen startup branch without starting the Uvicorn server."""
    executable = runtime_folder / "xhs-runtime"
    executable.touch()
    monkeypatch.setenv("HOME", str(home))
    for key in ("SQLITE_DB_PATH", "CHROMA_PERSIST_DIR", "CREATOR_THREADS_DB_PATH", "V2_DISCOVERY_SQLITE_PATH", "HF_HOME", "RUNTIME_STORAGE_DIAGNOSTICS_JSON"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    original_cwd = Path.cwd()
    try:
        spec = importlib.util.spec_from_file_location(f"runtime_startup_{runtime_folder.name}", "runtime_main.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        os.chdir(original_cwd)
    return home / "Library" / "Application Support" / "xhs-growth-agent"


def test_frozen_runtime_restart_and_upgrade_keep_credentials_outside_bundle(tmp_path, monkeypatch):
    from app.services.llm.configuration import UserLLMConfiguration
    from app.services.llm.configuration_store import SQLiteLLMConfigurationStore
    from app.services.xhs_credentials import XHSCredentialStore

    home = tmp_path / "clean-home"
    install_a = tmp_path / "runtime-a"
    install_b = tmp_path / "runtime-b"
    install_a.mkdir()
    install_b.mkdir()
    for install in (install_a, install_b):
        (install / "config.env").write_text("SQLITE_DB_PATH=./data/leak.db\n", encoding="utf-8")

    data_home = _run_frozen_runtime(monkeypatch, install_a, home)
    assert Path(os.environ["SQLITE_DB_PATH"]).is_relative_to(data_home)
    db_path = os.environ["SQLITE_DB_PATH"]
    now = datetime.now(timezone.utc)
    SQLiteLLMConfigurationStore(db_path).upsert(UserLLMConfiguration(
        workspace_id="local", user_id="local", base_url="https://llm.example", model="test-model",
        api_key="llm-secret", validation_status="validated", validated_at=now,
    ))
    XHSCredentialStore(db_path).replace("a1=xhs-secret; web_session=xhs-secret", "manual_cookie")

    restarted_home = _run_frozen_runtime(monkeypatch, install_b, home)
    assert restarted_home == data_home
    assert SQLiteLLMConfigurationStore(os.environ["SQLITE_DB_PATH"]).get("local", "local") is not None
    assert XHSCredentialStore(os.environ["SQLITE_DB_PATH"]).get_status().authenticated is True
    assert not list(install_a.glob("data/*.db"))
    assert not list(install_b.glob("data/*.db"))


def test_frozen_runtime_always_enables_released_content_research(tmp_path, monkeypatch):
    home = tmp_path / "clean-home"
    install = tmp_path / "runtime"
    install.mkdir()
    (install / "config.env").write_text(
        "F003_LITE_PREVIEW_ENABLED=false\n", encoding="utf-8"
    )

    _run_frozen_runtime(monkeypatch, install, home)

    assert os.environ["F003_LITE_PREVIEW_ENABLED"] == "true"


def test_frozen_runtime_diverts_multiprocessing_helpers_before_startup(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(multiprocessing, "freeze_support", lambda: calls.append("called"))
    home = tmp_path / "clean-home"
    install = tmp_path / "runtime"
    install.mkdir()
    (install / "config.env").write_text("LOG_LEVEL=INFO\n", encoding="utf-8")

    _run_frozen_runtime(monkeypatch, install, home)

    assert calls == ["called"]


def test_frozen_runtime_publishes_safe_resolved_storage_diagnostics(tmp_path, monkeypatch):
    home = tmp_path / "clean-home"
    install = tmp_path / "runtime"
    install.mkdir()
    (install / "config.env").write_text("SQLITE_DB_PATH=./data/leak.db\n", encoding="utf-8")

    data_home = _run_frozen_runtime(monkeypatch, install, home)

    diagnostics = json.loads(os.environ["RUNTIME_STORAGE_DIAGNOSTICS_JSON"])
    assert diagnostics["sqlite_db_path"] == str(data_home / "xhs_agent.db")
    assert diagnostics["sqlite_db_path"].startswith(str(data_home))
    assert diagnostics["build_id"]
    assert "leak.db" not in json.dumps(diagnostics)


def test_frozen_runtime_migrates_legacy_config_to_minimum_runtime_config(tmp_path, monkeypatch):
    home = tmp_path / "clean-home"
    install = tmp_path / "runtime"
    install.mkdir()
    (install / "config.env").write_text(
        Path("runtime.config.env").read_text(encoding="utf-8"), encoding="utf-8"
    )
    data_home = home / "Library" / "Application Support" / "xhs-growth-agent"
    data_home.mkdir(parents=True)
    user_config = data_home / "config.env"
    user_config.write_text(
        "OPENAI_API_KEY=legacy-secret\n"
        "XHS_SPIDER_COOKIES=legacy-cookie\n"
        "SQLITE_DB_PATH=./data/leak.db\n"
        "GENERATION_MAX_RETRIES=9\n",
        encoding="utf-8",
    )

    _run_frozen_runtime(monkeypatch, install, home)

    assert user_config.read_text(encoding="utf-8") == Path("runtime.config.env").read_text(encoding="utf-8")
    backups = list(data_home.glob("config.env.bak-*"))
    assert len(backups) == 1
    backup = backups[0].read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=legacy-secret" in backup
    assert "XHS_SPIDER_COOKIES=legacy-cookie" in backup
