"""Entry point for the packaged local runtime executable."""
from __future__ import annotations

import os
import sys
import time

# When running as a PyInstaller bundle, set base dir to the executable's location
# so relative paths (data/, chroma/) resolve next to the exe, not in temp dir.
if getattr(sys, "frozen", False):
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
        _append_missing_template_keys(_config_in_data, _config_in_exe)

    # Always load from the stable data-home location
    _load_env_file(_config_in_data)
    # Backward-compat: also load exe-dir .env (dev / legacy installs)
    _load_env_file(_dot_env)

    # ── Resolve data paths ────────────────────────────────────────────────────
    # Bundle templates are never allowed to redirect durable user state back
    # into the extracted application folder.  These paths are an installation
    # boundary, not user-configurable Runtime settings.
    os.environ["SQLITE_DB_PATH"] = os.path.join(_data_home, "xhs_agent.db")
    os.environ["CHROMA_PERSIST_DIR"] = os.path.join(_data_home, "chroma")
    os.environ["CREATOR_THREADS_DB_PATH"] = os.path.join(_data_home, "creator_threads.db")
    os.environ["V2_DISCOVERY_SQLITE_PATH"] = os.path.join(_data_home, "xhs_discovery.db")

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
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host="127.0.0.1",
        port=8000,
        log_level="info",
    )
