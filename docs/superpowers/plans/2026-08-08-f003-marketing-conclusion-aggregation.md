# F003 Marketing Conclusion Aggregation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aggregate admitted product-marketing claims into governed candidates and present each track as verified, directional, or insufficient without changing collection.

**Architecture:** Reuse `MarketingConclusionCandidateRecord` as the durable evidence-cluster boundary: a candidate contains one or more claim IDs, and the model only proposes that boundary. The deterministic evaluator continues to own lineage validation and unique note/author counting, adds `directional` below the verified `3 notes / 2 authors` threshold, and exposes that state through publication, read model, trace, and Creator.

**Tech Stack:** Python 3.11, FastAPI, SQLite, pytest/pytest-asyncio, Next.js/React/TypeScript, Playwright.

## Global Constraints

- Do not change Spider execution, query plans, confirmed subject structure, or add a follow-up collection action.
- `selected` remains the only track state allowed to support a direct seeding recommendation and requires at least 3 independent notes and 2 independent authors.
- `directional` is evidence-backed but is never a verified product-performance or direct-investment conclusion.
- Evidence from different people or scenarios may be aggregated; preserve qualifiers such as `儿童` in visible evidence details rather than adding a new audience gate.
- The LLM may propose a candidate but never supplies counts or decision state; all counts and states derive from persisted admitted claims and packets.
- Trace, API, and prompt boundaries must not expose provider payloads, source/author identities, credentials, or raw hidden data.
- Packet-only replay must retain provider-operation and packet identity sets exactly.
- Preserve unrelated local changes, including `app/ingest/xhs_spider` and untracked files.

## Critical Invariants

- **The LLM proposes candidates only; backend code computes evidence counts,
  decision state, and publication eligibility from durable records.** Tasks 1
  and 2 must keep this separation; no model-provided count or state may be
  persisted as authority.
- **`directional` is never a verified conclusion or direct seeding
  recommendation.** Tasks 3 and 4 must preserve this meaning in reports, API
  unions, Trace, and Creator cards.
- **Packet-only replay has zero collection delta and is deterministic.** Task 5
  must prove unchanged provider-operation and packet IDs, plus stable candidate,
  decision, citation, count, and reason identities across repeated replay.

---

### Task 1: Add the directional decision contract and deterministic evaluator matrix

**Files:**
- Modify: `app/content_research/persistence_models.py:178-220`
- Modify: `app/content_research/marketing_conclusions.py:35-315`
- Modify: `tests/unit/test_content_research_marketing_conclusions.py`
- Modify: `tests/integration/test_content_research_report_store.py:20-50`

**Interfaces:**
- `MARKETING_CONCLUSION_DECISION_STATES` includes `directional`.
- `MarketingConclusionTrackEvaluation.state` may be `directional` and retains `candidate_id`, `supporting_note_count`, `independent_author_count`, `body_quote_note_count`, and `reason_codes`.
- `evaluate_marketing_conclusions` returns `directional` when one valid candidate exists but fails note and/or author thresholds; invalid lineage remains terminal `insufficient_evidence`.

- [ ] **Step 1: Write RED decision-matrix tests.** Add a parametrized test using the existing `admitted_claims`, `packets_with_sources_and_authors`, and `candidate` helpers:

```python
@pytest.mark.parametrize(
    ("sources", "expected_state", "expected_notes", "expected_authors"),
    [
        ([("c1", "note_1", "author_a")], "directional", 1, 1),
        ([("c1", "note_1", "author_a"), ("c2", "note_2", "author_a")], "directional", 2, 1),
        ([("c1", "note_1", "author_a"), ("c2", "note_2", "author_b")], "directional", 2, 2),
        ([("c1", "note_1", "author_a"), ("c2", "note_2", "author_a"), ("c3", "note_3", "author_b")], "selected", 3, 2),
        ([("c1", "note_1", "author_a"), ("c2", "note_1", "author_a"), ("c3", "note_1", "author_a")], "directional", 1, 1),
    ],
)
def test_evaluator_classifies_valid_candidate_by_unique_source_threshold(
    sources, expected_state, expected_notes, expected_authors
):
    claim_ids = [item[0] for item in sources]
    result = evaluate_marketing_conclusions(
        candidates=[candidate("need", claim_ids)],
        admitted_claims=admitted_claims(*claim_ids),
        packets=packets_with_sources_and_authors(*sources),
        policy=marketing_policy(),
    )
    track = result.tracks["need"]
    assert (track.state, track.supporting_note_count, track.independent_author_count) == (
        expected_state, expected_notes, expected_authors,
    )
```

