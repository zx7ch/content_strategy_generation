# F003 Lite Structured Query Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the Lite structured-subject, deterministic `2 + 1` query plan, run-scoped single-flight collection, safe Trace, and packet-only historical recovery without rerunning Spider.

**Architecture:** Pre-research produces a versioned `SubjectStructure` that backend code validates and the user confirms. A deterministic compiler freezes Q1/Q2 and inactive Q3 into `RunPolicySnapshot`; the directional pipeline consumes them through a run-scoped collection ledger that separates physical provider operations from direction-level evaluation and lineage. Durable logical checkpoints explain structure, plan, binding, coverage, fallback, and historical revision while the existing Trace exposes only safe summaries.

**Tech Stack:** Python 3.11 dataclasses and Pydantic, FastAPI, SQLite/aiosqlite, pytest/pytest-asyncio, Next.js/React/TypeScript, Node test runner.

## Global Constraints

- Lite automatically executes exactly one confirmed primary core entity.
- Freeze at most two primary QueryGroups and one inactive coverage fallback; `candidate_cap=20` per group.
- Synonyms are admission equivalents and fallback material, never a default primary group.
- `detail_fetch_cap=30` is a per-direction detail-evaluation cap; physical calls are counted separately.
- Equivalent searches and provider note IDs are physically collected once per run while every logical direction/QueryGroup hit remains replayable.
- Existing candidates and packets are consumed before Q3 or another provider call.
- Clarification uses the normal Creator composer and the same Pre-research run; it is not an LLM recovery attempt.
- Historical runs never receive a new query plan and may only replay when their frozen identities and persisted packets validate.
- Trace remains newest-first and never exposes complete query text, raw user input, provider note ID, prompts, credentials, request headers, or raw provider payload.
- Preserve unrelated local changes, including the dirty `app/ingest/xhs_spider` submodule and existing untracked files.

---

## File Map

- Create `app/content_research/subject_structure.py`: versioned subject value objects, grounding, normalization, validation, safe summary, and fingerprints.
- Modify `app/content_research/presearch/prompts.py`: request the structured subject schema.
- Modify `app/content_research/presearch/service.py`: parse/repair structured output and return `subject_needs_confirmation` separately from model failure.
- Modify `app/content_research/api_schemas.py`: publish safe subject fields and add `clarify_subject` action payload.
- Modify `app/content_research/service.py`: persist structure, clarify the same run, confirm a matching structure, freeze query plans, and append historical revisions.
- Modify `app/content_research/workflow/plan_builder.py`: carry confirmed structure identity into the plan/task inputs.
- Create `app/content_research/workflow/query_planner.py`: deterministic Q1/Q2/Q3 compilation, normalization, deduplication, activation metadata, and plan hashing.
- Modify `app/content_research/contracts.py`: freeze structure/query/coverage/ledger versions and Q3 activation policy.
- Create `app/content_research/workflow/run_collection_ledger.py`: atomic run-level operation reservation, artifact completion, logical bindings, and shared failure lookup.
- Modify `app/content_research/async_pipeline_store.py`: transactional `INSERT ... ON CONFLICT DO NOTHING` reservation/read helpers.
- Modify `app/content_research/persistence_models.py`: allow the new logical checkpoint stages.
- Modify `app/content_research/workflow/directional_pipeline.py`: consume frozen active groups, reuse physical artifacts, count direction evaluations, evaluate coverage, and activate frozen Q3 once.
- Modify `app/content_research/workflow/task_router.py`: route adapter calls through the run collection ledger.
- Modify `app/content_research/observation/trace_service.py`: project physical-call and logical-reuse counters safely.
- Modify `app/content_research/reporting/read_model.py`: include safe new checkpoint summaries.
- Modify `frontend/src/lib/content-research-api.ts`: subject/clarification/Trace contracts.
- Modify `frontend/src/app/creator/page.tsx`: compact subject summary, normal-composer clarification mode, and safe Trace labels.

