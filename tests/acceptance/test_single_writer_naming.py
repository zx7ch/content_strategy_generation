from __future__ import annotations

import re
from pathlib import Path

_FORBIDDEN_GENERATION_SUFFIX = re.compile(
    r"(?i)(?:^|[^a-z0-9])v[12](?:[^a-z0-9]|$)|_v[12](?:[^a-z0-9]|$)|-v[12](?:[^a-z0-9]|$)"
)


def test_single_writer_refactor_introduces_no_generation_suffix_identifier() -> None:
    repository = Path(__file__).resolve().parents[2]
    refactor_files = (
        repository / "app/core/consistent_snapshot_reader.py",
        repository / "app/core/runtime_write_coordinator.py",
        repository / "app/core/single_writer_migration.py",
        repository / "app/core/sqlite_runtime_lock.py",
        repository / "tests/acceptance/test_runtime_write_coordinator.py",
        repository / "tests/acceptance/test_single_writer_migration.py",
        repository / "tests/acceptance/test_sqlite_single_writer_runtime.py",
        repository / "tests/unit/test_consistent_snapshot_reader.py",
    )

    violations = {
        str(path.relative_to(repository)): match.group(0)
        for path in refactor_files
        for match in _FORBIDDEN_GENERATION_SUFFIX.finditer(
            path.read_text(encoding="utf-8").replace(
                '"V2_DISCOVERY_SQLITE_PATH"',
                '"FROZEN_DISCOVERY_SQLITE_PATH"',
            )
        )
    }

    assert violations == {}
