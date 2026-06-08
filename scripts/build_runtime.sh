#!/usr/bin/env bash
# Build the XHS Growth Agent local runtime into a standalone executable.
# Usage: ./scripts/build_runtime.sh [--clean]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# ── optional clean ────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--clean" ]]; then
    echo "→ Cleaning previous build artifacts..."
    rm -rf build dist
fi

# ── install package in editable mode ─────────────────────────────────────────
# Required so importlib.metadata can read the version from pyproject.toml
# and PyInstaller's copy_metadata() can include the dist-info in the bundle.
echo "→ Installing package (pip install -e .)..."
pip install -e . --quiet

# ── check pyinstaller ─────────────────────────────────────────────────────────
if ! command -v pyinstaller &>/dev/null; then
    echo "PyInstaller not found. Installing..."
    pip install pyinstaller
fi

# ── pre-download embedding model for local build smoke checks ────────────────
# This warms the builder's HuggingFace cache only. The model is not bundled into
# the release archive; end-user runtimes download/load it into HF_HOME on first use.
echo "→ Pre-downloading embedding model for local cache (BAAI/bge-base-zh-v1.5)..."
python - <<'EOF'
from sentence_transformers import SentenceTransformer
SentenceTransformer("BAAI/bge-base-zh-v1.5")
print("  Model cached.")
EOF

# ── build ─────────────────────────────────────────────────────────────────────
echo "→ Running PyInstaller..."
pyinstaller runtime_main.spec

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