---

### Task 1: Versioned Subject Structure and Deterministic Trust Gate

**Files:**
- Create: `app/content_research/subject_structure.py`
- Modify: `app/content_research/presearch/prompts.py`
- Modify: `app/content_research/presearch/service.py`
- Test: `tests/unit/test_content_research_subject_structure.py`
- Test: `tests/unit/test_content_research_presearch.py`

**Interfaces:**
- Produces: `SubjectEntity`, `SubjectStructure`, `SubjectStructureDecision`, `parse_subject_structure(data, normalized_input)`, `subject_structure_fingerprint(structure)`.
- `PresearchChecklist.subject_structure` carries the JSON-safe validated structure or a `needs_confirmation` decision.

- [ ] **Step 1: Write failing value-object tests**

```python
def test_structure_requires_grounded_single_core_entity_for_lite():
    decision = parse_subject_structure(
        {"canonical_subject": "防晒服饰", "subject_type": "category",
         "core_entities": [{"canonical_name": "防晒服饰", "raw_mentions": ["防晒穿搭"]}],
         "research_intents": ["穿搭"], "context_modifiers": ["夏季"],
         "synonym_groups": {"防晒服饰": ["防晒衣"]}, "ambiguities": [],
         "resolution_state": "resolved"},
        normalized_input="夏季防晒穿搭",
    )
    assert decision.state == "confirmed"
    assert decision.structure.core_entities[0].raw_mentions == ("防晒穿搭",)

def test_structure_rejects_ungrounded_or_multiple_entities():
    ungrounded = {
        "canonical_subject": "防晒服饰", "subject_type": "category",
        "core_entities": [{"canonical_name": "防晒服饰", "raw_mentions": ["防晒衣"]}],
        "research_intents": ["推荐"], "context_modifiers": ["夏季通勤"],
        "synonym_groups": {}, "ambiguities": [], "resolution_state": "resolved",
    }
    multiple = {
        "canonical_subject": "防晒组合", "subject_type": "compound",
        "core_entities": [
            {"canonical_name": "防晒衣", "raw_mentions": ["防晒衣"]},
            {"canonical_name": "防晒霜", "raw_mentions": ["防晒霜"]},
        ],
        "research_intents": ["搭配"], "context_modifiers": [],
        "synonym_groups": {}, "ambiguities": [], "resolution_state": "resolved",
    }
    assert parse_subject_structure(ungrounded, "夏季通勤").reason_codes == ("core_entity_ungrounded",)
    assert "multiple_primary_entities" in parse_subject_structure(multiple, "防晒衣和防晒霜").reason_codes
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `pytest -q tests/unit/test_content_research_subject_structure.py`

Expected: import failure for `app.content_research.subject_structure`.

- [ ] **Step 3: Implement immutable subject types and code-owned validation**

```python
@dataclass(frozen=True)
class SubjectEntity:
    canonical_name: str
    raw_mentions: tuple[str, ...]

@dataclass(frozen=True)
class SubjectStructure:
    schema_version: str
    canonical_subject: str
    subject_type: str
    core_entities: tuple[SubjectEntity, ...]
    research_intents: tuple[str, ...]
    context_modifiers: tuple[str, ...]
    synonym_groups: tuple[tuple[str, tuple[str, ...]], ...]
    ambiguities: tuple[str, ...]
    resolution_state: str

@dataclass(frozen=True)
class SubjectStructureDecision:
    state: str
    structure: SubjectStructure | None
    reason_codes: tuple[str, ...]
