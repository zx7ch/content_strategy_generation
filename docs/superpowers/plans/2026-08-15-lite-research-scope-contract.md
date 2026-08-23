# Content Research Lite Scope Contract Implementation Plan

> **Status:** Superseded for remaining delivery work by
> [`2026-08-23-lite-research-final-vertical-slices.md`](./2026-08-23-lite-research-final-vertical-slices.md).
> Completed persistence/authority work remains valid; do not execute this
> plan's old product-marketing query and required-season/scenario tasks.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve user-confirmed retrieval scope through query execution, evidence admission, coverage decisions, and Lite report boundaries.

**Architecture:** Keep `SubjectStructure` as an immutable interpretation snapshot and introduce a versioned `ResearchScopeContract` as the execution authority. Query groups, candidate constraint matches, and coverage snapshots refer to the same contract version. Existing Trace remains unchanged; Creator reads dedicated scope and evidence projections.

**Tech Stack:** Python 3, FastAPI/Pydantic, SQLite content-research store, React/TypeScript, Vitest, pytest.

## Global Constraints

- Support text constraints and at most three query groups per direction in Lite.
- Query groups accept arbitrary non-empty user text; missing required terms is an advisory classification, never a validation error.
- Do not persist Cookies, LLM keys, request headers, or raw provider error bodies in scope/audit records.
- Do not change existing Trace presentation.
- Every collection, candidate match, coverage snapshot, and report projection is tied to an immutable scope-contract version.
- Observability is a completion condition for every task: every new state transition emits a redacted, queryable audit record and has a focused assertion proving its values describe the real persisted state.

## Cross-cutting Observability Contract

The implementation must not use a final logging-only pass. Each task extends a
single append-only scope-audit stream and read projection. An observation must
be emitted by the domain/service operation that commits the relevant state, not
by the UI and not by a best-effort asynchronous logger.

| Transition | Required audit event | Required assertions |
|---|---|---|
| Subject interpretation → scope suggestion | `scope_suggested` | structure hash, suggested constraints, suggested query groups and roles equal the returned draft |
| User confirmation → frozen contract | `scope_confirmed` | final query, origin, role, contract version and edit diff equal persisted contract |
| Query group → Spider result | `query_group_collected` | final query, contract version, role, request outcome, discovered count and failure code equal collection checkpoint |
| Candidate detail → constraint result | `candidate_scope_evaluated` | match evidence and exclusion reasons equal candidate projection |
| Coverage aggregate → human decision | `coverage_evaluated` / `coverage_resolved` | counts, unmet conditions and user action equal coverage snapshot and decision record |
| Scope decision → report | `report_scope_projected` | report mode, scope version and limitations equal the frozen decision and coverage data |

All event payloads are redacted by construction. They contain IDs, counts,
normalized query text, stable failure codes and field-level match summaries;
they never contain secret configuration, Cookie values, provider headers, or raw
provider bodies.

---

## File Structure

- Create `app/content_research/scope_contract.py`: typed contract, constraint/query validation, query classification, immutable version helpers.
- Modify `app/content_research/persistence_models.py`: typed scope contract and coverage snapshot records.
- Modify `app/content_research/bootstrap.py`, `app/content_research/stores/base.py`, `app/content_research/stores/sqlite_store.py`: SQLite tables and store API.
- Modify `app/content_research/api_schemas.py`, `app/api/routes/router.py`, `app/content_research/service.py`: prepare, confirm, resolve, and read APIs/actions.
- Modify `app/content_research/workflow/query_planner.py`, `app/content_research/contracts.py`, `app/content_research/admission/relevance.py`, `app/content_research/workflow/directional_pipeline.py`: contract-driven collection and admission.
- Modify `app/content_research/reporting/lite_read_model.py`: report scope and limitations projection.
- Modify `frontend/src/lib/content-research-api.ts`, `frontend/src/app/creator/page.tsx`, `frontend/src/app/creator/page.test.tsx`: confirmation and coverage-decision UI.
- Create focused unit/integration/e2e tests under `tests/unit`, `tests/integration`, `tests/e2e`, and frontend tests.

### Task 1: Define and persist scope contracts

**Files:**
- Create: `app/content_research/scope_contract.py`
- Modify: `app/content_research/persistence_models.py`
- Modify: `app/content_research/bootstrap.py`
- Modify: `app/content_research/stores/base.py`
- Modify: `app/content_research/stores/sqlite_store.py`
- Test: `tests/unit/test_content_research_scope_contract.py`
- Test: `tests/integration/test_content_research_scope_contract_store.py`

