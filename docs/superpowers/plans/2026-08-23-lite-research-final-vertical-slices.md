# Lite Content Research Final Vertical Slices Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the real Creator Content Research journey so the durable current run remains truthful across history/reload and a confirmed product-marketing query portfolio is executed, admitted, recovered, and reported without regressing Trace, LLM configuration, or Xiaohongshu login.

**Architecture:** Deliver five observable vertical feature slices through seven reviewable Tasks. Task 1 is the only non-product preparation Task and Task 7 only re-runs evidence. Every implementation Task 2–6 starts with a browser-to-owned-stack Acceptance RED and ends with a real Creator → Router → SQLite/worker → authoritative Creator projection proof. Shared framework code is introduced inside the first reachable slice that needs it, never as a separately accepted horizontal checkpoint. Historical v1 runs keep their frozen semantics.

**Tech Stack:** Python 3.10+, FastAPI/Pydantic, aiosqlite/SQLite, React 18, Next.js 14, TypeScript, Node test runner, pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-08-15-lite-research-scope-contract-design.md`; `docs/superpowers/specs/2026-08-22-task-5-creator-authority-contract.md`

## Global Constraints

- Product-marketing Scope v2 suggests one to three groups in deterministic order: `A`, available `A B`, available `A C`.
- A is the only required candidate condition. B and C are optional query aspects and never exclude a candidate or independently trigger `awaiting_scope_decision`.
- A user may confirm any one to three non-empty final queries; a query omitting A is `exploratory`, not invalid.
- `suggested_query`, `final_query`, and `origin` remain query-group scoped. Do not add slot-level provenance.
- Missing B/C renders “产品／体验检索词” and “场景／人群检索词” explanations and optional inputs; confirmation remains enabled with any existing non-empty group.
- Scope Contract v1 remains readable and executable under its original frozen constraints. Never rewrite or reinterpret historical v1 rows.
- Current-run precedence is explicit valid selection → durable `thread.active_run_id` → valid thread-local cache → newest readable historical artifact.
- Trace remains a truthful execution timeline. Query audit stays in Scope/evidence.
- LLM keys, Xiaohongshu Cookies, provider headers, and raw provider errors remain redacted.
- Every implementation Task 2–6 owns its Acceptance RED, inner TDD, foundation smoke, compatibility proof, browser-to-owned-stack journey, and deploy checkpoint.

## Test Policy and Impact Gate

| Lane | Meaning | Delivery rule |
|---|---|---|
| A — current-contract | Proves a Contract ID owned by this slice | Must pass before checkpoint. |
| B — foundation | Trace, LLM configuration/scope, XHS credentials/login, thread restoration | Must pass for Tasks 2–6. |
| C — superseded | Asserts removed marketing-goal, custom-question, structure-confirmation, old compiler, or B/C-required behavior | Rewrite/delete in the first replacing Task; never bend production code to satisfy it. |
| D — unrelated baseline | Fails before work and does not touch changed entrypoints | Record exact failure; block only if count/output worsens. |

Run before every implementation Task and reconcile every hit with Task 1's manifest:

```bash
rg -n 'primary_marketing_goal|custom_research_question|subject_structure_confirmation|PRODUCT_MARKETING_GOAL_FACETS|first_intent|上身感受|active_run_id|latestReport|restoredRunId|prepare_scope|confirm_scope' app/content_research frontend/src tests
```

Foundation smoke required at Tasks 2–6:

```bash
pytest -q \
  tests/e2e/test_content_research_trace_api.py \
  tests/e2e/test_content_research_model_configuration_api.py \
  tests/e2e/test_xhs_qr_login_api.py \
  tests/unit/test_content_research_llm_scope.py \
  tests/unit/test_xhs_credentials.py \
  tests/unit/test_xhs_qr_auth.py
cd frontend && node --import tsx --test \
  --test-name-pattern='trace|Xiaohongshu|model setup|model configuration' \
  src/app/creator/page.test.tsx src/lib/content-research-api.test.ts
```

## Vertical Closure Rule

Task 1 is a documented exception because it changes no product behavior. Each Task 2–6 must prove this composition before review:

```text
real Creator action
  → real Router request
  → real SQLite authority and, where applicable, real owned worker
  → deterministic or fault-controlled provider adapter
  → real Scope/Coverage/Trace/report read model
  → Creator renders the authoritative result
