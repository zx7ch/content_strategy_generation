# F003 Lite Structured Query Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the single-direction Lite structured-subject, deterministic `2 + 1` query plan, coverage fallback, safe Trace, and packet-only historical recovery without rerunning Spider.

**Architecture:** Pre-research produces a backend-validated `SubjectStructure` and uses the normal Creator composer for ambiguity clarification. A deterministic compiler freezes at most Q1/Q2 plus inactive Q3; the existing directional pipeline deduplicates candidates, evaluates frozen coverage, and activates Q3 once. Safe logical checkpoints explain structure, plan, coverage, fallback, and historical revision; cross-direction single-flight is explicitly deferred to Gate 4B.

**Tech Stack:** Python 3.11 dataclasses and Pydantic, FastAPI, SQLite/aiosqlite, pytest/pytest-asyncio, Next.js/React/TypeScript, Node test runner.

## Global Constraints

- Lite automatically executes exactly one confirmed primary core entity.
- Freeze at most two primary QueryGroups and one inactive Q3; `candidate_cap=20` per group.
- Synonyms are admission equivalents and Q3 material, never a default primary group.
- `detail_fetch_cap=30` remains the per-direction detail-evaluation cap.
- Merge equivalent Q1/Q2 and deduplicate canonical notes inside one direction while retaining all QueryGroup hits.
- Consume existing candidates and packets before Q3 or another provider call.
- Clarification uses the normal composer and same Pre-research run; it is not model recovery.
- Historical runs keep their original query plans and replay only from validated persisted packets.
- Trace stays newest-first and never exposes complete query text, raw user input, note ID, prompts, credentials, headers, or raw provider payload.
- Do not implement cross-direction operation ownership, shared artifacts, bindings, shared failures, or physical/logical double-entry counters in Task 5I.
- Preserve unrelated local changes, including `app/ingest/xhs_spider` and untracked files.

---

### Task 1: Structured Subject and Conversational Clarification

**Files:**
- Create: `app/content_research/subject_structure.py`
- Modify: `app/content_research/presearch/prompts.py`
- Modify: `app/content_research/presearch/service.py`
- Modify: `app/content_research/api_schemas.py`
- Modify: `app/content_research/persistence_models.py`
- Modify: `app/content_research/service.py`
- Modify: `app/content_research/workflow/plan_builder.py`
- Modify: `frontend/src/lib/content-research-api.ts`
- Modify: `frontend/src/app/creator/page.tsx`
- Test: `tests/unit/test_content_research_subject_structure.py`
- Test: `tests/unit/test_content_research_presearch.py`
- Test: `tests/e2e/test_content_research_presearch_api.py`
- Test: `tests/e2e/test_content_research_brief_confirm_api.py`
- Test: `tests/acceptance/test_content_research_creator_ui_contract.py`

**Interfaces:**
- Produces immutable `SubjectEntity`, `SubjectStructure`, `SubjectStructureDecision`, `parse_subject_structure(data, normalized_input)`, and `subject_structure_fingerprint(structure)`.
- API produces `subject_structure`, `subject_structure_hash`, `subject_structure_state`, and stable reason codes; workflow action `clarify_subject` accepts `clarification_text`.

- [x] **Step 1: Write failing subject validation tests**

Cover a grounded `夏季防晒穿搭` structure, empty/ungrounded entities, unresolved `苹果` ambiguity, multiple primary entities, orphan/duplicate synonyms, and malformed JSON. Assert numeric LLM confidence is ignored.

- [x] **Step 2: Run RED**

Run: `pytest -q tests/unit/test_content_research_subject_structure.py tests/unit/test_content_research_presearch.py -k 'structure or grounded or ambiguous'`

Expected: missing subject-structure module/schema behavior.

- [x] **Step 3: Implement the minimal value objects and trust gate**

```python
@dataclass(frozen=True)
class SubjectStructureDecision:
    state: str  # confirmed | needs_confirmation
    structure: SubjectStructure | None
    reason_codes: tuple[str, ...]
```

Normalize Unicode/whitespace/case, ground every raw mention in user input, require one Lite primary entity, validate synonym ownership/duplicates, and use `needs_confirmation` for semantic ambiguity. Keep the existing single bounded format-repair call; a second malformed response remains model-configuration failure.

- [x] **Step 4: Write failing same-run clarification tests**

