# Content Research Mainline Reliability Vertical Slices Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fragmented Creator Content Research lifecycle with one durable state machine, reliable mainline SQLite transitions, and truthful Trace projection while preserving LLM configuration and Xiaohongshu login.

**Architecture:** Keep the three approved remediation areas as acceptance axes, but implement them as six ordered vertical slices. The first slice establishes the deep lifecycle/coordinator seam through a complete Creator → PreResearch → Brief outcome; every later slice extends that stable seam with one independently observable capability and owns its UI, Router, persistence, worker/provider effects, Trace projection, failure semantics, old-code deletion, and cross-layer proof.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic, sqlite3/aiosqlite, React 18, Next.js 14, TypeScript, pytest, Node test runner, Playwright.

**Spec:** `docs/superpowers/specs/2026-08-23-content-research-mainline-reliability-design.md`

**Supersedes:** `docs/superpowers/plans/2026-08-23-lite-research-final-vertical-slices.md` for all new-Run lifecycle, SQLite coordination, Trace, and old-spec cleanup work.

## Global Constraints

- `workflow_run.content_research_state` plus monotonically increasing `state_revision` is the only business lifecycle authority.
- Frontend state, Scope, Trace, worker recovery, refresh, and restart read the same `RunProjection`; they never infer lifecycle from Brief, Scope, checkpoint, dispatch, or local cache.
- All public mutations use `expected_state`, `expected_revision`, and `command_id`; stale commands have zero business-write and external-effect delta.
- Provider calls and LLM calls occur outside SQLite transactions. Request facts commit before the call; outcome facts commit in a later short transaction.
- SQLite `BUSY/LOCKED` handling, connection configuration, fencing, retry budget, error classification, and reconciliation live behind `ContentResearchPersistenceCoordinator`.
- B/C remain optional; only A is a candidate admission condition. Exact frozen `final_query` values are the only retrieval authority.
- Old new-Run behavior, public types, UI, fixtures, and tests are deleted in the first slice that replaces them. Do not use `skip`, `xfail`, compatibility branches, or weakened assertions.
- Historical v1 persistence is read-only. Its decoder cannot authorize a new mutation, dispatch, retry, or publication.
- Every slice preserves validated workspace-scoped LLM configuration and Xiaohongshu QR/Cookie login, with keys and cookies redacted.
- Intercepted frontend tests prove rendering and stale-response handling only. Every slice also owns at least one Creator-to-owned-stack test with real Router and SQLite.

## Independence Rule

Literal zero dependency is impossible because there is intentionally one state machine. The required independence is checkpoint independence:

1. each slice begins from the last released public contract;
2. it does not expose a state that needs a later slice to become truthful or recoverable;
3. it owns the first composed proof of every layer it makes reachable;
4. reverting a later slice does not invalidate an earlier slice;
5. no separate “backend”, “frontend”, “SQLite”, “Trace cleanup”, or “final E2E” task may complete an earlier observable behavior.

Slices are implemented sequentially. Parallel implementation against the same lifecycle files is prohibited.

## Stable Interfaces Established by Slice 1

Create these focused modules and keep their public names stable through all slices:

```python
class ContentResearchState(str, Enum):
    PRESEARCH_RUNNING = "presearch_running"
    BRIEF_CONFIRMATION_REQUIRED = "brief_confirmation_required"
    SCOPE_CONFIRMATION_REQUIRED = "scope_confirmation_required"
    RETRIEVAL_QUEUED = "retrieval_queued"
    RETRIEVAL_RUNNING = "retrieval_running"
    COVERAGE_EVALUATING = "coverage_evaluating"
    COVERAGE_DECISION_REQUIRED = "coverage_decision_required"
    REPORT_COMPOSING = "report_composing"
    REPORT_READY = "report_ready"
    RECOVERY_REQUIRED = "recovery_required"
    CANCELLED_OR_FAILED = "cancelled_or_failed"

@dataclass(frozen=True)
class LifecycleCommand:
    command_id: str
    run_id: str
    expected_state: ContentResearchState
    expected_revision: int
    kind: str
    payload: Mapping[str, Any]

@dataclass(frozen=True)
class ExecutionEvent:
    run_id: str
    expected_revision: int
    attempt_id: str | None
    lease_token: str | None
    kind: str
    payload: Mapping[str, Any]

@dataclass(frozen=True)
class RunProjection:
    run_id: str
    thread_id: str
    state: ContentResearchState
    state_revision: int
    entered_at: datetime
    allowed_actions: tuple[str, ...]
    reason_code: str | None
    error: Mapping[str, Any] | None
    brief_id: str | None
    scope_contract_id: str | None
    execution_attempt_id: str | None
    coverage_snapshot_id: str | None
    publication_id: str | None

class ContentResearchPersistenceCoordinator:
    async def apply(self, command: LifecycleCommand) -> RunProjection: ...
    async def record(self, event: ExecutionEvent) -> RunProjection: ...
    async def load(self, run_id: str) -> RunProjection: ...
```

