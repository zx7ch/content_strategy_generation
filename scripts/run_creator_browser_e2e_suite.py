#!/usr/bin/env python3
"""Run each Creator Browser E2E in a fresh pytest process.

Next.js mutates its generated TypeScript include path at dev-server startup.
Process isolation keeps one test's compiler/runtime state from delaying the next
test while the outer controlled runner still enforces one bounded timeout.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_FILE = "tests/e2e/test_content_research_creator_browser.py"


def main() -> int:
    collection = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", TEST_FILE],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if collection.returncode != 0:
        sys.stdout.write(collection.stdout)
        sys.stderr.write(collection.stderr)
        return collection.returncode
    node_ids = [
        line.strip()
        for line in collection.stdout.splitlines()
        if line.startswith(f"{TEST_FILE}::")
    ]
    if not node_ids:
        print("Creator Browser E2E collection returned no tests", file=sys.stderr)
        return 2
    for index, node_id in enumerate(node_ids, start=1):
        print(f"[creator-browser {index}/{len(node_ids)}] {node_id}", flush=True)
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", node_id],
            cwd=ROOT,
            check=False,
        )
        if completed.returncode != 0:
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