```

Intercepted UI tests may supplement this proof but cannot replace it. A Task cannot defer its first cross-layer proof, truthful projection, authority, rollback behavior, or owned historical compatibility to Task 7.

## Task Map

| Task | Kind | Browser-to-owned-stack result | Checkpoint |
|---|---|---|---|
| 1 | Delivery control | Impact manifest and baseline; no product behavior | Documentation-only exception. |
| 2 | Vertical Slice 1 | Run B becomes durable current and survives reload/history races | Complete and deployable. |
| 3 | Vertical Slice 2 | Creator confirms A / A B / A C, including missing B/C through fenced replacement Drafts | Complete and deployable. |
| 4 | Vertical Slice 3 | Arbitrary non-empty edited query reaches provider exactly; admission still requires A | Complete and deployable. |
| 5 | Vertical Slice 4 | Limited Coverage expands or relaxes from Creator and refreshes from authoritative execution | Complete and deployable. |
| 6 | Vertical Slice 5 | Historical v1 remains frozen/readable/recoverable beside current v2 | Complete and deployable. |
| 7 | Release gate | Re-runs all owned journeys and authenticated Trace/LLM/XHS canary | Verification only. |

### Task 1: Freeze the impact manifest and test baseline

**Outcome:** Every affected executable entry and test has an owning Task and lane before product code changes; exact pre-existing failures are recorded.

**Files:** Create `docs/release/2026-08-23-lite-research-final-impact-manifest.md`; read every impact-inventory hit.

**Interfaces:** Each manifest row contains symbol/test, current meaning, target Contract IDs, owner Task 2–6, lane A/B/C/D, and baseline status. “Review later” is invalid.

- [ ] **Step 1: Inventory affected entrypoints**

Include these rows even when search finds no hit:

```text
presearch proposal; confirm_subject_structure; confirm_brief; plan builder;
product query compiler; prepare_scope; confirm_scope; dispatch guard; worker;
candidate admission; Coverage; report read model; thread restore; Scope read;
Trace read; LLM configuration/scope; XHS Cookie/QR login
```

- [ ] **Step 2: Run and record baseline suites**

```bash
pytest -q \
  tests/unit/test_content_research_query_planner.py \
  tests/unit/test_content_research_scope_contract.py \
  tests/e2e/test_content_research_brief_confirm_api.py \
  tests/e2e/test_content_research_scope_api.py \
  tests/e2e/test_content_research_trace_api.py \
  tests/e2e/test_content_research_model_configuration_api.py \
  tests/e2e/test_xhs_qr_login_api.py
cd frontend && npm test
```

- [ ] **Step 3: Validate completeness and commit**

```bash
rg -n 'confirm_brief|prepare_scope|confirm_scope|dispatch|admission|Coverage|Trace|LLM|XHS|Task [2-6]|Lane [A-D]' docs/release/2026-08-23-lite-research-final-impact-manifest.md
git add docs/release/2026-08-23-lite-research-final-impact-manifest.md
git commit -m "docs(content-research): freeze final delivery impact baseline"
```

### Task 2 / Vertical Slice 1: Make Run B current from Brief confirmation through reload

**Outcome:** In a real Creator thread containing historical Run A, confirming Run B atomically persists it as durable active; Scope, Trace and report target Run B immediately, after reload, and after delayed Run A responses.

**Contracts:** `STATE-5-7`, `AUTH-5-6`, `INV-5-5`, `INV-5-6`, `FAIL-5-4`, `FAIL-5-8`, `ACC-5-6`, `ACC-5-7`.

**Transition:** Run B awaiting Brief confirmation → valid confirmation → confirmed Brief/plan plus `thread.active_run_id=Run B` commit together → Creator projects Run B.

**Authority / transaction:** Extend the existing confirmation SQLite transaction; never issue a second post-commit `ThreadStore` update.

**Side effect:** No provider call; collection remains Scope-gated.

**Read / UI projection:** One precedence helper serves initial load, reload, Timeline selection, mutation refresh, Scope, Trace and report. Timeline retains Run A.

**Failure rows:** Stale/failed/rolled-back confirmation preserves Run A and creates no partial plan. Late Run A reads or transient Run B read errors cannot silently select Run A.

**Acceptance RED:** `test_creator_historical_run_never_overrides_durable_active_run_after_brief_confirmation`; paired transaction RED `test_confirm_brief_atomically_sets_the_thread_active_run`.

**Deployment safety:** Backend authority and Creator projection change in the same Task; no schema migration.

**Files:** Modify `app/content_research/async_dispatch.py`, `app/content_research/service.py:1179-1565`, `frontend/src/app/creator/page.tsx:2860-3050`, their focused tests, and `tests/e2e/test_content_research_creator_browser.py`.

- [ ] **Step 1: Write both Acceptance REDs**

The browser test creates Run A/report, confirms Run B from Creator, delays Run A, reloads, and asserts current Scope/Trace/report use Run B while Timeline retains Run A. The API test raises before transaction commit and asserts plan and active-run changes both roll back.

- [ ] **Step 2: Run REDs**

```bash
pytest -q \
  tests/e2e/test_content_research_brief_confirm_api.py::test_confirm_brief_atomically_sets_the_thread_active_run \
  tests/e2e/test_content_research_creator_browser.py::test_creator_historical_run_never_overrides_durable_active_run_after_brief_confirmation -vv