**Interfaces:**
- Produces `ResearchScopeContract`, `ScopeConstraint`, `ScopeQueryGroup`, `CoverageSnapshot`.
- Produces `classify_query_group(query, required_terms) -> Literal["coverage", "supplementary", "exploratory"]`.
- Store consumes `workflow_run_id` and version; later tasks consume immutable records.
- Produces the append-only scope-audit record model and store API used by every later task.

- [ ] **Step 1: Write failing contract tests**

```python
def test_user_query_missing_required_term_is_exploratory_not_invalid() -> None:
    contract = build_scope_contract(
        workflow_run_id="run_1", version=1,
        required_constraints=(ScopeConstraint("season", "夏季", "required"),),
        query_groups=(ScopeQueryGroup("q1", "夏季 衬衫", "白衬衫通勤", "user_edited"),),
    )
    assert contract.query_groups[0].execution_role == "exploratory"

def test_scope_contract_requires_one_core_object_and_at_most_three_queries() -> None:
    with pytest.raises(ValueError, match="at most 3"):
        build_scope_contract(... four_query_groups ...)
```

- [ ] **Step 2: Run the unit test to verify it fails**

Run: `pytest tests/unit/test_content_research_scope_contract.py -v`

Expected: FAIL because scope-contract types and builder do not exist.

- [ ] **Step 3: Implement typed scope contract and query classification**

```python
@dataclass(frozen=True)
class ScopeQueryGroup:
    id: str
    suggested_query: str
    final_query: str
    origin: Literal["system_suggested", "user_edited"]
    execution_role: Literal["coverage", "supplementary", "exploratory"]

def classify_query_group(final_query: str, required_terms: tuple[str, ...]) -> str:
    normalized = normalize_relevance_text(final_query)
    return "coverage" if all(normalize_relevance_text(term) in normalized for term in required_terms) else "exploratory"
```

- [ ] **Step 4: Add append-only SQLite records, scope-audit records, and store methods**

Create `content_research_scope_contracts` keyed by `(workflow_run_id, version)`, `content_research_coverage_snapshots` keyed by contract ID, and `content_research_scope_audit_events` keyed by immutable event ID. Add `save_scope_contract`, `get_scope_contract`, `list_scope_contracts`, `save_coverage_snapshot`, `append_scope_audit_event`, and `list_scope_audit_events` to the store protocol and SQLite implementation.

- [ ] **Step 5: Run persistence and audit-record tests**

Run: `pytest tests/unit/test_content_research_scope_contract.py tests/integration/test_content_research_scope_contract_store.py -v`

Expected: PASS; version 1 remains readable after version 2 is saved, and audit records are append-only and queryable by contract version.

- [ ] **Step 6: Commit**

```bash
git add app/content_research/scope_contract.py app/content_research/persistence_models.py app/content_research/bootstrap.py app/content_research/stores tests/unit/test_content_research_scope_contract.py tests/integration/test_content_research_scope_contract_store.py
git commit -m "feat(content-research): persist user-confirmed scope contracts"
```

### Task 2: Prepare and confirm an executable scope

**Files:**
- Modify: `app/content_research/api_schemas.py`
- Modify: `app/content_research/service.py`
- Modify: `app/api/routes/router.py`
- Modify: `app/content_research/workflow/query_planner.py`
- Test: `tests/e2e/test_content_research_brief_confirm_api.py`
- Test: `tests/e2e/test_content_research_scope_api.py`

**Interfaces:**
- Consumes subject structure and Task 1 contract builder.
- Produces workflow actions `prepare_scope` and `confirm_scope`.
- `confirm_scope` returns the frozen contract version and transitions only to collection-ready state.
- Emits `scope_suggested` and `scope_confirmed` from the same service calls that persist drafts/contracts.

- [ ] **Step 1: Write failing API tests for the summer commute fixture**

```python
async def test_prepare_scope_preserves_context_in_suggested_query_groups(client):
    response = await action(client, "prepare_scope", subject="夏季通勤长袖")
    scope = response.json()["result"]["scope"]
    assert scope["constraints"] == [
        {"id": "season", "value": "夏季", "mode": "required"},
        {"id": "scenario", "value": "通勤", "mode": "required"},
    ]
    assert any("夏季" in group["suggested_query"] for group in scope["query_groups"])

async def test_confirm_scope_accepts_arbitrary_user_query(client):
    response = await action(client, "confirm_scope", final_query="白衬衫通勤穿搭")
    assert response.json()["result"]["scope_contract"]["query_groups"][0]["execution_role"] == "exploratory"
```

- [ ] **Step 2: Run the API test to verify it fails**

