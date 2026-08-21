# Task 5 — Creator Scope UI implementation

## Outcome

The Creator now treats the persisted Scope projection as the only UI authority for Scope confirmation, Coverage decisions, execution recovery, and report scope presentation.

- A persisted pending Draft is restored from `GET /scope` after reload.
- While Coverage is awaiting a decision, ordinary Scope confirmation is unavailable.
- Expand, limited-report, and relax controls render only when the server declares the action available, and each action uses its own `valid_constraint_ids`.
- Expand exposes supplementary-query inputs; limited report and relax submit one `resolve_coverage` command. Relax is not followed by a separate start command.
- A known `retryable_failed` provider outcome exposes an exact server-owned replay request. `outcome_unknown` and manual-recovery states expose no replay control.
- Scope and resolution responses expose a lease-free execution-unit projection with safe trace summary metadata.
- Request acceptance is guarded by both selected-run epoch and a per-channel request generation, preventing stale responses across run switches and within the same run.
- Report presentation reads query groups only from the published report's frozen Scope.

## TDD evidence

The implementation was driven by focused tests for:

- Draft persistence across Creator reload;
- server-declared Coverage actions and reason rendering;
- supplementary-query expansion and exact action payloads;
- limited-report and atomic relax behavior;
- safe retry versus unknown-outcome manual recovery;
- lease removal from the API client projection;
- cross-run and same-run stale-response suppression;
- frozen report Scope rendering;
- browser journeys for unresolved Coverage, exact one-shot replay with one report, and a delayed old Scope response after switching runs.

The backend projection test also proves that changing the latest provider state from `retryable_failed` to `succeeded` removes replay authority even when the execution unit remains failed/replayable.

## Verification

Focused verification completed during implementation:

- Frontend node tests: 80 passed before final review fixes; the Creator-only suite passed 17/17 after the fixes.
- TypeScript: `npx tsc --noEmit` passed.
- Scope API plus new browser paths: 36 focused tests passed after final fixes.
- Ruff passed for the changed Python files.
- `git diff --check` passed for all Task 5 files.

The broader pre-existing Creator browser file was also run once before the final review fixes: 27 passed and 4 unrelated tests failed. Two failed during presearch/startup timing and two legacy resume tests received the existing `scope_execution_authorization_required` response. Those failures belong to deferred legacy/runtime or Task 4 integrity behavior and were not changed in Task 5.

## Scope boundaries

No Task 4 integrity-hardening behavior was reopened. Backend changes are limited to the safe projection fields needed by the approved Creator contract. Existing unrelated workspace changes and untracked plan/prototype files were left untouched.
