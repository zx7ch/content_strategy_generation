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

# ── optional clean ────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--clean" ]]; then
    echo "→ Cleaning previous build artifacts..."
    rm -rf build dist
fi

# ── install package in editable mode ─────────────────────────────────────────
# Required so importlib.metadata can read the version from pyproject.toml
# and PyInstaller's copy_metadata() can include the dist-info in the bundle.
echo "→ Installing package ($PYTHON -m pip install -e .)..."
"$PYTHON" -m pip install -e . --quiet

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
