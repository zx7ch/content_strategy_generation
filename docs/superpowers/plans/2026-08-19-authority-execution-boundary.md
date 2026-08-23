# Authority Execution Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an unresolved coverage decision produce a persisted, idempotent execution authorization that safely resumes the correct content-research workflow, while preserving an unchanged Scope for supplementary collection.

**Architecture:** `ScopeContract` remains the immutable meaning of research. `CoverageResolution` records the human decision; a new persisted execution authorization/revision references that decision and is the only authority that may queue continuation. Scope projection returns an unconfirmed Draft as a first-class state, and frontend commands/rendering are driven by server-provided allowed actions bound to one workflow run.

**Tech Stack:** Python/FastAPI/Pydantic, SQLite stores and migrations, TypeScript/Next.js, pytest, Node test runner.

## Global Constraints

- Do not create a new Scope revision merely because the user requests supplementary collection in the same research meaning.
- Create a new Scope revision only when a user changes research semantics: object/context, constraint value or mode, or final-query semantics.
- A single backend command owns decision persistence, execution authorization, and workflow continuation; its SQLite transaction persists the decision/authorization first, then its idempotent continuation step queues or resumes work. The frontend must not reconstruct this sequence through old start/report commands.
- All command and projection facts are bound to `workflow_run_id`; async UI responses for a stale run must not modify current UI state.
- Every collection, retry, governance, and report entrypoint verifies confirmed Scope plus active execution authorization where continuation is required.
- Preserve current Scope confirmation SQLite atomicity; do not broaden this task into a global sqlite3/aiosqlite transaction refactor.

---

### Task 1: Persist and execute an atomic coverage resolution

**Files:**
- Modify: `app/content_research/scope_contract.py`
- Modify: `app/content_research/stores/base.py`
- Modify: `app/content_research/stores/sqlite_store.py`
- Modify: `app/content_research/async_pipeline_store.py`
- Modify: `app/content_research/service.py`
- Modify: `app/content_research/api_schemas.py`
- Modify: `app/content_research/migrations.py`
- Test: `tests/e2e/test_content_research_scope_api.py`
- Test: `tests/integration/test_content_research_lite_read_model.py`

**Interfaces:**
- Produces an append-only `ScopeExecutionAuthorization` containing `id`, `workflow_run_id`, `scope_contract_id`, `scope_contract_version`, `coverage_snapshot_id`, `resolution`, `execution_revision`, `state`, and `created_at`.
- Produces `resolve_coverage_atomically(...)` in the store: one transaction persists the resolution audit, authorization, and any semantic Scope successor; it returns an existing matching authorization on replay and rejects a different decision for the same snapshot.
- Produces a `resolve_coverage` action result with `scope_contract`, `execution_authorization`, `report_mode`, `unmet_constraint_ids`, `allowed_resolutions`, and audit event.

- [ ] **Step 1: Write failing persistence and action tests**

```python
result = await client.post(actions_url, json={"action": "resolve_coverage", "payload": limited_payload})
assert result.json()["result"]["execution_authorization"]["resolution"] == "generate_limited_report"
assert store.list_scope_execution_authorizations(run_id)[0].state == "authorized_limited_report"
```

Add equivalent tests for `expand_required_constraint` and `relax_constraint`: expand retains the same Scope Contract and creates an authorization to collect; relax creates a semantic Scope successor and an authorization bound to that successor. Repeat the identical request and assert one authorization and one audit event; submit a different decision for the same coverage snapshot and assert validation failure.

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/e2e/test_content_research_scope_api.py tests/integration/test_content_research_lite_read_model.py -q`

Expected: FAIL because the authorization model/store operation and response facts do not exist.

- [ ] **Step 3: Add schema, migration, store transaction, and service continuation**

```python
authorization = store.resolve_coverage_atomically(
    workflow_run_id=workflow_run_id,
    snapshot_id=snapshot.id,
    resolution=request.resolution,
    successor_scope=successor_scope_or_none,
)
await workflow_runtime.continue_from_coverage_resolution(authorization)
```

Use the transaction to write the resolution audit and authorization before invoking dispatch. For `generate_limited_report`, authorize compose/publication. For same-Scope expansion, authorize and queue collection using the existing Contract; for relaxation, create the successor Contract and authorize collection only for that successor. Persist enough state that retrying after a dispatch failure returns a recoverable pending authorization rather than creating another decision.

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `pytest tests/e2e/test_content_research_scope_api.py tests/integration/test_content_research_lite_read_model.py -q`

Expected: PASS, including replay and different-decision conflict cases.

- [ ] **Step 5: Commit**

```bash
git add app/content_research tests/e2e/test_content_research_scope_api.py tests/integration/test_content_research_lite_read_model.py
git commit -m "feat(content-research): authorize coverage continuations"
```

### Task 2: Project Draft state and allowed server actions

**Files:**
- Modify: `app/content_research/api_schemas.py`
- Modify: `app/content_research/service.py`
- Modify: `app/api/routes/router.py`
- Test: `tests/e2e/test_content_research_scope_api.py`

**Interfaces:**
- `GET /content-research/workflows/{id}/scope` returns `state: "draft" | "confirmed" | "superseded"`, its persisted Draft, optional Contract, and `allowed_actions` with required payload fields/reasons.
- `CoverageSnapshot` projection exposes `allowed_resolutions`; `expand_required_constraint` appears only when at least one unmet required constraint exists.

- [ ] **Step 1: Write failing read-model tests**

```python
scope = (await client.get(scope_url)).json()
assert scope["state"] == "draft"
assert scope["draft"]["id"] == prepared_draft["id"]
assert scope["scope_contract"] is None
assert "confirm_scope" in scope["allowed_actions"]
```

Add tests that confirmed Scope retains its existing projection and that a coverage snapshot missing only sample/author thresholds omits expand/relax with an explicit unavailable reason.

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/e2e/test_content_research_scope_api.py -q`