Create an ambiguous presearch, send `clarify_subject` to the same workflow action endpoint, and assert the run ID is unchanged, external call count remains zero, model-recovery attempt count is unchanged, and the updated structure is confirmed. Confirming a stale/unconfirmed structure hash must fail.

- [x] **Step 5: Implement the API/service clarification boundary**

Allow `subject_structure` in `StageCheckpointRecord`, then persist each structure input/hash and a `subject_structure` checkpoint. `clarify_subject` appends clarification to the same Pre-research input, supersedes only the executable unconfirmed structure, and rejects runs whose formal collection started. Freeze the confirmed structure identity into Brief, Plan, task payloads, and `RunPolicySnapshot` inputs.

- [x] **Step 6: Write and implement the minimal Creator interaction**

Test that the Pre-research card has no input, the normal composer placeholder becomes `补充你要调研的具体对象……`, sending routes to `clarify_subject`, the normal message remains visible, and a valid response renders `核心对象｜意图｜场景` in the card.

- [x] **Step 7: Run GREEN**

Run: `pytest -q tests/unit/test_content_research_subject_structure.py tests/unit/test_content_research_presearch.py tests/e2e/test_content_research_presearch_api.py tests/e2e/test_content_research_brief_confirm_api.py tests/acceptance/test_content_research_creator_ui_contract.py`

- [x] **Step 8: Commit Task 1**

```bash
git add app/content_research/subject_structure.py app/content_research/presearch/prompts.py app/content_research/presearch/service.py app/content_research/api_schemas.py app/content_research/persistence_models.py app/content_research/service.py app/content_research/workflow/plan_builder.py frontend/src/lib/content-research-api.ts frontend/src/app/creator/page.tsx tests/unit/test_content_research_subject_structure.py tests/unit/test_content_research_presearch.py tests/e2e/test_content_research_presearch_api.py tests/e2e/test_content_research_brief_confirm_api.py tests/acceptance/test_content_research_creator_ui_contract.py
git commit -m "feat(content-research): confirm structured Lite subjects"
```

### Task 2: Deterministic Q1/Q2/Q3 Compiler and Frozen Policy

**Files:**
- Create: `app/content_research/workflow/query_planner.py`
- Modify: `app/content_research/workflow/directional_pipeline.py`
- Modify: `app/content_research/persistence_models.py`
- Modify: `app/content_research/contracts.py`
- Modify: `app/content_research/service.py`
- Test: `tests/unit/test_content_research_query_planner.py`
- Test: `tests/unit/test_content_research_contracts.py`
- Test: `tests/e2e/test_content_research_brief_confirm_api.py`

**Interfaces:**
- Produces `PlannedQueryGroup(role, activation, normalized_identity, query_group)` and `CompiledQueryPlan(primary_groups, fallback_group, plan_hash)` from the confirmed structure and direction definition.

- [x] **Step 1: Write failing compiler tests**

Assert Q1 is core entity + primary intent; Q2 is core entity + explicit focus or second direction facet; normalized duplicates merge while retaining both roles; Q2 may be absent; exactly one synonym-based Q3 is frozen inactive; ordering/hash are stable; every group has candidate cap 20 and frozen time window.

- [x] **Step 2: Run RED**

Run: `pytest -q tests/unit/test_content_research_query_planner.py`

Expected: missing compiler and activation metadata.

- [x] **Step 3: Implement the minimal compiler**

Normalize Unicode/case/whitespace/punctuation and confirmed aliases for identity. Do not build a Cartesian product. Q3 uses the next frozen alias plus uncovered focus and has `activation="coverage_fallback"`.

- [x] **Step 4: Freeze and validate the plan**

Allow `query_plan` in `StageCheckpointRecord`. Add structure/hash, `query_compiler_version`, `coverage_policy_version`, primary/fallback caps, group role/activation, and full stable group payload to `locked_query_plan`. Update `_frozen_query_groups`, policy validation, plan hash, and direction relevance QueryGroup IDs. New runs use v2; history remains unchanged.

- [x] **Step 5: Run GREEN**

Run: `pytest -q tests/unit/test_content_research_query_planner.py tests/unit/test_content_research_contracts.py tests/e2e/test_content_research_brief_confirm_api.py`

- [x] **Step 6: Commit Task 2**

