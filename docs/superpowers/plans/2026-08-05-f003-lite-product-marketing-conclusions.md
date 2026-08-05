# F003 Lite Product-Marketing Conclusions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a compact Lite product-marketing report with one evidence-backed conclusion for each of need, value, and message, plus one goal-aware action recommendation and an honest insufficient-evidence state.

**Architecture:** Brief confirmation freezes one user-selected marketing goal and a single `marketing_conclusion_policy` alongside the existing subject, query, and admission contract. Existing admission first makes direct quote evidence eligible; a bounded expert then proposes multi-claim conclusion candidates and a deterministic evaluator selects at most one primary conclusion per track. The governed snapshot retains every qualified conclusion, while the Lite read model renders only the primary conclusion, evidence strength, and extra-qualified count. One compact `marketing_conclusion` checkpoint explains outcome or recovery without exposing report text or raw evidence.

**Tech Stack:** Python 3.11 dataclasses/Pydantic, FastAPI, SQLite/aiosqlite, pytest/pytest-asyncio, Next.js/React/TypeScript, Node test runner.

## Global Constraints

- Work only for Lite `product_marketing`; do not expand Spider direction coverage or introduce a new provider capability.
- The user chooses exactly one primary marketing goal at Brief confirmation; it is immutable for the run.
- Use one stable `marketing_conclusion_policy` name and one report contract path. Remove obsolete Lite report artifacts during migration; do not introduce P1 `v1`/`v2` naming or legacy fallback reads.
- A direct quote must support both the frozen core object and the frozen first research intent before it can be admitted for P1 conclusions.
- A selected conclusion requires at least three distinct canonical notes and two independent author identities; one note counts once per track even when it contains multiple quotes.
- The conclusion-analysis LLM receives only admitted claims and safe quote metadata. It proposes candidates but cannot admit, rank, or invent evidence.
- A conclusion-analysis LLM/configuration failure is recoverable `analysis_unavailable`, never evidence insufficiency.
- Do not run Spider after conclusion evidence is insufficient. Existing Q3 remains the only frozen coverage fallback and may activate once.
- Historical replay runs only from persisted packets and must prove provider-operation and packet identity deltas are zero.
- Lite publishes at most one conclusion per `need`, `value`, and `message`; extra qualified conclusions are durable and counted but not expanded.
- Trace is newest-first and exposes only conclusion state, actionable reason codes, support counts when meaningful, and replay deltas. It never exposes conclusion text, quotes, raw input/query, note/author IDs, prompts, credentials, or provider payloads.

## File Structure

| File | Responsibility |
|---|---|
| `app/content_research/contracts.py` | Freeze/validate marketing goal and conclusion policy; include first-intent anchors in formal relevance. |
| `app/content_research/api_schemas.py` | Confirm-Brief request goal, Lite report response conclusion sections, and safe Trace shape. |
| `app/content_research/workflow/plan_builder.py` | Carry frozen primary goal from confirmation into plan/task input. |
| `app/content_research/service.py` | Persist the goal, invoke conclusion analysis before governed snapshot creation, include catalog in snapshot, and replay the same downstream path. |
| `app/content_research/admission/relevance.py` | Enforce direct core-object plus first-intent quote support. |
| `app/content_research/marketing_conclusions.py` | New focused domain module for candidate validation, support calculation, selection, action recommendation, and safe checkpoint payloads. |
| `app/content_research/persistence_models.py` | New typed conclusion candidate/decision records and the `marketing_conclusion` checkpoint stage. |
| `app/content_research/stores/base.py` / `app/content_research/stores/sqlite_store.py` / `app/content_research/migrations.py` | Store, read, and migrate conclusion records; remove superseded Lite report artifacts. |
| `app/content_research/reporting/composer.py` / `app/content_research/reporting/contracts.py` | Compose governed conclusion cards and one goal-aware action section. |
| `app/content_research/reporting/lite_read_model.py` | Project primary cards, support strength, additional count, insufficient state, and action recommendation. |
| `app/content_research/observation/trace_service.py` | Project one compact `marketing_conclusion` logical checkpoint. |
| `frontend/src/lib/content-research-api.ts` / `frontend/src/app/creator/page.tsx` | Select a marketing goal and render three conclusion cards, action, evidence strength, insufficient state, and Trace copy. |
| `tests/unit/test_content_research_marketing_conclusions.py` | New evaluator and candidate contract tests. |
| Existing admission, confirmation, read-model, trace, replay, workflow, and browser tests | Contract integration and regression coverage. |