```

Normalize Unicode/whitespace/case, require every raw mention in normalized input, reject empty/orphan/duplicate groups, reject unresolved ambiguity, and reject more than one primary entity for Lite. Do not use numeric LLM confidence.

- [ ] **Step 4: Change the presearch prompt and parser to require the structure**

Add the exact JSON fields from the design and keep the existing checklist fields. On malformed output, retain the existing one bounded format-repair request. A second malformed response remains `waiting_model_config`; a valid but ambiguous structure returns `subject_needs_confirmation` with stable reason codes and no fallback checklist pretending the topic is confirmed.

- [ ] **Step 5: Run subject and presearch tests GREEN**

Run: `pytest -q tests/unit/test_content_research_subject_structure.py tests/unit/test_content_research_presearch.py`

Expected: all selected tests pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add app/content_research/subject_structure.py app/content_research/presearch/prompts.py app/content_research/presearch/service.py tests/unit/test_content_research_subject_structure.py tests/unit/test_content_research_presearch.py
git commit -m "feat(content-research): validate structured Lite subjects"
```

### Task 2: Same-Run Conversational Clarification and Brief Confirmation

**Files:**
- Modify: `app/content_research/api_schemas.py`
- Modify: `app/content_research/service.py`
- Modify: `app/content_research/workflow/plan_builder.py`
- Modify: `frontend/src/lib/content-research-api.ts`
- Modify: `frontend/src/app/creator/page.tsx`
- Test: `tests/e2e/test_content_research_presearch_api.py`
- Test: `tests/e2e/test_content_research_brief_confirm_api.py`
- Test: `frontend/src/lib/content-research-api.test.ts`
- Test: `tests/acceptance/test_content_research_creator_ui_contract.py`

**Interfaces:**
- Consumes: `SubjectStructureDecision` from Task 1.
- Produces: workflow action `clarify_subject`, `ContentResearchPresearchResponse.subject_structure`, `.subject_structure_state`, `.subject_structure_reason_codes`, and a confirmed structure hash stored in Brief/Plan.

- [ ] **Step 1: Write failing API tests for clarification**

```python
async def test_ambiguous_subject_clarifies_in_same_run_without_spider(client):
    response = await client.post(
        "/content-research/presearch",
        json={"seed_text": "苹果适合年轻人吗", "thread_id": "thread-1"},
    )
    created = response.json()
    assert created["status"] == "subject_needs_confirmation"
    clarified = await client.post(
        f"/content-research/workflows/{created['workflow_run_id']}/actions",
        json={"action": "clarify_subject", "payload": {"clarification_text": "苹果品牌，关注年轻人的内容偏好"}},
    )
    assert clarified.json()["workflow_run_id"] == created["workflow_run_id"]
    assert clarified.json()["subject_structure_state"] == "confirmed"
    trace = await client.get(
        f"/content-research/workflows/{created['workflow_run_id']}/trace"
    )
    assert trace.json()["external_api_summary"]["call_count"] == 0
```

Also assert clarification does not increment the presearch model-recovery attempt counter and formal confirmation rejects a stale/unconfirmed structure hash.

- [ ] **Step 2: Run backend API tests RED**

Run: `pytest -q tests/e2e/test_content_research_presearch_api.py tests/e2e/test_content_research_brief_confirm_api.py -k 'clarif or subject_structure'`

Expected: missing action/schema fields.

- [ ] **Step 3: Implement same-run clarification service boundary**

Add `clarify_subject(workflow_run_id, clarification_text)` that appends the clarification to the stored Presearch input, calls the Task 1 Presearch boundary, persists a new input/structure fingerprint and `subject_structure` checkpoint, and updates the existing Brief. It must reject runs whose formal collection already started.

- [ ] **Step 4: Require confirmed structure identity when freezing the plan**

Extend `BriefConfirmation` and `ContentResearchBriefConfirmRequest` with `subject_structure_hash`. `_build_and_persist_confirmed_plan` loads the stored structure, checks state/hash, stores the structure and generation identity in Brief/Plan/task payloads, and refuses free-text subject replacement after confirmation.

- [ ] **Step 5: Write failing frontend contract tests**