```

- [ ] **Step 3: Implement atomic persistence and single Creator precedence path**

Extend `ContentResearchAsyncDispatch.persist_confirmation(..., active_run_id: str)`. Replace `latestReport`-first and cache-before-durable branches. Fence asynchronous projections by request ticket and selected run ID.

- [ ] **Step 4: Run owned journeys and foundation smoke**

```bash
pytest -q tests/e2e/test_content_research_brief_confirm_api.py \
  tests/e2e/test_content_research_creator_browser.py::test_creator_historical_run_never_overrides_durable_active_run_after_brief_confirmation \
  tests/e2e/test_content_research_creator_browser.py::test_creator_discards_a_late_scope_response_after_switching_runs
cd frontend && npm test
```

Verify Trace, model configuration and XHS state are projected for Run B, not merely that its label is selected.

- [ ] **Step 5: Commit**

```bash
git add app/content_research/async_dispatch.py app/content_research/service.py \
  frontend/src/app/creator/page.tsx frontend/src/app/creator/page.test.tsx \
  tests/e2e/test_content_research_brief_confirm_api.py tests/e2e/test_content_research_creator_browser.py
git commit -m "fix(content-research): keep the durable run current end to end"
```

### Task 3 / Vertical Slice 2: Execute the complete suggested Scope v2 portfolio

**Outcome:** Creator shows only concrete executable `A`, available `A B`, available `A C`. Missing B/C can be completed inline through a fenced replacement Draft. Confirmation executes the exact current Draft through worker, A-only admission, Coverage, Trace and report.

**Contracts:** `STATE-QP-1`, `STATE-QP-2`, `AUTH-QP-1`–`AUTH-QP-4`, `INV-QP-1`–`INV-QP-3`, `FAIL-QP-1`, `FAIL-QP-4`, `ACC-QP-1`, `ACC-QP-2`, `ACC-QP-4`.

**Transition:** confirmed Brief → persisted v2 Draft → exact confirmation → immutable Scope → worker/provider → detail admission → Coverage/report.

**Authority / transaction:** Frozen `query_groups[].final_query` is retrieval authority; only A is required; confirmation freezes text/provenance before dispatch.

**Side effect:** Real owned worker with recording provider; Draft preparation calls no provider.

**Read / UI projection:** Existing Scope card displays exact queries, group origin, optional inputs and copy `产品／体验检索词 — 例如：凉感、显瘦` / `场景／人群检索词 — 例如：夏季通勤`. Query audit is not moved into Trace.

**Failure rows:** One/two groups remain confirmable when B/C is absent. Empty Enter makes no request. Late/double replacement responses cannot overwrite the newest Draft. Missing B/C never excludes candidates or creates a missing-required decision.

**Acceptance RED:** `test_creator_confirms_suggested_v2_portfolio_and_provider_receives_exact_queries` covers three suggested groups; `test_creator_completes_missing_aspects_and_only_latest_draft_executes` covers replacement Drafts and response reordering. Both cross Creator/Router/SQLite/worker/provider/admission/Coverage/read models.

**Deployment safety:** v2 types/compiler/storage/API/UI are introduced together. There is no dormant framework or API-only checkpoint; v1 decoding remains unchanged.

**Files:** Modify `app/content_research/scope_contract.py`, `app/content_research/workflow/query_planner.py`, `app/content_research/api_schemas.py`, `app/content_research/contracts.py`, `app/content_research/workflow/plan_builder.py`, `app/content_research/workflow/task_router.py`, `app/content_research/service.py`, `app/content_research/admission/relevance.py`, `app/content_research/reporting/lite_read_model.py`, `frontend/src/lib/content-research-api.ts`, `frontend/src/app/creator/page.tsx`, and assigned unit/integration/API/browser tests.

- [ ] **Step 1: Write browser Acceptance RED**

With `A=长袖衬衫`, `B=凉感`, `C=夏季通勤`, assert exactly `长袖衬衫`, `长袖衬衫 凉感`, `长袖衬衫 夏季通勤`; no abstract goal input; provider calls match; a detailed A match without B/C is eligible. Add a second browser RED starting with A only: enter B, delay it, enter C, release responses out of order, and assert only the newest replacement Draft is rendered and executed.

- [ ] **Step 2: Write inner compiler/storage/admission REDs**

```python
assert queries("长袖衬衫", "凉感", ["夏季", "通勤"]) == (
    "长袖衬衫", "长袖衬衫 凉感", "长袖衬衫 夏季通勤",
)
assert queries("长袖衬衫", None, []) == ("长袖衬衫",)
assert required_constraint_ids(v2_contract) == ("core_object",)
```

Persist one v1 and one v2 contract; both must round-trip without changing v1 constraints/query groups.

- [ ] **Step 3: Run REDs**

```bash
pytest -q tests/unit/test_content_research_query_planner.py \
  tests/unit/test_content_research_scope_contract.py \
  tests/integration/test_content_research_scope_contract_store.py \
  tests/e2e/test_content_research_brief_confirm_api.py \
  tests/e2e/test_content_research_scope_api.py \
  tests/e2e/test_content_research_source_collection_api.py \
  tests/e2e/test_content_research_creator_browser.py::test_creator_confirms_suggested_v2_portfolio_and_provider_receives_exact_queries \
  tests/e2e/test_content_research_creator_browser.py::test_creator_completes_missing_aspects_and_only_latest_draft_executes -vv
