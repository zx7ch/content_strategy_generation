# F003 Lite Admission and Publication Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Admit evidence using the author identity Spider actually supplies, keep transient report-publication gaps out of permanent chat history, and replay the current run from persisted packets without another Spider call.

**Architecture:** Author independence uses a conservative namespaced identity: `id:<author_id>` when present, otherwise `name:<normalized author>`, with identical display names collapsed to one author. A packet-only replay entry reads the completed selection and packet checkpoints and invokes admission directly; governance, snapshot composition, audit, publication, and materialization then append a newer immutable report version. Creator treats HTTP 404 and `published report artifact is missing` as a temporary publication-not-ready state and keeps polling.

**Tech Stack:** Python, SQLite, pytest, Next.js, TypeScript, Node test runner.

## Global Constraints

- Do not copy an author display name into the `author_id` field.
- Prefer a stable author ID; use normalized author name only when the provider did not return an ID.
- Collapse identical normalized names so fallback identity cannot inflate independent-author counts.
- A packet with neither author ID nor author name remains ineligible.
- Downstream replay must not expose or accept discovery/detail/comment callbacks and must not create Provider operation records.
- Existing packets, source projections, provider operations, and collection checkpoints remain immutable.
- A newer report version may be appended; the existing Creator `artifact_result` message remains the single run-scoped timeline message.

---

### Task 1: Spider-compatible author identity contract

**Files:** `app/content_research/workflow/directional_pipeline.py`, `app/content_research/admission/evaluator.py`, `app/content_research/contracts.py`, `tests/integration/test_content_research_direction_pipeline_store.py`.

- [x] Write a failing integration test proving author-name-only packets can satisfy the frozen sample threshold, while duplicate normalized names count once and missing identities remain ineligible.
- [x] Run the targeted test and verify the existing `eligible_source_count == 0` behavior fails the new expectation.
- [x] Implement `admission_author_identity(projection)` and use it consistently for eligibility and independent-author counting.
- [x] Record the identity kind in decision diagnostics without fabricating `author_id`.
- [x] Run directional pipeline and admission evaluator tests.

### Task 2: Publication-not-ready UI semantics

**Files:** `frontend/src/lib/content-research-api.ts`, `frontend/src/lib/content-research-api.test.ts`, `frontend/src/app/creator/page.tsx`.

- [x] Write failing unit tests for HTTP 404 and `published report artifact is missing` being classified as pending, while unrelated 500 errors remain fatal.
- [x] Preserve response status in the thrown API error and implement `isContentResearchReportPending(error)`.
- [x] Replace message-regex-only checks in polling and restoration with the shared classifier.
- [x] Verify that a pending publication returns `null`, keeps polling, and never appends a permanent report-read error.
- [x] Run frontend unit tests and TypeScript typecheck.

### Task 3: Packet-only downstream replay

**Files:** `app/content_research/workflow/directional_pipeline.py`, `app/content_research/service.py`, `tests/integration/test_content_research_direction_pipeline_store.py`, `tests/unit/test_content_research_governed_completion.py`.

- [x] Write a failing integration test that persists collection checkpoints and successfully replays admission through an entry with no provider callback.
- [x] Implement `DirectionalEvidencePipeline.replay_admission_from_persisted_packets(...)` by loading completed selection/detail and packet checkpoints and calling `_run_admission` directly.
- [x] Implement `ContentResearchService.replay_downstream_from_persisted_packets(workflow_run_id)` to replay admission, governance, snapshot, audit, publication, and materialization.
- [x] Assert Provider operation count and packet IDs are unchanged across replay.
- [x] Run targeted backend tests.

### Task 4: Replay the current acceptance run and record evidence

**Files:** `docs/features/f003/F003_content_research_lite_delivery_plan.md`, `docs/superpowers/plans/2026-08-02-f003-lite-task-5g-trace-parity.md`.

- [x] Record baseline packet IDs, Provider operation count, existing publication ID/state, and admission counts for `run_04a898dc71634c3fa7f49ddff3bc6a65`.
- [x] Invoke only `replay_downstream_from_persisted_packets` for that run.
- [x] Verify packet IDs and Provider operation count are unchanged, new decisions include admitted evidence, and the latest report is readable without refresh.
- [x] Verify the Creator timeline still contains one run-scoped `artifact_result` message.
- [x] Record exact acceptance evidence and retain Task 5G-2B as separate timing scope.