Assert the normal composer sends `clarify_subject` when the active run state is `subject_needs_confirmation`, the card contains no text input, and the compact line renders core object/intent/context.

- [ ] **Step 6: Implement the Creator clarification mode**

Keep the card read-only. Change the composer placeholder to `补充你要调研的具体对象……`, route submit to `clarify_subject`, keep the message in the ordinary timeline, update the existing card from the response, and return to ordinary content-research confirmation when valid.

- [ ] **Step 7: Run backend and frontend tests GREEN**

Run: `pytest -q tests/e2e/test_content_research_presearch_api.py tests/e2e/test_content_research_brief_confirm_api.py tests/acceptance/test_content_research_creator_ui_contract.py`

Run: `cd frontend && node --test src/lib/content-research-api.test.ts`

- [ ] **Step 8: Commit Task 2**

```bash
git add app/content_research/api_schemas.py app/content_research/service.py app/content_research/workflow/plan_builder.py frontend/src/lib/content-research-api.ts frontend/src/app/creator/page.tsx tests/e2e/test_content_research_presearch_api.py tests/e2e/test_content_research_brief_confirm_api.py frontend/src/lib/content-research-api.test.ts tests/acceptance/test_content_research_creator_ui_contract.py
git commit -m "feat(content-research): clarify Lite subjects in conversation"
```

### Task 3: Versioned `2 + 1` Query Compiler and Frozen Policy

**Files:**
- Create: `app/content_research/workflow/query_planner.py`
- Modify: `app/content_research/workflow/directional_pipeline.py`
- Modify: `app/content_research/contracts.py`
- Modify: `app/content_research/service.py`
- Test: `tests/unit/test_content_research_query_planner.py`
- Test: `tests/unit/test_content_research_contracts.py`
- Test: `tests/e2e/test_content_research_brief_confirm_api.py`

**Interfaces:**
- Produces: `QueryRole`, `PlannedQueryGroup`, `CompiledQueryPlan`, `compile_lite_query_plan(structure, direction, explicit_focus, run_as_of_at)`.
- Frozen QueryGroup payload adds `role`, `activation`, and `normalized_identity`; Q3 is frozen but inactive.

- [ ] **Step 1: Write failing compiler tests**

```python
def test_compiler_deduplicates_q1_q2_and_keeps_roles():
    plan = compile_lite_query_plan(structure, direction, explicit_focus="夏季穿搭", run_as_of_at=NOW)
    assert len(plan.primary_groups) <= 2
    assert len({g.normalized_identity for g in plan.primary_groups}) == len(plan.primary_groups)
    assert plan.fallback_group.activation == "coverage_fallback"
    assert all(g.role != "synonym_primary" for g in plan.groups)
```

Cover absent focus, duplicate Q1/Q2, frozen synonym fallback, candidate cap 20, stable ordering, and stable hash across insertion order.

- [ ] **Step 2: Run compiler tests RED**

Run: `pytest -q tests/unit/test_content_research_query_planner.py`

- [ ] **Step 3: Implement the focused compiler**

Normalize Unicode/case/whitespace/punctuation and confirmed aliases for identity. Compile Q1 core + primary direction intent; compile Q2 core + explicit focus or second frozen direction facet only when distinct; compile one inactive Q3 alias + uncovered focus. Do not make a Cartesian product.

- [ ] **Step 4: Freeze compiler and coverage versions in the snapshot**

Add `subject_structure`, `subject_structure_hash`, `query_compiler_version`, `coverage_policy_version`, primary/fallback caps, and full group activation metadata to `locked_query_plan`. Update `_frozen_query_groups` and plan hashing to verify every field.

- [ ] **Step 5: Run compiler, contract, and confirmation tests GREEN**

Run: `pytest -q tests/unit/test_content_research_query_planner.py tests/unit/test_content_research_contracts.py tests/e2e/test_content_research_brief_confirm_api.py`

