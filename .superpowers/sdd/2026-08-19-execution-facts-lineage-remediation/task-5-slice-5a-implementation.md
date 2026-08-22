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
