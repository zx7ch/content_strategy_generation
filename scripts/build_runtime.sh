#!/usr/bin/env bash
# Build the XHS Growth Agent local runtime into a standalone executable.
# Usage: ./scripts/build_runtime.sh [--clean]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Keep packaging tools tied to one interpreter.  A clean login shell may expose
# python3 without bare pip/pyinstaller commands even when the project venv has
# the required build dependencies.
if [[ -n "${PYTHON_BIN:-}" ]]; then
    PYTHON="$PYTHON_BIN"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
    PYTHON="$ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
else
    echo "Python 3 is required to build the runtime." >&2
    exit 1
fi

# A user-facing archive must identify one committed source tree exactly.  A
# dirty build can silently combine one branch with uncommitted files from
# another branch, which makes the resulting ZIP impossible to reproduce.
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "A Git checkout is required to build a release Runtime." >&2
    exit 1
fi
if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
    echo "Refusing to build a release Runtime from a dirty worktree:" >&2
    git status --short >&2
    exit 1
fi
BUILD_GIT_COMMIT="$(git rev-parse HEAD)"
BUILD_VERSION="$($PYTHON -c 'import pathlib, tomllib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"])')"

# ── optional clean ────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--clean" ]]; then
    echo "→ Cleaning previous build artifacts..."
    rm -rf build dist
fi

# ── install package in editable mode ─────────────────────────────────────────
# Required so importlib.metadata can read the version from pyproject.toml
# and PyInstaller's copy_metadata() can include the dist-info in the bundle.
echo "→ Installing package ($PYTHON -m pip install --no-build-isolation -e .)..."
"$PYTHON" -m pip install --no-build-isolation -e . --quiet

# ── check pyinstaller ─────────────────────────────────────────────────────────
if ! "$PYTHON" -c "import PyInstaller" >/dev/null 2>&1; then
    echo "PyInstaller not found. Installing..."
    "$PYTHON" -m pip install pyinstaller
fi

# ── pre-download embedding model for local build smoke checks ────────────────
# This warms the builder's HuggingFace cache only. The model is not bundled into
# the release archive; end-user runtimes download/load it into HF_HOME on first use.
echo "→ Pre-downloading embedding model for local cache (BAAI/bge-base-zh-v1.5)..."
"$PYTHON" - <<'EOF'
from sentence_transformers import SentenceTransformer
SentenceTransformer("BAAI/bge-base-zh-v1.5")
print("  Model cached.")
EOF

# ── build ─────────────────────────────────────────────────────────────────────
echo "→ Running PyInstaller..."
"$PYTHON" -m PyInstaller --noconfirm runtime_main.spec

# Record the exact clean source identity inside the folder that will be zipped.
# The artifact gate compares selected packaged sources byte-for-byte with this
# commit, so a concurrent branch switch or post-check source mutation fails the
# release instead of producing another mixed archive.
BUILD_PLATFORM="$(uname -s)"
BUILD_ARCHITECTURE="$(uname -m)"
BUILD_TIMESTAMP="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
export BUILD_GIT_COMMIT BUILD_VERSION BUILD_PLATFORM BUILD_ARCHITECTURE BUILD_TIMESTAMP
"$PYTHON" - <<'PY'
from __future__ import annotations

import json
import os
from pathlib import Path

payload = {
    "schema_version": "xhs_runtime_build_info_v1",
    "git_commit": os.environ["BUILD_GIT_COMMIT"],
    "git_dirty": False,
    "version": os.environ["BUILD_VERSION"],
    "platform": os.environ["BUILD_PLATFORM"],
    "architecture": os.environ["BUILD_ARCHITECTURE"],
    "built_at": os.environ["BUILD_TIMESTAMP"],
}
target = Path("dist/xhs-runtime/build-info.json")
target.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
PY

# ── zip for distribution ──────────────────────────────────────────────────────
echo "→ Packaging into xhs-runtime.zip..."
cd dist
# Remove old zip if exists
rm -f xhs-runtime.zip
zip -r --symlinks xhs-runtime.zip xhs-runtime/
cd "$ROOT"

echo ""
echo "✓ Build complete."
echo "  Executable:  dist/xhs-runtime/xhs-runtime"
echo "  Distributable: dist/xhs-runtime.zip"
echo ""
echo "  Send dist/xhs-runtime.zip to users directly."
echo "  User data (SQLite, Chroma) lives in ~/Library/Application Support/xhs-growth-agent/"
echo "  and is NOT affected by rebuilds."