---

### Task 1: Freeze the Marketing Goal and First-Intent Evidence Gate

**Files:**
- Modify: `app/content_research/api_schemas.py:86-98`
- Modify: `app/content_research/contracts.py:150-250, build_default_snapshot`
- Modify: `app/content_research/admission/relevance.py`
- Modify: `app/content_research/workflow/plan_builder.py`
- Modify: `app/content_research/service.py:1006-1290`
- Modify: `frontend/src/lib/content-research-api.ts:62-71`
- Modify: `frontend/src/app/creator/page.tsx:697-914`
- Test: `tests/e2e/test_content_research_brief_confirm_api.py`
- Test: `tests/unit/test_content_research_admission_evaluator.py`
- Test: `tests/unit/test_content_research_product_marketing_admission.py`
- Test: `frontend/src/lib/content-research-api.test.ts`

**Interfaces:**
- Produces `ContentResearchBriefConfirmRequest.primary_marketing_goal: str` and `BriefConfirmation.primary_marketing_goal: str`.
- Produces `effective_policy["marketing_conclusion_policy"]` with the exact tracks `need`, `value`, `message`, thresholds `3`/`2`, core-and-first-intent requirement, and display cap `1`.
- Changes `query_relevance_reason(...)` to return `first_intent_not_supported` when a quote passes core support but lacks the frozen first-intent anchor.

- [ ] **Step 1: Write failing Brief-confirmation tests**

```python
async def test_confirm_brief_freezes_one_primary_marketing_goal(client, seeded_presearch):
    response = await client.post(
        f"/content-research/briefs/{seeded_presearch.brief_id}/confirm",
        json={
            "confirmed_subject": "夏季凉感T恤",
            "subject_structure_hash": seeded_presearch.subject_structure_hash,
            "subject_type": "category",
            "selected_competitors": [],
            "custom_competitors": [],
            "selected_directions": ["product_marketing"],
            "custom_research_question": "",
            "primary_marketing_goal": "content_seeding",
        },
    )
    assert response.status_code == 200
    policy = response.json()["policy_snapshot"]["effective_policy"]
    assert policy["marketing_conclusion_policy"] == {
        "primary_marketing_goal": "content_seeding",
        "tracks": ["need", "value", "message"],
        "minimum_notes_per_conclusion": 3,
        "minimum_independent_authors_per_conclusion": 2,
        "require_core_and_first_intent_support": True,
        "maximum_primary_conclusions_per_track": 1,
    }
```

- [ ] **Step 2: Write failing first-intent relevance tests**

```python
def test_product_marketing_quote_requires_core_and_first_intent(frozen_contract, packet):
    generic_tshirt = candidate_from_quote(packet, "纯棉T恤搭配牛仔裤")
    assert query_relevance_reason(
        candidate=generic_tshirt,
        packet=packet,
        contract=frozen_contract,
        policy_snapshot=frozen_contract.snapshot,
    ) == "first_intent_not_supported"

    cooling_tshirt = candidate_from_quote(packet, "这件T恤穿上有明显凉感")
    assert query_relevance_reason(
        candidate=cooling_tshirt,
        packet=packet,
        contract=frozen_contract,
        policy_snapshot=frozen_contract.snapshot,
    ) is None
```

- [ ] **Step 3: Run the RED tests**

Run: `pytest -q tests/e2e/test_content_research_brief_confirm_api.py tests/unit/test_content_research_admission_evaluator.py tests/unit/test_content_research_product_marketing_admission.py -k 'marketing_goal or first_intent'`

Expected: FAIL because the confirmation schema has no marketing goal and relevance only checks core anchors.

- [ ] **Step 4: Implement the frozen confirmation contract**

