# Task 3 re-review remediation

- Scope: all two P1 and two P2 findings from `task-3-rereview.md`.
- Boundary: Task 3 execution ownership, manifest selection, and normal-dispatch fencing only. No Task 4 report/publication lineage fields or UI projection were added.

## Test-first failure evidence

Each production change followed a focused red/green cycle:

1. The async superseded-checkpoint regression failed because `AsyncDirectionalPersistenceSession.save_stage_checkpoint()` accepted a replacement whose immutable ownership changed from `(scope-a, unit-a, 1, 2)` to `(scope-b, unit-b, 7, 9)`.
2. The production-order governed-snapshot regression failed because there was no way to extend the Coverage-owned input manifest with a marketing checkpoint generated after Coverage persistence. The old test's impossible future checkpoint membership was removed.
3. The two normal-dispatch publication regressions failed because `ReportPublicationMaterializer` did not accept `dispatch_context`; consequently neither artifact attachment nor timeline publication could be bound to the dispatch lease.
4. The existing takeover E2E used `datetime.now()` to manufacture expiry. It now uses the fixed persisted instant `2000-01-01T00:00:00+00:00`.

## Remediation

### Production-order governance projection

- Coverage remains the frozen evidence decision made before governance.
- Marketing governance now returns its actually persisted checkpoint.
- Only after that checkpoint exists, `_extend_manifest_with_generated_checkpoints()` verifies its exact workflow/Scope/unit/attempt/revision ownership, durable ID, selected input fingerprint, and terminal status, then extends the publication-time manifest.
- `_publish_report_after_workflow_completion()` accepts only an extension of the exact persisted Coverage manifest: ownership and packet membership must be identical and existing checkpoint membership must be preserved.
- The E2E now proves the real marketing checkpoint is absent from the earlier persisted Coverage manifest yet present in the governed snapshot, with non-empty current-execution marketing conclusions. A same-execution unselected checkpoint and an older execution remain excluded.

### Normal-dispatch artifact and timeline fencing

- `ReportPublicationMaterializer` accepts exactly one of `ExecutionContext` or `DispatchLeaseContext`.
- Artifact attachment selects `DispatchLeaseFencedWorkflowRunManager` on the normal-dispatch path, so the dispatch predicate and `workflow_artifacts` insert share one `BEGIN IMMEDIATE` transaction.
- Timeline publication applies `workflow_dispatch_guard()` on the `ThreadStore` connection before the idempotent Creator message append; guard and append share the same transaction.
- `ContentResearchService` carries the live normal-dispatch context through report creation, artifact materialization, and timeline publication.
- Deterministic integration tests pause after the last artifact read, perform takeover, then release the stale worker. They prove zero stale artifact writes and zero stale timeline writes.

### Immutable async checkpoint ownership

- The async session rejects an in-memory superseded-checkpoint replacement when workflow/Scope/unit/attempt/revision ownership differs.
- The SQL conflict update excludes all ownership columns and updates a superseded row only when every stored ownership value matches the incoming value.
- A concurrent stale-session regression proves a session opened before the checkpoint existed cannot bypass the SQL ownership predicate; the original row is preserved.

## Verification

Focused Task 3 matrix plus the two new materialization takeover cases:

```text
103 passed in 17.88s
```

The matrix covers dispatch worker behavior, authorized-continuation E2E, Scope API E2E, Scope/Coverage/store/governance integration, migrations, governed-snapshot selection, direct and async checkpoint immutability, and post-read artifact/timeline takeover fencing.

```text
ruff check <all modified Task 3 Python production/test files>
All checks passed!  (repository-level deprecated Ruff configuration warning only)

git diff --check
clean
```