Expected: FAIL because `get_scope_projection` raises when no Contract exists and because allowed actions are absent.

- [ ] **Step 3: Implement stateful projection**

```python
return ContentResearchScopeProjectionResponse(
    workflow_run_id=workflow_run_id,
    state="draft" if contract is None else "confirmed",
    draft=_scope_draft_payload(draft),
    scope_contract=_scope_contract_payload(contract) if contract else None,
    allowed_actions=allowed_scope_actions(...),
)
```

Keep Draft and Contract audit events separate but return the events applicable to the selected projection. The route remains read-only and must not repair or confirm a Draft as a side effect.

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `pytest tests/e2e/test_content_research_scope_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/content_research app/api/routes/router.py tests/e2e/test_content_research_scope_api.py
git commit -m "feat(content-research): project pending scope actions"
```

### Task 3: Bind continuation entrypoints to execution authority

**Files:**
- Modify: `app/content_research/service.py`
- Modify: `app/content_research/lite_report.py`
- Modify: `app/content_research/workflow_runtime.py` or the current runtime adapter owning collection/retry dispatch
- Test: `tests/e2e/test_content_research_scope_api.py`
- Test: `tests/e2e/test_content_research_report_publication_timeline_api.py`

**Interfaces:**
- Continuation commands accept/resolve an active `ScopeExecutionAuthorization` for the requested run.
- Collection/retry and limited report/publication reject a workflow with `awaiting_scope_decision` unless the matching authorization exists and has the required state.

- [ ] **Step 1: Write failing bypass tests**

```python
with pytest.raises(ContentResearchValidationError, match="execution authorization"):
    await service.start_formal_research(workflow_run_id=run_id)
```

Test the legacy start/retry/report paths for a run awaiting coverage decision, then resolve coverage and assert only the matching authorized path succeeds.

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/e2e/test_content_research_scope_api.py tests/e2e/test_content_research_report_publication_timeline_api.py -q`

Expected: FAIL because existing legacy paths only inspect the Contract or workflow state.

- [ ] **Step 3: Implement one authority guard at the runtime boundary**

```python
authorization = store.get_active_scope_execution_authorization(workflow_run_id)
if authorization is None or not authorization.allows(operation):
    raise ContentResearchValidationError("This operation requires an active execution authorization")