Run: `pytest tests/e2e/test_content_research_scope_api.py -v`

Expected: FAIL because these actions and schemas do not exist.

- [ ] **Step 3: Add Pydantic request/response schemas and workflow actions**

Add `ScopeConstraintInput`, `ScopeQueryGroupInput`, `PrepareScopeResponse`, and `ConfirmScopeRequest`. Route all actions through the existing `/actions` endpoint. Reject blank or fourth query groups with stable API error codes; do not reject arbitrary text that omits required terms.

- [ ] **Step 4: Compile suggested groups from all required terms**

For `夏季通勤长袖`, generate a full-coverage group, a core alias group, and a goal facet group. Preserve context modifiers as required suggestions; do not tie their use to synonyms/fallback availability.

- [ ] **Step 5: Emit and verify scope decision observations**

Persist `scope_suggested` after producing the returned draft and `scope_confirmed` in the transaction that saves the immutable contract. Extend the API test to compare every observed suggested/final query, `origin`, role, structure hash, and contract version with the action result and SQLite record.

- [ ] **Step 6: Run focused tests**

Run: `pytest tests/e2e/test_content_research_scope_api.py tests/e2e/test_content_research_brief_confirm_api.py tests/unit/test_content_research_query_planner.py -v`

Expected: PASS; existing confirmed-brief behavior remains compatible and the audit stream proves what the user accepted.

- [ ] **Step 7: Commit**

```bash
git add app/content_research/api_schemas.py app/content_research/service.py app/api/routes/router.py app/content_research/workflow/query_planner.py tests/e2e/test_content_research_scope_api.py tests/e2e/test_content_research_brief_confirm_api.py tests/unit/test_content_research_query_planner.py
git commit -m "feat(content-research): confirm editable lite retrieval scope"
```

### Task 3: Use scope contracts for matching and coverage decisions

**Files:**
- Modify: `app/content_research/contracts.py`
- Modify: `app/content_research/admission/relevance.py`
- Modify: `app/content_research/workflow/directional_pipeline.py`
- Modify: `app/content_research/service.py`
- Test: `tests/unit/test_content_research_scope_matching.py`
- Test: `tests/integration/test_content_research_scope_coverage.py`

**Interfaces:**
- Consumes Task 1 scope contracts and detailed source fields.
- Produces per-candidate `constraint_matches`, a coverage snapshot, and state `awaiting_scope_decision` when required coverage fails.
- Emits `query_group_collected`, `candidate_scope_evaluated`, and `coverage_evaluated` from checkpoint/projection persistence boundaries.

- [ ] **Step 1: Write failing matching and coverage tests**

```python
def test_core_alias_and_required_contexts_admit_a_candidate() -> None:
    match = evaluate_scope_match(
        source={"title": "夏季通勤衬衫", "content_text": "轻薄不易皱", "tags": []},
        contract=summer_commute_contract(),
    )
    assert match.constraint_matches["core_object"].status == "matched"
    assert match.constraint_matches["season"].status == "matched"
    assert match.constraint_matches["scenario"].status == "matched"

def test_missing_required_summer_blocks_exploratory_source() -> None:
    assert evaluate_scope_match(source=autumn_shirt(), contract=summer_commute_contract()).eligibility == "excluded"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/unit/test_content_research_scope_matching.py -v`

Expected: FAIL because matching is still based on query relevance literals.

- [ ] **Step 3: Implement contract-driven matching**

Replace the product-marketing `core_entity + first_intent` literal gate with a scope-match projection. Accept configured core aliases; require a matched evidence field for each required constraint. Keep provenance validation, canonical source checks, and quote-field validation unchanged.

- [ ] **Step 4: Implement coverage snapshot and state transition**

Aggregate matches by constraint, query group, and independent author. Persist counts and reason codes. When unmet, record `awaiting_scope_decision`; do not broaden queries or generate a normal report.

- [ ] **Step 5: Emit and verify collection, match, and coverage observations**

At collection checkpoint persistence, append one `query_group_collected` event per final query. At candidate projection persistence, append `candidate_scope_evaluated` with field-level match summaries. At coverage snapshot persistence, append `coverage_evaluated`. The integration fixture must assert counts and reason codes exactly match the corresponding checkpoint/projection/snapshot, including an autumn-only source excluded from summer scope.

- [ ] **Step 6: Run focused tests**

Run: `pytest tests/unit/test_content_research_scope_matching.py tests/integration/test_content_research_scope_coverage.py tests/unit/test_content_research_product_marketing_admission.py -v`

