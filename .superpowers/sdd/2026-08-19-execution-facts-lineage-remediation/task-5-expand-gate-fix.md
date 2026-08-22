# Task 5 Slice 5B Expand delivery-gate fix

## Scope

This fix closes only the P1 in `task-5-expand-gate-review.md` against
`ACC-5-2`. It does not change production lifecycle semantics, historical
Repair, Slice 5C/5D, or Task 4.

## RED and root cause

The previous browser test stopped after the action response and immediate
Scope projection. It passed if the Content Research worker was disabled or
ignored the supplementary query.

The strengthened test first failed while waiting for a durable worker result.
Tracing the persisted failure showed that the offline seed was not a valid
predecessor state: it omitted the initial product-marketing task and the
frozen locked query plan that a supplementary continuation must inherit.

## Fix

- Keep one expensive owned-stack browser case: Expand only.
- Persist a lifecycle-valid, already-completed initial product-marketing task,
  workflow child, confirmed Scope, locked query plan, and unresolved Coverage
  before starting the browser stack.
- Use a deterministic capable source adapter in the backend process and record
  its provider-boundary query to a test-only JSONL file.
- Keep the real Content Research background worker loop. Idle only the
  unrelated generic JobWorker in this gate so the test owns one SQLite worker
  composition surface.
- Reload Creator after worker completion and assert its real Scope projection
  and visible follow-up Coverage state.
- Remove Limited and Relax from this browser parameterization; their backend
  route/integration and frontend payload coverage remains.

## Green evidence

- Expand browser owned-stack path: passed twice (`13.69s`, `12.12s`).
- Persisted continuation contains exactly `夏季 防晒 长袖衬衫` and is
  `completed`.
- Adapter call record and `provider_request_recorded` execution fact contain
  the same query.
- Authorization-owned continuation task is `completed`.
- Attempt 0 is `completed` with provider state `succeeded`.
- Revision-2 Coverage has the original unresolved snapshot as
  `source_coverage_snapshot_id`.
- Browser reload receives that terminal execution/Coverage projection and
  displays the resulting Coverage-decision card.
- Focused backend continuation/route: `2 passed`.
- Frontend test command: `82 passed`.
- TypeScript, Ruff, compileall, and `git diff --check`: passed.

## Systemic-risk decision

No product lifecycle change is required for ACC-5-2. The deterministic source
is test-only. SQLite scheduling between the unrelated generic JobWorker and
the Content Research worker is outside this narrowed gate; this test isolates
that unrelated queue rather than changing either production worker.

## Return point

Request an independent re-review of the single Expand gate. Slice 5C/5D,
legacy no-Scope Repair, and Task 4 remain unchanged.

## Final-review P1 closure

The final review found that the golden path still accepted either a follow-up
Coverage decision or a published report. That branch was too permissive for
this fixture: its confirmed required core object is `长袖衬衫`, while the
deterministic adapter evidence is about `徒步短裤`.

The test now requires all of the fixture-specific outcomes:

- the persisted revision-2 Coverage state is `awaiting_scope_decision`;
- no report publication exists for the workflow;
- reload receives the same follow-up Coverage state and shows the Coverage
  decision card;
- no published report is visible in Creator.

RED was demonstrated by mutating the observed revision-2 state to
`satisfied`; the strict assertion failed at the intended boundary. After
removing the mutation, the exact golden path passed twice (`12.71s`,
`12.75s`); focused Ruff and Python compile checks also passed. No production
or harness behavior changed. The three final-review P2 test-quality debts
remain deferred as instructed.
