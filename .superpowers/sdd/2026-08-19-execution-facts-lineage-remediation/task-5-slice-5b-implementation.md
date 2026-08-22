# Task 5 Slice 5B implementation report

## Outcome

ACC-5-2 now has three browser-to-owned-stack journeys for Limited, Expand,
and Relax. The browser uses the real Creator page, real `/scope` projection,
real `/actions` mutation, real SQLite persistence, and the real continuation
worker. Only the pre-existing unresolved confirmed-Scope prerequisite is
seeded before the owned stack starts.

## Behavior proved

- Pending Coverage hides normal Scope confirmation controls.
- The server marks `prepare_scope` and `confirm_scope` unavailable and exposes
  one available `resolve_coverage` command.
- All three server-projected decisions are visible.
- The Coverage fixture places a non-contract ID first in `unmet_constraint_ids`;
  Expand and Relax still submit the exact `core_object` target declared in
  each action's `valid_constraint_ids`.
- Limited sends no constraint or supplementary query fields.
- Expand preserves Scope version 1; Relax observes the successor Scope version
  2; all three post-action projections remove the resolved decision and expose
  the accepted execution unit.

No production code or lifecycle semantics changed. Historical no-Scope Repair
remains deferred as `DEBT-5-2`; Slices 5C and 5D were not modified.

## Harness finding

The first real-stack test created prerequisite data after the backend and
worker were already running. Under composed load, those fixture writes
contended with the owned SQLite stack and produced `database is locked` before
the Coverage UI was exercised. A timing retry would have hidden the ownership
mistake. The final fixture persists the prerequisite before process startup,
then starts the real backend and browser.

## Verification

- Browser-to-owned-stack: 3 passed (`limited`, `expand`, `relax`) in 37.03s.
- Coverage decision identity/concurrency and real worker continuation: 9 passed
  in 3.21s.
- Focused Creator/API tests: 4 passed.
- `ruff check tests/e2e/test_content_research_creator_browser.py`: passed.
- `python3 -m compileall -q tests/e2e/test_content_research_creator_browser.py`:
  passed.
- `git diff --check`: passed.