Add a bounded `primary_marketing_goal` field to the API request and frontend request type. Add the same field to `BriefConfirmation`, persist it in Brief/plan/task payloads, and pass it into `build_default_snapshot`. Validate one non-empty catalog value at the boundary; reject missing, unknown, or list-valued goals. In `build_default_snapshot`, construct exactly:

```python
marketing_conclusion_policy = {
    "primary_marketing_goal": primary_marketing_goal,
    "tracks": ["need", "value", "message"],
    "minimum_notes_per_conclusion": 3,
    "minimum_independent_authors_per_conclusion": 2,
    "require_core_and_first_intent_support": True,
    "maximum_primary_conclusions_per_track": 1,
}
```

Store it only in the immutable effective policy. Do not allow it to be overwritten by a workflow action after formal collection begins.

- [ ] **Step 5: Implement the relevance gate**

Extend the existing frozen relevance payload with a normalized `first_intent_anchor` derived from `subject_structure.research_intents[0]`. In `query_relevance_reason`, preserve all current provenance/field/core checks, then apply:

```python
if not any(anchor in quote for anchor in relevance["core_entity_anchors"]):
    return QUERY_SUBJECT_NOT_SUPPORTED
if relevance["first_intent_anchor"] not in quote:
    return "first_intent_not_supported"
return None
```

Use the same normalization function as existing anchor matching. Do not match a complete query string or use a category vocabulary. Treat `first_intent_not_supported` as rejected for product-marketing findings and as a stable coverage reason.

- [ ] **Step 6: Add the minimal Creator goal selector**

Place one required, single-select goal control in the existing Brief confirmation card, adjacent to direction selection. Keep the selected value in component state and include it in `confirmBrief()`:

```ts
await confirmContentResearchBrief(briefId, {
  confirmed_subject: subject,
  subject_structure_hash: presearch.subject_structure_hash ?? null,
  subject_type: "category",
  selected_competitors: selectedCompetitors,
  custom_competitors: customCompetitors,
  selected_directions: selectedDirections,
  custom_research_question: customQuestion.trim(),
  primary_marketing_goal: primaryMarketingGoal,
});
```

Disable confirmation until the goal is selected when `product_marketing` is selected. Do not add a default that silently changes user intent.

- [ ] **Step 7: Run GREEN and frontend type tests**

Run: `pytest -q tests/e2e/test_content_research_brief_confirm_api.py tests/unit/test_content_research_admission_evaluator.py tests/unit/test_content_research_product_marketing_admission.py`

Run: `npm --prefix frontend test -- --run src/lib/content-research-api.test.ts`

Expected: all focused tests pass.

- [ ] **Step 8: Commit Task 1**

```bash
git add app/content_research/api_schemas.py app/content_research/contracts.py app/content_research/admission/relevance.py app/content_research/workflow/plan_builder.py app/content_research/service.py frontend/src/lib/content-research-api.ts frontend/src/app/creator/page.tsx tests/e2e/test_content_research_brief_confirm_api.py tests/unit/test_content_research_admission_evaluator.py tests/unit/test_content_research_product_marketing_admission.py frontend/src/lib/content-research-api.test.ts
git commit -m "feat(content-research): freeze Lite marketing goals"
```

### Task 2: Persist and Deterministically Govern Conclusion Candidates

**Files:**
- Create: `app/content_research/marketing_conclusions.py`
- Modify: `app/content_research/persistence_models.py`
- Modify: `app/content_research/stores/base.py`
- Modify: `app/content_research/stores/sqlite_store.py`
- Modify: `app/content_research/migrations.py`
- Test: `tests/unit/test_content_research_marketing_conclusions.py`
- Test: `tests/integration/test_content_research_report_store.py`

**Interfaces:**
- Produces immutable `MarketingConclusionCandidateRecord` and `MarketingConclusionDecisionRecord` typed records.
- Produces `evaluate_marketing_conclusions(*, candidates, admitted_claims, packets, policy) -> MarketingConclusionEvaluation`.
- `MarketingConclusionEvaluation` exposes a qualified catalog, one primary decision or an explicit terminal state per track, and safe support counts.

- [ ] **Step 1: Write failing evaluator tests**