`RunProjection` always contains `run_id`, `thread_id`, `state`, `state_revision`, `entered_at`, `allowed_actions`, `reason_code`, safe `error`, and IDs of the current Brief, Scope, execution attempt, Coverage snapshot, and publication when present.

The current HTTP surface is consolidated as follows:

- `POST /content-research/presearch` creates and activates a Run, then returns `RunProjection` plus its PreResearch view.
- `POST /content-research/workflows/{run_id}/actions` accepts only current command-envelope actions and delegates every mutation to the coordinator.
- `GET /content-research/workflows/{run_id}` returns the authoritative current projection and state-owned views.
- Scope, Trace, evidence, and report endpoints remain read-only projections tied to the same Run/revision.

## Test Lanes Required in Every Slice

```bash
# Frontend current-contract tests.
cd frontend && npm test

# Foundation gate; use the complete files, not name-only mocks.
pytest -q \
  tests/e2e/test_content_research_model_configuration_api.py \
  tests/e2e/test_xhs_qr_login_api.py \
  tests/e2e/test_xhs_login_api.py \
  tests/unit/test_content_research_llm_scope.py \
  tests/unit/test_xhs_credentials.py \
  tests/unit/test_xhs_qr_auth.py

# Old-spec scan. Every hit must be a read-only historical decoder or deleted.
rg -n 'confirm_subject_structure|subject_needs_confirmation|subject_structure_state|start_formal_research|primary_marketing_goal|custom_research_question' \
  app/content_research app/api frontend/src tests
```

Any changed-entrypoint failure belongs to the slice and blocks it. Only unrelated, pre-recorded baseline failures may be reported separately.

## Slice Map

| Slice | Independently observable result | Primary contracts | Estimate |
|---|---|---|---:|
| 1. Lifecycle backbone | Submit subject → real PreResearch → correct Brief; refresh/restart/Run history remain correct | `STATE-CR-01/02/10/11`, `AUTH-CR-01..04/07/08`, `INV-CR-01/02/04/16/18` | 4–6 days |
| 2. Brief to Scope | Confirm Brief → editable Scope v2 queries; no collection or premature frozen/running UI | `STATE-CR-02/03`, `AUTH-CR-05/08`, `INV-CR-03/04` | 3–4 days |
| 3. Complete happy path | Confirm Scope once → reliable XHS retrieval → Coverage satisfied → verified report | `STATE-CR-03..06/08/09/10`, `INV-CR-05..11/15..17`, `SQL-CR-01..10` | 6–8 days |
| 4. Limited report branch | Insufficient Coverage → user chooses Limited → truthful limited report | `STATE-CR-07..09`, `INV-CR-11/14/15/16` | 2–3 days |
| 5. Expand/Relax successor | Insufficient Coverage → Expand or Relax → fenced successor retrieval → report | `INV-CR-12/13/17`, `FAIL-CR-02/05/07/08/10` | 3–5 days |
| 6. Full Trace projection | UI shows authoritative state, real operations, safe errors, exact note refs, and no stale fallback | `TRACE-CR-01..06`, `ACC-TRACE-01..08` | 3–4 days |

Estimated total: 21–30 effective engineering days and approximately 4,800–7,500 changed lines after overlap and old-code deletion.

## Contract Ownership Matrix

The lifecycle remediation acceptance suite is cumulative. Slice 1 defines the
complete legal transition table and rejects every illegal combination in pure
tests. Browser/worker scenarios are added in the slice that first makes their
transition reachable; by the end of Slice 5, all `ACC-STATE-*` rows are owned
and passing. Slice 6 then completes the `ACC-TRACE-*` presentation contract.