```

- [ ] **Step 4: Implement the smallest complete vertical path**

Add `compile_product_marketing_query_portfolio(...)`, dual-version decoding and group-level origin; wire new product marketing through Router, atomic Scope confirmation, worker, admission, Coverage/report and Creator. `PrepareScopeRequest` accepts optional `product_experience_aspect` and `context_audience_aspect`; each non-empty Enter creates a new persisted Draft, while Creator accepts only the current run/request ticket. Dispatch consumes only frozen final queries; admission reads only `core_object`. Remove `custom_research_question`, `primary_marketing_goal`, and `subject_structure_confirmation` from new Brief/UI/request paths in this Task.

- [ ] **Step 5: Run complete proof, frontend tests and foundation smoke**

```bash
pytest -q tests/unit/test_content_research_query_planner.py \
  tests/unit/test_content_research_scope_contract.py \
  tests/integration/test_content_research_scope_contract_store.py \
  tests/e2e/test_content_research_brief_confirm_api.py \
  tests/e2e/test_content_research_scope_api.py \
  tests/e2e/test_content_research_source_collection_api.py \
  tests/e2e/test_content_research_creator_browser.py::test_creator_confirms_suggested_v2_portfolio_and_provider_receives_exact_queries \
  tests/e2e/test_content_research_creator_browser.py::test_creator_completes_missing_aspects_and_only_latest_draft_executes