```

Call the same guard from collection dispatch/retry, governance continuation, and limited report generation; do not duplicate state inference in HTTP routes. Normal initial collection after `confirm_scope` continues to use its initial authorization path.

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `pytest tests/e2e/test_content_research_scope_api.py tests/e2e/test_content_research_report_publication_timeline_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/content_research tests/e2e/test_content_research_scope_api.py tests/e2e/test_content_research_report_publication_timeline_api.py
git commit -m "fix(content-research): gate continuation on execution authority"
```

### Task 4: Drive Creator UI from persisted Scope projection

**Files:**
- Modify: `frontend/src/lib/content-research-api.ts`
- Modify: `frontend/src/lib/content-research-api.test.ts`
- Modify: `frontend/src/app/creator/page.tsx`
- Test: `tests/e2e/test_content_research_creator_browser.py`

**Interfaces:**
- API types represent nullable Contract, Scope state, allowed actions, allowed resolutions, and execution authorization returned from `resolve_coverage`.
- Creator state is keyed by `workflow_run_id`; each async load uses a monotonically increasing request token and discards responses no longer belonging to the selected run.

- [ ] **Step 1: Write failing frontend and browser tests**

```ts
const projection = await getContentResearchScope(runId);
assert.equal(projection.state, "draft");
assert.equal(projection.scope_contract, null);
```

Add browser coverage for reload after prepare before confirm, a threshold-only gap where Expand is disabled with its server reason, and switching to a second run before the first Scope response resolves.

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `npm test -- content-research-api.test.ts` and `pytest tests/e2e/test_content_research_creator_browser.py -q`

Expected: FAIL because the UI clears Draft on a 404, renders locally inferred coverage actions, and accepts stale responses.

- [ ] **Step 3: Implement projection-driven, run-scoped UI**

```ts
const token = ++scopeRequestToken.current;
const projection = await getContentResearchScope(runId);
if (token !== scopeRequestToken.current || runId !== selectedRunIdRef.current) return;
setScopeProjection(projection);
```

Render the persisted Draft confirmation card whenever `state === "draft"`. Render only server-advertised coverage actions; calling resolve coverage refreshes projection and workflow state rather than invoking legacy start/report endpoints from the browser.

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `npm test -- content-research-api.test.ts` and `pytest tests/e2e/test_content_research_creator_browser.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/content-research-api.ts frontend/src/lib/content-research-api.test.ts frontend/src/app/creator/page.tsx tests/e2e/test_content_research_creator_browser.py
git commit -m "fix(creator): restore and continue authorized scope workflows"
```

### Cross-task acceptance gate: Creator user journeys

**Purpose:** This is not a fifth domain change. It is the mandatory user-facing acceptance gate after Tasks 1–4, because the boundaries cross API, persistence, worker execution, and Creator state.

**Files:**
- Test: `tests/e2e/test_content_research_creator_browser.py`
- Test: `tests/e2e/test_content_research_authorized_continuation_e2e.py`
- Test: `frontend/src/app/creator/page.test.tsx`

**Required journeys:**

1. **Draft survives refresh:** prepare Scope, reload/re-enter the same Creator run, see the editable Draft and its original query values, confirm it, then start initial collection. No 404, lost form, or duplicate Contract.
2. **Initial insufficient coverage is actionable:** complete initial collection with a real initial `awaiting_scope_decision` snapshot. The Creator shows server-declared choices. If the unmet issue is only sample/author thresholds, Expand/Relax are unavailable with a reason and Limited Report remains available.
3. **Expand same Scope:** choose a valid unmet required constraint and supplementary query. The UI does not call legacy start/retry. It displays continuation progress; real worker execution reaches the provider with that persisted query, then returns a new authorization-bound Coverage Snapshot while the Scope revision remains unchanged.
4. **Relax semantic Scope:** choose a valid unmet required constraint to relax. The UI creates/observes a successor Scope revision, waits for the server-owned continuation, and never mixes the prior run's Coverage or report into the new revision.
5. **Limited report and replay:** choose Limited Report, simulate a lost action response, reload/retry the exact action, and show one completed limited report with its limitation text. There must be no duplicate publication or dead-end `running` UI state.
6. **Run switch race:** start a delayed Scope/Report load for run A, switch to run B, resolve A late, and assert all cards/messages/progress remain bound to B.
7. **Legacy-path regression:** initial confirmed Scope still begins normal collection; an `awaiting_scope_decision` run cannot be advanced through legacy start/retry/report endpoints without its execution authorization.

**Acceptance rules:**

- Browser tests assert visible card state, enabled/disabled controls, explanatory unavailable reason, progress/retry behavior, and final report identity—not React internals alone.
- Backend E2E uses the real command → worker → router/pipeline → persistence path with deterministic fake provider/runtime seams; it must not replace `_execute_formal_research` for these journeys.
- A journey passes only if every projection, action response, worker result, and displayed report has the same `workflow_run_id`, Scope revision, and applicable execution authorization/revision.
- Run the complete gate after Task 4 and before branch-level review:

```bash
pytest tests/e2e/test_content_research_creator_browser.py tests/e2e/test_content_research_authorized_continuation_e2e.py -q
npm test -- frontend/src/app/creator/page.test.tsx frontend/src/lib/content-research-api.test.ts
```

## Plan self-review

- Scope coverage: Tasks 1–3 close the backend authority chain, recovery, idempotency, and bypass checks. Task 2 restores the pending Draft read state and server action contract. Task 4 removes local lifecycle inference and stale-run UI writes.
- Intentional exclusion: a global SQLite write-path refactor is excluded; each new decision write is explicitly atomic.
- Terminology check: Scope revision appears only for a semantic Scope change. Same-Scope supplementary collection creates an execution authorization/revision, not a Scope successor.
- Coupling check: the cross-task acceptance gate exercises persisted facts, workflow runtime, worker continuation, API projection, and Creator rendering together; a task is not release-ready merely because its local suite passes.
