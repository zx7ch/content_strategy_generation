#!/usr/bin/env python3
"""Run a browser E2E command with durable output and completion evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_status(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary_path = Path(handle.name)
    temporary_path.replace(path)


def run(command: list[str], log_path: Path, status_path: Path) -> int:
    started_at = _timestamp()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        log_path.write_text(f"Unable to start command: {exc}\n", encoding="utf-8")
        _write_status(
            status_path,
            {
                "command": command,
                "started_at": started_at,
                "completed_at": _timestamp(),
                "returncode": 127,
                "launch_error": str(exc),
            },
        )
        return 127

    with log_path.open("w", encoding="utf-8") as log_file:
        assert process.stdout is not None
        for line in process.stdout:
            log_file.write(line)
            log_file.flush()
            sys.stdout.write(line)
            sys.stdout.flush()
    returncode = process.wait()
    _write_status(
        status_path,
        {
            "command": command,
            "started_at": started_at,
            "completed_at": _timestamp(),
            "returncode": returncode,
            "pid": process.pid,
        },
    )
    return returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-path", type=Path, required=True)
    parser.add_argument("--status-path", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args(argv)
    command = arguments.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("a command after -- is required")
    return run(command, arguments.log_path, arguments.status_path)


if __name__ == "__main__":
    raise SystemExit(main())