cd frontend && npm test
```

Run the global foundation smoke. Confirm v2 worker events in Trace and validated/redacted LLM/XHS state.

- [ ] **Step 6: Commit**

```bash
git add app/content_research frontend/src tests/unit tests/integration tests/e2e
git commit -m "feat(content-research): execute suggested scope v2 portfolios"
```

### Task 4 / Vertical Slice 3: Execute arbitrary user-edited final queries

**Outcome:** Creator accepts any non-empty edited query, including one without A; the exact frozen text reaches the provider while admission still independently requires A.

**Contracts:** `STATE-QP-1`, `STATE-QP-2`, `AUTH-QP-1`–`AUTH-QP-3`, `INV-QP-2`, `INV-QP-3`, `FAIL-QP-2`, `FAIL-QP-3`, `ACC-QP-3`.

**Transition:** Draft → inline edit → exact Draft confirmation → frozen Scope → execution/admission/projection.

**Authority / transaction:** Validate one to three non-empty queries and exact Draft identity. Missing A classifies the group `exploratory`; it does not weaken A admission.

**Side effect:** Recording provider receives exact edited strings.

**Read / UI projection:** Creator shows suggestion, final text and `origin=user_edited`; reload reads persisted values rather than recompiling.

**Failure rows:** Empty/stale commands produce zero Scope/dispatch delta; duplicate confirmation stays idempotent.

**Acceptance RED:** `test_creator_executes_arbitrary_edited_query_but_admits_only_core_match` crosses Creator/Router/SQLite/worker/provider/admission/Scope read model.

**Deployment safety:** Extends a complete v2 path without changing compiler output or v1 semantics.

**Files:** Modify `app/content_research/scope_contract.py`, `app/content_research/service.py`, `app/content_research/admission/relevance.py`, `frontend/src/app/creator/page.tsx`, `frontend/src/app/creator/page.test.tsx`, `tests/e2e/test_content_research_scope_api.py`, `tests/e2e/test_content_research_source_collection_api.py`, and `tests/e2e/test_content_research_creator_browser.py`.

- [ ] **Step 1: Write browser and API REDs**

```python
assert provider.calls == ["衬衫真实测评"]
assert frozen_group.origin == "user_edited"
assert candidate_matching_a.eligibility == "eligible"
assert candidate_without_a.eligibility == "excluded"
```

- [ ] **Step 2: Run REDs**

```bash
pytest -q tests/e2e/test_content_research_scope_api.py -k 'edited or empty or stale' \
  tests/e2e/test_content_research_creator_browser.py::test_creator_executes_arbitrary_edited_query_but_admits_only_core_match -vv
```

- [ ] **Step 3: Implement exact freezing and projection**

Never normalize, append A, or compile after confirmation. Reject empty/stale commands before writes. Persist and read suggestion/final/origin directly.

- [ ] **Step 4: Run complete vertical proof and foundation smoke**

```bash
pytest -q tests/e2e/test_content_research_scope_api.py \
  tests/e2e/test_content_research_source_collection_api.py \
  tests/e2e/test_content_research_creator_browser.py::test_creator_executes_arbitrary_edited_query_but_admits_only_core_match
cd frontend && npm test
```

Run the global foundation smoke.

- [ ] **Step 5: Commit**

```bash
git add app/content_research frontend/src/app/creator tests/e2e
git commit -m "feat(content-research): execute exact user-edited queries"
```

### Task 5 / Vertical Slice 4: Execute truthful Coverage decisions from Creator

**Outcome:** When v2 collection is Limited by required-A/sample/author thresholds, Creator shows the authoritative Coverage snapshot and the existing Expand/Relax action runs through the real backend before the same card refreshes.

**Contracts:** `AUTH-QP-2`, `INV-QP-3`, `FAIL-QP-4`, `ACC-QP-4`, `STATE-5-2`, `AUTH-5-1`–`AUTH-5-3`, `INV-5-2`, `FAIL-5-2`, `ACC-5-2`.

**Transition:** v2 Scope with Limited Coverage → existing Expand or Relax action → exact run/snapshot guard → worker/recalculation → successor Coverage/report projection.

**Authority / transaction:** The command targets the exact active run, Scope and Coverage snapshot. Stale, duplicate or Run A commands cannot mutate Run B. Missing B/C is never unmet required coverage.

**Side effect:** Expand uses the owned worker and recording provider. Relax changes only server-authorized policy and recomputes from authoritative evidence.

**Read / UI projection:** Reuse current UI components and buttons. Scope, Coverage, Trace and report refresh from server state; Creator never invents an optimistic terminal result.

**Failure rows:** Stale snapshot, duplicate click, provider failure/unknown outcome, late Run A refresh and insufficient post-expand evidence stay truthful and retryable.

**Acceptance RED:** `test_creator_expand_reaches_worker_and_refreshes_real_result` crosses Creator/Router/SQLite/worker/provider/Coverage/report/Trace. Limited/Relax retain real Router integration plus frontend payload evidence required by `ACC-5-2`.

**Deployment safety:** Extends the complete v2 path without new stages or UI components. Failed commands preserve the last authoritative snapshot.

**Files:** Modify `app/content_research/service.py`, `app/content_research/reporting/lite_read_model.py`, the exact worker/Coverage owner recorded by Task 1's manifest, `frontend/src/app/creator/page.tsx`, `frontend/src/app/creator/page.test.tsx`, `tests/e2e/test_content_research_scope_api.py`, `tests/e2e/test_content_research_source_collection_api.py`, and `tests/e2e/test_content_research_creator_browser.py`.

- [ ] **Step 1: Write the Expand browser Acceptance RED and Limited/Relax focused REDs**

Seed Limited Coverage with A matched but sample/author thresholds unmet. Invoke Expand and assert exact run/snapshot payloads, provider calls, and refreshed authoritative Coverage/Trace/report. Retain real Router decision-identity tests and frontend payload tests for Limited/Relax; do not create new UI.

- [ ] **Step 2: Write stale, duplicate and provider-failure REDs**

Assert stale Run A or predecessor snapshot commands create zero Run B deltas; duplicate Expand dispatches once; provider failure remains visible and never publishes success.

- [ ] **Step 3: Run REDs and implement guarded full-stack actions**

Use existing Router action names and UI components. Do not add a lifecycle stage, replacement button, or client-owned Coverage calculation.

- [ ] **Step 4: Run complete vertical proof and foundation smoke**

```bash
pytest -q tests/e2e/test_content_research_scope_api.py -k 'limited or expand or relax' \
  tests/e2e/test_content_research_source_collection_api.py \
  tests/e2e/test_content_research_creator_browser.py::test_creator_expand_reaches_worker_and_refreshes_real_result