```bash
git add app/content_research/workflow/query_planner.py app/content_research/workflow/directional_pipeline.py app/content_research/persistence_models.py app/content_research/contracts.py app/content_research/service.py tests/unit/test_content_research_query_planner.py tests/unit/test_content_research_contracts.py tests/e2e/test_content_research_brief_confirm_api.py
git commit -m "feat(content-research): freeze Lite 2 plus 1 query plans"
```

### Task 3: Direction Coverage Decision and Frozen Q3 Activation

**Files:**
- Modify: `app/content_research/persistence_models.py`
- Modify: `app/content_research/workflow/directional_pipeline.py`
- Modify: `app/content_research/contracts.py`
- Test: `tests/unit/test_content_research_directional_pipeline.py`
- Test: `tests/integration/test_content_research_direction_pipeline_store.py`

**Interfaces:**
- Produces `coverage_decision` and `fallback_decision` checkpoints with staged counts, stable reasons, and the frozen Q3 ID.

- [x] **Step 1: Write failing coverage tests**

Cover independent failures of minimum relevant eligible samples, minimum authors, direct core support, explicit user focus, and invalid/unavailable detail replacement. Assert sufficient Q1/Q2 skips Q3; an unmet condition activates only frozen Q3 once; Q3 exhaustion returns partial/insufficient; refresh/replay preserves the decision and provider operation IDs.

- [x] **Step 2: Run RED**

Run: `pytest -q tests/unit/test_content_research_directional_pipeline.py tests/integration/test_content_research_direction_pipeline_store.py -k 'coverage or fallback or explicit_focus'`

Expected: missing coverage/fallback checkpoints and inactive-group handling.

- [x] **Step 3: Add the two checkpoint stages**

Allow `coverage_decision` and `fallback_decision` in `StageCheckpointRecord`. Fingerprint coverage from plan hash, candidate manifest, direction policy, relevance version, and staged counts; fingerprint fallback from coverage fingerprint plus frozen Q3 ID and stable reason codes.

- [x] **Step 4: Implement staged counts and Q3 activation**

Record discovered, deduplicated, relevant, detail-eligible, admitted, and independent-author counts. Consume the existing per-direction `detail_fetch_cap=30`; invalid prevalidation candidates and duplicate hits do not consume a slot. Exhaust persisted primary candidates before activating Q3. Reuse a completed fallback decision on recovery and never generate a new query.

- [x] **Step 5: Keep existing provider failure scope**

Do not add shared cross-direction behavior. Preserve current single-direction auth, outcome-unknown, automatic retry, note-unavailable replacement, and checkpoint recovery semantics. Q3 is normal control flow and does not increment error/retry counts.

- [x] **Step 6: Run GREEN**

Run: `pytest -q tests/unit/test_content_research_directional_pipeline.py tests/integration/test_content_research_direction_pipeline_store.py`

- [ ] **Step 7: Commit Task 3**

```bash
git add app/content_research/persistence_models.py app/content_research/workflow/directional_pipeline.py app/content_research/contracts.py tests/unit/test_content_research_directional_pipeline.py tests/integration/test_content_research_direction_pipeline_store.py
git commit -m "feat(content-research): activate frozen Lite coverage fallback"
```

### Task 4: Safe Trace, Historical Replay, and Acceptance

**Files:**
- Modify: `app/content_research/persistence_models.py`
- Modify: `app/content_research/service.py`
- Modify: `app/content_research/contracts.py`
- Modify: `app/content_research/workflow/directional_pipeline.py`
- Modify: `app/content_research/observation/trace_service.py`
- Modify: `app/content_research/reporting/read_model.py`
- Modify: `app/content_research/api_schemas.py`
- Modify: `frontend/src/lib/content-research-api.ts`
- Modify: `frontend/src/app/creator/page.tsx`
- Test: `tests/unit/test_content_research_trace_service.py`
- Test: `tests/integration/test_content_research_packet_replay.py`
- Test: `tests/e2e/test_content_research_creator_browser.py`
- Test: `tests/e2e/test_content_research_formal_workflow_e2e.py`
- Modify: `docs/features/f003/F003_content_research_lite_delivery_plan.md`

**Interfaces:**
- Adds safe `subject_structure`, `query_plan`, `coverage_decision`, `fallback_decision`, and `relevance_revision` checkpoint projections.
- Guards `replay_downstream_from_persisted_packets(workflow_run_id)` with an append-only `query_relevance_v2` revision for eligible history.

- [ ] **Step 1: Write failing Trace safety tests**

