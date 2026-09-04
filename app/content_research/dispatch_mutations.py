"""Writer adapter for Content Research queue and lease repositories."""

from __future__ import annotations

import asyncio
import sqlite3

from app.content_research.lifecycle.mutations import AsyncSQLiteConnectionFacade
from app.content_research.stores.mutations import decode_store_value, encode_store_value
from app.core.runtime_write_coordinator import (
    DomainMutationRejectedError,
    MutationApplication,
    MutationIdentityConflictError,
    RuntimeMutationHandler,
    TypedMutation,
)

COORDINATED_DISPATCH_ACTIONS = {
    "formal": frozenset({"enqueue", "claim_next", "renew", "complete"}),
    "continuation": frozenset({"claim_next", "renew", "complete"}),
}


class _DispatchMutationHandler:
    mutation_kind = "execute_content_research_dispatch_command"

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        from app.content_research.async_dispatch import (
            AsyncFormalResearchDispatchRepository,
            AsyncScopeExecutionContinuationRepository,
        )

        payload = dict(mutation.domain_payload)
        repository_name = payload.get("repository")
        action = payload.get("action")
        kwargs = payload.get("kwargs")
        allowed = COORDINATED_DISPATCH_ACTIONS.get(str(repository_name))
        if (
            allowed is None
            or not isinstance(action, str)
            or action not in allowed
            or not isinstance(kwargs, dict)
        ):
            raise MutationIdentityConflictError()

        repository_type = (
            AsyncFormalResearchDispatchRepository
            if repository_name == "formal"
            else AsyncScopeExecutionContinuationRepository
        )
        repository = object.__new__(repository_type)
        repository._db_path = ":coordinator:"
        repository._writer = None
        repository._borrowed_connection = AsyncSQLiteConnectionFacade(connection)
        previous_row_factory = connection.row_factory
        connection.row_factory = sqlite3.Row
        try:
            method = object.__getattribute__(repository, action)

            async def invoke():
                return await method(
                    **{key: decode_store_value(value) for key, value in kwargs.items()}
                )

            try:
                result = asyncio.run(invoke())
            except (KeyError, ValueError, RuntimeError) as exc:
                raise DomainMutationRejectedError(str(exc)) from exc
        finally:
            connection.row_factory = previous_row_factory

        return MutationApplication(
            result_contract="content_research_dispatch_result",
            result_fields={"result": encode_store_value(result)},
            advances_trace_revision=mutation.run_id is not None,
        )


def content_research_dispatch_handlers() -> tuple[RuntimeMutationHandler, ...]:
    return (_DispatchMutationHandler(),)