- [ ] **Step 6: Commit Task 3**

```bash
git add app/content_research/workflow/query_planner.py app/content_research/workflow/directional_pipeline.py app/content_research/contracts.py app/content_research/service.py tests/unit/test_content_research_query_planner.py tests/unit/test_content_research_contracts.py tests/e2e/test_content_research_brief_confirm_api.py
git commit -m "feat(content-research): freeze deterministic Lite query plans"
```

### Task 4: Run-Scoped Single-Flight Collection Ledger

**Files:**
- Create: `app/content_research/workflow/run_collection_ledger.py`
- Modify: `app/content_research/persistence_models.py`
- Modify: `app/content_research/async_pipeline_store.py`
- Modify: `app/content_research/workflow/task_router.py`
- Test: `tests/unit/test_content_research_run_collection_ledger.py`
- Test: `tests/integration/test_content_research_run_collection_singleflight.py`

**Interfaces:**
- Produces: `PhysicalOperationIdentity`, `OperationReservation`, `RunCollectionLedger.reserve()`, `.complete()`, `.fail()`, `.bind()`, `.artifact()`.
- Uses deterministic run-owned checkpoint IDs with `subagent_task_id="run_collection:<run_id>"`.

- [ ] **Step 1: Write failing atomic reservation tests**

```python
async def test_concurrent_consumers_get_one_owner_and_one_reuser(db_path):
    identity = PhysicalOperationIdentity.search(
        run_id="run_1", provider="xiaohongshu",
        normalized_query_identity="query-hash", sort="likes",
        time_window={"end_at": "2026-08-04T00:00:00+00:00"},
        candidate_cap=20, cursor=None,
    )
    ledger = RunCollectionLedger(db_path)
    first, second = await asyncio.gather(
        ledger.reserve(identity=identity, consumer_id="pm"),
        ledger.reserve(identity=identity, consumer_id="cp"),
    )
    assert sorted([first.role, second.role]) == ["owner", "reuser"]
    assert first.physical_operation_id == second.physical_operation_id
```

Cover completed reuse, outcome-unknown blocking, one shared auth failure, stable artifact refs, and distinct cursors producing distinct search operations.

- [ ] **Step 2: Run ledger tests RED**

Run: `pytest -q tests/unit/test_content_research_run_collection_ledger.py tests/integration/test_content_research_run_collection_singleflight.py`

- [ ] **Step 3: Extend allowed checkpoint stages**

Add `subject_structure`, `query_plan`, `collection_artifact`, `collection_binding`, `coverage_decision`, `fallback_decision`, and `relevance_revision` to `StageCheckpointRecord` validation. Keep schema `content_research_stage_checkpoint_v1` readable for old stages.

- [ ] **Step 4: Implement atomic reservation in the async persistence session**

Use `BEGIN IMMEDIATE` plus deterministic `INSERT ... ON CONFLICT(id) DO NOTHING`, then read the winning row in the same transaction. Never overwrite an existing running/completed/outcome-unknown physical fact. Store only safe request identity in the operation checkpoint.

- [ ] **Step 5: Persist collection artifacts and bindings separately**

Search artifacts contain persisted candidate/page refs; detail artifacts contain canonical source and normalized packet refs. Binding payloads contain direction ID, QueryGroup ID/role, physical operation ID, artifact ref, `reused`, and `evaluation_slot_consumed`—not raw provider payload.

- [ ] **Step 6: Route adapter calls through the ledger**

Owners perform and complete the adapter call. Reusers await/read the terminal artifact. A failed or outcome-unknown result propagates to all bindings; child completion never deletes the run-owned record.

- [ ] **Step 7: Run ledger tests GREEN**

Run: `pytest -q tests/unit/test_content_research_run_collection_ledger.py tests/integration/test_content_research_run_collection_singleflight.py`

- [ ] **Step 8: Commit Task 4**