```python
def test_evaluator_selects_one_conclusion_from_three_notes_and_two_authors():
    evaluation = evaluate_marketing_conclusions(
        candidates=[candidate("need", ["c1", "c2", "c3"])],
        admitted_claims=admitted_claims("c1", "c2", "c3"),
        packets=packets_with_sources_and_authors(
            ("c1", "note_1", "author_a"),
            ("c2", "note_2", "author_a"),
            ("c3", "note_3", "author_b"),
        ),
        policy=marketing_policy(),
    )
    assert evaluation.tracks["need"].state == "selected"
    assert evaluation.tracks["need"].supporting_note_count == 3
    assert evaluation.tracks["need"].independent_author_count == 2


def test_evaluator_does_not_count_multiple_claims_from_one_note_twice():
    evaluation = evaluate_marketing_conclusions(
        candidates=[candidate("value", ["c1", "c2", "c3"])],
        admitted_claims=admitted_claims("c1", "c2", "c3"),
        packets=packets_with_sources_and_authors(
            ("c1", "note_1", "author_a"),
            ("c2", "note_1", "author_a"),
            ("c3", "note_2", "author_b"),
        ),
        policy=marketing_policy(),
    )
    assert evaluation.tracks["value"].state == "insufficient_evidence"
    assert evaluation.tracks["value"].reason_codes == ("conclusion_note_count_unmet",)
```

- [ ] **Step 2: Add failure tests for invalid and tied candidates**

Cover a non-admitted claim ID, a claim from another direction, an unsupported track, an added causal phrase, duplicate candidate merge, and two equally supported competing statements. Assert the tied track becomes `no_single_primary_conclusion` and never uses an ID tie-breaker.

- [ ] **Step 3: Run RED**

Run: `pytest -q tests/unit/test_content_research_marketing_conclusions.py`

Expected: FAIL because no conclusion types, persistence records, or evaluator exist.

- [ ] **Step 4: Create narrow data records and store methods**

Add records with only relationship and decision fields required by the evaluator:

```python
@dataclass(frozen=True)
class MarketingConclusionCandidateRecord(TypedPersistenceRecord):
    workflow_run_id: str = ""
    research_plan_id: str = ""
    track: str = ""


@dataclass(frozen=True)
class MarketingConclusionDecisionRecord(TypedPersistenceRecord):
    workflow_run_id: str = ""
    research_plan_id: str = ""
    candidate_id: str | None = None
    track: str = ""
    state: str = ""
```

Require `track in {"need", "value", "message"}`. Require decision states `selected`, `qualified`, `insufficient_evidence`, `no_single_primary_conclusion`, and `analysis_unavailable`. Add explicit SQLite tables/indexes keyed by run, plan, and track; do not hide these records in an untyped JSON blob. Update the store protocol with save/list methods and add migration cleanup that removes superseded Lite report artifacts before P1 report records are materialized.

- [ ] **Step 5: Implement pure evaluation and safe payloads**

In `marketing_conclusions.py`, define explicit immutable values for proposal and outcome. The evaluator must:

1. resolve each supporting claim to an admitted decision, packet, canonical note, and author identity;
2. deduplicate note identity and author identity inside each track;
3. reject invalid support with stable reason codes;
4. merge exact duplicate statements with the same normalized support set;
5. rank qualified candidates by author count, note count, then body-quote count;
6. emit `no_single_primary_conclusion` for distinct candidates tied on all ranking values;
7. expose only counts/reason codes in `safe_trace_payload()`.

Use this concrete ranking key:

```python
ranking_key = (
    candidate.independent_author_count,
    candidate.supporting_note_count,
    candidate.body_quote_note_count,
)
```

Select only a unique maximum. A candidate statement must be non-empty, bounded to the existing report text limit, and may not contain the prohibited outcome terms already enforced by product-marketing admission.

- [ ] **Step 6: Run GREEN and persistence integration tests**

Run: `pytest -q tests/unit/test_content_research_marketing_conclusions.py tests/integration/test_content_research_report_store.py`

Expected: all conclusion states round-trip through SQLite and evaluator tests pass.

- [ ] **Step 7: Commit Task 2**

```bash
git add app/content_research/marketing_conclusions.py app/content_research/persistence_models.py app/content_research/stores/base.py app/content_research/stores/sqlite_store.py app/content_research/migrations.py tests/unit/test_content_research_marketing_conclusions.py tests/integration/test_content_research_report_store.py
git commit -m "feat(content-research): govern Lite marketing conclusions"
```

