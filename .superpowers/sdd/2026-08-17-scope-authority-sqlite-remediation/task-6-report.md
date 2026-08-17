# Task 6 report: coverage resolution and report scope boundaries

## Delivered

- Added the `resolve_coverage` workflow action and a strict request schema that
  consumes the latest persisted Scope Contract version and exactly one of:
  `expand_required_constraint`, `generate_limited_report`, or
  `relax_constraint`.
- `generate_limited_report` persists an explicit `coverage_resolved`
  authorization against the inadequate coverage snapshot without creating or
  mutating a Scope Contract version. Replaying the same command returns the
  existing authorization.
- `expand_required_constraint` requires one or two distinct, user-supplied
  queries that explicitly target the selected unmet required constraint. It
  creates immutable contract v2 with supplementary query groups and records the
  v1-to-v2 decision in the same SQLite transaction. The service never invents,
  rewrites, or auto-broadens an expansion query.
- `relax_constraint` creates immutable contract v2 with exactly the selected
  unmet required constraint changed to `preferred`. Contract v1 remains
  required and unchanged; the v1-to-v2 decision is stored atomically with v2.
- The normal formal-workflow path continues to stop before governance and
  publication while coverage is `awaiting_scope_decision`. It proceeds past
  that boundary only when the exact snapshot has an explicit persisted
  `generate_limited_report` authorization.
- Lite report reads refuse to expose an already-persisted normal publication
  while the latest Scope Contract is awaiting a decision or awaiting collection
  after a versioned expand/relax outcome.
- Authorized limited reports project `report_mode=limited`, final query groups,
  contract version, exact persisted per-constraint counts, unmet constraint IDs,
  and concise constraint limitation records. The summer fixture retains the
  exact `season` / `夏季` limitation, observed counts, and minimum thresholds.
- Successful scoped report projection appends one retry-stable
  `report_scope_projected` audit event. Its mode, selected outcome, version,
  query groups, constraint counts, unmet IDs, and limitations equal the returned
  Lite projection and persisted coverage snapshot.
- Unknown outcomes and stale/non-pending coverage commands are rejected without
  writing a new contract or decision event.
- Existing Trace presentation and frontend files were not changed.

## TDD evidence

### Pending-report and limited-projection RED

The first tests named the production breaks they guard: removing the pending
Scope authorization check, omitting the explicit limited-report command, or
dropping the exact frozen season limitation.

Command run before production changes:

```text
pytest \
  tests/e2e/test_content_research_scope_api.py::test_generate_limited_report_resolution_preserves_v1_and_exact_season_decision \
  tests/integration/test_content_research_lite_read_model.py::test_lite_reader_blocks_published_report_while_scope_resolution_is_pending \
  tests/integration/test_content_research_lite_read_model.py::test_limited_lite_report_projects_exact_season_limitation_and_audit -q
```

Observed RED:

```text
FFF
resolve_coverage returned HTTP 422 instead of 200
DID NOT RAISE PublishedReportNotFoundError
KeyError: 'report_mode'
```

After the minimal authorization, guard, and projection implementation, the same
command produced `3 passed`.

### Versioned expand/relax and report-audit RED

The next tests named three additional breaks: synthesizing expansion queries,
mutating frozen v1 during relaxation, and omitting the selected outcome from the
report projection audit.

Command run before those production branches:

```text
pytest \
  tests/e2e/test_content_research_scope_api.py::test_expand_required_constraint_creates_v2_from_only_user_supplied_queries \
  tests/e2e/test_content_research_scope_api.py::test_relax_constraint_creates_v2_and_keeps_v1_required \
  tests/e2e/test_content_research_scope_api.py::test_resolve_coverage_rejects_unknown_outcome_without_writing \
  tests/integration/test_content_research_lite_read_model.py::test_limited_lite_report_projects_exact_season_limitation_and_audit -q
```

Observed RED:

```text
FF.F
expand_required_constraint returned HTTP 422 instead of 200
relax_constraint returned HTTP 422 instead of 200
KeyError: 'resolution' in report_scope_projected payload
```

After implementing the two immutable version transitions and exact projection
outcome, the same command produced `4 passed`.

### Independent-review retry/concurrency RED

The task-level reviewer identified three Important idempotency races. Tests then
simulated a lost HTTP response for both versioned outcomes and a competing
writer inserting the same deterministic limited/report-projection audit event.

Command run before the hardening changes:

```text
pytest \
  tests/e2e/test_content_research_scope_api.py::test_versioned_coverage_resolution_replays_after_lost_response \
  tests/e2e/test_content_research_scope_api.py::test_limited_resolution_reconciles_a_concurrent_duplicate \
  tests/integration/test_content_research_lite_read_model.py::test_lite_report_read_reconciles_concurrent_projection_event -q
```

Observed RED:

```text
FFFF
both expand/relax replays returned HTTP 422 instead of 200
concurrent limited authorization returned HTTP 422 instead of 200
concurrent report projection raised duplicate Scope audit event ValueError
```

The hardened command path now returns the exact persisted version/event on
replay and reconciles deterministic duplicate inserts by re-reading the matching
persisted event. The same command then produced `4 passed`.

The independent reviewer re-ran those four cases and approved the fixes with no
remaining Blocker or Important findings; scoped `git diff --check` was clean.

## Verification

Task-focused command from the implementation plan:

```text
pytest tests/e2e/test_content_research_scope_api.py \
  tests/integration/test_content_research_lite_read_model.py \
  tests/unit/test_content_research_report_composer.py -q
```

Result: `46 passed in 3.79s`.

Focused plus adjacent Scope persistence/coverage regressions:

```text
pytest tests/integration/test_content_research_scope_contract_store.py \
  tests/integration/test_content_research_scope_coverage.py \
  tests/e2e/test_content_research_scope_api.py \
  tests/integration/test_content_research_lite_read_model.py \
  tests/unit/test_content_research_report_composer.py -q
```

Initial result: `61 passed in 3.77s`.

After independent-review idempotency hardening, the same regression set produced
`65 passed in 4.06s`.

Static verification:

```text
ruff check app/content_research/service.py \
  app/content_research/reporting/lite_read_model.py \
  app/content_research/api_schemas.py \
  tests/integration/test_content_research_lite_read_model.py \
  tests/e2e/test_content_research_scope_api.py
git diff --check
```

Result: Ruff reported `All checks passed!` (with the repository's existing
top-level-linter configuration deprecation warning); `git diff --check` exited
0.

## Remaining concerns

- Expansion persists the user-authorized supplementary contract version, but
  orchestration of a new supplementary collection pass remains owned by the
  existing formal-research retry/dispatch lifecycle rather than this command.
- Older formal-workflow E2E fixtures that bypass mandatory Scope confirmation
  remain outside this task's allowed file scope, as recorded in Task 5.
