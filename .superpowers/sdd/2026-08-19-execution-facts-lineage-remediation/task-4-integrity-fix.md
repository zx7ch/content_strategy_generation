# Task 4 publication-integrity fix

## Scope

Implemented the approved report-publication integrity design and closed the two final
Task 4 P1 lifecycle findings. No Task 5 UI work was performed.

## Root causes and changes

1. **Crash-durable exact retry command**
   - `retry_failed_report_finalization` now verifies the requested publication against
     the runtime's latest durable `run_failed` event inside the same transaction.
   - The transition to `finalizing_report` and
     `run_report_publication_retry_started(publication_id=...)` are one atomic commit.
   - A restart in `finalizing_report` resumes only that event's exact publication ID;
     it never falls back to a workflow-global latest publication.

2. **Truthful attempt outcome for publication-only failure**
   - Report materialization failures retain their typed publication-boundary exception.
   - The worker records the research execution attempt as `completed` while the
     publication continuation remains failed/retryable. A publication persistence side
     effect no longer rewrites successful research as a failed attempt.

3. **Append-only post-publication integrity state**
   - Migration `0031` adds append-only report-integrity events.
   - When an already readable report's frozen attempt later becomes `failed` or
     `outcome_unknown`, attempt terminalization appends a safe integrity event in the
     same transaction. It does not mutate the publication, draft, decision, snapshot,
     content, Scope, Coverage, or attempt identity.
   - Read models/API expose `integrity_state`, `integrity_reason`, and safe recovery
     guidance. An explicitly selected flagged historical report stays readable.
   - Materialization rejects flagged publications, including a flag committed between
     the initial read and artifact creation; the integrity predicate is rechecked inside
     the artifact write transaction.
   - A separately identified healthy successor (`previous_version_id`) can become the
     default current report while the original remains explicitly readable and flagged.

## TDD evidence

- Atomic retry tests first failed because the runtime method accepted no publication ID.
- The restart regression first failed after the committed `finalizing_report` transition
  because recovery had no active exact retry command.
- The worker regression first failed because a publication-only exception terminalized
  the execution attempt as `failed`.
- The integrity acceptance test first failed because migration `0031`, event persistence,
  read projection, and materialization refusal did not exist.
- The API projection test first failed because Lite responses lacked top-level integrity
  state/reason/recovery fields.
- The migration rollback test first left the new integrity table behind after a later
  DDL statement failed; migration `0031` now executes statement-by-statement inside the
  outer transaction.
- Independent review found two additional races. New red tests proved that the runtime
  accepted a different publication than its failure event and that a concurrent integrity
  flag could race artifact attachment; both are now closed transactionally.

## Acceptance coverage

- SQLite `BEFORE`/`AFTER` failure triggers cover both retry writes: runtime-state update
  and retry-intent event insert. Every injected failure rolls back to the original exact
  failed state.
- A committed retry followed by simulated process exit resumes the exact older failed
  publication even when a newer valid publication exists.
- A readable publication whose frozen attempt later fails remains readable as
  `integrity_flagged`, cannot be re-materialized, and retains its frozen record exactly.
- A healthy successor from a separate successful execution stays distinguishable and is
  selected as current; the flagged original remains available by publication ID.

## Verification

- Focused report/store/migration/runtime/worker matrix:
  `121 passed, 2 deselected`.
- The two deselected full-materializer cases are the pre-existing stale timeline assertions
  documented in the Task 4 final review; they expect an `artifact_result` message before
  workflow success. The current post-success publication boundary is unchanged.
- Focused Ruff check: passed (repository-level deprecated-settings warning only).
- Independent code review after fixes: no remaining Critical/Important findings.