| Owner | Exact Contract IDs |
|---|---|
| Slice 1 | `STATE-CR-01`, `STATE-CR-02`, `STATE-CR-10`, `STATE-CR-11`; `AUTH-CR-01`, `AUTH-CR-02`, `AUTH-CR-03`, `AUTH-CR-04`, `AUTH-CR-07`, `AUTH-CR-08`; `INV-CR-01`, `INV-CR-02`, `INV-CR-04`, `INV-CR-16`, `INV-CR-18`; `FAIL-CR-01`, `FAIL-CR-03`, `FAIL-CR-04`, `FAIL-CR-08`, `FAIL-CR-12`; `ACC-STATE-01`, `ACC-STATE-02`, `ACC-STATE-03`, `ACC-STATE-05`, `ACC-STATE-09`, `ACC-STATE-10`, `ACC-STATE-12`, `ACC-STATE-13` |
| Slice 2 | `STATE-CR-03`; `AUTH-CR-05`; `INV-CR-03`; `FAIL-CR-02`; `ACC-STATE-04` for Brief and Scope-Draft commands |
| Slice 3 | `STATE-CR-04`, `STATE-CR-05`, `STATE-CR-06`, `STATE-CR-08`, `STATE-CR-09`; `AUTH-CR-06`; `INV-CR-05`, `INV-CR-06`, `INV-CR-07`, `INV-CR-08`, `INV-CR-09`, `INV-CR-10`, `INV-CR-15`, `INV-CR-17`; `SQL-CR-01`, `SQL-CR-02`, `SQL-CR-03`, `SQL-CR-04`, `SQL-CR-05`, `SQL-CR-06`, `SQL-CR-07`, `SQL-CR-08`, `SQL-CR-09`, `SQL-CR-10`; `FAIL-CR-05`, `FAIL-CR-06`, `FAIL-CR-07`, `FAIL-CR-09`, `FAIL-CR-10`, `FAIL-CR-11`; `ACC-STATE-04`, `ACC-STATE-06`, `ACC-STATE-07`, `ACC-STATE-08`, `ACC-STATE-11`; `ACC-SQL-01`, `ACC-SQL-02`, `ACC-SQL-03`, `ACC-SQL-04`, `ACC-SQL-05`, `ACC-SQL-06`, `ACC-SQL-07`, `ACC-SQL-08` |
| Slice 4 | `STATE-CR-07`; `INV-CR-11`, `INV-CR-14`; limited-path extension of `INV-CR-15`, `INV-CR-16`, `FAIL-CR-02`, `FAIL-CR-05`, `FAIL-CR-11`, `ACC-STATE-04`, `ACC-STATE-06`, `ACC-STATE-11` |
| Slice 5 | `INV-CR-12`, `INV-CR-13`; successor-path extension of `INV-CR-17`, `FAIL-CR-02`, `FAIL-CR-05`, `FAIL-CR-07`, `FAIL-CR-08`, `FAIL-CR-10`, `ACC-STATE-03`, `ACC-STATE-04`, `ACC-STATE-06`, `ACC-STATE-07`, `ACC-STATE-08`, `ACC-STATE-09` |
| Slice 6 | `TRACE-CR-01`, `TRACE-CR-02`, `TRACE-CR-03`, `TRACE-CR-04`, `TRACE-CR-05`, `TRACE-CR-06`; `ACC-TRACE-01`, `ACC-TRACE-02`, `ACC-TRACE-03`, `ACC-TRACE-04`, `ACC-TRACE-05`, `ACC-TRACE-06`, `ACC-TRACE-07`, `ACC-TRACE-08` |
| Every replacing slice | `OLD-CR-01`, `OLD-CR-02`, `OLD-CR-03`, `OLD-CR-04`, `OLD-CR-05`, `OLD-CR-06`, `OLD-CR-07`, `OLD-CR-08` |

`FAIL-CR-01` through `FAIL-CR-12` remain cumulative regression tests after
their first owning slice. No later slice may delete or weaken an earlier failure
proof.

---

### Slice 1: Lifecycle Backbone Through PreResearch and Brief

**Outcome:** A Creator user submits a research subject and sees only `presearch_running`, then the approved Brief card. Run B is durable active from creation, Run A remains history, refresh/restart restore the same state, and failures converge instead of showing a false running state.

**Files:**

- Create `app/content_research/lifecycle/__init__.py`
- Create `app/content_research/lifecycle/models.py`
- Create `app/content_research/lifecycle/transitions.py`
- Create `app/content_research/lifecycle/coordinator.py`
- Create `app/content_research/lifecycle/projection.py`
- Modify `app/content_research/migrations.py`
- Modify `app/content_research/presearch/service.py`
- Modify `app/content_research/service.py`
- Modify `app/content_research/api_schemas.py`
- Modify `app/api/routes/router.py`
- Modify `frontend/src/lib/content-research-api.ts`
- Modify `frontend/src/app/creator/page.tsx`
- Test `tests/unit/test_content_research_lifecycle_transitions.py`
- Test `tests/integration/test_content_research_lifecycle_coordinator.py`
- Test `tests/e2e/test_content_research_presearch_api.py`
- Test `tests/e2e/test_content_research_creator_browser.py`
- Test `frontend/src/app/creator/page.test.tsx`