```bash
git add app/content_research/workflow/run_collection_ledger.py app/content_research/persistence_models.py app/content_research/async_pipeline_store.py app/content_research/workflow/task_router.py tests/unit/test_content_research_run_collection_ledger.py tests/integration/test_content_research_run_collection_singleflight.py
git commit -m "feat(content-research): single-flight Lite collection calls"
```

### Task 5: Direction Evaluation Cap, Coverage Decisions, and Frozen Q3

**Files:**
- Modify: `app/content_research/workflow/directional_pipeline.py`
- Modify: `app/content_research/workflow/run_collection_ledger.py`
- Modify: `app/content_research/contracts.py`
- Test: `tests/unit/test_content_research_directional_pipeline.py`
- Test: `tests/integration/test_content_research_direction_pipeline_store.py`

**Interfaces:**
- Consumes: frozen active/inactive groups from Task 3 and artifacts/bindings from Task 4.
- Produces: `CoverageDecision` checkpoint with staged counts and `FallbackDecision` checkpoint with stable reason codes.

- [ ] **Step 1: Write failing coverage and cap tests**

```python
async def test_reused_detail_counts_once_per_direction_but_one_physical_call():
    runs = await execute_two_directions_with_shared_note(detail_fetch_cap=3)
    assert runs["pm"].direction_detail_evaluated_count == 1
    assert runs["cp"].direction_detail_evaluated_count == 1
    assert runs["trace"].physical_detail_call_count == 1

async def test_q3_activates_once_for_explicit_focus_gap_and_survives_resume():
    first = await execute_until_fallback_checkpoint()
    resumed = await resume_same_run()
    assert first.fallback_group_id == resumed.fallback_group_id
    assert resumed.provider_queries.count(first.fallback_group_id) == 1
```

Cover minimum samples, independent authors, core support, explicit focus, invalid/unavailable replacement, Q3 exhausted partial state, and Q3 not counted as retry/error.

Define `execute_two_directions_with_shared_note(detail_fetch_cap)` in the same
integration test file to build two frozen direction contracts, one shared fake
adapter note, two logical task executions, and the final safe Trace projection.
Define `execute_until_fallback_checkpoint()` and `resume_same_run()` there to
use one temporary SQLite database and return the persisted fallback ID plus the
capturing adapter's issued QueryGroup IDs.

- [ ] **Step 2: Run focused pipeline tests RED**

Run: `pytest -q tests/unit/test_content_research_directional_pipeline.py tests/integration/test_content_research_direction_pipeline_store.py -k 'fallback or evaluation or coverage or reused'`

- [ ] **Step 3: Implement direction evaluation counting**

Consume an evaluation slot when a distinct canonical note detail is first considered by a direction, regardless of artifact reuse. Do not consume for invalid prevalidation candidates or repeated hits of the same note in the same direction. Stop at frozen cap 30.

- [ ] **Step 4: Implement durable staged coverage and Q3 activation**

Persist discovered/deduplicated/relevant/detail-eligible/admitted/author/focus counts. Activate only the frozen Q3 when a stable reason remains after primary pools are consumed. Reuse an existing fallback decision on replay and publish partial/insufficient focus semantics after Q3 exhaustion.

- [ ] **Step 5: Propagate shared failures with correct scope**

Auth pauses the run once; outcome-unknown blocks all consumers; automatic retry remains physical-operation scoped; note-unavailable lets each direction choose its next persisted candidate without rerunning search.

- [ ] **Step 6: Run directional tests GREEN**

Run: `pytest -q tests/unit/test_content_research_directional_pipeline.py tests/integration/test_content_research_direction_pipeline_store.py`

- [ ] **Step 7: Commit Task 5**

```bash
git add app/content_research/workflow/directional_pipeline.py app/content_research/workflow/run_collection_ledger.py app/content_research/contracts.py tests/unit/test_content_research_directional_pipeline.py tests/integration/test_content_research_direction_pipeline_store.py
git commit -m "feat(content-research): activate deterministic coverage fallback"
```

