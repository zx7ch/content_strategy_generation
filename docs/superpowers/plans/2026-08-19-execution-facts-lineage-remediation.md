# Content Research Execution Facts and Lineage Remediation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the existing Scope authority design so that one persisted human decision owns one observable, lease-fenced execution unit and only its proven evidence, coverage, and report can become visible to a user.

**Architecture:** A stable `execution_unit_id` represents one accepted coverage decision and its resulting work. An internal `attempt_no` and `lease_token` fence a particular worker attempt; they do not create new user-facing Scope meanings. All collection, coverage, governance, report, publication, and trace writes carry an immutable execution context and are conditional on the active lease. Trace is a read-only projection of durable execution facts, not a reconstruction from the current Scope or in-memory worker state.

**Tech Stack:** Python/FastAPI/Pydantic, SQLite with `sqlite3` and `aiosqlite`, async worker, pytest, TypeScript/Next.js browser tests.

**Spec:** [Authority lifecycle design](../../release/2026-08-16-content-research-authority-lifecycle.html) and [existing authority-execution plan](2026-08-19-authority-execution-boundary.md). This plan supersedes the unfinished Task 3 only; existing Scope contract versions retain their current meaning: a user-initiated semantic relaxation, never a label for this remediation.

## Global Constraints

- Do not create or name a new Scope revision merely to deploy this remediation. A Scope contract changes only when the user changes research semantics.
- `execution_unit_id` is stable for an exact replay of the same human decision. A retry creates only `attempt_no + 1` inside that unit.
- Every provider-side request uses `execution_unit_id` and `attempt_no` as its correlation key when the adapter supports it.
- A worker may mutate an execution unit only while it owns the exact live `lease_token`. Conditional writes that lose the lease must make no domain artifact and record a durable `fenced` execution fact.
- A crash after a provider request is started but before an outcome is durable is `outcome_unknown`; it must not be automatically replayed.
- Scope, evidence, coverage, report, publication, and trace reads must select explicit persisted lineage. They must never infer provenance from the latest Scope Contract or all records in a workflow.
- Preserve unrelated user changes and do not broaden this work into a general sqlite3/aiosqlite refactor.

## Execution-fact vocabulary and invariant

| Term | Identity | Meaning |
|---|---|---|
| Scope Contract | existing `scope_contract_id` | Immutable research semantics; changes only for an actual user semantic relaxation. |
| Execution Unit | `execution_unit_id` | One accepted coverage decision and all work it authorizes. |
| Attempt | `(execution_unit_id, attempt_no)` | One concrete worker attempt; retries advance only `attempt_no`. |
| Lease | `(execution_unit_id, attempt_no, lease_token)` | Temporary right to make writes for the claimed attempt. |
| Execution Fact | durable ordered row | Command accepted, claimed, provider request recorded, outcome, fenced write, coverage/report/publication created. |

Invariant: **one immutable user decision → one execution unit → one or more lease-fenced attempts → only facts belonging to the successful attempt may feed the resulting Coverage or publication.**

---

### Task 1: Persist execution units, attempts, and truthful trace facts

**Files:**
- Modify: `app/content_research/scope_contract.py`
- Modify: `app/content_research/persistence_models.py`
- Modify: `app/content_research/migrations.py`
- Modify: `app/content_research/stores/base.py`
- Modify: `app/content_research/stores/sqlite_store.py`
- Modify: `app/content_research/async_pipeline_store.py`
- Modify: `app/content_research/async_dispatch.py`
- Test: `tests/integration/test_content_research_scope_store.py`
- Test: `tests/unit/test_content_research_dispatch_worker.py`

**Interfaces:**
- Produce immutable `ScopeExecutionUnit(id, workflow_run_id, scope_contract_id, coverage_snapshot_id, resolution, operation, state, created_at)`.
- Produce `ScopeExecutionAttempt(execution_unit_id, attempt_no, state, lease_owner, lease_token, lease_expires_at, provider_state, created_at)`.
- Produce `ExecutionFact(execution_unit_id, attempt_no, sequence_no, kind, payload, created_at)` where `kind` is one of `decision_accepted`, `attempt_claimed`, `provider_request_recorded`, `provider_outcome_recorded`, `lease_fenced`, `coverage_persisted`, `publication_persisted`, or `outcome_unknown`.
- Replace caller-visible continuation identity with `execution_unit_id`; retain old authorization/continuation rows only as migration-compatible aliases until all readers use the new interface.
- Store interface exposes a small deep seam: `claim_execution_unit`, `renew_execution_unit_lease`, `record_provider_request`, `record_provider_outcome`, `complete_execution_unit`, and `execution_trace`.

