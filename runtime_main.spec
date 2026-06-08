# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for XHS Growth Agent local runtime.

Build:
    pyinstaller runtime_main.spec

Output: dist/xhs-runtime/  (folder, not single file)
"""

import sys
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

block_cipher = None

# ── collect heavy packages that PyInstaller misses ──────────────────────────
chromadb_datas, chromadb_binaries, chromadb_hiddenimports = collect_all("chromadb")
st_datas, st_binaries, st_hiddenimports = collect_all("sentence_transformers")
tokenizers_datas, tokenizers_binaries, tokenizers_hiddenimports = collect_all("tokenizers")
transformers_datas, transformers_binaries, transformers_hiddenimports = collect_all("transformers")
lg_checkpoint_datas, lg_checkpoint_binaries, lg_checkpoint_hiddenimports = collect_all("langgraph_checkpoint_sqlite")

# ── include package dist-info so importlib.metadata.version() works ─────────
# This is what allows app/config.py to read the version from pyproject.toml
# inside the frozen bundle.
pkg_meta_datas = copy_metadata("xhs-note-generator")

a = Analysis(
    ["runtime_main.py"],
    pathex=["."],
    binaries=chromadb_binaries + st_binaries + tokenizers_binaries + transformers_binaries + lg_checkpoint_binaries,
    datas=(
        chromadb_datas
        + st_datas
        + tokenizers_datas
        + transformers_datas
        + lg_checkpoint_datas
        + pkg_meta_datas
        # include app source so imports resolve correctly
        + [("app", "app"), ("experiments", "experiments")]
    ),
    hiddenimports=(
        chromadb_hiddenimports
        + st_hiddenimports
        + tokenizers_hiddenimports
        + transformers_hiddenimports
        + lg_checkpoint_hiddenimports
        + [
            # uvicorn internals
            "uvicorn.lifespan.on",
            "uvicorn.lifespan.off",
            "uvicorn.protocols.http.auto",
            "uvicorn.protocols.websockets.auto",
            "uvicorn.logging",
            # async SQLite
            "aiosqlite",
            # pydantic
            "pydantic.deprecated.class_validators",
            "pydantic_settings",
            # LLM clients
            "anthropic",
            "openai",
            "httpx",
            # misc
            "structlog",
            "loguru",
            "langgraph",
            "langgraph.checkpoint.sqlite.aio",
            "langchain_core",
            # xhs_spider deps (lazy-loaded at spider runtime)
            "openpyxl",
            "retry",
            "execjs",
            "requests",
        ]
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # exclude test / dev tools to keep size down
        "pytest",
        "black",
        "ruff",
        "mypy",
        "IPython",
        "jupyter",
        "notebook",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="xhs-runtime",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,   # keep console so users can see startup logs / errors
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="xhs-runtime",
)

# ── Copy user-facing files into the dist folder after build ─────────────────
import shutil as _shutil
import os as _os
import sys as _sys

_dist = _os.path.join("dist", "xhs-runtime")

# config.env template — always included on all platforms.
# Use the tracked example file, not a developer's local config.env, so release
# archives never accidentally include local API keys or cookies.
if _os.path.exists(".env.example"):
    _shutil.copy2(".env.example", _os.path.join(_dist, "config.env"))

# macOS launcher
if _sys.platform == "darwin":
    if _os.path.exists("start.command"):
        _shutil.copy2("start.command", _os.path.join(_dist, "start.command"))
