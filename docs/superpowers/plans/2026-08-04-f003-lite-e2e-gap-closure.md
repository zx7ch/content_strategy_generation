# F003 Lite End-to-End Gap Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close P0 one-shot subject confirmation, user-operated packet replay, and Creator acceptance for `夏季凉感T恤`.

**Architecture:** Keep model proposals at the existing Pre-research boundary, but make the backend trust gate one-shot and expose an explicit structured confirmation action. A replay coordinator first establishes an immutable, compatible relevance revision and then calls the existing packet-only downstream replay; its UI entry is derived from the report projection. Acceptance tests exercise Creator against configured adapters, while deterministic fixtures cover unavailable external services.

**Tech Stack:** Python 3.11, Pydantic, FastAPI, SQLite/aiosqlite, pytest/pytest-asyncio, Next.js/React/TypeScript, Playwright.

## Global Constraints

- A received Pre-research model response is never repaired or regenerated; only transport/configuration recovery can resume the same logical operation.
- User structured values are authoritative, normalized, bounded, deduplicated, and frozen without an LLM or Spider call.
- Packet recovery never owns a source-provider adapter and must preserve provider-operation and evidence-packet identity sets.
- Recovery is available only for an evidence-only `query_subject_not_supported` publication with terminal specialist tasks, persisted packets, and no newer successful matching revision.
- Trace projections remain newest-first and exclude raw subject/query/prompt/note ID/provider payload/credentials/headers.
- Preserve unrelated local changes, including `app/ingest/xhs_spider` and existing untracked files.

---

### Task 1: One-shot structure trust and structured confirmation

**Files:**
- Modify: `app/content_research/subject_structure.py`
- Modify: `app/content_research/api_schemas.py`
- Modify: `app/content_research/service.py`
- Modify: `frontend/src/lib/content-research-api.ts`
- Modify: `frontend/src/app/creator/page.tsx`
- Test: `tests/unit/test_content_research_subject_structure.py`
- Test: `tests/unit/test_content_research_presearch.py`
- Test: `tests/e2e/test_content_research_presearch_api.py`
- Test: `tests/acceptance/test_content_research_creator_ui_contract.py`

**Interfaces:** Add action `confirm_subject_structure` whose payload is `{subject_structure_hash, core_object, research_intent, context_modifiers}`. It returns the normal `ContentResearchPresearchResponse`. `parse_subject_structure` returns `needs_confirmation` for complete-sentence, overlapping-role, empty-first-intent, or incompatible-normalization proposals.

- [ ] **Step 1: Write RED tests.** Assert `夏季凉感T恤` decomposes into `T恤` / `凉感` / `夏季`; semantic failures require confirmation after exactly one model result; confirmation rejects stale hash, accepts non-substring user values, normalizes `,`/`，`/`、`, and does not create an LLM or Spider operation.
- [ ] **Step 2: Run RED.** `pytest -q tests/unit/test_content_research_subject_structure.py tests/unit/test_content_research_presearch.py tests/e2e/test_content_research_presearch_api.py -k 'one_shot or confirmation or subject_structure'`
- [ ] **Step 3: Implement backend.** Extend structural validation, define the Pydantic payload, construct a resolved one-entity `SubjectStructure`, replace only the executable unconfirmed structure, append a safe checkpoint, and reject formal-collection or non-waiting runs. Keep `clarify_subject` only as compatibility behavior; do not call it from Lite Creator.
- [ ] **Step 4: Implement Creator.** Replace the untrusted free-text instruction with a card containing `核心对象 *`, `研究意图 *`, and `使用场景`; use trusted values as defaults and `T恤`/`凉感`/`夏季` only as placeholders for empty values. Submit the explicit action and retain the usual confirmed Brief card.
- [ ] **Step 5: Run GREEN.** Run the Step 2 command plus `npm --prefix frontend test -- content-research-api.test.ts`.

### Task 2: Eligible packet-replay action and recovery projection