- [ ] **Step 1: Write failing store tests for stable decision identity and ordered facts**

```python
unit, created = store.resolve_coverage_to_execution_unit_atomically(
    snapshot=snapshot, decision=expand_decision
)
replayed, replay_created = store.resolve_coverage_to_execution_unit_atomically(
    snapshot=snapshot, decision=expand_decision
)
assert unit.id == replayed.id
assert created is True and replay_created is False
assert [fact.kind for fact in store.execution_trace(unit.id)] == ["decision_accepted"]
```

Add a competing-connection SQLite test: only one execution unit is created for the identical decision; a different decision for the same snapshot raises a conflict. Add a trace ordering test that rejects duplicate `(execution_unit_id, attempt_no, sequence_no)`.

- [ ] **Step 2: Run the RED store tests**

Run: `pytest tests/integration/test_content_research_scope_store.py -q`

Expected: FAIL because execution-unit and execution-fact storage does not exist.

- [ ] **Step 3: Add models, migration, atomic creation, and compatibility readers**

Create SQLite tables for execution units, attempts, and facts. Index `(workflow_run_id, scope_contract_id)`, `(execution_unit_id, attempt_no)`, and `(execution_unit_id, attempt_no, sequence_no)`. In the existing coverage-resolution transaction, write the decision audit, execution unit, initial `decision_accepted` fact, and pending attempt record atomically. Backfill existing authorizations/continuations as one unit and attempt zero without reinterpreting any Scope contract.

```python
def resolve_coverage_to_execution_unit_atomically(...) -> tuple[ScopeExecutionUnit, bool]:
    # BEGIN IMMEDIATE; validate snapshot and exact decision fingerprint.
    # Insert-or-return the same unit, then append decision_accepted once.
```

- [ ] **Step 4: Run focused persistence tests**

Run: `pytest tests/integration/test_content_research_scope_store.py tests/e2e/test_content_research_scope_api.py -q`

Expected: PASS, including exact decision replay and migration backfill.

- [ ] **Step 5: Commit**

```bash
git add app/content_research tests/integration/test_content_research_scope_store.py tests/e2e/test_content_research_scope_api.py
git commit -m "feat(content-research): persist execution unit facts"
```

### Task 2: Fence all worker and provider side effects by the live attempt lease

**Files:**
- Modify: `app/content_research/worker.py`
- Modify: `app/content_research/async_dispatch.py`
- Modify: `app/content_research/service.py`
- Modify: `app/content_research/workflow_runtime.py` or the current runtime adapter owning provider dispatch
- Modify: `app/content_research/stores/base.py`
- Modify: `app/content_research/stores/sqlite_store.py`
- Test: `tests/unit/test_content_research_dispatch_worker.py`
- Test: `tests/e2e/test_content_research_authorized_continuation_e2e.py`

**Interfaces:**
- `execute_execution_unit(claim: ExecutionUnitClaim)` requires `state == "running"` and the exact owner/token/current attempt.
- `ExecutionContext(execution_unit_id, attempt_no, lease_token, scope_contract_id)` is passed to collection, checkpoint, Coverage, governance, and report calls; it is not accepted from HTTP payloads.
- `record_provider_request` commits intent before adapter invocation; `record_provider_outcome` commits `succeeded`, `retryable_failed`, `terminal_failed`, or `outcome_unknown` after it.

- [ ] **Step 1: Write RED tests for stale worker fencing and unknown outcomes**

```python
claim_a = await repository.claim_execution_unit(owner="A", lease_seconds=0)
claim_b = await repository.claim_execution_unit(owner="B", lease_seconds=120)
await service.execute_execution_unit(claim_a)
assert store.execution_trace(unit.id)[-1].kind == "lease_fenced"
assert store.get_coverage_for_unit(unit.id) is None
```

Add an adapter that raises after its request is recorded but before a response is available. Assert the attempt ends `outcome_unknown`, an exact replay does not invoke the adapter again, and the projection exposes an explicit recovery state. Add the equivalent test for a known retryable timeout: exact replay uses the same unit and attempt number increments.

- [ ] **Step 2: Run RED worker/E2E tests**

Run: `pytest tests/unit/test_content_research_dispatch_worker.py tests/e2e/test_content_research_authorized_continuation_e2e.py -q`