Add tests that a missing author, invalid quote span, cross-run packet, non-admitted claim, or wrong direction never becomes `directional`; it remains the existing fail-closed terminal result. Update record round-trip coverage to persist `directional`.

- [ ] **Step 2: Run RED.**

```bash
pytest -q tests/unit/test_content_research_marketing_conclusions.py tests/integration/test_content_research_report_store.py
```

Expected: failures because `directional` is not a permitted record state and below-threshold candidates are classified as `insufficient_evidence`.

- [ ] **Step 3: Implement the minimal evaluator change.** Add `directional` to the durable-state and trace-state allowlists. Change `_terminal_outcome` so it preserves a valid below-threshold candidate as `directional` instead of discarding its identity and counts; preserve `insufficient_evidence` for no candidate or invalid candidate. Keep `catalog` restricted to qualified (`selected`) candidates so existing recommendation logic does not mistake a directional candidate for verified evidence.

- [ ] **Step 4: Add safe-trace assertions.** Extend `test_safe_trace_payload_exposes_only_counts_and_reason_codes` with a directional track and assert it includes only state, counts, and reason codes—no statement, claim ID, note ID, or author ID.

- [ ] **Step 5: Run GREEN.**

```bash
pytest -q tests/unit/test_content_research_marketing_conclusions.py tests/integration/test_content_research_report_store.py
```

- [ ] **Step 6: Commit.**

```bash
git add app/content_research/persistence_models.py app/content_research/marketing_conclusions.py tests/unit/test_content_research_marketing_conclusions.py tests/integration/test_content_research_report_store.py
git commit -m "feat(content-research): classify directional conclusions"
```

### Task 2: Make the analysis proposal contract aggregation-oriented and bounded

**Files:**
- Modify: `app/content_research/marketing_conclusion_analysis.py:43-246`
- Modify: `tests/unit/test_content_research_marketing_conclusion_analysis.py`

**Interfaces:**
- The safe LLM input claim shape becomes `{claim_id, claim_type, intent_id, quote, field_path}`.
- A valid LLM response has at most one candidate for each of `need`, `value`, and `message`.
- The parser returns durable candidates whose `supporting_claim_ids` are sorted unique known IDs; the model response still has exactly `track`, `statement`, and `supporting_claim_ids` per candidate.

- [ ] **Step 1: Write RED protocol tests.** Update the safe-input assertion to expect `claim_type` and `intent_id`, and assert author ID, note ID, source URL, evidence-packet ID, raw payload, ranking, and provider identity remain absent. Add fake-LLM tests for:

```python
{
  "candidates": [
    {"track": "message", "statement": "夏日清凉感表达", "supporting_claim_ids": ["claim_1", "claim_2", "claim_3"]}
  ]
}
```

and for duplicate candidate tracks, unknown IDs, duplicate support IDs, empty support, and an expression claim whose proposed statement contains a prohibited product-performance term. Assert each invalid response raises `MarketingConclusionAnalysisError` before persistence.

- [ ] **Step 2: Run RED.**

```bash
pytest -q tests/unit/test_content_research_marketing_conclusion_analysis.py
```

Expected: the current safe payload lacks `claim_type`/`intent_id`, duplicate track candidates are accepted, and message-angle performance wording is not rejected at proposal parsing.

- [ ] **Step 3: Implement proposal validation.** Build `safe_claims` with the two new claim metadata fields. Replace the system prompt with an explicit aggregation contract: one narrow candidate at most per track, combine mutually supporting claims where possible, preserve visible qualifiers in supporting evidence, use `message_angle` only for content-expression statements, and return no candidate when no coherent statement exists. In `_parse_candidates`, track seen tracks and reject a second candidate for the same track. Add a deterministic validation that rejects a `message` statement containing the existing prohibited outcome vocabulary; do not introduce an audience compatibility gate.