**Transition:** no Run → `submit_research_subject` → `presearch_running` → `presearch_completed` → `brief_confirmation_required`; failures enter `recovery_required`, retry returns only to `presearch_running`, cancel enters `cancelled_or_failed`.

**Authority / transaction:** Migration adds `content_research_state`, `state_revision`, `state_entered_at`, and transition/error records. Run creation, initial transition, and `thread.active_run_id` commit together. PreResearch result, Brief Draft, new state/revision, and transition event commit together.

**Side effect:** Real configured LLM call after request fact commits. No Scope, dispatch, worker, or XHS call is reachable.

**Read / UI projection:** Creator renders by `RunProjection.state`. The old “还需要你确认调研主体” card, `subject_needs_confirmation`, and premature frozen/running cards are removed. Trace exposes current state/revision, PreResearch transitions, and safe LLM/local-persistence error.

**Failure rows:** Duplicate submission command is idempotent; stale Run/response cannot replace active Run; restart reconciles request-without-outcome; LLM/configuration failure is recoverable; old checkpoint cannot recreate the deleted card.

**Acceptance RED:** `test_creator_submit_subject_reaches_only_the_approved_brief_and_restores_it` and `test_creator_run_b_remains_active_after_reload_and_late_run_a_response`.

- [ ] **Step 1: Write migration and pure-transition REDs**

Assert legal transitions, revision increments, illegal Brief/Scope combinations, duplicate `command_id`, stale revision rejection, and old-row read-only decoding.

- [ ] **Step 2: Write the two Creator-to-owned-stack Acceptance REDs**

Use real Router and SQLite plus a deterministic recording LLM. Assert no `confirm_subject_structure`, no Scope row, no dispatch row, Run B active, Run A present only in Timeline, and refresh/restart parity.

- [ ] **Step 3: Run REDs**

```bash
pytest -q \
  tests/unit/test_content_research_lifecycle_transitions.py \
  tests/integration/test_content_research_lifecycle_coordinator.py \
  tests/e2e/test_content_research_presearch_api.py \
  tests/e2e/test_content_research_creator_browser.py \
  -k 'approved_brief or run_b or lifecycle' -vv
```

- [ ] **Step 4: Implement the coordinator seam and PreResearch transitions**

Keep provider calls outside the transaction. Return the new projection from create/read APIs. Delete `confirm_subject_structure` action/schema/service/UI and rewrite or delete every test that expects it.

- [ ] **Step 5: Prove failure, restart, stale response, and foundation behavior**

Run the owned tests, `frontend npm test`, foundation gate, and old-spec scan. Inject LLM failure, process restart, late Run A response, and stale checkpoint.

- [ ] **Step 6: Commit the independently releasable checkpoint**

```bash
git add app/content_research/lifecycle app/content_research app/api/routes/router.py \
  frontend/src tests
git commit -m "refactor(content-research): establish the authoritative lifecycle backbone"
```

---

### Slice 2: Brief Confirmation to Scope v2 Draft

**Outcome:** Confirming the approved Brief produces one editable Scope card containing only exact executable queries. B/C explanations and inline inputs are clear and optional. Nothing is frozen or dispatched until the user confirms queries.

**Files:**

- Modify `app/content_research/lifecycle/models.py`
- Modify `app/content_research/lifecycle/transitions.py`
- Modify `app/content_research/lifecycle/coordinator.py`
- Modify `app/content_research/scope_contract.py`
- Modify `app/content_research/workflow/query_planner.py`
- Modify `app/content_research/workflow/plan_builder.py`
- Modify `app/content_research/service.py`
- Modify `app/content_research/api_schemas.py`
- Modify `frontend/src/lib/content-research-api.ts`
- Modify `frontend/src/app/creator/page.tsx`
- Test `tests/unit/test_content_research_query_planner.py`
- Test `tests/integration/test_content_research_scope_contract_store.py`
- Test `tests/e2e/test_content_research_brief_confirm_api.py`
- Test `tests/e2e/test_content_research_scope_api.py`
- Test `tests/e2e/test_content_research_creator_browser.py`

**Transition:** `brief_confirmation_required` → `confirm_brief` → `scope_confirmation_required`; revising the subject returns to `presearch_running` and invalidates the old Brief command.