Assert newest-first workflow ordering is unchanged; structure/plan short hashes, group counts, merged count, staged coverage counts, and fallback reasons are visible; Q3 does not increase error/retry counts; recursive projection excludes complete query, raw subject, note ID, Prompt, secrets, request headers, and raw provider payload. Legacy operations remain readable without fabricated new fields.

- [ ] **Step 2: Write failing historical replay tests**

Seed a legacy snapshot, completed selection, persisted packets, and terminal specialist task. Assert replay appends `relevance_revision`, changes neither provider-operation nor packet ID sets, and republishes from admission onward. Reject mismatched subject/snapshot/query groups, missing packets/checkpoints, and unsupported revisions.

- [ ] **Step 3: Run RED**

Run: `pytest -q tests/unit/test_content_research_trace_service.py tests/integration/test_content_research_packet_replay.py -k 'structure or query_plan or coverage or fallback or revision or safe'`

- [ ] **Step 4: Implement safe logical checkpoint projection**

Allow `relevance_revision` in `StageCheckpointRecord`; `subject_structure`, `query_plan`, `coverage_decision`, and `fallback_decision` were introduced by Tasks 1–3. Project only stage/status/time, short hashes, roles/counts, direction ID, and stable reason codes. Keep existing specialist-scoped provider operation aggregation; do not add consumer/reuse counters.

- [ ] **Step 5: Implement guarded historical revision and replay**

Generate structure from the already confirmed legacy subject with no Spider capability, validate base snapshot and locked QueryGroup identities, append immutable `query_relevance_v2` revision, include its hash/version in admission fingerprints, and enforce identical operation/packet sets before and after publication.

- [ ] **Step 6: Render minimal Trace details**

In the existing expert/Pre-research cards show compact subject status, primary/fallback group counts, merged count, staged coverage, and Q3 state/reason. Keep current timeline order and stage numbers.

- [ ] **Step 7: Run focused regression and build**

Run: `pytest -q tests/unit/test_content_research_subject_structure.py tests/unit/test_content_research_query_planner.py tests/unit/test_content_research_directional_pipeline.py tests/unit/test_content_research_trace_service.py tests/integration/test_content_research_direction_pipeline_store.py tests/integration/test_content_research_packet_replay.py tests/e2e/test_content_research_presearch_api.py tests/e2e/test_content_research_brief_confirm_api.py tests/e2e/test_content_research_creator_browser.py tests/e2e/test_content_research_formal_workflow_e2e.py`

Run: `cd frontend && npx tsc --noEmit && npm run build`

Run: `.venv/bin/ruff check app/content_research tests/unit tests/integration tests/e2e`

Run: `git diff --check`

- [ ] **Step 8: Record evidence and commit Task 4**

Record exact test counts, one new-run acceptance, one historical replay, unchanged operation/packet counts, and publication state in Task 5I. Do not claim cross-direction dedup or Gate 4B completion.

```bash
git add app/content_research/persistence_models.py app/content_research/service.py app/content_research/contracts.py app/content_research/workflow/directional_pipeline.py app/content_research/observation/trace_service.py app/content_research/reporting/read_model.py app/content_research/api_schemas.py frontend/src/lib/content-research-api.ts frontend/src/app/creator/page.tsx tests/unit/test_content_research_trace_service.py tests/integration/test_content_research_packet_replay.py tests/e2e/test_content_research_creator_browser.py tests/e2e/test_content_research_formal_workflow_e2e.py docs/features/f003/F003_content_research_lite_delivery_plan.md
git commit -m "feat(content-research): complete Lite structured query delivery"
```

---

## Plan Self-Review

- Task 1 covers structure trust, ambiguity, same-run composer clarification, and confirmation identity.
- Task 2 covers deterministic Q1/Q2/Q3, normalized deduplication, frozen budgets, and versioning.
- Task 3 covers staged evidence counts, per-direction cap, one-time Q3 activation, and recovery determinism.
- Task 4 covers safe newest-first Trace, eligible historical packet-only replay, frontend visibility, and end-to-end verification.
- Cross-direction single-flight, collection artifacts/bindings, shared failure propagation, and physical/logical double-entry counters are explicitly deferred to Gate 4B.
- Multi-entity decomposition, card input, embeddings, adaptive budgets, multilingual expansion, complex negation, cross-run cache, and repeated query rewriting remain out of scope.
