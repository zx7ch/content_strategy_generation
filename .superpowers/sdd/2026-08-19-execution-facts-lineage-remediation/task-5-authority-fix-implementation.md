# Task 5 authority fix implementation

## Outcome

Task 5 now has one backend Scope-authority fence ahead of every legacy repair,
retry, and resume dispatch. Direct persisted-packet repair is fenced at its
public service boundary as well. Creator no longer chooses retry versus resume
from Trace state: it accepts only an exact available action in the server's
`recovery_projection.allowed_actions`.

Persisted Draft confirmation now requires the matching available
`confirm_scope` row from the server projection, including the exact Draft id
and structure hash. Coverage controls continue to select only
server-projected `valid_constraint_ids`, and known replay remains available
only when the latest execution attempt is `retryable_failed`.

## Changed seams

- `ContentResearchService.run_workflow_action` applies the existing
  `_require_scope_execution_authority` guard before legacy repair/retry/resume.
- `ContentResearchService.repair_from_persisted_packets` applies the same guard
  before reading or changing repair state, closing the direct-call bypass.
- `LiteReportReader` projects exact retry/resume/repair actions and empty
  request payloads; legacy `next_action` remains only as compatibility data.
- `projectedRecoveryAction` is the single Creator-side parser for recovery
  authority. Semantic recovery hints without an exact allowed action are not
  executable.
- `ContentResearchRequestEpoch` uses the dedicated `recovery-command` channel;
  newer same-channel responses supersede older ones without invalidating
  independent Scope/report reads.

## Verification

- RED confirmed: repair on unresolved coverage returned report-not-found
  instead of Scope-authority-required; direct repair bypassed the guard;
  recoverable report omitted exact actions; persisted Draft rendered Confirm
  with empty `allowed_actions`.
- Backend focused tests: 5 passed across authority rejection, exact known retry,
  required-constraint resolution metadata, recoverable report projection, and
  report API recovery projection.
- Frontend test suite: 82 passed.
- TypeScript: `tsc --noEmit` passed.
- Python lint: `ruff check` passed.
- Browser subset: 3 passed (unknown outcome, server-declared coverage payload,
  known retry); Draft reload case timed out before `/presearch` response and did
  not reach the changed Scope assertion.

## Explicit exclusions

- No Task 4 report-integrity behavior or integrity recovery was changed.
- Existing browser Task 5 tests mock `/scope` and `/actions`; they are useful UI
  acceptance checks but are not real-stack authority proof. The real API tests
  cover the backend guard and exact known-retry projection. Adding a full
  browser-plus-live-backend authority fixture is deferred rather than widening
  this fix.
- Legacy `next_action: resume_run` remains in the response for compatibility,
  but Creator deliberately ignores it.
