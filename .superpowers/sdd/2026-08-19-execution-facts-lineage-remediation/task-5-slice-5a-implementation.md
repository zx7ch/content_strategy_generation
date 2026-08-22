# Task 5 Slice 5A Implementation

## Outcome

Slice 5A now has one server-owned answer for legacy recovery. The durable
runtime/report facts select the exact action; the service requires that same
action before any workflow write. A pending Coverage or any Execution Unit
removes legacy recovery, while an eligible historical no-unit run remains
recoverable.

The Creator-facing Coverage resolution result now exposes only the safe
`execution_unit` projection. Persisted legacy authorization and continuation
records remain available to workers and internal tests.

## RED evidence

- A paused historical run projected `resume_formal_research`, but a raw
  `retry_formal_research` request reached deeper retry validation instead of
  failing at mutation authority.
- A real Router response still contained `execution_authorization`.

Both failures were observed before the implementation change.

## Implementation

- Added `workflow_mutation_authority.py` as the shared durable-fact projector
  for legacy Resume, Retry and persisted-packet Repair.
- Centralized eligibility from runtime state, failure checkpoints, report
  reason, tasks and evidence packets.
- Added an ownership preflight so pending Coverage and Execution Units reject
  raw legacy entrypoints before reads that might select a recovery path and
  before all domain writes.
- Kept LiteReader and service mutation validation on the same projected action.
- Removed public `execution_authorization`; internal persisted records and
  worker continuation behavior are unchanged.
- Extended the real browser Draft restore journey to compare the submitted
  Draft ID, structure hash and query payload with the real `/scope` command,
  then prove one Contract was persisted.

## Verification

- `pytest -q tests/e2e/test_content_research_scope_api.py tests/e2e/test_content_research_authorized_continuation_e2e.py tests/integration/test_content_research_lite_read_model.py tests/unit/test_content_research_lite_read_model.py` — 83 passed.
- `pytest -q tests/e2e/test_content_research_creator_browser.py::test_creator_restores_persisted_scope_draft_after_reload` — 1 passed against the owned stack.
- `npm test` — 82 passed.
- `npx tsc --noEmit` — passed.
- Focused `ruff check` and `git diff --check` — passed.

## Scope boundary

No Slice 5B–5D behavior was implemented. The existing Task 4
report-publication finalizing-crash integrity risk remains explicitly deferred
and was not changed by this slice.

## Independent-review remediation

The first independent review found three additional implementation defects.
They are now closed without changing the accepted local single-user contract:

- Scope confirmation checks the workflow's latest projected Draft inside the
  existing `BEGIN IMMEDIATE` transaction. A raw older Draft receives 422 and
  creates no Contract, confirmation, or audit row; the current Draft remains
  confirmable.
- Persisted-packet Repair now runs one pure durable preflight shared by action
  projection, mutation admission, and the replay boundary. It covers the
  brief, frozen policy, terminal successful/partial tasks, direction
  contracts, sample policies, relevance-plan structure, completed selection
  and packet checkpoints, and referenced packet rows.
- Public Coverage POST results and subsequent GET Scope projections omit both
  `execution_authorization` and `execution_authorization_id`; internal
  authorization rows remain unchanged for workers.

### Remediation proof

- Four narrow RED-to-GREEN acceptance tests passed for stale Draft rejection,
  public response filtering, and eligible/ineligible Repair.
- 67 focused Router/SQLite, packet-replay, and Scope-store tests passed (one
  unrelated pre-existing Task 4 integrity test was excluded because its
  isolated fixture lacks the `workflow_runs` table).
- Focused Ruff and `git diff --check` passed.

`DEBT-5-1` remains deliberately out of scope: a future multi-user or external
workflow-action deployment needs a durable mutation claim/lease. No such
parallel actor exists in the current local single-user Creator boundary.
