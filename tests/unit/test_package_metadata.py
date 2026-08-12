from __future__ import annotations

import subprocess
import sys
from pathlib import Path


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
