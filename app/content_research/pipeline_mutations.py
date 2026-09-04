"""Writer adapter for one directional persistence batch."""

from __future__ import annotations

import asyncio
import sqlite3

from app.content_research.lifecycle.mutations import AsyncSQLiteConnectionFacade
from app.content_research.stores.mutations import decode_store_value
from app.core.runtime_write_coordinator import (
    DomainMutationRejectedError,
    MutationApplication,
    MutationIdentityConflictError,
    RuntimeMutationHandler,
    TypedMutation,
)


class _DirectionalFlushMutationHandler:
    mutation_kind = "flush_content_research_directional_records"

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        from app.content_research.async_pipeline_store import AsyncDirectionalPersistenceSession

        payload = dict(mutation.domain_payload)
        pending = payload.get("pending")
        events = payload.get("events")
        if not isinstance(pending, list) or not isinstance(events, list):
            raise MutationIdentityConflictError()
        session = AsyncDirectionalPersistenceSession(
            ":coordinator:",
            execution_context=decode_store_value(payload.get("execution_context")),
            dispatch_context=decode_store_value(payload.get("dispatch_context")),
        )
        session._pending = [decode_store_value(item) for item in pending]
        session._pending_scope_events = [decode_store_value(item) for item in events]
        session._borrowed_connection = AsyncSQLiteConnectionFacade(connection)
        previous_row_factory = connection.row_factory
        connection.row_factory = sqlite3.Row
        try:
            try:
                asyncio.run(session._flush_once())
            except (KeyError, ValueError, RuntimeError) as exc:
                raise DomainMutationRejectedError(str(exc)) from exc
        finally:
            connection.row_factory = previous_row_factory
        return MutationApplication(
            result_contract="content_research_directional_flush_result",
            result_fields={
                "record_ids": [getattr(item, "id") for item in session._pending],
                "event_ids": [getattr(item, "id") for item in session._pending_scope_events],
            },
            advances_trace_revision=mutation.run_id is not None,
        )


def content_research_pipeline_handlers() -> tuple[RuntimeMutationHandler, ...]:
    return (_DirectionalFlushMutationHandler(),)