cd frontend && npm test
```

Run the global foundation smoke.

- [ ] **Step 5: Commit**

```bash
git add app/content_research frontend/src/app/creator tests/e2e
git commit -m "feat(content-research): execute truthful v2 coverage decisions"
```

### Task 6 / Vertical Slice 5: Preserve v1 history and recovery beside current v2

**Outcome:** In one Creator thread, historical Run A/Scope v1 remains readable and recoverable under frozen v1 semantics while current Run B uses v2; neither alters the other's authority, Coverage, Trace or report.

**Contracts:** `AUTH-QP-4`, `FAIL-QP-5`, `ACC-QP-5`, `STATE-5-7`, `AUTH-5-6`, `FAIL-5-8`, `ACC-5-7`.

**Transition:** load/reload/replay/Expand/Relax with v1 history and active v2 → version-owned recovery → unchanged persisted contracts and truthful current projection.

**Authority / transaction:** Persisted schema version selects immutable decoding/recovery. Reads never migrate or reinterpret. Task 2's current-run precedence remains authoritative.

**Side effect:** Recovery sends queries frozen in the targeted version through real worker and recording provider.

**Read / UI projection:** Timeline opens Run A read-only; returning current restores Run B Scope/Trace/report and version-specific limitations.

**Failure rows:** Newest Run A report cannot replace Run B; unreadable v1 shows error without mutation; late Run A recovery cannot paint Run B.

**Acceptance RED:** `test_creator_keeps_v1_history_frozen_beside_current_v2_recovery` crosses Creator/Router/SQLite/worker/provider/versioned Scope/Coverage/report read models.

**Deployment safety:** Remove remaining new-run v1 creation only after historical v1 read/replay/recovery passes; never rewrite stored rows.

**Files:** Modify `app/content_research/scope_contract.py`, `app/content_research/service.py`, `app/content_research/reporting/lite_read_model.py`, `frontend/src/app/creator/page.tsx`, `tests/integration/test_content_research_scope_contract_store.py`, `tests/e2e/test_content_research_scope_api.py`, and `tests/e2e/test_content_research_creator_browser.py`.

- [ ] **Step 1: Write mixed-version browser Acceptance RED**

Seed Run A v1/report, create Run B v2, replay or Expand Run A, return/reload, and assert provider calls/Coverage use each frozen version while current panels return to Run B.

- [ ] **Step 2: Write immutable store/recovery REDs**

Snapshot serialized v1/v2 rows before reads/recovery and assert byte-equivalent contract/constraint/query payloads afterward.

- [ ] **Step 3: Run REDs, centralize version dispatch, remove new-run v1 creation**

Historical v1 executes its own frozen required constraints; new product-marketing preparation always creates v2.

- [ ] **Step 4: Run mixed-version/current-run proof and foundation smoke**

```bash
pytest -q tests/integration/test_content_research_scope_contract_store.py \
  tests/e2e/test_content_research_scope_api.py -k 'limited or expand or relax or v1 or replay' \
  tests/e2e/test_content_research_creator_browser.py::test_creator_keeps_v1_history_frozen_beside_current_v2_recovery \
  tests/e2e/test_content_research_creator_browser.py::test_creator_historical_run_never_overrides_durable_active_run_after_brief_confirmation
