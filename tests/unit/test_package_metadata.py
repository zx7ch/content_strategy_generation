from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RUNTIME_IMPORT_DISTRIBUTIONS = {
    "aiosqlite": "aiosqlite",
    "anthropic": "anthropic",
    "chromadb": "chromadb",
    "fastapi": "fastapi",
    "httpx": "httpx",
    "langgraph": "langgraph",
    "numpy": "numpy",
    "openai": "openai",
    "psycopg": "psycopg",
    "pydantic": "pydantic",
    "pydantic_settings": "pydantic-settings",
    "qrcode": "qrcode",
    "sentence_transformers": "sentence-transformers",
    "structlog": "structlog",
}

ROOT = Path(__file__).resolve().parents[2]


def test_editable_install_exposes_runtime_distribution_metadata(tmp_path):
    environment = tmp_path / "package-metadata-venv"
    subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True)
    python = environment / "bin" / "python"

    installed = subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-deps",
            "-e",
            str(ROOT),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert installed.returncode == 0, installed.stdout + installed.stderr
    version = subprocess.run(
        [
            str(python),
            "-c",
            "import importlib.metadata as metadata; print(metadata.version('xhs-note-generator'))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert version.stdout.strip() == "2.0.0"


def test_direct_runtime_imports_are_declared_in_project_dependencies():
    lines = (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines()
    dependencies = []
    in_dependencies = False
    for line in lines:
        if line == "dependencies = [":
            in_dependencies = True
            continue
        if in_dependencies and line == "]":
            break
        if in_dependencies:
            dependencies.append(line.strip().strip(",").strip('"'))
    declared_names = {
        dependency.split("[", 1)[0].split("=", 1)[0].split(">", 1)[0].lower()
        for dependency in dependencies
    }

    missing = sorted(
        distribution
        for distribution in RUNTIME_IMPORT_DISTRIBUTIONS.values()
        if distribution not in declared_names
    )

    assert missing == []