**Authority / transaction:** Confirmed Brief, Plan, Scope Draft, state/revision, and transition event commit together. A Draft replacement from optional B/C input invalidates its predecessor in the same transaction.

**Side effect:** None. Query planning and Draft persistence cannot call XHS or enqueue work.

**Read / UI projection:** Scope card shows `A`, available `A B`, available `A C`, group-level origin, and only real `final_query` text. Missing B/C does not disable confirmation or add another button/stage.

**Failure rows:** Duplicate Brief confirmation creates one Plan/Draft; stale Draft replacement has zero delta; one or two groups remain valid; arbitrary non-empty final edits remain allowed.

**Acceptance RED:** `test_creator_confirms_brief_and_receives_editable_scope_without_collection` and `test_creator_missing_bc_replaces_only_the_latest_scope_draft`.

- [ ] **Step 1: Write query-contract, atomic-confirmation, and browser REDs**

Assert `A/A B/A C`, A-only fallback, B/C labels, no abstract “重点了解什么”, no Scope Contract, no dispatch, and no frozen/running UI.

- [ ] **Step 2: Run REDs**

```bash
pytest -q \
  tests/unit/test_content_research_query_planner.py \
  tests/integration/test_content_research_scope_contract_store.py \
  tests/e2e/test_content_research_brief_confirm_api.py \
  tests/e2e/test_content_research_scope_api.py \
  tests/e2e/test_content_research_creator_browser.py \
  -k 'scope_draft or missing_bc or without_collection' -vv
```

- [ ] **Step 3: Implement the transition and current Scope UI**

Route Brief confirmation and Draft replacement through the coordinator. Delete automatic `formal_research`, legacy Brief fields/forms, and `start_formal_research` from the new-Run schemas, service dispatch, frontend API, UI, and tests.

- [ ] **Step 4: Verify reload, duplicate/stale commands, frontend, and foundations**

Reload at `scope_confirmation_required`; reorder two Draft responses; double-submit Brief; run test lanes and old-spec scan.

- [ ] **Step 5: Commit**

```bash
git add app/content_research frontend/src tests
git commit -m "feat(content-research): make brief confirmation produce the exact scope draft"
```

---

### Slice 3: Atomic Scope Confirmation Through the Complete Happy Path

**Outcome:** One Creator confirmation freezes the exact current Scope and starts retrieval. The real owned worker sends each frozen query to the recording/real XHS adapter, stores safe note facts, evaluates adequate Coverage, composes and publishes one verified report, and every intermediate UI state is truthful.

**Files:**

- Modify `app/content_research/lifecycle/models.py`
- Modify `app/content_research/lifecycle/transitions.py`
- Modify `app/content_research/lifecycle/coordinator.py`
- Create `app/content_research/lifecycle/sqlite_policy.py`
- Create `app/content_research/lifecycle/reconciler.py`
- Modify `app/content_research/async_dispatch.py`
- Modify `app/content_research/worker.py`
- Modify `app/content_research/stores/sqlite_store.py`
- Modify `app/content_research/workflow/directional_pipeline.py`
- Modify `app/content_research/sources/xiaohongshu/adapter.py`
- Modify `app/content_research/admission/product_marketing.py`
- Modify `app/content_research/reporting/execution.py`
- Modify `app/content_research/reporting/publication_materializer.py`
- Modify `app/content_research/service.py`
- Modify `frontend/src/app/creator/page.tsx`
- Test `tests/integration/test_content_research_sqlite_write_coordination.py`
- Test `tests/unit/test_content_research_dispatch_worker.py`
- Test `tests/e2e/test_content_research_source_collection_api.py`
- Test `tests/e2e/test_content_research_report_publication_timeline_api.py`
- Test `tests/e2e/test_content_research_creator_browser.py`

**Transition:** `scope_confirmation_required` → `retrieval_queued` → `retrieval_running` → `coverage_evaluating` → `report_composing` → `report_ready`; any known failure converges to `recovery_required`.

**Authority / transaction:** Scope Contract, unique dispatch, state/revision, and event commit together. Worker claim, attempt, lease, and state commit together. Every worker write matches Run/Scope/attempt/lease/revision. Report publication and Run terminal state use the publication integrity boundary.

**Side effect:** Exact XHS queries and report LLM calls occur only after request facts commit and outside database transactions.

**Read / UI projection:** Creator shows frozen Scope only after confirmation, queued/running only after durable dispatch/claim, real result counts while retrieving, and the verified report only after publication. Trace already exposes state, transitions, safe errors, operation status, and counts; richer note presentation is added by Slice 6.