### Task 6: Safe Trace and Checkpoint Observability

**Files:**
- Modify: `app/content_research/observation/trace_service.py`
- Modify: `app/content_research/reporting/read_model.py`
- Modify: `app/content_research/api_schemas.py`
- Modify: `frontend/src/lib/content-research-api.ts`
- Modify: `frontend/src/app/creator/page.tsx`
- Test: `tests/unit/test_content_research_trace_service.py`
- Test: `tests/e2e/test_content_research_creator_browser.py`

**Interfaces:**
- Produces safe `query_plan_summary`, `coverage_summary`, `fallback_summary`, `collection_reuse_summary`, `physical_detail_call_count`, and direction `detail_evaluated_count` projections.

- [ ] **Step 1: Write failing safe-projection tests**

Assert one physical operation with two bindings appears as call count 1, consumer count 2, reuse count 1; Q3 activation does not increase retry/error counts; newest-first ordering remains; forbidden query/note/prompt/secret strings are absent recursively.

- [ ] **Step 2: Run Trace tests RED**

Run: `pytest -q tests/unit/test_content_research_trace_service.py -k 'physical or binding or coverage or fallback or safe'`

- [ ] **Step 3: Deduplicate physical operations by run-owned identity**

Replace the current `(subagent_task_id, operation_fingerprint)` public aggregation key with stable `physical_operation_id`. Continue reading legacy task-scoped operations when the new ID is absent; never fabricate binding/reuse counts for legacy records.

- [ ] **Step 4: Project logical checkpoints and double counters safely**

Allow only stage/status/time, short hashes, role/count/reason fields, direction ID, consumer/reuse counts, and evaluation usage. Do not publish complete normalized query, raw subject, provider note ID, prompt, request, completion payload, or artifact content.

- [ ] **Step 5: Render concise Trace details in Creator**

Within the existing expert step card show query-plan counts, merged count, physical call count, reuse count, direction evaluation count/cap, coverage counts, and fallback reason. Keep current newest-first event order and workflow stage numbering.

- [ ] **Step 6: Run backend and browser Trace tests GREEN**

Run: `pytest -q tests/unit/test_content_research_trace_service.py tests/e2e/test_content_research_creator_browser.py -k 'trace or subject or fallback or reuse'`

- [ ] **Step 7: Commit Task 6**

```bash
git add app/content_research/observation/trace_service.py app/content_research/reporting/read_model.py app/content_research/api_schemas.py frontend/src/lib/content-research-api.ts frontend/src/app/creator/page.tsx tests/unit/test_content_research_trace_service.py tests/e2e/test_content_research_creator_browser.py
git commit -m "feat(content-research): expose safe Lite query observability"
```

### Task 7: Historical Relevance Revision and End-to-End Acceptance

**Files:**
- Modify: `app/content_research/service.py`
- Modify: `app/content_research/contracts.py`
- Modify: `app/content_research/workflow/directional_pipeline.py`
- Test: `tests/integration/test_content_research_packet_replay.py`
- Test: `tests/e2e/test_content_research_formal_workflow_e2e.py`
- Modify: `docs/features/f003/F003_content_research_lite_delivery_plan.md`

**Interfaces:**
- Produces: append-only `relevance_revision` checkpoint and guarded `replay_downstream_from_persisted_packets(workflow_run_id)` behavior for eligible history.

- [ ] **Step 1: Write failing historical replay tests**

```python
async def test_v1_run_appends_revision_and_replays_without_provider_calls(service):
    before = provider_operation_ids(RUN_ID)
    result = await service.replay_downstream_from_persisted_packets(RUN_ID)
    assert result["provider_operation_count"] == len(before)
    assert provider_operation_ids(RUN_ID) == before
    assert latest_checkpoint(RUN_ID, "relevance_revision").status == "completed"
    assert result["publication_state"] in {"complete_verified_report", "partial_verified_report"}
```

