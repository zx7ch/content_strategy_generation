"""Shared report execution-lineage identity and frozen-snapshot parser."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReportExecutionLineage:
    """One indivisible Scope/Coverage/attempt identity for a published report."""

    scope_contract_id: str
    execution_unit_id: str
    coverage_snapshot_id: str
    attempt_no: int

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                self.scope_contract_id,
                self.execution_unit_id,
                self.coverage_snapshot_id,
            )
        ) or (
            not isinstance(self.attempt_no, int)
            or isinstance(self.attempt_no, bool)
            or self.attempt_no < 0
        ):
            raise ValueError("report execution lineage is invalid")

    @classmethod
    def optional(
        cls,
        *,
        scope_contract_id: str | None,
        execution_unit_id: str | None,
        coverage_snapshot_id: str | None,
        attempt_no: int | None,
    ) -> ReportExecutionLineage | None:
        values = (
            scope_contract_id,
            execution_unit_id,
            coverage_snapshot_id,
            attempt_no,
        )
        if all(value is None for value in values):
            return None
        if any(value is None for value in values):
            raise ValueError("report execution lineage must be complete")
        return cls(
            scope_contract_id=scope_contract_id,
            execution_unit_id=execution_unit_id,
            coverage_snapshot_id=coverage_snapshot_id,
            attempt_no=attempt_no,
        )

    @classmethod
    def from_record(cls, record: Any) -> ReportExecutionLineage | None:
        return cls.optional(
            scope_contract_id=record.scope_contract_id,
            execution_unit_id=record.execution_unit_id,
            coverage_snapshot_id=record.coverage_snapshot_id,
            attempt_no=record.attempt_no,
        )

    @classmethod
    def from_governed_snapshot(
        cls, governed_snapshot: object
    ) -> ReportExecutionLineage | None:
        if not isinstance(governed_snapshot, dict):
            return None
        value = governed_snapshot.get("execution_lineage")
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("frozen report execution lineage is invalid")
        return cls.optional(
            scope_contract_id=value.get("scope_contract_id"),
            execution_unit_id=value.get("execution_unit_id"),
            coverage_snapshot_id=value.get("coverage_snapshot_id"),
            attempt_no=value.get("successful_attempt_no"),
        )

    def as_record_kwargs(self) -> dict[str, str | int]:
        return {
            "scope_contract_id": self.scope_contract_id,
            "execution_unit_id": self.execution_unit_id,
            "coverage_snapshot_id": self.coverage_snapshot_id,
            "attempt_no": self.attempt_no,
        }


def validate_frozen_report_execution_lineage(
    record: Any, governed_snapshot: object
) -> ReportExecutionLineage | None:
    """Require persisted and frozen lineage to be both legacy or exactly equal."""
    record_lineage = ReportExecutionLineage.from_record(record)
    frozen_lineage = ReportExecutionLineage.from_governed_snapshot(governed_snapshot)
    if record_lineage != frozen_lineage:
        raise ValueError("report record and frozen snapshot execution lineage mismatch")
    return record_lineage
