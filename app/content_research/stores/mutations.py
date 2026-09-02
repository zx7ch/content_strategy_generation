"""Closed Writer command surface for Content Research business records."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from app.core.runtime_write_coordinator import (
    DomainMutationRejectedError,
    MutationApplication,
    MutationIdentityConflictError,
    RuntimeMutationHandler,
    TypedMutation,
)

COORDINATED_STORE_ACTIONS = frozenset(
    {
        "save_brief",
        "delete_workflow",
        "save_plan",
        "save_direction",
        "save_subagent_task",
        "save_trace",
        "append_observation_event",
        "save_scope_draft_with_audit_event",
        "save_scope_contract",
        "save_scope_contract_with_audit_event",
        "save_coverage_snapshot",
        "save_coverage_snapshot_with_audit_event",
        "resolve_coverage_to_execution_unit_atomically",
        "append_execution_fact",
        "claim_execution_unit",
        "renew_execution_unit_lease",
        "execution_context_is_live",
        "recover_interrupted_tasks_atomically",
        "record_provider_request",
        "record_provider_outcome",
        "complete_execution_unit",
        "resolve_coverage_and_authorize_execution_atomically",
        "save_scope_execution_continuation",
        "append_scope_audit_event",
        "save_evidence_record",
        "append_evidence_lineage",
        "save_result_snapshot",
        "append_human_decision",
        "save_run_policy_snapshot",
        "save_sample_policy",
        "save_direction_contract",
        "save_canonical_source",
        "resolve_canonical_source",
        "save_direction_source_projection",
        "save_directional_evidence_packet",
        "save_claim_candidate",
        "save_claim_candidate_with_scope_audit_event",
        "save_claim_admission_decision",
        "save_direction_result_decision",
        "save_weak_signal",
        "save_cross_direction_record",
        "save_aggregate_claim",
        "save_marketing_conclusion_candidate",
        "save_marketing_conclusion_decision",
        "save_stage_checkpoint",
        "save_budget_ledger_entry",
        "save_report_draft",
        "save_report_faithfulness_decision",
        "save_report_publication",
        "append_report_integrity_event",
    }
)


def _record_types() -> dict[str, type[Any]]:
    from app.content_research import (
        analysis_persistence,
        contracts,
        models,
        persistence_models,
        runtime,
        scope_contract,
    )
    from app.content_research.evidence import models as evidence_models
    from app.content_research.stores import sqlite_store

    return {
        value.__name__: value
        for module in (
            analysis_persistence,
            contracts,
            evidence_models,
            models,
            persistence_models,
            runtime,
            scope_contract,
            sqlite_store,
        )
        for value in vars(module).values()
        if isinstance(value, type)
        and is_dataclass(value)
        and value.__module__.startswith("app.content_research")
    }


def encode_store_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "$record": type(value).__name__,
            "fields": {
                field.name: encode_store_value(getattr(value, field.name))
                for field in fields(value)
            },
        }
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return {"$tuple": [encode_store_value(item) for item in value]}
    if isinstance(value, Mapping):
        return {str(key): encode_store_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [encode_store_value(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"unsupported Content Research mutation value: {type(value).__name__}")


def decode_store_value(value: Any) -> Any:
    if isinstance(value, list):
        return [decode_store_value(item) for item in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {"$datetime"}:
        return datetime.fromisoformat(str(value["$datetime"]))
    if set(value) == {"$tuple"}:
        return tuple(decode_store_value(item) for item in value["$tuple"])
    if set(value) == {"$record", "fields"}:
        record_type = _record_types().get(str(value["$record"]))
        record_fields = value["fields"]
        if record_type is None or not isinstance(record_fields, dict):
            raise MutationIdentityConflictError()
        return record_type(
            **{name: decode_store_value(item) for name, item in record_fields.items()}
        )
    return {key: decode_store_value(item) for key, item in value.items()}


class _ContentResearchStoreMutationHandler:
    mutation_kind = "execute_content_research_store_command"

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        from app.content_research.scope_contract import ExecutionLeaseFencedError
        from app.content_research.stores.sqlite_store import (
            SQLiteContentResearchStore,
            _BorrowedSQLiteConnection,
        )

        payload = dict(mutation.domain_payload)
        action = payload.get("action")
        args = payload.get("args")
        kwargs = payload.get("kwargs")
        if (
            not isinstance(action, str)
            or action not in COORDINATED_STORE_ACTIONS
            or not isinstance(args, list)
            or not isinstance(kwargs, dict)
        ):
            raise MutationIdentityConflictError()

        store = object.__new__(SQLiteContentResearchStore)
        store._db_path = ":coordinator:"
        store._execution_context = decode_store_value(payload.get("execution_context"))
        store._dispatch_context = decode_store_value(payload.get("dispatch_context"))
        store._read_transaction_connection = None
        store._writer = None
        store._coordinated_connection = _BorrowedSQLiteConnection(connection)
        previous_row_factory = connection.row_factory
        connection.row_factory = sqlite3.Row
        try:
            method = object.__getattribute__(store, action)
            try:
                result = method(
                    *(decode_store_value(item) for item in args),
                    **{key: decode_store_value(item) for key, item in kwargs.items()},
                )
            except ExecutionLeaseFencedError as exc:
                return MutationApplication(
                    result_contract="content_research_store_result",
                    result_fields={"rejected": "execution_lease_fenced", "message": str(exc)},
                )
            except (KeyError, ValueError, RuntimeError) as exc:
                raise DomainMutationRejectedError(str(exc)) from exc
        finally:
            connection.row_factory = previous_row_factory

        return MutationApplication(
            result_contract="content_research_store_result",
            result_fields={"result": encode_store_value(result)},
            advances_trace_revision=mutation.run_id is not None,
        )


def content_research_store_handlers() -> tuple[RuntimeMutationHandler, ...]:
    return (_ContentResearchStoreMutationHandler(),)