### Task 3: Generate Candidates from Admitted Claims and Recover Safely

**Files:**
- Create: `app/content_research/marketing_conclusion_analysis.py`
- Modify: `app/content_research/service.py:1857-2050, 3060-3160`
- Modify: `app/content_research/reporting/execution.py`
- Modify: `app/content_research/persistence_models.py`
- Test: `tests/unit/test_content_research_marketing_conclusion_analysis.py`
- Test: `tests/integration/test_content_research_packet_replay.py`
- Test: `tests/e2e/test_content_research_formal_workflow_e2e.py`

**Interfaces:**
- Produces `MarketingConclusionAnalysisService.generate(*, workflow_run_id, research_plan_id, policy, admitted_claims) -> tuple[MarketingConclusionCandidateRecord, ...]`.
- `ContentResearchService._publish_report_after_workflow_completion(...)` awaits conclusion generation/governance before `create_result_snapshot(...)`.
- Produces a single `marketing_conclusion` checkpoint with completed, insufficient, tied, or waiting-user state.

- [ ] **Step 1: Write failing bounded-analysis tests**

```python
async def test_conclusion_analysis_receives_only_admitted_claims():
    llm = RecordingLLM(response=conclusion_json("need", ["claim_1", "claim_2", "claim_3"]))
    service = MarketingConclusionAnalysisService(llm=llm)
    await service.generate(
        workflow_run_id="run_1",
        research_plan_id="plan_1",
        policy=marketing_policy(),
        admitted_claims=[admitted_claim("claim_1"), admitted_claim("claim_2"), admitted_claim("claim_3")],
    )
    assert llm.last_request_payload["claims"] == [
        {"claim_id": "claim_1", "quote": "…", "field_path": "content_text"},
        {"claim_id": "claim_2", "quote": "…", "field_path": "content_text"},
        {"claim_id": "claim_3", "quote": "…", "field_path": "content_text"},
    ]
    assert "note_id" not in llm.last_request_text
    assert "raw_payload" not in llm.last_request_text
```

- [ ] **Step 2: Write failing recovery and idempotency tests**

```python
async def test_model_failure_waits_for_repair_without_spider_retry(seeded_terminal_run):
    await seeded_terminal_run.publish_with_conclusion_llm_failure()
    checkpoint = seeded_terminal_run.marketing_conclusion_checkpoint()
    assert checkpoint.status == "waiting_user"
    assert checkpoint.payload["reason_codes"] == ["marketing_analysis_unavailable"]
    assert seeded_terminal_run.provider_operation_ids_after == seeded_terminal_run.provider_operation_ids_before

    await seeded_terminal_run.resume_after_model_repair()
    assert seeded_terminal_run.conclusion_llm_calls == 2
    assert seeded_terminal_run.provider_operation_ids_after == seeded_terminal_run.provider_operation_ids_before
```

- [ ] **Step 3: Run RED**

Run: `pytest -q tests/unit/test_content_research_marketing_conclusion_analysis.py tests/integration/test_content_research_packet_replay.py -k 'conclusion or analysis_unavailable'`

Expected: FAIL because report publication has no conclusion-analysis stage or recoverable model boundary.

- [ ] **Step 4: Implement the bounded expert service**

Use the configured analysis LLM through the existing content-research LLM scope. Send JSON-only messages containing the primary marketing goal, track names, and safe admitted claim records. Require this exact response shape:

```json
{
  "candidates": [
    {
      "track": "need",
      "statement": "A bounded conclusion grounded in supplied claims.",
      "supporting_claim_ids": ["claim_a", "claim_b", "claim_c"]
    }
  ]
}
```

Reject malformed responses, unknown tracks, unknown claim IDs, duplicate IDs, and empty statements before persistence. The model receives no ranking field and no author/note identity. Persist its valid proposals; pass them immediately to the Task 2 evaluator.

- [ ] **Step 5: Integrate conclusion governance before snapshot creation**