- [ ] **Step 4: Add a regression test for mixed qualifiers.** Use two admitted claims whose quotes include `儿童` and a general audience phrase. Return one multi-claim candidate and assert it parses successfully, keeps both IDs, and the safe prompt carries no identity data. This locks the agreed policy: mixing is permitted, evidence qualifiers remain inspectable downstream.

- [ ] **Step 5: Run GREEN.**

```bash
pytest -q tests/unit/test_content_research_marketing_conclusion_analysis.py
```

- [ ] **Step 6: Commit.**

```bash
git add app/content_research/marketing_conclusion_analysis.py tests/unit/test_content_research_marketing_conclusion_analysis.py
git commit -m "feat(content-research): aggregate marketing claim proposals"
```

### Task 3: Persist directional decisions and compose faithful reports

**Files:**
- Modify: `app/content_research/service.py:3548-3750`
- Modify: `app/content_research/reporting/composer.py:360-520`
- Modify: `app/content_research/reporting/read_model.py`
- Modify: `app/content_research/reporting/publication_materializer.py`
- Modify: `app/content_research/reporting/lite_read_model.py:430-545`
- Modify: `tests/integration/test_content_research_lite_read_model.py`
- Modify: `tests/integration/test_content_research_report_publication_materializer.py`
- Modify: `tests/integration/test_content_research_report_read_model.py`
- Modify: `tests/unit/test_content_research_report_faithfulness.py`

**Interfaces:**
- A directional `MarketingConclusionDecisionRecord` saves `candidate_id`, counts, `reason_codes`, and `additional_qualified_count=0`.
- The Lite API track union gains a directional branch: `{state: "directional", conclusion_id, statement, citation_group_ids, supporting_note_count, independent_author_count, note_gap, author_gap, reason_codes, verification_direction}`.
- Report publications gain `directional_report` only when no marketing track is selected and at least one is directional.

- [ ] **Step 1: Write RED integration tests.** Seed a governed snapshot with a valid one-note candidate and assert:

```python
track = report["sections"]["marketing_conclusions"]["need"]
assert track["state"] == "directional"
assert track["statement"] == "儿童夏季活动后的闷汗方向"
assert track["supporting_note_count"] == 1
assert track["independent_author_count"] == 1
assert track["note_gap"] == 2
assert track["author_gap"] == 1
assert track["citation_group_ids"]
```

Add publication matrix fixtures for all-insufficient → `evidence_only_report`, directional-only → `directional_report`, and selected-plus-directional → `partial_verified_report`. Add a faithfulness test that rejects a directional report if statement, claim IDs, or citations do not match its saved candidate and governed cards.

- [ ] **Step 2: Run RED.**

```bash
pytest -q tests/integration/test_content_research_lite_read_model.py tests/integration/test_content_research_report_publication_materializer.py tests/integration/test_content_research_report_read_model.py tests/unit/test_content_research_report_faithfulness.py
```

Expected: directional records are not projected, the report has no directional publication state, and audit rules only recognise selected conclusions.

- [ ] **Step 3: Implement service and composition.** Persist `directional` from `MarketingConclusionTrackEvaluation` without treating it as a model failure. Include directional decisions when assembling the governed marketing-conclusion payload. Add a composer branch that validates candidate lineage/citations like `selected`, computes `note_gap=max(0, 3-note_count)` and `author_gap=max(0, 2-author_count)`, emits a directional `ReportSection`, and sets a fixed verification direction stating that it is not a direct investment or efficacy conclusion.

- [ ] **Step 4: Implement publication/read-model rules.** Extend publication-state calculation to detect the three matrices from Step 1. Project directional tracks and citations into the Lite read model, preserve old terminal reports, and make priority action choose selected conclusions only; when there are no selected tracks but directional tracks exist, it must instruct validation before strategy rather than recommend seeding.

