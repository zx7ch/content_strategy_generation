from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_creator_browser_e2e.py"


def test_controlled_browser_runner_persists_log_and_child_exit_code(tmp_path):
    log_path = tmp_path / "creator-browser-e2e.log"
    status_path = tmp_path / "creator-browser-e2e.status.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--log-path",
            str(log_path),
            "--status-path",
            str(status_path),
            "--",
            sys.executable,
            "-c",
            "import sys; print('controlled runner output'); sys.exit(7)",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 7
    assert log_path.read_text(encoding="utf-8") == "controlled runner output\n"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["returncode"] == 7
    assert status["completed_at"]
    assert status["command"][-2:] == ["-c", "import sys; print('controlled runner output'); sys.exit(7)"]