In `_publish_report_after_workflow_completion`, load the frozen policy and all admitted product-marketing claims. Execute the service and evaluator before `create_result_snapshot`. On completed evaluation, persist candidate and decision records and append exactly one `marketing_conclusion` checkpoint. Then make `_build_governed_snapshot` include `marketing_conclusions` from decisions.

On `LLMProviderFailure`, do not call `ReportExecutionService` and do not publish an evidence-only report. Set the workflow to its existing recoverable waiting-user state with `marketing_analysis_unavailable` and recovery action `repair_model_configuration_and_resume`. On resume, reuse persisted packets/claims and skip all source adapter calls.

- [ ] **Step 6: Extend packet-only replay**

After replayed admission, invoke the same conclusion service/evaluator before publication. Capture provider operation and packet identity sets before and after; fail closed if either differs. Reuse an existing completed conclusion checkpoint with the same internal input fingerprint instead of producing a second catalog or report message.

- [ ] **Step 7: Run GREEN**

Run: `pytest -q tests/unit/test_content_research_marketing_conclusion_analysis.py tests/integration/test_content_research_packet_replay.py tests/e2e/test_content_research_formal_workflow_e2e.py -k 'marketing_conclusion or replay or analysis_unavailable'`

Expected: valid model output becomes governed conclusions, model failure is recoverable, and replay has zero collection deltas.

- [ ] **Step 8: Commit Task 3**

```bash
git add app/content_research/marketing_conclusion_analysis.py app/content_research/service.py app/content_research/reporting/execution.py app/content_research/persistence_models.py tests/unit/test_content_research_marketing_conclusion_analysis.py tests/integration/test_content_research_packet_replay.py tests/e2e/test_content_research_formal_workflow_e2e.py
git commit -m "feat(content-research): analyze Lite marketing conclusions"
```

### Task 4: Project the Compact Report and Evidence Details

**Files:**
- Modify: `app/content_research/reporting/contracts.py`
- Modify: `app/content_research/reporting/composer.py`
- Modify: `app/content_research/reporting/lite_read_model.py`
- Modify: `app/content_research/api_schemas.py:318-350`
- Modify: `frontend/src/lib/content-research-api.ts`
- Modify: `frontend/src/app/creator/page.tsx:1656-1900`
- Test: `tests/unit/test_content_research_lite_read_model.py`
- Test: `tests/integration/test_content_research_lite_read_model.py`
- Test: `tests/e2e/test_content_research_creator_browser.py`

**Interfaces:**
- Produces `sections.marketing_conclusions` keyed by `need`, `value`, and `message`, and `sections.priority_action`.
- Each selected track contains `statement`, `citation_group_ids`, `supporting_note_count`, `independent_author_count`, and `additional_qualified_count`.
- Each insufficient/tied track contains `state`, `reason_codes`, and a bounded verification direction but no synthetic statement.

- [ ] **Step 1: Write failing read-model tests**

```python
async def test_lite_report_projects_only_primary_marketing_conclusion(store, db_path):
    report = await LiteReportReader(store, db_path).read(workflow_run_id="run_1")
    need = report["sections"]["marketing_conclusions"]["need"]
    assert need["state"] == "selected"
    assert need["statement"] == "高温通勤场景中的凉感需求…"
    assert need["supporting_note_count"] == 3
    assert need["independent_author_count"] == 2
    assert need["additional_qualified_count"] == 1
    assert len(need["citation_group_ids"]) == 3
    assert "other_qualified_statements" not in need
```

- [ ] **Step 2: Write failing browser tests for selected and insufficient tracks**

```python
def test_creator_renders_three_marketing_tracks_and_evidence_strength(browser_page):
    seed_marketing_conclusion_report(browser_page, need="selected", value="insufficient_evidence", message="selected")
    report = published_report(browser_page)
    expect(report.get_by_role("heading", name="场景与需求")).to_be_visible()
    expect(report.get_by_text("3 篇笔记 · 2 位独立作者")).to_be_visible()
    expect(report.get_by_text("暂无可验证结论")).to_be_visible()
    expect(report.get_by_text("另有 1 条合格结论")).to_be_visible()
```

- [ ] **Step 3: Run RED**