- [ ] **Step 5: Add mixed-qualifier projection coverage.** Seed a directional candidate with a supporting claim containing `儿童`; assert the report evidence detail/card carries that claim text or quote. Do not require the aggregate statement itself to invent an audience restriction.

- [ ] **Step 6: Run GREEN.**

```bash
pytest -q tests/integration/test_content_research_lite_read_model.py tests/integration/test_content_research_report_publication_materializer.py tests/integration/test_content_research_report_read_model.py tests/unit/test_content_research_report_faithfulness.py
```

- [ ] **Step 7: Commit.**

```bash
git add app/content_research/service.py app/content_research/reporting/composer.py app/content_research/reporting/read_model.py app/content_research/reporting/publication_materializer.py app/content_research/reporting/lite_read_model.py tests/integration/test_content_research_lite_read_model.py tests/integration/test_content_research_report_publication_materializer.py tests/integration/test_content_research_report_read_model.py tests/unit/test_content_research_report_faithfulness.py
git commit -m "feat(content-research): publish directional evidence"
```

### Task 4: Extend trace/API types and Creator’s three-state cards

**Files:**
- Modify: `app/content_research/observation/trace_service.py`
- Modify: `frontend/src/lib/content-research-api.ts:150-290`
- Modify: `frontend/src/app/creator/page.tsx:91-100,1053-1310`
- Modify: `frontend/src/lib/content-research-api.test.ts`
- Modify: `tests/unit/test_content_research_trace_service.py`
- Modify: `tests/e2e/test_content_research_trace_api.py`
- Modify: `tests/e2e/test_content_research_creator_browser.py`

**Interfaces:**
- `ContentResearchMarketingConclusionTraceTrack.state` includes `directional`.
- `ContentResearchMarketingConclusionTrack` adds a directional discriminated-union member matching Task 3.
- `LitePublicationState` and `reportStateLabel` include `directional_report`.
- Creator renders `待验证方向` with counts, gaps, citations, evidence-detail controls, and `不可作为功效或投放定论`.

- [ ] **Step 1: Write RED trace/API/browser tests.** Add a safe trace fixture with a directional count/reason-code projection and assert it contains no candidate text or source identity. Add a frontend API parser test for the directional union. Add a Creator browser fixture whose report has one directional marketing track and assert it:

```python
expect(page.get_by_text("待验证方向", exact=True)).to_be_visible()
expect(page.get_by_text("当前 1 篇 / 1 位作者", exact=True)).to_be_visible()
expect(page.get_by_text("还缺 2 篇独立笔记", exact=True)).to_be_visible()
expect(page.get_by_text("该方向不可作为功效或投放定论", exact=True)).to_be_visible()
expect(page.get_by_text("已验证结论", exact=True)).to_have_count(0)
page.get_by_role("button", name="证据详情").click()
expect(page.get_by_text(re.compile("儿童"))).to_be_visible()
```

- [ ] **Step 2: Run RED.**

```bash
pytest -q tests/unit/test_content_research_trace_service.py tests/e2e/test_content_research_trace_api.py tests/e2e/test_content_research_creator_browser.py -k 'marketing or directional'
npm --prefix frontend test -- content-research-api.test.ts
```

Expected: type unions reject the new state and Creator renders the old "暂无可验证结论" branch.

- [ ] **Step 3: Implement projections and card rendering.** Extend safe trace projection and TypeScript discriminated unions. Keep the existing selected card unchanged. Add a separate directional card branch that renders the candidate statement, counts, gaps, non-investment boundary, and normal citation buttons. Include `directional_report` in state labels and ensure it is not handled by the evidence-only branch, so directional marketing tracks are visible.

- [ ] **Step 4: Run GREEN.**

```bash
pytest -q tests/unit/test_content_research_trace_service.py tests/e2e/test_content_research_trace_api.py tests/e2e/test_content_research_creator_browser.py -k 'marketing or directional'
npm --prefix frontend test -- content-research-api.test.ts
npm --prefix frontend run build
```

- [ ] **Step 5: Commit.**