**Failure rows:** Duplicate confirmation creates one dispatch; short SQLite locks retry; exhausted locks become `LOCAL_PERSISTENCE_BUSY`; auth loss becomes login-then-retry; request without outcome becomes `outcome_unknown`; late worker/lease is fenced; restart reconciles without duplicate provider calls; publication failure cannot show success.

**Acceptance RED:** `test_creator_confirm_scope_executes_one_complete_verified_run` plus fault tests for lock, auth, crash, duplicate confirmation, and stale worker.

- [ ] **Step 1: Write the happy-path Browser-to-owned-stack RED**

Use one to three frozen queries, the real Router/SQLite/worker, recording XHS and recording report LLM. Assert exact provider calls, note IDs, A-only admission, one publication, state revisions, Timeline, Scope, report, and Trace parity.

- [ ] **Step 2: Write SQLite and execution failure REDs**

Cover `ACC-SQL-01..08`, duplicate confirmation, short/long writer lock, heartbeat plus note batch, provider-request crash, process restart, auth expiry, late lease, and publication rollback.

- [ ] **Step 3: Run REDs**

```bash
pytest -q \
  tests/integration/test_content_research_sqlite_write_coordination.py \
  tests/unit/test_content_research_dispatch_worker.py \
  tests/e2e/test_content_research_source_collection_api.py \
  tests/e2e/test_content_research_report_publication_timeline_api.py \
  tests/e2e/test_content_research_creator_browser.py \
  -k 'complete_verified_run or lock or auth or stale_worker or duplicate_scope' -vv
```

- [ ] **Step 4: Implement the complete reachable path behind the coordinator**

Remove read-side `BEGIN IMMEDIATE`, centralize connection PRAGMAs/close/retry, move all provider waits outside transactions, persist request/outcome facts, fence writes, and make worker failure call the same state transition rather than updating only dispatch/task rows.

- [ ] **Step 5: Verify resources, restart, frontend, foundations, and old-code deletion**

Assert bounded connection/file-handle counts during polling. Kill/restart the owned worker after request-fact persistence. Run the full slice tests, frontend suite, foundation gate, and old-spec scan.

- [ ] **Step 6: Commit**

```bash
git add app/content_research frontend/src tests
git commit -m "feat(content-research): execute the reliable research happy path"
```

---

### Slice 4: Insufficient Coverage to a Limited Report

**Outcome:** When Coverage is insufficient, Creator shows the exact server-owned gap and allowed actions. Choosing Limited records the decision and publishes one visibly constrained report without changing Scope or re-running XHS.

**Files:**

- Modify `app/content_research/lifecycle/models.py`
- Modify `app/content_research/lifecycle/transitions.py`
- Modify `app/content_research/lifecycle/coordinator.py`
- Modify `app/content_research/decisions/service.py`
- Modify `app/content_research/reporting/execution.py`
- Modify `app/content_research/reporting/lite_read_model.py`
- Modify `app/content_research/service.py`
- Modify `frontend/src/app/creator/page.tsx`
- Test `tests/integration/test_content_research_scope_coverage.py`
- Test `tests/e2e/test_content_research_human_decisions_api.py`
- Test `tests/e2e/test_content_research_creator_browser.py`

**Transition:** `coverage_evaluating` → `coverage_insufficient` → `coverage_decision_required` → `generate_limited_report` → `report_composing` → `report_ready`.

**Authority / transaction:** Coverage snapshot, allowed actions, decision identity, state/revision, and event are atomic at each boundary. Duplicate Limited commands resolve to the same decision/publication.

**Side effect:** No additional XHS call. Report LLM runs after the durable Limited decision.

**Read / UI projection:** The normal happy path never shows this card. The limited report displays exact evidence limitations and cannot claim unmet findings.

**Failure rows:** Stale snapshot/version rejected; repeated click produces one report; report failure returns to recovery for the exact decision; refresh restores the decision card or published limited report.

**Acceptance RED:** `test_creator_selects_limited_and_receives_one_truthful_limited_report`.

- [ ] **Step 1: Write state, API, browser, and report-faithfulness REDs**

Add `test_limited_decision_requires_current_coverage_identity`,
`test_duplicate_limited_decision_publishes_once`, and
`test_creator_selects_limited_and_receives_one_truthful_limited_report`.
Record XHS adapter call count before the decision and assert it does not change.
Assert the report carries `publication_state=partial_verified_report`, the exact
Coverage reason codes, frozen citation IDs, and no unsupported conclusion.

- [ ] **Step 2: Run REDs**

