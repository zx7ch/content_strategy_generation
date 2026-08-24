"""Process and browser discovery helpers for browser-driven test suites."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from threading import Thread
from typing import Iterator

import httpx
import pytest


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", 0))
        except PermissionError as exc:  # pragma: no cover - environment specific
            pytest.skip(f"socket bind unavailable in current environment: {exc}")
        return int(sock.getsockname()[1])


def chrome_executable() -> str:
    candidates = [
        os.getenv("PLAYWRIGHT_CHROME_EXECUTABLE", "").strip(),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    pytest.skip("Chrome executable unavailable for browser-driven test")


@contextmanager
def run_process(
    *,
    cmd: list[str],
    cwd: Path,
    env: dict[str, str],
    ready_url: str,
    ready_timeout: float,
    name: str,
) -> Iterator[None]:
    process = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    # A child process whose stdout pipe is never drained eventually blocks in
    # logging. For the Creator E2E stack that can pause the backend while it
    # owns a SQLite transaction and turn a healthy happy path into a 30-second
    # `database is locked` failure. Keep only a bounded diagnostic tail while
    # continuously draining the pipe.
    output: deque[str] = deque(maxlen=400)

    def drain_output() -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            output.append(line)

    output_reader = Thread(target=drain_output, daemon=True)
    output_reader.start()
    try:
        deadline = time.time() + ready_timeout
        while time.time() < deadline:
            if process.poll() is not None:
                output_reader.join(timeout=1)
                raise AssertionError(f"{name} exited before startup:\n{''.join(output)}")
            try:
                response = httpx.get(ready_url, timeout=0.5, follow_redirects=True)
                if response.status_code < 500:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:  # pragma: no cover - startup failure
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            output_reader.join(timeout=1)
            raise AssertionError(f"{name} did not start in time:\n{''.join(output)}")
        yield
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - shutdown failure
                process.kill()
                process.wait(timeout=5)
        output_reader.join(timeout=1)