Run: `pytest -q tests/unit/test_content_research_lite_read_model.py tests/integration/test_content_research_lite_read_model.py tests/e2e/test_content_research_creator_browser.py -k 'marketing_conclusion or evidence_strength'`

Expected: FAIL because Lite only projects raw claim cards and has no marketing conclusion section.

- [ ] **Step 4: Extend report composition and read projection**

Add report section kinds for the three structured marketing tracks and one priority action. Compose them only from `marketing_conclusions` in the governed snapshot, with citation anchors generated from each selected conclusion's frozen citation groups. Do not turn direct claim-card statements into a substitute conclusion.

In `LiteReportReader`, validate every selected conclusion against its decision, cited claims, and citation groups. Return no conclusion prose for insufficient/tied/unavailable states. Derive one action recommendation from the selected marketing goal and selected conclusions; label it `建议` and require `supporting_conclusion_ids`. If no selected conclusion exists, return a verification action rather than a strategy assertion.

- [ ] **Step 5: Render the Creator report**

Replace the product-marketing raw finding list with three fixed sections in this order: `场景与需求`, `可被相信的产品卖点`, `内容表达`. For a selected section show its statement, exact strength text, extra-qualified count when positive, and existing evidence controls. For insufficient/tied show `暂无可验证结论`, translated reason, and verification direction. Render one `优先行动建议` block below the tracks and a fixed scope/limitations block.

Keep citations in the existing drawer/external-note controls. Do not render quote text in Trace and do not add an expansion that reveals extra conclusion statements.

- [ ] **Step 6: Run GREEN and build**

Run: `pytest -q tests/unit/test_content_research_lite_read_model.py tests/integration/test_content_research_lite_read_model.py tests/e2e/test_content_research_creator_browser.py -k 'marketing_conclusion or evidence_strength'`

Run: `npm --prefix frontend test -- --run src/app/creator/page.test.tsx && npm --prefix frontend run build`

Expected: report has exactly three tracks, evidence details remain navigable, and the production build passes.

- [ ] **Step 7: Commit Task 4**

```bash
git add app/content_research/reporting/contracts.py app/content_research/reporting/composer.py app/content_research/reporting/lite_read_model.py app/content_research/api_schemas.py frontend/src/lib/content-research-api.ts frontend/src/app/creator/page.tsx tests/unit/test_content_research_lite_read_model.py tests/integration/test_content_research_lite_read_model.py tests/e2e/test_content_research_creator_browser.py
git commit -m "feat(creator): render Lite marketing conclusions"
```

### Task 5: Add One Actionable Trace Checkpoint and Complete Acceptance

**Files:**
- Modify: `app/content_research/observation/trace_service.py:536-600`
- Modify: `app/content_research/persistence_models.py`
- Modify: `app/content_research/api_schemas.py`
- Modify: `frontend/src/lib/content-research-api.ts`
- Modify: `frontend/src/app/creator/page.tsx:1560-1590`
- Modify: `docs/features/f003/F003_content_research_lite_delivery_plan.md`
- Test: `tests/unit/test_content_research_trace_service.py`
- Test: `tests/e2e/test_content_research_trace_api.py`
- Test: `tests/e2e/test_content_research_formal_workflow_e2e.py`

**Interfaces:**
- Adds `marketing_conclusion` as the only new public logical checkpoint stage.
- Produces only selected support counts, failure reason codes/recovery action, and replay deltas; candidate counts and raw evidence are excluded.

- [ ] **Step 1: Write failing safe Trace tests**

```python
async def test_trace_projects_only_actionable_marketing_conclusion_facts(trace_service, brief):
    trace = await trace_service.build_trace(workflow_run_id="run_1", brief=brief)
    checkpoint = next(item for item in trace.logical_checkpoints if item["stage"] == "marketing_conclusion")
    assert checkpoint["tracks"]["need"] == {
        "state": "selected",
        "supporting_note_count": 3,
        "independent_author_count": 2,
    }
    assert "candidate_count" not in str(checkpoint)
    assert "statement" not in str(checkpoint)
    assert "note_id" not in str(checkpoint)
    assert "author_id" not in str(checkpoint)
```

- [ ] **Step 2: Write failure and replay trace tests**

