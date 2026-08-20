# Task 3 implementation report

- Base: `04c5d11` (`test(content-research): type continuation test doubles`)
- Scope: Task 3 only — workflow-wide initial eligibility, execution-owned evidence, explicit Coverage manifests, manifest-scoped governance, and stale normal-dispatch recovery fencing.
- Task 4 report/publication lineage and UI projection work were not started.

## RED

The Task 3 E2E regressions were added and observed failing for the intended missing behavior:

1. A later confirmed Scope reopened initial execution after an older unresolved Coverage snapshot. The expected workflow-lineage rejection was absent.
2. Formal packet/checkpoint records did not accept immutable Scope/unit/attempt/revision ownership, so mixed-evidence Coverage could not express an exact execution manifest.
3. Cross-direction governance did not accept a Coverage manifest and therefore read all workflow claims.
4. Normal dispatch recovery did not accept or validate the claimed job owner/token before rewriting interrupted task state.

The negative manifest-persistence assertion was also mutation-checked: removing the store-side ownership validation changed the failure from `evidence ownership mismatch` to a later uniqueness error, proving the regression exercises the validation seam.

## Implementation

- Added `initial_execution_eligibility(workflow_run_id, scope_contract_id)` at the store seam. Initial execution is allowed only before the workflow has formal Coverage, authorization/unit/attempt, governed snapshot/governance, or report artifacts. A newly confirmed Scope cannot reset an unresolved prior decision; execution now requires the existing explicit authorization path.
- Added immutable `scope_contract_id`, `execution_unit_id`, `attempt_no`, and `execution_revision` ownership to directional packets, stage checkpoints, and claim candidates. Migration `0029` adds the columns and lineage indexes without altering Task 1 execution-decision identity.
- Added frozen `CoverageManifest` membership and persisted it with Coverage. Coverage construction filters packet/checkpoint inputs by exact workflow, Scope, unit, successful attempt, and execution revision. Store-side persistence rejects mismatched authorization, source snapshot, Scope, revision, attempt state, packets, or checkpoints.
- Included execution ownership in continuation packet/checkpoint/claim identities so a retry attempt cannot silently reuse another attempt's formal records.
- Passed the persisted Coverage manifest into cross-direction and marketing governance. Both consume only claims and packets belonging to the manifest; governance checkpoints carry the same ownership.
- Moved normal dispatch recovery behind an exact live job owner/token/expiry check. A stale recovery returns without task, provider, checkpoint, Coverage, governance, or report writes.
- Updated migration fixtures that intentionally seed legacy Coverage rows to name the legacy columns explicitly, keeping Task 1 identity/backfill tests compatible with the append-only `0029` columns.

## Verification

```sh
pytest tests/e2e/test_content_research_scope_api.py tests/e2e/test_content_research_authorized_continuation_e2e.py -q
# 41 passed

pytest tests/unit/test_content_research_dispatch_worker.py tests/integration/test_content_research_scope_coverage.py tests/integration/test_content_research_scope_contract_store.py tests/integration/test_content_research_cross_direction_governance.py tests/unit/test_content_research_cross_direction_governance.py tests/unit/test_content_research_migrations.py -q
# 52 passed

# The two focused groups were also rerun together immediately before commit.
# 93 passed in 74.16s

ruff check <all Task 3 Python production/test files>
# All checks passed (repository-level deprecated Ruff configuration warning only)

git diff --check
# clean
```

The broader `pytest tests/unit tests/integration -q -k content_research` audit produced `470 passed, 20 failed, 630 deselected`. Those failures are outside Task 3's focused suites and cover already-stale contracts such as the pre-Scope action list, missing required Scope setup in direct formal-execution tests, old migration-version expectations, and report materialization behavior. No Task 3 completion claim relies on that broader non-green suite.

## Files

- Execution ownership and manifests: `persistence_models.py`, `scope_contract.py`, `migrations.py`, `stores/base.py`, `stores/sqlite_store.py`.
- Lineage-aware execution: `workflow/directional_pipeline.py`, `admission/candidates.py`, `admission/cross_direction.py`, `service.py`, `worker.py`.
- Regressions: `test_content_research_scope_api.py`, `test_content_research_authorized_continuation_e2e.py`, plus named-column compatibility updates in `test_content_research_migrations.py`.
