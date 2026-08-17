# Task 3 report: durable Scope read projection

## Delivered

- Added `GET /content-research/workflows/{workflow_run_id}/scope?version={version}`.
- The projection reads the latest persisted Scope Draft, the requested Scope Contract (or latest confirmed contract), and persisted suggestion/confirmation audit records.
- Added store protocol and SQLite reads for the workflow's latest Scope Draft and that draft's immutable audit records.
- Normalized all persisted `created_at` values to ISO strings using the existing scope payload serializers.
- The response redacts audit payload keys through the existing recursive `safe_public_projection` guard. It does not expose audit metadata, coverage snapshots, candidate matching, or evidence projections.
- Missing workflows and absent requested/latest contracts raise `ContentResearchNotFoundError`, preserving the existing 404 error mapping.

## TDD evidence

Added `test_scope_projection_recovers_persisted_draft_contract_and_audits` before production code. The expected mutation it catches is a missing or API-reconstructed Scope route rather than a read assembled from stored Draft, Contract, and audit facts.

RED command:

```text
pytest tests/e2e/test_content_research_scope_api.py -v
FAILED test_scope_projection_recovers_persisted_draft_contract_and_audits
assert 404 == 200
HTTP Request: GET .../content-research/workflows/run_confirm_1/scope "HTTP/1.1 404 Not Found"
```

The recovery test covers the default latest contract, `?version=1`, and a missing requested version. It asserts the persisted Draft and Contract IDs, ordered persisted audit names, and ISO-string timestamps.

## Verification

```text
pytest tests/e2e/test_content_research_scope_api.py tests/integration/test_content_research_scope_contract_store.py -v
20 passed in 2.36s
```

`git diff --check` exited successfully.

Ruff import ordering was fixed. Full Ruff on the task files still reports two pre-existing `N806` findings in `app/api/routes/router.py:3812-3813` (`_JOB_TYPE_LABEL` and `_JOB_STATUS_LABEL`), unrelated to this Scope route. The project also emits its existing configuration deprecation warning for top-level lint settings.

## Scope boundaries

No candidate-matching, coverage, evidence, or additional projections were added.