Assert `marketing_analysis_unavailable` exposes `waiting_user` and the one recovery action. Assert packet-only replay exposes only `replayed_from_persisted_packets`, `provider_operation_count_delta=0`, and `packet_count_delta=0`; no query, prompt, quote, candidate count, or raw identifier reaches the API.

- [ ] **Step 3: Run RED**

Run: `pytest -q tests/unit/test_content_research_trace_service.py tests/e2e/test_content_research_trace_api.py -k 'marketing_conclusion'`

Expected: FAIL because `marketing_conclusion` is not a supported safe checkpoint projection.

- [ ] **Step 4: Implement the minimal Trace projection**

Add `marketing_conclusion` to the allowed checkpoint stages and safe projection set. Permit only this exact shape:

```python
{
    "stage": "marketing_conclusion",
    "status": record.status,
    "tracks": safe_tracks,
    "reason_codes": safe_reason_codes,
    "recovery_action": safe_recovery_action,
    "replayed_from_persisted_packets": replayed,
    "provider_operation_count_delta": operation_delta,
    "packet_count_delta": packet_delta,
}
```

Include a track's support counts only when selected or when a sample threshold reason needs explanation. Reject all other payload keys by constructing this projection field-by-field rather than recursively passing through the checkpoint payload.

- [ ] **Step 5: Render concise Trace copy**

Map the checkpoint to the title `营销结论判定`. Display the selected/insufficient/tied/unavailable state and translated stable reasons. Do not show candidate counts, policy hashes, quote text, or any IDs. Route `analysis_unavailable` to the existing model-configuration recovery UI rather than a second independent action.

- [ ] **Step 6: Run complete regression and acceptance**

Run: `pytest -q tests/unit/test_content_research_marketing_conclusions.py tests/unit/test_content_research_marketing_conclusion_analysis.py tests/unit/test_content_research_admission_evaluator.py tests/unit/test_content_research_product_marketing_admission.py tests/unit/test_content_research_lite_read_model.py tests/unit/test_content_research_trace_service.py tests/integration/test_content_research_report_store.py tests/integration/test_content_research_lite_read_model.py tests/integration/test_content_research_packet_replay.py tests/e2e/test_content_research_brief_confirm_api.py tests/e2e/test_content_research_trace_api.py tests/e2e/test_content_research_creator_browser.py tests/e2e/test_content_research_formal_workflow_e2e.py`

Run: `npm --prefix frontend test && npm --prefix frontend run build`

Run: `.venv/bin/ruff check app/content_research tests/unit tests/integration tests/e2e`

Run: `git diff --check`

Then execute one configured Creator run for `夏季凉感T恤`, select `product_marketing` and a primary marketing goal, and record: three-track outcome, citation/support counts or exact insufficiency reasons, extra-qualified count, model state, and provider-operation/packet deltas. Do not call the run successful if an external dependency is unavailable.

- [ ] **Step 7: Record evidence and commit Task 5**

Record exact command results and the real acceptance state in `docs/features/f003/F003_content_research_lite_delivery_plan.md`.

```bash
git add app/content_research/observation/trace_service.py app/content_research/persistence_models.py app/content_research/api_schemas.py frontend/src/lib/content-research-api.ts frontend/src/app/creator/page.tsx docs/features/f003/F003_content_research_lite_delivery_plan.md tests/unit/test_content_research_trace_service.py tests/e2e/test_content_research_trace_api.py tests/e2e/test_content_research_formal_workflow_e2e.py
git commit -m "feat(content-research): observe Lite marketing conclusions"
```

## Plan Self-Review

- Task 1 freezes the user choice and makes first intent a formal evidence gate.
- Task 2 adds only the conclusion-level data model and deterministic evaluator needed to enforce the 3-note/2-author contract.
- Task 3 keeps the LLM in a bounded proposal role, makes model failure recoverable, and preserves packet-only replay.
- Task 4 turns the catalog into the specified three-card Lite report without exposing raw note titles as conclusions.
- Task 5 adds exactly one actionable Trace stage and verifies safety, replay invariants, frontend behavior, and real acceptance.
- No task adds uncontrolled collection, a fixed business vocabulary, embeddings, multi-direction synthesis, or a legacy P1 report fallback.