Expected: PASS; early/autumn-only sources cannot support summer conclusions and the audit explains the exclusion without an ambiguous generic failure.

- [ ] **Step 7: Commit**

```bash
git add app/content_research/contracts.py app/content_research/admission/relevance.py app/content_research/workflow/directional_pipeline.py app/content_research/service.py tests/unit/test_content_research_scope_matching.py tests/integration/test_content_research_scope_coverage.py tests/unit/test_content_research_product_marketing_admission.py
git commit -m "feat(content-research): govern evidence with frozen scope constraints"
```

### Task 4: Resolve inadequate coverage and project report boundaries

**Files:**
- Modify: `app/content_research/service.py`
- Modify: `app/content_research/reporting/lite_read_model.py`
- Modify: `app/content_research/api_schemas.py`
- Test: `tests/integration/test_content_research_lite_read_model.py`
- Test: `tests/e2e/test_content_research_scope_api.py`

**Interfaces:**
- Consumes coverage snapshot and `resolve_coverage` action.
- Produces either a next immutable scope version or a limited-report authorization and visible limitations.
- Emits `coverage_resolved` and `report_scope_projected` with the exact selected outcome.

- [ ] **Step 1: Write failing decision tests**

```python
async def test_unmet_required_coverage_requires_explicit_resolution(client):
    result = await complete_collection_with_only_five_summer_sources(client)
    assert result["workflow_execution_state"] == "awaiting_scope_decision"

async def test_limited_report_retains_exact_unmet_constraint(client):
    response = await action(client, "resolve_coverage", resolution="generate_limited_report")
    report = (await get_lite_report(client)).json()
    assert report["status_strip"]["report_mode"] == "limited"
    assert report["sections"]["limitations_scope"][0]["constraint_id"] == "season"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/e2e/test_content_research_scope_api.py tests/integration/test_content_research_lite_read_model.py -v`

Expected: FAIL because inadequate coverage currently falls directly to generic insufficient evidence.

- [ ] **Step 3: Implement exactly three resolutions**

`expand_required_constraint` creates the next version with up to two supplementary groups; `relax_constraint` changes one required condition to preferred and saves the user decision; `generate_limited_report` preserves the version and authorizes a limited report. Reject any action other than these values.

- [ ] **Step 4: Add Lite report scope projection**

Expose final query groups, contract version, per-constraint counts, report mode, and concise limitation records. A normal report is unavailable while scope decision is pending.

- [ ] **Step 5: Emit and verify decision-to-report observations**

Append `coverage_resolved` in the same transaction as the human decision and `report_scope_projected` when building the Lite read model. Tests must compare report mode, contract version, unresolved constraints, and limitation text against these audit events and persisted coverage snapshot.

- [ ] **Step 6: Run focused tests**

Run: `pytest tests/e2e/test_content_research_scope_api.py tests/integration/test_content_research_lite_read_model.py tests/unit/test_content_research_report_composer.py -v`

Expected: PASS; limitation text and its audit trail distinguish missing season coverage from unrelated runtime/provider failures.

- [ ] **Step 7: Commit**

```bash
git add app/content_research/service.py app/content_research/reporting/lite_read_model.py app/content_research/api_schemas.py tests/e2e/test_content_research_scope_api.py tests/integration/test_content_research_lite_read_model.py tests/unit/test_content_research_report_composer.py
git commit -m "feat(content-research): require scope decisions before limited reports"
```

### Task 5: Build Creator scope-confirmation and coverage-decision UI

**Files:**
- Modify: `frontend/src/lib/content-research-api.ts`
- Modify: `frontend/src/lib/content-research-api.test.ts`
- Modify: `frontend/src/app/creator/page.tsx`
- Modify: `frontend/src/app/creator/page.test.tsx`
- Test: `tests/acceptance/test_content_research_creator_ui_contract.py`

**Interfaces:**
- Consumes prepare/confirm/resolve action payloads from Tasks 2 and 4.
- Produces only existing workflow action calls; no browser-only scope state becomes authoritative.
- Displays persisted scope/coverage facts only; it does not create audit truth in the browser.

- [ ] **Step 1: Write failing frontend tests**