```bash
git add app/content_research/observation/trace_service.py frontend/src/lib/content-research-api.ts frontend/src/app/creator/page.tsx frontend/src/lib/content-research-api.test.ts tests/unit/test_content_research_trace_service.py tests/e2e/test_content_research_trace_api.py tests/e2e/test_content_research_creator_browser.py
git commit -m "feat(creator): render directional conclusions"
```

### Task 5: Prove idempotent historical packet-only replay and run the focused suite

**Files:**
- Modify: `tests/integration/test_content_research_packet_replay.py`
- Modify: `tests/e2e/test_content_research_formal_workflow_e2e.py`
- Modify: `docs/bugfix/20260807_f003_presearch_to_spider_closure.md`

**Interfaces:**
- `repair_from_persisted_packets` / packet-only replay may recompute conclusion decisions under the new algorithm but may not create provider operations or evidence packets.
- Replaying an unchanged input twice preserves candidate IDs, decision IDs, counts, reason codes, citation identities, and publication state.

- [ ] **Step 1: Write RED replay invariants.** Seed the existing historical product-marketing packet fixture with the seven admitted claims or an equivalent deterministic fixture. Capture provider-operation IDs and packet IDs before replay; replay twice; assert the first report is one selected message track plus two directional tracks and `partial_verified_report`, then assert the second replay has identical decision/candidate/citation IDs and no operation or packet delta.

- [ ] **Step 2: Run RED.**

```bash
pytest -q tests/integration/test_content_research_packet_replay.py tests/e2e/test_content_research_formal_workflow_e2e.py -k 'packet_only or marketing_conclusion or replay'
```

Expected: existing replay has no directional state and publishes the former evidence-only/insufficient projection.

- [ ] **Step 3: Make replay fingerprints intentional.** Update only the conclusion/report algorithm version or checkpoint fingerprint inputs required for the new directional algorithm. Preserve operation and packet fingerprints; do not change dispatch, collection, selection, detail, or packet stages. Ensure prior historical reports remain readable until explicit replay.

- [ ] **Step 4: Run GREEN and full focused verification.**

```bash
pytest -q tests/unit/test_content_research_marketing_conclusions.py tests/unit/test_content_research_marketing_conclusion_analysis.py tests/unit/test_content_research_report_faithfulness.py tests/unit/test_content_research_trace_service.py tests/integration/test_content_research_report_store.py tests/integration/test_content_research_lite_read_model.py tests/integration/test_content_research_report_publication_materializer.py tests/integration/test_content_research_report_read_model.py tests/integration/test_content_research_packet_replay.py tests/e2e/test_content_research_trace_api.py tests/e2e/test_content_research_creator_browser.py tests/e2e/test_content_research_formal_workflow_e2e.py
npm --prefix frontend test -- content-research-api.test.ts
npm --prefix frontend run build
.venv/bin/ruff check app/content_research tests/unit tests/integration tests/e2e
git diff --check
```

- [ ] **Step 5: Record the controlled replay result.** Append the replay run ID, report state, track states, operation delta, packet delta, and rerun identity result to the closure document. Do not include credentials, raw provider payloads, or author identities.

- [ ] **Step 6: Commit.**

```bash
git add tests/integration/test_content_research_packet_replay.py tests/e2e/test_content_research_formal_workflow_e2e.py docs/bugfix/20260807_f003_presearch_to_spider_closure.md
git commit -m "test(content-research): verify directional replay invariants"
```

## Plan Self-Review

- Task 1 covers the exact `selected`/`directional`/`insufficient_evidence` decision table, deduplication, invalid-lineage fail-closed behavior, persistence, and safe trace.
- Task 2 covers aggregation-oriented model input, one-candidate-per-track protocol, controlled fake-LLM decisions, identity redaction, and permitted mixed qualifiers.
- Task 3 covers persistence, provenance, citation faithfulness, gap computation, publication matrix, and priority-action boundaries.
- Task 4 covers trace/API contracts and real Creator interactions, including a directional card that is visibly not a verified or direct-investment conclusion.
- Task 5 covers explicit historical replay, idempotency, zero collection delta, and focused end-to-end verification.
- The plan does not add collection, audience gates, changes to the verified threshold, or any new provider capability.
