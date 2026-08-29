"""Entry point for the packaged local runtime executable."""
from __future__ import annotations

import json
import os
import sys
import time

# When running as a PyInstaller bundle, set base dir to the executable's location
# so relative paths (data/, chroma/) resolve next to the exe, not in temp dir.
if getattr(sys, "frozen", False):
    # PyInstaller replaces this hook so resource-tracker and spawned worker
    # invocations are diverted before they can execute the Runtime entrypoint.
    # Without it, a helper process recursively starts Uvicorn on port 8000.
    import multiprocessing as _multiprocessing

    _multiprocessing.freeze_support()

    _base = os.path.dirname(sys.executable)
    os.chdir(_base)

    def _load_env_file(path: str) -> None:
        """Load key=value pairs from an env file, skipping already-set vars."""
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip()
                    if key and key not in os.environ:
                        os.environ[key] = value
        except OSError:
            pass

    def _env_keys(path: str) -> set[str]:
        """Read configured env keys from a dotenv-style file."""
        keys: set[str] = set()
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#") or "=" not in stripped:
                        continue
                    key, _, _value = stripped.partition("=")
                    key = key.strip()
                    if key:
                        keys.add(key)
        except OSError:
            pass
        return keys

    def _append_missing_template_keys(user_config: str, template_config: str) -> list[str]:
        """Append new template config keys without changing existing user values."""
        if not os.path.exists(user_config) or not os.path.exists(template_config):
            return []

        user_keys = _env_keys(user_config)
        missing_lines: list[str] = []
        missing_keys: list[str] = []

        try:
            with open(template_config, encoding="utf-8") as template:
                for line in template:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#") or "=" not in stripped:
                        continue
                    key, _, _value = stripped.partition("=")
                    key = key.strip()
                    if key and key not in user_keys:
                        missing_lines.append(line if line.endswith("\n") else line + "\n")
                        missing_keys.append(key)
                        user_keys.add(key)
        except OSError:
            return []

        if not missing_lines:
            return []

        backup_path = f"{user_config}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            import shutil as _shutil

            _shutil.copy2(user_config, backup_path)
            with open(user_config, "a", encoding="utf-8") as f:
                f.write("\n# Added from the latest runtime config template.\n")
                f.writelines(missing_lines)
        except OSError as exc:
            print(
                f"WARNING: Could not update config template keys in {user_config}: {exc}",
                file=sys.stderr,
            )
            return []

        print(
            "Detected new runtime config keys and appended them to your config.env: "
            + ", ".join(missing_keys),
            file=sys.stderr,
        )
        print(f"Config backup created at: {backup_path}", file=sys.stderr)
        print(f"Please review: {user_config}", file=sys.stderr)
        return missing_keys

    def _migrate_runtime_config(user_config: str, template_config: str) -> bool:
        """Replace legacy active config with the minimal supported template."""
        try:
            with open(template_config, encoding="utf-8") as template:
                template_text = template.read()
            with open(user_config, encoding="utf-8") as current:
                current_text = current.read()
        except OSError:
            return False

        if current_text == template_text:
            return False

        backup_path = f"{user_config}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            import shutil as _shutil

            _shutil.copy2(user_config, backup_path)
            with open(user_config, "w", encoding="utf-8") as current:
                current.write(template_text)
        except OSError as exc:
            print(
                f"WARNING: Could not migrate legacy runtime config at {user_config}: {exc}",
                file=sys.stderr,
            )
            return False

        print(
            f"Migrated legacy runtime config to the minimal template. Backup created at: {backup_path}",
            file=sys.stderr,
        )
        return True

    # ── User data lives outside the exe so it survives upgrades ──────────────
    # macOS/Linux: ~/Library/Application Support/xhs-growth-agent/
    _data_home = os.path.join(
        os.path.expanduser("~"), "Library", "Application Support", "xhs-growth-agent"
    )
    os.makedirs(_data_home, exist_ok=True)

    # ── Config file migration ─────────────────────────────────────────────────
    # config.env is stored in the data directory (survives exe updates).
    # On first install the exe directory contains a blank template; we copy it
    # into the data directory so the user edits one stable location forever.
    # Subsequent updates ship a new blank template next to the exe, but since
    # the data-directory config already exists it is never overwritten.
    _config_in_data = os.path.join(_data_home, "config.env")
    _config_in_exe = os.path.join(_base, "config.env")
    _dot_env = os.path.join(_base, ".env")

    if not os.path.exists(_config_in_data):
        # First install: seed from exe-dir template (or create empty file)
        import shutil as _shutil
        if os.path.exists(_config_in_exe):
            _shutil.copy2(_config_in_exe, _config_in_data)
        else:
            open(_config_in_data, "w").close()
    else:
        _migrate_runtime_config(_config_in_data, _config_in_exe)

    # Always load from the stable data-home location
    _load_env_file(_config_in_data)
    # Backward-compat: also load exe-dir .env (dev / legacy installs)
    _load_env_file(_dot_env)

    # Content Research is a released Runtime feature. Older installations may
    # retain the former internal-preview value in their durable config; do not
    # let that stale switch expose an unusable Creator entry after an upgrade.
    os.environ["F003_LITE_PREVIEW_ENABLED"] = "true"

    # ── Resolve data paths ────────────────────────────────────────────────────
    # Bundle templates are never allowed to redirect durable user state back
    # into the extracted application folder.  These paths are an installation
    # boundary, not user-configurable Runtime settings.
    os.environ["SQLITE_DB_PATH"] = os.path.join(_data_home, "xhs_agent.db")
    os.environ["CHROMA_PERSIST_DIR"] = os.path.join(_data_home, "chroma")
    os.environ["CREATOR_THREADS_DB_PATH"] = os.path.join(_data_home, "creator_threads.db")
    os.environ["V2_DISCOVERY_SQLITE_PATH"] = os.path.join(_data_home, "xhs_discovery.db")

    _sqlite_db_path = os.path.abspath(os.environ["SQLITE_DB_PATH"])
    try:
        _sqlite_stat = os.stat(_sqlite_db_path)
        _db_exists = True
        _db_size_bytes = _sqlite_stat.st_size
        _db_modified_at = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(_sqlite_stat.st_mtime)
        )
    except OSError:
        _db_exists = False
        _db_size_bytes = 0
        _db_modified_at = None
    os.environ["RUNTIME_STORAGE_DIAGNOSTICS_JSON"] = json.dumps(
        {
            "build_id": os.environ.get("RUNTIME_BUILD_ID")
            or os.environ.get("RUNTIME_VERSION")
            or "local-runtime",
            "sqlite_db_path": _sqlite_db_path,
            "db_exists": _db_exists,
            "db_size_bytes": _db_size_bytes,
            "db_modified_at": _db_modified_at,
        },
        sort_keys=True,
    )

    # ── Model cache ───────────────────────────────────────────────────────────
    # Redirect ALL HuggingFace downloads (sentence-transformers, transformers,
    # future models) to the user data directory so they:
    #   • survive exe updates (not in the system cache which users may clear)
    #   • are shared across runtime versions (no re-download after update)
    #   • are removed cleanly when the user uninstalls the app
    # Adding a new model in config is enough — HF handles download on first use.
    os.environ["HF_HOME"] = os.path.join(_data_home, "hf_cache")

import uvicorn

if __name__ == "__main__":
    if getattr(sys, "frozen", False):
        from app.core.logging import configure_logging

        _runtime_log = os.path.join(_data_home, "runtime.log")
        configure_logging(log_file=_runtime_log, force=True)
        import logging

        logging.getLogger("xhs_runtime").info(
            "Runtime storage diagnostics: %s",
            os.environ["RUNTIME_STORAGE_DIAGNOSTICS_JSON"],
        )

        def _log_unhandled_exception(exc_type, exc_value, exc_traceback) -> None:
            import logging

            logging.getLogger("xhs_runtime").critical(
                "Unhandled Runtime exception", exc_info=(exc_type, exc_value, exc_traceback)
            )

        sys.excepthook = _log_unhandled_exception
    try:
        _runtime_port = int(os.environ.get("RUNTIME_PORT", "8000"))
    except ValueError:
        _runtime_port = 8000
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host="127.0.0.1",
        port=_runtime_port,
        log_level="info",
    )
