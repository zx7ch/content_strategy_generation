"""Process and browser discovery helpers for local deterministic E2E tests."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
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
    pytest.skip("Chrome executable unavailable for local browser E2E")


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
    output: list[str] = []
    try:
        deadline = time.time() + ready_timeout
        while time.time() < deadline:
            if process.poll() is not None:
                if process.stdout is not None:
                    output.append(process.stdout.read())
                raise AssertionError(f"{name} exited before startup:\n{''.join(output)}")
            try:
                response = httpx.get(ready_url, timeout=0.5, follow_redirects=True)
                if response.status_code < 500:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:  # pragma: no cover - startup failure
            process.terminate()
            try:
                stdout, _ = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, _ = process.communicate(timeout=5)
            output.append(stdout)
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
