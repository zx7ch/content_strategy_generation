# Content Research Data Lifecycle P0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve and expose Content Research evidence across Runtime restarts, archive runs instead of deleting them, and minimize active Runtime configuration.

**Architecture:** SQLite remains the authoritative store. Runtime startup publishes only safe storage diagnostics; the Content Research evidence route remains the safe API seam for a new user audit dialog. The end-run action becomes an archive state transition, retaining all run-scoped records.

**Tech Stack:** Python 3.11, FastAPI, SQLite/aiosqlite, Next.js/React/TypeScript, Pytest, Node test runner, PyInstaller acceptance tests.

## Global Constraints

- Work from `localwork`; do not switch package source to `master`.
- Never expose API keys, cookies, tokens, raw provider payloads, or raw login sessions.
- Existing users' LLM and Xiaohongshu UI settings remain in SQLite.
- All production behavior changes begin with a red test.

---

### Task 1: Make ending a run archive it

**Files:**
- Modify: `app/content_research/service.py`
- Modify: `frontend/src/app/creator/page.tsx`
- Test: `tests/e2e/test_content_research_trace_api.py`
- Test: `frontend/src/app/creator/page.test.tsx`

**Interfaces:**
- Consumes: `WorkflowRunManagerRuntime.end_content_research_run(workflow_run_id, thread_id)`.
- Produces: action response with `archived: true`, preserving the same `workflow_run_id` for evidence and report reads.

- [ ] Write a backend test that seeds a run, calls `end_content_research`, and asserts `get_direction_evidence` and `get_lite_report` remain readable.
- [ ] Run the test and verify it fails because the current implementation deletes both workflow and research records.
- [ ] Replace destructive delete calls with a completed archive event and active-run-pointer cleanup.
- [ ] Run the backend test and verify it passes.
- [ ] Update Creator completion copy from “结束并清除” to “已归档，可继续查看”.
- [ ] Add a frontend assertion for the archive copy and run the affected page tests.

### Task 2: Publish safe Runtime storage diagnostics

**Files:**
- Modify: `runtime_main.py`
- Modify: `app/api/routes/router.py`
- Test: `tests/unit/test_runtime_config_migration.py`
- Test: `tests/e2e/test_runtime_connection_layer.py`

**Interfaces:**
- Produces: `/health.runtime_diagnostics = {build_id, sqlite_db_path, db_exists, db_size_bytes, db_modified_at}`.
- The path is the resolved absolute path after Runtime owns configuration.

- [ ] Write a runtime-startup test that supplies legacy storage settings and asserts diagnostics resolve under Application Support.
- [ ] Run it and verify it fails because health does not expose diagnostics.
- [ ] Store a safe immutable diagnostics payload during frozen startup and attach it to health.
- [ ] Run unit and health tests; assert no configured secret value is serialized.

### Task 3: Migrate the active config to the minimal Runtime config

**Files:**
- Modify: `runtime.config.env`
- Modify: `runtime_main.py`
- Test: `tests/unit/test_runtime_config_migration.py`

**Interfaces:**
- Consumes: legacy `$DATA_HOME/config.env`.
- Produces: timestamped backup plus active config containing only template comments and `LOG_LEVEL=INFO`.

- [ ] Write a test with API key, cookie, model, storage-path, and tuning fields; assert a backup contains the input and active config equals the minimum template.
- [ ] Run it and verify it fails because current migration appends missing keys only.
- [ ] Add an allowlist/template migration that rewrites active config safely after backup.
- [ ] Verify a Runtime restart still finds UI-configured credentials in SQLite and storage paths remain Runtime-owned.

### Task 4: Add user-facing candidate audit and JSON export

**Files:**
- Modify: `frontend/src/lib/content-research-api.ts`
- Modify: `frontend/src/app/creator/page.tsx`
- Test: `frontend/src/lib/content-research-api.test.ts`
- Test: `frontend/src/app/creator/page.test.tsx`

**Interfaces:**
- Consumes: `GET /content-research/workflows/{run}/directions/{direction}/evidence`.
- Produces: `getContentResearchDirectionEvidence(...)` and a dialog using safe candidates, selections, exclusions, and packets.

- [ ] Write an API helper test that asserts the evidence URL and safe response handling.
- [ ] Run it and verify it fails because no helper exists.
- [ ] Implement typed helper and minimal safe response types.
- [ ] Write a page test for evidence-only report: “查看候选与筛选” opens the dialog and renders an exclusion reason.
- [ ] Run it and verify it fails because the dialog is absent.
- [ ] Implement the dialog and local JSON Blob download from the safe API response.
- [ ] Run frontend tests and verify no credential-related fields are rendered or exported.

### Task 5: Add frozen Runtime restart release gate

**Files:**
- Modify: `tests/acceptance/test_runtime_release_artifact.py`
- Test: `tests/acceptance/test_runtime_release_artifact.py`

**Interfaces:**
- Consumes: extracted `dist/xhs-runtime.zip` and a controlled persisted Content Research fixture.
- Produces: release proof that the same run survives stop/start and health identifies the same stable DB.

- [ ] Write a failing acceptance case that starts the frozen Runtime with legacy config and asserts health diagnostics plus persisted run reads after restart.
- [ ] Run the narrow acceptance test and verify it fails before implementation.
- [ ] Add only the fixture/setup needed to seed safe run data in the stable DB.
- [ ] Run the acceptance test; assert candidate, Trace, and report reads remain available across restart.
- [ ] Run the relevant Python and frontend release suites.

### Task 6: Package and verify

**Files:**
- Modify: `runtime_main.spec` only if packaging requires the runtime config template explicitly.
- Test: all Task 1–5 tests and package smoke checks.

- [ ] Build `dist/xhs-runtime.zip` from `localwork`.
- [ ] Verify `unzip -t`, health diagnostics, minimal config migration, restart persistence, archive retention, and candidate audit.
- [ ] Record artifact SHA-256 and test results in the release audit.
- [ ] Commit only P0 data-lifecycle/config/audit changes as a focused release fix.
