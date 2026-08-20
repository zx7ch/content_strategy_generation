# Task 3 review remediation implementation report

- Base: `b730172` (`feat(content-research): bind evidence to coverage execution`)
- Review: `.superpowers/sdd/2026-08-19-execution-facts-lineage-remediation/task-3-review.md`
- Scope: all three P1 and all five P2 findings from the Task 3 review, plus the approved pending-Coverage UI/backend projection decision.
- Boundary: no Task 4 report/publication lineage fields or frontend implementation were added. The existing report builder only receives the exact persisted Coverage manifest needed to correct Task 3 input selection.

## RED evidence

The remediation was driven by focused regressions before production changes:

1. An unresolved `Coverage` still allowed `prepare_scope`/`confirm_scope`; an older authorization was rejected when legacy data contained a later Scope.
2. The governed snapshot admitted old-execution claims, governance, marketing conclusions, and checkpoints. A final mutation test added a same-execution checkpoint omitted from `checkpoint_ids`; it displaced the manifest-selected marketing checkpoint until exact checkpoint membership was enforced.
3. Normal-dispatch recovery allowed a takeover between its lease read and task writes, and a late real provider callback could persist after takeover.
4. A checkpoint retry could rewrite the checkpoint's Scope/unit/attempt/revision tuple.
5. There was no pre-`0029` fixture proving historical unowned evidence remains excluded from a new manifest.

The direct service/private-helper tests previously placed under E2E were removed or replaced. Manifest/governance details now live in unit/integration coverage; the E2E regression uses the real HTTP action, dispatch worker, task router, provider adapter, persistence pipeline, and takeover path.

## Implementation

### Exclusive pending-Coverage decision

- Added one store predicate for the workflow's unresolved `awaiting_scope_decision` Coverage: a pending snapshot with no authorization that resolves it.
- `prepare_scope` and `confirm_scope` reject with `coverage_decision_required` at the service boundary. Their store transactions repeat the predicate under `BEGIN IMMEDIATE`, so concurrent callers cannot create a draft or contract after the decision becomes pending.
- `resolve_coverage(relax_constraint)` remains the only path that atomically creates the semantic successor Scope and its execution authorization.
- Explicit execution authority now resolves its own persisted Scope and source Coverage instead of blindly selecting `contracts[-1]`; a legacy later Scope can no longer strand an older valid authorization.
- The scope projection marks ordinary prepare/confirm unavailable with `coverage_decision_required`, names `resolve_coverage` as recovery, and returns the allowed resolution actions in `decision_recovery` for the UI.

### Manifest-governed snapshot inputs

- Added a frozen `ExecutionOwnership` matcher and made `CoverageManifest` extend it.
- `CoverageManifest.owns()` enforces exact Scope/unit/attempt/revision plus packet membership for packets/candidates and exact checkpoint membership for checkpoints.
- Governed snapshot construction now filters claim candidates/admissions, cross-direction records, aggregate claims, direction-view claim IDs, marketing checkpoints/conclusions, and checkpoint summaries through the selected Coverage manifest.
- Publication selects the exact Coverage for initial collection, authorized continuation, or limited-report authorization and passes its manifest through governed snapshot creation.
- Repeated ownership predicates in the pipeline, cross-direction governance, admission readers, and marketing governance were consolidated behind the typed matcher/store seam.

### Atomic normal-dispatch fencing and recovery

- Added immutable `DispatchLeaseContext` and dispatch-scoped store, async pipeline, and workflow-manager views.
- Each normal-dispatch domain write starts a write transaction and verifies the exact running owner/token/unexpired lease in that transaction.
- Provider discovery/detail/comment callbacks check the live dispatch context immediately before the external operation; any late result is rejected by the transaction-fenced persistence views.
- Interrupted-task recovery now validates the lease and rewrites every affected task in one `BEGIN IMMEDIATE` transaction. Tasks with a durable running provider-operation checkpoint become non-replayable `outcome_unknown`; safe pre-operation tasks return to `queued`.
- Worker time is injectable. The concurrency regression uses a fixed timestamp and a blocking SQLite trigger to prove takeover waits behind the complete recovery transaction; a stale repeat performs zero writes.
- The real-worker E2E pauses a real adapter callback, forces lease takeover with fixed persisted time, releases the old callback, and proves no late task, observation, checkpoint, Coverage, governance, report, workflow-event, or artifact mutation.

### Immutable ownership and migration coverage

- Stage-checkpoint upserts preserve their original execution ownership columns and reject any attempted reassignment while still permitting status retirement/retry updates.
- The `0029` regression seeds packet/candidate/checkpoint/Coverage data, removes the migration and lineage columns/indexes to simulate the legacy schema, reapplies migrations, and proves the historical records remain unowned and fail a new manifest's ownership validation.

## Verification

```sh
pytest tests/unit/test_content_research_dispatch_worker.py \
  tests/e2e/test_content_research_authorized_continuation_e2e.py \
  tests/e2e/test_content_research_scope_api.py \
  tests/integration/test_content_research_scope_coverage.py \
  tests/integration/test_content_research_scope_contract_store.py \
  tests/integration/test_content_research_cross_direction_governance.py \
  tests/unit/test_content_research_cross_direction_governance.py \
  tests/unit/test_content_research_migrations.py \
  tests/unit/test_content_research_governed_completion.py::test_governed_snapshot_partitions_admitted_claims_and_weak_signals \
  tests/unit/test_content_research_governed_completion.py::test_governed_snapshot_uses_only_manifest_owned_claims_governance_and_checkpoints \
  tests/integration/test_content_research_contract_store.py::test_stage_checkpoint_status_can_be_retired_without_creating_a_second_row \
  tests/integration/test_content_research_contract_store.py::test_stage_checkpoint_retry_cannot_change_immutable_execution_ownership -q
# 99 passed in 11.33s

ruff check <all modified Task 3 Python production and test files>
# All checks passed (repository-level deprecated Ruff configuration warning only)

git diff --check
# clean
```

An exploratory run of the complete governed-completion and contract-store modules reached `103 passed, 4 failed`. The four failures are pre-existing stale tests: three call `_execute_formal_research` without the now-required confirmed Scope, and one expects migrations to stop at `0020` although the repository is already at `0029`. None exercises code introduced by this remediation, and the focused replacements above are green.