```bash
pytest -q \
  tests/integration/test_content_research_scope_coverage.py \
  tests/e2e/test_content_research_human_decisions_api.py \
  tests/e2e/test_content_research_creator_browser.py \
  -k 'limited or truthful_limited_report' -vv
```

- [ ] **Step 3: Implement the Limited transition, report projection, and UI**

Add only `generate_limited_report` to the current command envelope. Persist the
Coverage decision before invoking the report LLM; use the existing publication
integrity boundary; project the limitation block from the saved decision rather
than prompt output.

- [ ] **Step 4: Verify duplicate/stale commands, report failure, and foundations**

Run the three complete test files above, `frontend npm test`, foundation gate,
and old-spec scan. Fault the report LLM after decision commit and prove refresh
shows `recovery_required` with a report-only recovery action and no new XHS call.
- [ ] **Step 5: Commit**

```bash
git add app/content_research frontend/src tests
git commit -m "feat(content-research): publish a truthful limited coverage report"
```

---

### Slice 5: Expand and Relax Through Fenced Successor Execution

**Outcome:** From the same insufficient Coverage card, Expand creates a new execution unit under the same Scope; Relax creates an explicit successor Scope. Each path runs through the owned worker and returns to Coverage/report without reusing an old attempt or accepting late writes.

**Files:**

- Modify `app/content_research/lifecycle/models.py`
- Modify `app/content_research/lifecycle/transitions.py`
- Modify `app/content_research/lifecycle/coordinator.py`
- Modify `app/content_research/async_dispatch.py`
- Modify `app/content_research/worker.py`
- Modify `app/content_research/scope_contract.py`
- Modify `app/content_research/decisions/service.py`
- Modify `app/content_research/service.py`
- Modify `frontend/src/app/creator/page.tsx`
- Test `tests/integration/test_content_research_scope_coverage.py`
- Test `tests/integration/test_content_research_sqlite_write_coordination.py`
- Test `tests/e2e/test_content_research_human_decisions_api.py`
- Test `tests/e2e/test_content_research_authorized_continuation_e2e.py`
- Test `tests/e2e/test_content_research_creator_browser.py`

**Transition:** `coverage_decision_required` → Expand → `retrieval_queued` with same Scope/new execution unit; or Relax → `retrieval_queued` with successor Scope/new execution unit; both continue through the complete Slice 3 path.

**Authority / transaction:** Decision, exact target identity, successor unit/Scope, unique dispatch, state/revision, and event commit together. Only the active attempt/lease may write.

**Side effect:** Exact supplementary or successor queries execute once. `outcome_unknown` is never automatically replayed.

**Read / UI projection:** Refresh shows the exact saved decision and active successor. Old Scope remains history and cannot be selected as current authority.

**Failure rows:** Duplicate decision, stale Coverage snapshot, restart, Run A/Run B coexistence, old attempt completion, delayed Scope/Trace/report response, auth loss, and unknown provider outcome.

**Acceptance RED:** `test_creator_expand_and_relax_each_execute_one_fenced_successor`.

- [ ] **Step 1: Write paired Expand/Relax Browser-to-owned-stack REDs**

Add `test_creator_expand_executes_one_same_scope_successor` and
`test_creator_relax_executes_one_successor_scope`. Assert Expand preserves the
Scope ID and increments execution identity; Relax preserves the predecessor
link and creates a new Scope ID/version. Both must reach one new provider call
set and one final publication.

- [ ] **Step 2: Write duplicate, restart, late-worker, and unknown-outcome REDs**

Add `test_duplicate_coverage_command_creates_one_successor`,
`test_restart_resumes_only_the_active_successor`,
`test_late_predecessor_worker_cannot_write_successor`, and
`test_unknown_successor_outcome_is_not_replayed`. Assert stale attempts create
zero source/evidence/publication delta.

- [ ] **Step 3: Run REDs**

```bash
pytest -q \
  tests/integration/test_content_research_scope_coverage.py \
  tests/integration/test_content_research_sqlite_write_coordination.py \
  tests/e2e/test_content_research_human_decisions_api.py \
  tests/e2e/test_content_research_authorized_continuation_e2e.py \
  tests/e2e/test_content_research_creator_browser.py \
  -k 'expand or relax or successor or predecessor' -vv
```

- [ ] **Step 4: Implement successor transactions and authoritative refresh**

Make `resolve_coverage` create decision, exact successor identity, dispatch,
state/revision, and event in one coordinator transaction. Pass only the frozen
successor authority to the worker. Creator discards any Scope/Trace/report
response whose Run ID or request ticket is no longer current.

- [ ] **Step 5: Verify both complete journeys and foundations**