Expected: FAIL because current workers fence only their final continuation row.

- [ ] **Step 3: Pass an execution context through every mutating path**

Move continuation ownership validation to the execution seam before `_recover_interrupted_tasks`. Make recovery accept an `ExecutionContext` and only recover tasks belonging to its current attempt. Before every checkpoint, packet, Coverage, governance, report, or publication write, invoke one store-side conditional mutation that checks the live lease. On loss, append `lease_fenced` using a transaction that cannot create domain output.

```python
claim = await repository.claim_execution_unit(...)
await service.execute_execution_unit(claim)  # service re-validates exact lease
```

Only requeue `retryable_failed`; never auto-requeue `outcome_unknown` or terminal failures. The command/replay route returns the durable recovery state instead of creating a second unit.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/unit/test_content_research_dispatch_worker.py tests/e2e/test_content_research_authorized_continuation_e2e.py -q`

Expected: PASS with provider-called-once unknown outcome, provider-called-twice known retryable failure, and zero stale-worker artifacts.

- [ ] **Step 5: Commit**

```bash
git add app/content_research tests/unit/test_content_research_dispatch_worker.py tests/e2e/test_content_research_authorized_continuation_e2e.py
git commit -m "fix(content-research): fence execution unit attempts"
```

### Task 3: Make Scope eligibility and evidence/Coverage ownership lineage-wide

**Files:**
- Modify: `app/content_research/service.py`
- Modify: `app/content_research/directional_pipeline.py`
- Modify: `app/content_research/persistence_models.py`
- Modify: `app/content_research/stores/base.py`
- Modify: `app/content_research/stores/sqlite_store.py`
- Modify: `app/content_research/migrations.py`
- Test: `tests/e2e/test_content_research_scope_api.py`
- Test: `tests/e2e/test_content_research_authorized_continuation_e2e.py`

**Interfaces:**
- Add immutable execution ownership fields to formal packets/checkpoints/claims: `scope_contract_id`, `execution_unit_id`, `attempt_no`, and `execution_revision` where the existing projection requires it.
- `initial_execution_eligibility(workflow_run_id, scope_contract_id)` returns allowed only if no earlier formal Coverage, authorization/unit, execution attempt, or governed artifact exists for that workflow lineage.
- `persist_scope_coverage_evaluation(context, manifest)` evaluates only records matching the context’s Scope and successful attempt, never all workflow packets.

- [ ] **Step 1: Write RED tests for the Scope reset bypass and mixed evidence**

```python
await create_unresolved_coverage(run_id, original_scope)
await confirm_semantically_relaxed_scope(run_id)
with pytest.raises(ContentResearchValidationError, match="unresolved coverage decision"):
    await service.start_formal_research(workflow_run_id=run_id)
assert provider.calls == []
assert store.list_result_snapshots_for_workflow(run_id) == []
```

Create a packet under an old Scope and a packet under the current execution unit. Persist Coverage for the latter and assert only its packet IDs/counts are used. Add a stale normal dispatch recovery test that verifies no task status, provider call, checkpoint, Coverage, governance record, or report changes.

- [ ] **Step 2: Run RED lineage tests**

Run: `pytest tests/e2e/test_content_research_scope_api.py tests/e2e/test_content_research_authorized_continuation_e2e.py -q`

Expected: FAIL because eligibility currently infers only from the latest Scope and governance reads all workflow records.

- [ ] **Step 3: Enforce workflow-wide eligibility and explicit manifests**

At scope confirmation and execution entrypoints, inspect the full persisted workflow lineage. A later semantic Scope contract cannot reset a prior unresolved decision. It must receive its own explicitly authorized initial execution only after the previous unit is terminally resolved/superseded according to the existing user decision rules.

Build an execution manifest from `ExecutionContext`; all downstream queries filter on it. Persist the manifest’s packet/checkpoint IDs with Coverage and reject a Coverage write whose authorization, Scope, revision, source snapshot, or packet ownership does not match.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/e2e/test_content_research_scope_api.py tests/e2e/test_content_research_authorized_continuation_e2e.py -q`

Expected: PASS, including zero-write stale jobs and no cross-Scope evidence inclusion.

- [ ] **Step 5: Commit**

```bash
git add app/content_research tests/e2e/test_content_research_scope_api.py tests/e2e/test_content_research_authorized_continuation_e2e.py
git commit -m "fix(content-research): bind coverage to execution lineage"
```

