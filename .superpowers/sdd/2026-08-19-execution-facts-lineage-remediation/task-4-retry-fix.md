# Task 4 exact publication retry fix

## Scope

Closed the sole Task 4 re-review P1. No Task 5 work was performed.

## Root cause

Report finalization persisted only the generic `report_publication_failed` code/message.
Retry therefore scanned every materializable publication for the workflow and selected the
latest `(created_at, id)`, which could silently recover a different Scope/lineage publication.

## Change

- Materialization failures now carry the exact persisted publication ID back to the workflow
  failure boundary.
- `WorkflowRunManager.fail_run()` durably records that ID on the `run_failed` event.
- Retry reads only the latest `run_failed` event, requires its exact publication ID, resolves
  only that record, verifies workflow ownership and materializable parent lineage, and fails
  closed before reopening finalization if any check fails.
- A repeated materialization failure preserves the same publication ID in the new failure.
- The workflow-wide newest-publication scan was removed.

## Regression

Expanded
`test_failed_materialization_retry_reuses_the_exact_persisted_execution_publication` to create
two different, valid Scope/coverage/execution lineages for one workflow. Publication A is
recorded as the failed materialization; publication B is persisted afterward. The test proves
that retry materializes and exposes only A, retains A's Scope lineage, leaves B without a final
artifact, and durably associates the failure event with A.

TDD red evidence before the implementation:

```text
FAILED ... expected publication A, got newer publication B
1 failed
```

## Verification

```text
pytest tests/e2e/test_content_research_report_publication_timeline_api.py \
  tests/integration/test_content_research_lite_read_model.py \
  tests/integration/test_content_research_report_read_model.py \
  tests/integration/test_content_research_report_publication_materializer.py::test_materialization_rejects_publication_with_mismatched_lineage_before_artifact_or_message -q
40 passed in 5.67s

pytest tests/integration/test_content_research_report_execution.py \
  tests/integration/test_content_research_report_store.py \
  tests/unit/test_content_research_migrations.py \
  tests/unit/test_workflow_run_manager.py -q
50 passed in 2.80s

ruff check app/content_research/service.py \
  tests/e2e/test_content_research_report_publication_timeline_api.py
All checks passed!

ruff check --ignore UP037,UP045 app/services/workflow_run_manager.py
All checks passed!

git diff --check
clean
```

`UP037`/`UP045` are ignored only for `workflow_run_manager.py` because that existing file has
34 unrelated legacy `Optional`/quoted-annotation findings. The changed failure-event code is
clean under the rest of the configured Ruff rules.
