#!/usr/bin/env python3
"""Run a browser E2E command with durable output and completion evidence."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
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


def run(
    command: list[str],
    log_path: Path,
    status_path: Path,
    *,
    timeout_seconds: float,
) -> int:
    started_at = _timestamp()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
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

    timed_out = False
    with log_path.open("w", encoding="utf-8") as log_file:
        assert process.stdout is not None
        def stream_output() -> None:
            for line in process.stdout:
                log_file.write(line)
                log_file.flush()
                sys.stdout.write(line)
                sys.stdout.flush()

        reader = threading.Thread(target=stream_output, daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout_seconds
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        if process.poll() is None:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        returncode = process.wait()
        reader.join(timeout=5)
    _write_status(
        status_path,
        {
            "command": command,
            "started_at": started_at,
            "completed_at": _timestamp(),
            "returncode": returncode,
            "pid": process.pid,
            "timed_out": timed_out,
            "timeout_seconds": timeout_seconds,
        },
    )
    return 124 if timed_out else returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-path", type=Path, required=True)
    parser.add_argument("--status-path", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args(argv)
    command = arguments.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("a command after -- is required")
    if arguments.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    return run(
        command,
        arguments.log_path,
        arguments.status_path,
        timeout_seconds=arguments.timeout_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