```

Run the global foundation smoke.

- [ ] **Step 5: Commit**

```bash
git add app/content_research frontend/src/app/creator/page.tsx tests/integration \
  tests/e2e/test_content_research_scope_api.py tests/e2e/test_content_research_creator_browser.py
git commit -m "fix(content-research): preserve version-owned scope recovery"
```

### Task 7: Final release verification and evidence capture

**Outcome:** Re-run evidence owned by Tasks 2–6, compare complete suites with Task 1 baseline, and perform an authenticated canary. This Task cannot introduce or repair lifecycle behavior.

**Files:** Modify `docs/release/2026-08-15-f003-test-evidence.md`; implementation is verify-only.

- [ ] **Step 1: Re-run every browser journey owned by the five slices and foundation smoke**

```bash
pytest -q \
  tests/e2e/test_content_research_creator_browser.py::test_creator_historical_run_never_overrides_durable_active_run_after_brief_confirmation \
  tests/e2e/test_content_research_creator_browser.py::test_creator_confirms_suggested_v2_portfolio_and_provider_receives_exact_queries \
  tests/e2e/test_content_research_creator_browser.py::test_creator_completes_missing_aspects_and_only_latest_draft_executes \
  tests/e2e/test_content_research_creator_browser.py::test_creator_executes_arbitrary_edited_query_but_admits_only_core_match \
  tests/e2e/test_content_research_creator_browser.py::test_creator_expand_reaches_worker_and_refreshes_real_result \
  tests/e2e/test_content_research_creator_browser.py::test_creator_keeps_v1_history_frozen_beside_current_v2_recovery
```

Failures return to the owning Task; do not patch lifecycle behavior only here.

- [ ] **Step 2: Run build and touched-area suites**

```bash
cd frontend && npm run build
pytest -q tests/unit/test_content_research_query_planner.py \
  tests/unit/test_content_research_scope_contract.py \
  tests/unit/test_content_research_llm_scope.py \
  tests/integration/test_content_research_scope_contract_store.py \
  tests/e2e/test_content_research_brief_confirm_api.py \
  tests/e2e/test_content_research_scope_api.py \
  tests/e2e/test_content_research_trace_api.py \
  tests/e2e/test_content_research_model_configuration_api.py \
  tests/e2e/test_xhs_qr_login_api.py
```

- [ ] **Step 3: Run complete suites as delta gate**

```bash
pytest -q
cd frontend && npm test
```

New failures, increased failure count, touched-file failures, and all Lane A/B failures block release. Unchanged unrelated Lane D failures are recorded separately.

- [ ] **Step 4: Run authenticated canary**

With validated LLM and authenticated XHS, create Run B beside Run A, confirm v2, collect detail, edit one query, replace one missing aspect, recover v1 once, reload, and record redacted evidence:

```text
active_run_id = Run B
Scope schema = content_research_scope_contract_v2
provider queries = frozen final queries
candidate required constraint = A only
Trace run = Run B
LLM status = validated (key redacted)
XHS status = authenticated (Cookie redacted)
historical Run A = readable with frozen v1 semantics
```

- [ ] **Step 5: Commit evidence**

```bash
git add docs/release/2026-08-15-f003-test-evidence.md
git commit -m "docs(content-research): record final vertical-slice evidence"
```

## Plan Readiness

**READY.** Seven Tasks contain one delivery-control exception, five closed browser-to-owned-stack slices, and one release-only evidence gate. There is no dormant framework Task, backend-only product checkpoint, or deferred first E2E. Tasks 2–6 each introduce required framework and implementation behind one Acceptance RED, prove real Creator/Router/SQLite/worker/read-model composition, and run Trace/LLM/XHS foundation smoke before checkpoint. Task 7 only broadens evidence and cannot repair behavior.