### Task 4: Freeze report and publication lineage, and expose a truthful trace projection

**Files:**
- Modify: `app/content_research/service.py`
- Modify: `app/content_research/persistence_models.py`
- Modify: `app/content_research/reporting/composer.py`
- Modify: `app/content_research/reporting/read_model.py`
- Modify: `app/content_research/stores/base.py`
- Modify: `app/content_research/stores/sqlite_store.py`
- Modify: `app/content_research/migrations.py`
- Modify: `app/content_research/api_schemas.py`
- Test: `tests/e2e/test_content_research_report_publication_timeline_api.py`
- Test: `tests/integration/test_content_research_lite_read_model.py`

**Interfaces:**
- Governed snapshot metadata and `ReportDraftRecord`, `ReportFaithfulnessDecisionRecord`, and `ReportPublicationRecord` include `scope_contract_id`, `execution_unit_id`, `coverage_snapshot_id`, and successful `attempt_no`.
- `PublishedReportReader.read(publication_id)` renders Scope and trace solely from that publication’s frozen governed snapshot.
- `ExecutionTraceReader.read(execution_unit_id)` returns ordered durable facts and exposes `outcome_unknown`/`lease_fenced`; it never invents provider success from a current workflow status.

- [ ] **Step 1: Write RED report-lineage and trace tests**

```python
first = await publish_for_scope(run_id, original_scope)
second = await publish_for_scope(run_id, relaxed_scope)
assert first.publication_id != second.publication_id
assert (await report_reader.read(publication_id=first.publication_id))["scope_contract_id"] == original_scope.id
assert (await report_reader.read(publication_id=second.publication_id))["scope_contract_id"] == relaxed_scope.id
```

Add a test that equal claim text under distinct Scope contracts does not deduplicate publications. Add a trace test with `provider_request_recorded` and no outcome that returns `outcome_unknown`, rather than a completed report. Add a negative test that a publication cannot reference an unowned Coverage snapshot.

- [ ] **Step 2: Run RED report tests**

Run: `pytest tests/e2e/test_content_research_report_publication_timeline_api.py tests/integration/test_content_research_lite_read_model.py -q`

Expected: FAIL because input fingerprints and readers do not bind Scope/Coverage/execution-unit lineage.

- [ ] **Step 3: Persist and validate frozen lineage**

Extend governed-snapshot construction with the execution manifest and use those fields in `_governed_input_fingerprint`. Add foreign-lineage validation in report composer/materializer before drafts, audit decisions, publications, or artifacts are saved. The read model must return the snapshot’s frozen Scope payload, not fetch the newest contract for the workflow.

```python
if publication.execution_unit_id != snapshot.execution_unit_id:
    raise PublishedReportNotFoundError("publication execution lineage mismatch")
```

