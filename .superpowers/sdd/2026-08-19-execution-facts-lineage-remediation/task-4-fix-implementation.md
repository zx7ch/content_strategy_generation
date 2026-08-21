# Task 4 review-fix implementation

## Scope

Fixed every Task 4 review finding without starting Task 5. Task 1-3 behavior and the
post-success timeline publication boundary were preserved.

## Correctness fixes

1. **Retry trace ordering**
   - `ExecutionTraceReader` now validates the durable composite ordinal
     `(attempt_no, sequence_no)`.
   - Sequence numbers may restart at 1 for a later attempt without making the trace
     invalid.

2. **Failed publication recovery**
   - A `report_publication_failed` retry now selects and re-materializes the latest
     persisted, materializable publication for the workflow.
   - Recovery no longer re-enters report composition without the original
     authorization/context/manifest, so it cannot create a second null-lineage report.
   - If no persisted publication exists, recovery fails closed instead of publishing a
     lineage-less replacement.

3. **Fail-closed frozen lineage validation**
   - Added the shared `ReportExecutionLineage` value object and one symmetric
     `validate_frozen_report_execution_lineage` comparison.
   - A report is legacy-compatible only when both the persisted record and governed
     snapshot lack lineage.
   - Reader, materializer, SQLite persistence validation, composer, domain contracts,
     and persistence records now use the shared lineage implementation.

4. **Scope-aware publication dedupe acceptance**
   - Added a production publication-path regression using two persisted semantic Scope
     contracts with identical non-empty admitted claim text and evidence.
   - The two Scope executions create two publications.
   - Mutation check: removing `execution_lineage` from the governed input fingerprint
     made the test fail because both calls selected the same publication; restoring the
     lineage key returned it to green.

## TDD evidence

- Retry trace regression first failed with
  `PublishedReportNotFoundError: execution facts are not monotonic` for attempts 0 and 1.
- Publication retry regression first failed by re-entering the authority-gated compose
  path instead of using the persisted publication.
- Frozen-lineage regression first returned a public report after all four persisted
  lineage columns were nulled while the snapshot retained `execution_lineage`.
- The dedupe acceptance test passed against the intended implementation and was then
  mutation-checked as described above.

## Verification

- `pytest tests/e2e/test_content_research_report_publication_timeline_api.py tests/integration/test_content_research_lite_read_model.py -q`
  - `36 passed`
- `pytest tests/integration/test_content_research_report_execution.py tests/integration/test_content_research_report_store.py tests/unit/test_content_research_migrations.py -q`
  - `25 passed`
- `pytest tests/integration/test_content_research_report_read_model.py tests/integration/test_content_research_report_publication_materializer.py::test_materialization_rejects_publication_with_mismatched_lineage_before_artifact_or_message -q`
  - `4 passed`
- Focused `ruff check` over every changed Python file
  - passed (only the repository's existing top-level Ruff-settings deprecation warning)
- `git diff --check`
  - passed
- `python3 -m compileall -q` over the changed production modules
  - passed

An additional full run of
`tests/integration/test_content_research_report_publication_materializer.py` exposed two
unrelated legacy assertions that expect an artifact-result message while the workflow is
still `finalizing_report`. Current production behavior deliberately publishes that
message only after the run commits `succeeded`; the lineage-specific materializer test
passes and those assertions were not changed as part of Task 4.