```tsx
it("lets the user edit every suggested query before confirmation", async () => {
  renderCreatorWithPreparedScope();
  await user.type(screen.getByLabelText("检索组 2"), "白衬衫通勤穿搭");
  await user.click(screen.getByRole("button", {name: "确认并开始调研"}));
  expect(confirmScope).toHaveBeenCalledWith(expect.objectContaining({
    query_groups: expect.arrayContaining([expect.objectContaining({final_query: "白衬衫通勤穿搭"})]),
  }));
});

it("shows the three coverage resolutions and does not render a normal report while pending", async () => {
  renderCreatorWithPendingCoverage();
  expect(screen.getByText("继续补充夏季样本")).toBeVisible();
  expect(screen.queryByText("研究结论")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run frontend tests to verify failure**

Run: `cd frontend && npm test -- --run src/app/creator/page.test.tsx src/lib/content-research-api.test.ts`

Expected: FAIL because scope confirmation UI and client methods do not exist.

- [ ] **Step 3: Add API client methods and typed payloads**

Add `prepareContentResearchScope`, `confirmContentResearchScope`, and `resolveContentResearchCoverage`. Map stable API errors to existing safe feedback conventions.

- [ ] **Step 4: Add Creator dialogs in report/conversation theme**

Render the pre-collection confirmation card with editable inputs for every query group and advisory labels for coverage/exploratory groups. Render the coverage-decision card only for `awaiting_scope_decision`. Keep Trace controls and candidate audit unchanged.

- [ ] **Step 5: Verify UI values come from the persisted audit/read projection**

In frontend tests, return deliberately different suggested and final query strings plus an `exploratory` role. Assert the confirmation and coverage UI renders those server values and that a page reload reconstructs the same state from the read API rather than local component state.

- [ ] **Step 6: Run UI and acceptance tests**

Run: `cd frontend && npm test -- --run src/app/creator/page.test.tsx src/lib/content-research-api.test.ts && npm run build`

Run: `pytest tests/acceptance/test_content_research_creator_ui_contract.py -v`

Expected: PASS; production build completes, displayed scope facts match the persisted read model, and current Trace UI assertions remain unchanged.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/content-research-api.ts frontend/src/lib/content-research-api.test.ts frontend/src/app/creator/page.tsx frontend/src/app/creator/page.test.tsx tests/acceptance/test_content_research_creator_ui_contract.py
git commit -m "feat(creator): confirm editable content research scope"
```

### Task 6: Validate the complete scope-audit chain in release gates

**Files:**
- Modify: `app/content_research/observation/trace_service.py`
- Modify: `app/content_research/service.py`
- Modify: `tests/e2e/test_content_research_direction_evidence_api.py`
- Create: `tests/acceptance/test_content_research_scope_contract_release.py`

**Interfaces:**
- Consumes the observability records implemented incrementally by Tasks 1–4.
- Proves a frozen package preserves a complete redacted scope-audit chain; does not alter Trace UI payload semantics.

- [ ] **Step 1: Write failing audit and release tests**

```python
def test_evidence_api_exposes_scope_provenance_without_secrets(client):
    body = client.get(evidence_url).json()
    assert body["scope_contract"]["version"] == 1
    assert body["candidates"][0]["constraint_matches"]
    assert "cookie" not in json.dumps(body).lower()

def test_summer_commute_release_fixture_never_uses_autumn_only_note_for_summer_claim():
    report = run_fixture("夏季通勤长袖", autumn_only_notes=True)
    assert report["workflow_execution_state"] == "awaiting_scope_decision"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/e2e/test_content_research_direction_evidence_api.py tests/acceptance/test_content_research_scope_contract_release.py -v`

Expected: FAIL because the audit projection and fixture gate do not exist.

- [ ] **Step 3: Implement the deterministic release fixture**

Use a fake source adapter with summer, autumn, alias, and multi-author notes. Exercise system suggestion, arbitrary user query edit, candidate matching, pending coverage, limited report, and relaxed contract version. Assert the ordered audit stream contains all six required events and each event agrees with the persisted domain state.

- [ ] **Step 4: Run the full focused gate**

Run: `pytest tests/unit/test_content_research_scope_contract.py tests/unit/test_content_research_scope_matching.py tests/integration/test_content_research_scope_contract_store.py tests/integration/test_content_research_scope_coverage.py tests/e2e/test_content_research_scope_api.py tests/e2e/test_content_research_direction_evidence_api.py tests/acceptance/test_content_research_scope_contract_release.py -v`

Run: `cd frontend && npm test -- --run src/app/creator/page.test.tsx src/lib/content-research-api.test.ts && npm run build`

Expected: PASS; every transition has a truthful redacted audit record, existing Trace tests remain unchanged, and the release package gate remains in the standard release suite.

- [ ] **Step 5: Commit**

```bash
git add app/content_research/observation/trace_service.py app/content_research/service.py tests/e2e/test_content_research_direction_evidence_api.py tests/acceptance/test_content_research_scope_contract_release.py
git commit -m "test(content-research): gate scope fidelity in lite research"
```