**Files:**
- Modify: `app/content_research/api_schemas.py`
- Modify: `app/content_research/service.py`
- Modify: `app/content_research/reporting/lite_read_model.py`
- Modify: `app/content_research/observation/trace_service.py`
- Modify: `frontend/src/lib/content-research-api.ts`
- Modify: `frontend/src/app/creator/page.tsx`
- Test: `tests/integration/test_content_research_packet_replay.py`
- Test: `tests/integration/test_content_research_lite_read_model.py`
- Test: `tests/e2e/test_content_research_creator_browser.py`

**Interfaces:** Add `repair_from_persisted_packets` action. Return an idempotent result containing `status`, `packet_count`, `provider_operation_count`, `subject_structure_state`, and `recovery_projection`; a confirmation-required result leaves the run waiting for `confirm_subject_structure`.

- [ ] **Step 1: Write RED tests.** Seed an eligible evidence-only run and prove one click reuses packets with unchanged operation/packet IDs; prove repeated click reuses the revision; prove missing snapshot, groups, packets, terminal task, stale structure, or a newer successful revision fail closed; prove untrusted historical proposal reaches the structured card with no Spider call.
- [ ] **Step 2: Run RED.** `pytest -q tests/integration/test_content_research_packet_replay.py tests/integration/test_content_research_lite_read_model.py -k 'repair or eligibility or revision or packet'`
- [ ] **Step 3: Implement service coordination.** Add eligibility discovery before `_replay_relevance_context`, use its existing one proposal only when a compatible confirmed structure is absent, append/reuse the immutable revision by fingerprint, and call `replay_downstream_from_persisted_packets` only after confirmation. Capture before/after ID sets around the replay and retain recovery state for refresh.
- [ ] **Step 4: Implement projection and UI.** Project only eligibility, packet count, revision state, authority, short hashes, replayed stage range, and zero-collection facts. Show `使用已有笔记重新处理` solely in the eligible evidence-only report and display `复用 N 条已有笔记 · 新增采集 0 次`; route correction through the Task 1 card.
- [ ] **Step 5: Run GREEN.** Run the Step 2 command plus `pytest -q tests/e2e/test_content_research_creator_browser.py -k 'repair or persisted or recovery'`.

### Task 3: Creator and configured-adapter acceptance

**Files:**
- Modify: `tests/e2e/test_content_research_creator_browser.py`
- Modify: `tests/e2e/test_content_research_formal_workflow_e2e.py`
- Modify: `docs/features/f003/F003_content_research_lite_delivery_plan.md`

- [ ] **Step 1: Write RED acceptance coverage.** Exercise the Creator entry for `夏季凉感T恤`, assert the frozen Q1/Q2/Q3 representation and safe newest-first trace, then cover browser refresh and the eligible historical recovery action. Gate the real OpenAI/Xiaohongshu run on configured credentials and assert recoverable UI state, rather than fabricating success, when either provider is unavailable.
- [ ] **Step 2: Run RED.** `pytest -q tests/e2e/test_content_research_creator_browser.py tests/e2e/test_content_research_formal_workflow_e2e.py -k 'summer or structured or persisted'`
- [ ] **Step 3: Complete the acceptance harness and evidence record.** Use persisted real note citation assertions only after a configured run publishes. Record operation/detail caps, Q3 activation count, report/publication state, trace safety, refresh result, and historical ID equality in the F003 delivery plan.
- [ ] **Step 4: Run GREEN and full focused verification.** `pytest -q tests/unit/test_content_research_subject_structure.py tests/unit/test_content_research_presearch.py tests/integration/test_content_research_packet_replay.py tests/integration/test_content_research_lite_read_model.py tests/e2e/test_content_research_presearch_api.py tests/e2e/test_content_research_creator_browser.py tests/e2e/test_content_research_formal_workflow_e2e.py`; `npm --prefix frontend run build`; `.venv/bin/ruff check app/content_research tests/unit tests/integration tests/e2e`; `git diff --check`.

## Plan Self-Review

- Task 1 covers the P0-1 trust conditions, authoritative structured correction, stale rejection, and no second model call.
- Task 2 covers P0-2 eligibility, idempotency, packet-only execution, recovery refresh, safe observability, and UI entry point.
- Task 3 covers P0-3 configured external acceptance, graceful external failure, trace, refresh, and historical acceptance evidence.
- No task adds vocabulary classification, multiple-intent execution, a Spider fallback, or cross-direction sharing.