In the same test file, define `provider_operation_ids(run_id)` by filtering
`StageCheckpointRecord` for `stage_name == "operation"`, and define
`latest_checkpoint(run_id, stage)` by sorting matching records by
`(created_at, id)` and returning the final item. The fixture must seed a legacy
snapshot, completed selection, persisted packets, and terminal specialist task;
it must not provide a source adapter to the replay boundary.

Also reject mismatched subject/snapshot/query groups, missing selection/packet checkpoints, unsupported revisions, and any replay that changes operation or packet identity sets.

- [ ] **Step 2: Run replay tests RED**

Run: `pytest -q tests/integration/test_content_research_packet_replay.py`

- [ ] **Step 3: Implement append-only revision validation**

Generate the structure from the already confirmed subject and locked legacy plan with no Spider capability, validate base snapshot/query identities, persist `query_relevance_v2` revision identity, include its hash/version in admission fingerprints, and leave the original snapshot/contract unchanged.

- [ ] **Step 4: Extend downstream replay guardrails**

Require terminal successful/partial specialist tasks, completed selection and packet checkpoints, at least one persisted packet, matching revision identities, and identical provider-operation/packet sets before and after publication.

- [ ] **Step 5: Run full focused regression**

Run: `pytest -q tests/unit/test_content_research_subject_structure.py tests/unit/test_content_research_query_planner.py tests/unit/test_content_research_run_collection_ledger.py tests/unit/test_content_research_directional_pipeline.py tests/unit/test_content_research_trace_service.py tests/integration/test_content_research_run_collection_singleflight.py tests/integration/test_content_research_direction_pipeline_store.py tests/integration/test_content_research_packet_replay.py tests/e2e/test_content_research_presearch_api.py tests/e2e/test_content_research_brief_confirm_api.py tests/e2e/test_content_research_formal_workflow_e2e.py`

Run: `cd frontend && npx tsc --noEmit && npm run build`

- [ ] **Step 6: Run hygiene checks**

Run: `.venv/bin/ruff check app/content_research tests/unit tests/integration tests/e2e`

Run: `git diff --check`

- [ ] **Step 7: Record exact acceptance evidence**

Update Task 5I with test counts, one new-run ID, one historical replay run ID, before/after provider operation counts, physical/reuse/evaluation Trace counters, and publication state. Do not mark complete if a real authenticated canary required by Gate 4B was not executed.

- [ ] **Step 8: Commit Task 7**

```bash
git add app/content_research/service.py app/content_research/contracts.py app/content_research/workflow/directional_pipeline.py tests/integration/test_content_research_packet_replay.py tests/e2e/test_content_research_formal_workflow_e2e.py docs/features/f003/F003_content_research_lite_delivery_plan.md
git commit -m "feat(content-research): complete Lite structured query delivery"
```

---

## Plan Self-Review

- Spec coverage: Tasks 1–3 cover subject trust, clarification, compact confirmation, `2 + 1`, normalization, deduplication, and frozen versions; Tasks 4–5 cover single-flight, artifacts, bindings, shared failures, direction caps, staged coverage, and Q3; Task 6 covers safe newest-first observability; Task 7 covers history and no-Spider replay.
- Scope: multi-entity auto decomposition, card input, embeddings, adaptive run-wide budget, multilingual expansion, complex negation, cross-run cache, and repeated LLM query rewriting are absent by design.
- Type consistency: `SubjectStructure`, `CompiledQueryPlan`, `RunCollectionLedger`, `CoverageDecision`, physical operation identity, structure/plan hashes, and checkpoint stage names have one spelling throughout the tasks.
- Recovery consistency: clarification is same-run Pre-research without recovery-budget use; provider recovery is physical-operation scoped; historical replay cannot compile or execute a new query plan.