Run the five complete test files above, `frontend npm test`, foundation gate,
and old-spec scan. Repeat after restarting API and worker between decision
commit and worker claim.
- [ ] **Step 6: Commit**

```bash
git add app/content_research frontend/src tests
git commit -m "feat(content-research): execute fenced coverage successors"
```

---

### Slice 6: Full Trace and Concrete Note Projection

**Outcome:** Opening Trace for any current or historical Run displays its authoritative state/revision, transition reasons, real LLM/XHS operations, safe structured errors, and concrete persisted note references. A failed backend operation cannot remain visually running.

**Files:**

- Modify `app/content_research/api_schemas.py`
- Modify `app/content_research/observation/trace_service.py`
- Modify `app/content_research/lifecycle/projection.py`
- Modify `app/content_research/service.py`
- Modify `app/api/routes/router.py`
- Modify `frontend/src/lib/content-research-trace.ts`
- Modify `frontend/src/lib/content-research-api.ts`
- Modify `frontend/src/app/creator/page.tsx`
- Test `tests/unit/test_content_research_trace_service.py`
- Test `tests/e2e/test_content_research_trace_api.py`
- Test `tests/e2e/test_content_research_creator_browser.py`
- Test `frontend/src/lib/content-research-trace.test.ts`
- Test `frontend/src/app/creator/page.test.tsx`

**Transition:** None. Trace is a read-only projection and cannot advance or recover the Run.

**Authority / transaction:** Top-level state comes only from Run state/revision. Transition events and operation facts were written with their owning slices. Note items resolve through persisted canonical source/evidence IDs. Trace reads use read-only connections and create no lock or writes.

**Side effect:** None.

**Read / UI projection:** Render current state, from/to/event/revision timeline, provider/model/status/latency/counts, note source ID/title/author/URL/result state, and safe `code/stage/operation/retryable/attempts/recovery_action`.

**Failure rows:** Redact Cookie, Key, headers, raw provider payload, and raw exception; preserve safe errors. Ignore stale checkpoint as current state; bind every response to selected Run/request ticket; dispatch failure must render recovery/failed, never running.

**Acceptance RED:** `test_creator_trace_shows_real_notes_and_backend_failure_without_stale_running` plus API redaction and read-only lock tests.

- [ ] **Step 1: Write response-contract, redaction, stale-checkpoint, and browser REDs**
- [ ] **Step 2: Run REDs**

```bash
pytest -q \
  tests/unit/test_content_research_trace_service.py \
  tests/e2e/test_content_research_trace_api.py \
  tests/e2e/test_content_research_creator_browser.py \
  -k 'real_notes or backend_failure or redaction or stale_checkpoint' -vv
```

- [ ] **Step 3: Replace fallback inference with the authoritative Trace projection**

Delete `_derive_current_stage`, Brief-status recovery inference, and checkpoint-as-current-state branches. Keep historical checkpoint events only in the transition/operation timeline.

- [ ] **Step 4: Implement the current UI mapping and stale-response fence**

Frontend must consume the response contract directly and never synthesize running/retry states.

- [ ] **Step 5: Run all Trace tests, frontend, foundation gate, and old-spec scan**
- [ ] **Step 6: Commit**

```bash
git add app/content_research/observation app/content_research/api_schemas.py \
  app/content_research/lifecycle app/content_research/service.py app/api/routes/router.py \
  frontend/src tests
git commit -m "feat(content-research): project truthful execution trace and note references"
```

## Per-Slice Review Gate

Before accepting any slice, the reviewer must answer yes to all questions:

1. Does its Browser-to-owned-stack RED traverse real Router and SQLite?
2. Does every newly reachable state have a truthful UI and Trace projection?
3. Are duplicate, stale, failure, refresh, and restart semantics proved for its transitions?
4. Are provider calls outside transactions and fenced by exact identity?
5. Did the slice delete the old implementation and old tests it replaced?
6. Do LLM configuration and Xiaohongshu login foundation gates still pass?
7. Can the branch stop here without a later slice being required to make this checkpoint correct?

If any answer is no, the slice is incomplete and cannot be handed to the next task.

## Final Release Verification — Not a Development Task

After Slice 6, rerun all six owned journeys and one authenticated canary:

```text
Creator → Router → lifecycle coordinator → SQLite → worker
→ authenticated Xiaohongshu → persisted notes → Coverage/report → Trace
```

This gate may discover a regression, but it may not be the first cross-layer proof for any slice. Release requires zero affected old-spec failures, clean secret redaction, bounded SQLite resources, and a saved sanitized canary trace.
