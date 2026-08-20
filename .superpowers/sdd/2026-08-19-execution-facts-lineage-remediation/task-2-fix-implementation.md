# Task 2 P1 remediation implementation report

- Review: `.superpowers/sdd/2026-08-19-execution-facts-lineage-remediation/task-2-review.md`
- Scope: Task 2 P1 findings only; Task 1 execution-decision identity and migrations are unchanged.

## RED

The following focused command was run before implementation:

```sh
pytest tests/unit/test_content_research_dispatch_worker.py::test_live_context_write_serializes_takeover_and_rejects_later_stale_checkpoint tests/e2e/test_content_research_authorized_continuation_e2e.py::test_limited_continuation_replays_then_publishes_through_real_worker tests/e2e/test_content_research_authorized_continuation_e2e.py::test_unknown_provider_outcome_is_durable_and_exact_replay_does_not_call_again tests/e2e/test_content_research_authorized_continuation_e2e.py::test_terminal_provider_failure_is_not_requeued_by_exact_replay tests/e2e/test_content_research_authorized_continuation_e2e.py::test_limited_report_without_owned_publication_remains_recoverable -q
```

Observed five expected failures:

1. `SQLiteContentResearchStore` had no execution-context-bound transaction seam.
2. Limited-report execution wrote no `publication_persisted` ownership fact.
3. the public trace rejected `outcome_unknown` with a Pydantic literal error;
4. terminal provider failure projected `replayable`;
5. a forged durable `succeeded` runtime with no publication completed the unit.

## Implementation

- Added a context-bound SQLite store view. Each connection begins `BEGIN IMMEDIATE`, checks the exact latest attempt/token/expiry/Scope predicate, and then performs the read/write in that same transaction. A stale transaction commits only `lease_fenced` and raises before domain SQL runs.
- Executed continuations through a scoped service whose task, observation, checkpoint, Coverage, governance, governed snapshot, report, and publication stores are all bound to the claim's immutable `ExecutionContext`.
- Added an equivalent guarded workflow transaction manager for continuation-owned run/step/event/artifact transitions. Report materialization and the Creator timeline publication are guarded in their own SQLite write transactions.
- Appended `publication_persisted` in the same transaction as `ReportPublicationRecord`, binding the durable publication to the execution unit/attempt fact stream.
- Replaced the limited-report runtime-status postcondition with a durable unit-owned publication fact, matching `ReportPublicationRecord`, and matching materialized workflow artifact check. Missing publication remains a retryable failed attempt.
- Extended the trace and TypeScript contracts with `outcome_unknown`. Hydrated the latest provider state when reading an execution unit so `terminal_failed` projects `manual_recovery_required` while a local retryable failure remains `replayable`.

## Concurrency and worker regressions

- The SQLite concurrency regression blocks inside a real checkpoint trigger after the live predicate has passed. A competing takeover blocks behind that transaction; after takeover, the old context's next checkpoint write is fenced and creates no row.
- The real worker regression pauses the real adapter after provider intent is durable, expires both leases, lets a second worker take over, then releases the first worker's late callback. Task rows, observations, checkpoints, Coverage, governance, report records, workflow events, and artifacts remain identical to the post-takeover snapshot. No service/store mutation is mocked.

## GREEN and verification

```sh
pytest tests/unit/test_content_research_dispatch_worker.py tests/e2e/test_content_research_authorized_continuation_e2e.py -q
# 21 passed

pytest tests/integration/test_content_research_scope_contract_store.py tests/unit/test_content_research_dispatch_worker.py tests/e2e/test_content_research_scope_api.py tests/e2e/test_content_research_authorized_continuation_e2e.py tests/e2e/test_content_research_report_publication_timeline_api.py tests/integration/test_content_research_lite_read_model.py -q
# 99 passed

cd frontend && npm test -- content-research-api.test.ts
# 73 passed

ruff check app/content_research/api_schemas.py app/content_research/execution_lease.py app/content_research/reporting/publication_materializer.py app/content_research/scope_contract.py app/content_research/service.py app/content_research/stores/sqlite_store.py tests/unit/test_content_research_dispatch_worker.py tests/e2e/test_content_research_authorized_continuation_e2e.py
# All checks passed

git diff --check
# clean
```

No Task 3 lineage fields, migrations, or eligibility behavior were started.
