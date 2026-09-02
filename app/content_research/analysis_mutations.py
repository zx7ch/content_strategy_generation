"""Writer command adapter for fenced Content Research analysis persistence."""

from __future__ import annotations

import sqlite3

from app.content_research.stores.mutations import decode_store_value, encode_store_value
from app.core.runtime_write_coordinator import (
    MutationApplication,
    MutationIdentityConflictError,
    RuntimeMutationHandler,
    TypedMutation,
)

COORDINATED_ANALYSIS_ACTIONS = frozenset(
    {
        "freeze_evidence_snapshot",
        "get_or_create_analysis_unit",
        "create_analysis_attempt",
        "save_analysis_job_context",
        "claim_next_analysis_job",
        "recover_expired_analysis_jobs",
        "claim_analysis_attempt",
        "fail_analysis_attempt",
        "succeed_analysis_attempt",
        "succeed_analysis_attempt_with_checkpoint",
        "renew_analysis_attempt",
        "expire_analysis_attempts",
        "complete_analysis_checkpoint",
        "fail_analysis_checkpoint",
        "complete_analysis_track",
    }
)


class _AnalysisMutationHandler:
    mutation_kind = "execute_content_research_analysis_command"

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        from app.content_research.analysis_persistence import (
            AnalysisActiveAttemptConflictError,
            AnalysisIdentityConflictError,
            AnalysisLeaseFencedError,
            SQLiteMarketingAnalysisRepository,
            _BorrowedSQLiteConnection,
        )

        payload = dict(mutation.domain_payload)
        action = payload.get("action")
        args = payload.get("args")
        kwargs = payload.get("kwargs")
        if (
            not isinstance(action, str)
            or action not in COORDINATED_ANALYSIS_ACTIONS
            or not isinstance(args, list)
            or not isinstance(kwargs, dict)
        ):
            raise MutationIdentityConflictError()

        repository = object.__new__(SQLiteMarketingAnalysisRepository)
        repository._db_path = ":coordinator:"
        repository._read_transaction_connection = None
        repository._writer = None
        repository._coordinated_connection = _BorrowedSQLiteConnection(connection)
        previous_row_factory = connection.row_factory
        connection.row_factory = sqlite3.Row
        try:
            method = object.__getattribute__(repository, action)
            try:
                result = method(
                    *(decode_store_value(item) for item in args),
                    **{key: decode_store_value(item) for key, item in kwargs.items()},
                )
            except AnalysisLeaseFencedError as exc:
                return MutationApplication(
                    result_contract="content_research_analysis_result",
                    result_fields={"rejected": "lease_fenced", "message": str(exc)},
                )
            except (
                AnalysisActiveAttemptConflictError,
                AnalysisIdentityConflictError,
                KeyError,
                ValueError,
            ) as exc:
                rejection = (
                    "active_attempt"
                    if isinstance(exc, AnalysisActiveAttemptConflictError)
                    else "identity_conflict"
                    if isinstance(exc, AnalysisIdentityConflictError)
                    else "validation"
                )
                return MutationApplication(
                    result_contract="content_research_analysis_result",
                    result_fields={"rejected": rejection, "message": str(exc)},
                )
        finally:
            connection.row_factory = previous_row_factory

        return MutationApplication(
            result_contract="content_research_analysis_result",
            result_fields={"result": encode_store_value(result)},
            advances_trace_revision=mutation.run_id is not None,
        )


def content_research_analysis_handlers() -> tuple[RuntimeMutationHandler, ...]:
    return (_AnalysisMutationHandler(),)
