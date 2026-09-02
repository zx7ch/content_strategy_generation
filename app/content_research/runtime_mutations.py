"""Writer adapter for Content Research cost and stage checkpoints."""

from __future__ import annotations

import sqlite3

from app.content_research.stores.mutations import decode_store_value, encode_store_value
from app.core.runtime_write_coordinator import (
    DomainMutationRejectedError,
    MutationApplication,
    MutationIdentityConflictError,
    RuntimeMutationHandler,
    TypedMutation,
)


class _ResearchRuntimeMutationHandler:
    mutation_kind = "execute_content_research_runtime_command"

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        from app.content_research.runtime import CheckpointRuntime, LLMCostLedger
        from app.content_research.stores.sqlite_store import _BorrowedSQLiteConnection

        payload = dict(mutation.domain_payload)
        action = payload.get("action")
        kwargs = payload.get("kwargs")
        if action not in {"record_cost", "checkpoint"} or not isinstance(kwargs, dict):
            raise MutationIdentityConflictError()
        target = LLMCostLedger(":coordinator:") if action == "record_cost" else CheckpointRuntime(":coordinator:")
        target._coordinated_connection = _BorrowedSQLiteConnection(connection)
        previous_row_factory = connection.row_factory
        connection.row_factory = sqlite3.Row
        try:
            try:
                if action == "record_cost":
                    result = target._record(
                        **{key: decode_store_value(value) for key, value in kwargs.items()}
                    )
                else:
                    result = target.checkpoint(
                        **{key: decode_store_value(value) for key, value in kwargs.items()}
                    )
            except ValueError as exc:
                raise DomainMutationRejectedError(str(exc)) from exc
        finally:
            connection.row_factory = previous_row_factory
        return MutationApplication(
            result_contract="content_research_runtime_result",
            result_fields={"result": encode_store_value(result)},
            advances_trace_revision=mutation.run_id is not None,
        )


def content_research_runtime_handlers() -> tuple[RuntimeMutationHandler, ...]:
    return (_ResearchRuntimeMutationHandler(),)