Build the public trace response from execution facts only, with a monotonic sequence number and safe public projection of diagnostic data.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/e2e/test_content_research_report_publication_timeline_api.py tests/integration/test_content_research_lite_read_model.py -q`

Expected: PASS, including historical Scope readback and truthful unknown-outcome trace.

- [ ] **Step 5: Commit**

```bash
git add app/content_research tests/e2e/test_content_research_report_publication_timeline_api.py tests/integration/test_content_research_lite_read_model.py
git commit -m "fix(content-research): freeze report execution lineage"
```

### Task 5: Make scope projection and Creator journeys execution-unit driven

**Contract Pack:** [`2026-08-22-task-5-creator-authority-contract.md`](../specs/2026-08-22-task-5-creator-authority-contract.md).

Task 5 is implemented as the four observable vertical slices in that Contract
Pack. The older Step 1–5 checklist below is retained as implementation
inventory only; it is not sufficient acceptance evidence by itself. In
particular, intercepted `/scope` or `/actions` browser tests are supplemental
UI tests, not proof of a Creator-to-owned-stack user journey.

**Files:**
- Modify: `app/content_research/api_schemas.py`
- Modify: `app/content_research/service.py`
- Modify: `frontend/src/lib/content-research-api.ts`
- Modify: `frontend/src/lib/content-research-api.test.ts`
- Modify: `frontend/src/app/creator/page.tsx`
- Test: `frontend/src/app/creator/page.test.tsx`
- Test: `tests/e2e/test_content_research_creator_browser.py`

**Interfaces:**
- Scope projection returns `execution_unit` with state, recovery state, allowed actions, and read-only trace summary; it does not expose lease tokens or accept attempt IDs from the browser.
- Creator stores run-scoped projections by `workflow_run_id`, uses a monotonic request epoch, and renders the server-declared recovery state.

- [ ] **Step 1: Write RED browser and API client tests**

```ts
const unit = await resolveCoverage(runId, expandPayload);
expect(unit.execution_unit.id).toEqual(replayed.execution_unit.id);
expect(unit.execution_unit.attempt_no).toBeGreaterThan(0);
expect(unit.execution_unit.lease_token).toBeUndefined();
```

Add browser journeys for: pending draft restored after reload; first unresolved Coverage exposes valid actions; known timeout then exact replay shows retry progress and one final report; unknown outcome shows a non-retryable recovery state; an old response after switching runs cannot replace the selected run’s cards, trace, or report.

- [ ] **Step 2: Run RED UI tests**

Run: `npm test -- content-research-api.test.ts page.test.tsx` and `pytest tests/e2e/test_content_research_creator_browser.py -q`

Expected: FAIL because projections expose authorizations/continuations rather than a stable execution unit and the UI has no unknown-outcome state.

- [ ] **Step 3: Implement projection-driven UI**

Map legacy authorization/continuation response fields to the execution-unit projection only at the backend compatibility edge. Replace browser-side start/retry/report reconstruction with one resolve/replay command plus projection refresh. Preserve current confirmed initial research journey.

- [ ] **Step 4: Run focused tests**

Run: `npm test -- content-research-api.test.ts page.test.tsx` and `pytest tests/e2e/test_content_research_creator_browser.py -q`

Expected: PASS, including no leaked lease data and stale-response suppression.

- [ ] **Step 5: Commit**

```bash
git add app/content_research frontend/src/lib/content-research-api.ts frontend/src/lib/content-research-api.test.ts frontend/src/app/creator/page.tsx frontend/src/app/creator/page.test.tsx tests/e2e/test_content_research_creator_browser.py
git commit -m "fix(creator): project execution unit recovery"
```

## Acceptance matrix (mandatory after Tasks 1–5)

| User journey / fault | Durable facts to assert | Worker/provider assertion | User-visible assertion |
|---|---|---|---|
| Exact duplicate coverage decision | One execution unit, one `decision_accepted`; replay references same ID | At most one active attempt | One progress card; no duplicate report/publication |
| Known retryable provider timeout | Same unit, a new attempt, ordered request/outcome facts | Provider called again only for the new attempt | Progress resumes; final Scope remains unchanged unless user changed semantics |
| Crash/unknown provider outcome | `provider_request_recorded`, then `outcome_unknown`; no retryable command | Provider not auto-called again | Explicit manual-recovery state, never a false completed result |
| Lease expiry and takeover | Old attempt emits/has `lease_fenced`; only new token writes artifacts | Old worker’s late callback changes no domain rows | No duplicate progress/report; trace explains handoff |
| Old unresolved Scope plus later semantic Scope confirmation | No fresh initial-execution eligibility for stale job | Stale normal dispatch/recovery makes zero provider calls | User cannot bypass the pending decision through old start/retry controls |
| Cross-execution packet injection | Coverage manifest contains only matching Scope/unit/attempt packet IDs | Wrong task packet ignored | Coverage reasons and report citations match the selected Scope |
| Historical report read | Separate frozen publications for different Scope contracts | No worker work required | Opening each report displays its own original Scope, Coverage, citations, and trace |
| Run switch / late browser response | No write side effect from reads | No extra dispatch from UI | Cards, messages, trace, and report stay on the selected run |

### Final verification and review gate

- [ ] Run backend focused suite:

```bash
pytest tests/integration/test_content_research_scope_store.py tests/unit/test_content_research_dispatch_worker.py tests/e2e/test_content_research_scope_api.py tests/e2e/test_content_research_authorized_continuation_e2e.py tests/e2e/test_content_research_report_publication_timeline_api.py tests/integration/test_content_research_lite_read_model.py -q
```

- [ ] Run frontend focused suite:

```bash
npm test -- content-research-api.test.ts page.test.tsx
pytest tests/e2e/test_content_research_creator_browser.py -q
```

- [ ] Run `ruff check app/content_research tests` and `git diff --check`.
- [ ] Request two independent reviews: one checks the execution-fact invariant against the acceptance matrix; one checks migration, SQLite concurrency, and report/evidence lineage. Fix every P0/P1 before marking this remediation complete.
