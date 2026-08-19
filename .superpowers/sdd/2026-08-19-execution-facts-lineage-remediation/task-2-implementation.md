# Task 2 implementation report

- Base: `45e6f7c` (`fix(content-research): finalize decision identity trace`)
- Commit: this Task 2 commit (`fix(content-research): fence execution unit attempts`)

## RED

```sh
pytest tests/unit/test_content_research_dispatch_worker.py::test_worker_passes_the_claimed_execution_attempt_to_the_service -q
```

Observed the expected failure: the worker invoked the legacy continuation seam and dropped the claimed execution attempt.

```sh
pytest tests/e2e/test_content_research_authorized_continuation_e2e.py::test_failed_expand_replay_uses_a_new_worker_attempt_and_reaches_coverage tests/e2e/test_content_research_authorized_continuation_e2e.py::test_unknown_provider_outcome_is_durable_and_exact_replay_does_not_call_again -q
```

Observed two expected failures: attempts had no durable provider outcome state, and an interrupted adapter call terminalized the execution unit as ordinary `failed` instead of `outcome_unknown`.

```sh
pytest tests/e2e/test_content_research_authorized_continuation_e2e.py::test_stale_execution_claim_is_fenced_before_any_continuation_artifact -q
```

Observed the expected failure because `ContentResearchService` had no lease-validating `execute_execution_unit` seam.

## GREEN

```sh
pytest tests/unit/test_content_research_dispatch_worker.py tests/e2e/test_content_research_authorized_continuation_e2e.py -q
```

Result: 18 tests passed, including real-worker known retry, unknown outcome, terminal failure, and stale-claim fencing journeys.

```sh
pytest tests/integration/test_content_research_scope_contract_store.py tests/unit/test_content_research_dispatch_worker.py tests/e2e/test_content_research_scope_api.py tests/e2e/test_content_research_authorized_continuation_e2e.py tests/e2e/test_content_research_report_publication_timeline_api.py tests/integration/test_content_research_lite_read_model.py -q
```

Result: 96 tests passed.

```sh
ruff check app/content_research/scope_contract.py app/content_research/stores/base.py app/content_research/stores/sqlite_store.py app/content_research/async_pipeline_store.py app/content_research/workflow/task_router.py app/content_research/workflow/directional_pipeline.py app/content_research/service.py app/content_research/worker.py tests/unit/test_content_research_dispatch_worker.py tests/e2e/test_content_research_authorized_continuation_e2e.py
git diff --check
```

Result: Task 2 files passed Ruff and the diff has no whitespace errors.

## Delivered behavior

- The worker carries the claimed attempt into `execute_execution_unit`; the service constructs the immutable `ExecutionContext` internally and revalidates the exact latest live lease.
- Provider intent is durable before adapter invocation. Outcomes use `succeeded`, `retryable_failed`, `terminal_failed`, or `outcome_unknown`, with execution-unit/attempt correlation in adapter context and facts.
- Async checkpoint/packet flushes and Coverage persistence check the exact live lease in the same SQLite write transaction; rejected writes append `lease_fenced` without creating their domain artifact.
- Exact replay advances the attempt only after a known retryable failure. Terminal failures and unknown outcomes are not requeued.
- Completion occurs only after the operation-specific Coverage or publication postcondition is observed.

## Caveats

- Task 3 evidence/packet ownership columns and lineage-filtered governance queries remain intentionally out of scope. Task 2 carries and fences the context; Task 3 still owns explicit evidence manifests.
- Repository-wide `ruff check app/content_research tests` is not green at this base: it reports 124 pre-existing violations in unrelated files. The exact Task 2 file set is clean.
- The exhaustive Content Research glob is also not a clean baseline: 578 tests passed and 45 unrelated legacy/auth/browser contract tests failed. The plan-defined Task 2 suite and the Task 1–4 focused regression set are green.
