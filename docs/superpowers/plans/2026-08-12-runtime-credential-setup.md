# Runtime Credential Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move user setup into Creator's right sidebar and persist both LLM and Xiaohongshu credentials across Runtime restarts and upgrades.

**Architecture:** Runtime startup owns all user-data paths and removes template path overrides before application settings load. A focused SQLite credential store reconstructs Spider authentication on startup; QR and manual Cookie updates atomically replace it. Creator's existing Model Service card remains the LLM surface and gains a sibling Xiaohongshu Login card with both direct login methods.

**Tech Stack:** FastAPI, SQLite, Pydantic, React/TypeScript, PyInstaller, pytest.

## Global Constraints

- User-owned databases, LLM configuration, Xiaohongshu Cookie, and model cache live outside the executable bundle.
- Public APIs, Trace, logs, error payloads, and UI never return Cookie or LLM Key values.
- QR and Cookie entry are both visible in the Creator right sidebar before a research topic is submitted.
- A failed QR or Cookie replacement does not overwrite an active credential.
- Runtime restart and replacement upgrade preserve both LLM configuration and Xiaohongshu login state.

---

### Task 1: Enforce the Runtime user-data boundary

**Files:**
- Modify: `runtime_main.py`
- Modify: `.env.example`, `config.env`
- Test: `tests/unit/test_runtime_config_migration.py`

**Consumes:** Frozen Runtime startup and the bundled `config.env` template.

**Produces:** A startup path policy which overrides template-relative storage paths and places all durable runtime state in Application Support.

- [x] **Step 1: Write a failing frozen-startup test**

Create a temporary HOME and template containing `SQLITE_DB_PATH=./data/xhs_agent.db`; execute the frozen startup path and assert `SQLITE_DB_PATH`, `CREATOR_THREADS_DB_PATH`, `CHROMA_PERSIST_DIR`, `V2_DISCOVERY_SQLITE_PATH`, and `HF_HOME` all resolve under `Library/Application Support/xhs-growth-agent`.

- [x] **Step 2: Run the test to verify it fails**

Run: `pytest -q tests/unit/test_runtime_config_migration.py`

Expected: failure showing the template-relative SQLite path wins.

- [x] **Step 3: Implement the minimum path policy**

In `runtime_main.py`, preserve non-storage user configuration but force the five storage environment keys to paths under `_data_home` after loading the stable config. Remove storage-path defaults from the distributed templates so first launch cannot reintroduce bundle-relative paths.

- [x] **Step 4: Verify the unit test and package metadata test**

Run: `pytest -q tests/unit/test_runtime_config_migration.py tests/unit/test_package_metadata.py`

Expected: all pass.

### Task 2: Add a redacted local Xiaohongshu credential store

**Files:**
- Modify: `app/content_research/migrations.py`
- Create: `app/services/xhs_credentials.py`
- Modify: `app/services/xhs_qr_auth.py`
- Test: `tests/unit/test_xhs_credentials.py`
- Test: `tests/unit/test_xhs_qr_auth.py`

**Consumes:** Runtime SQLite database path and upstream `XHSPcAuth` Cookie representation.

**Produces:** `XHSCredentialStore.get_active()`, `replace(cookie, source)`, `clear()`, and a redacted `XHSLoginStatus` projection.

- [x] **Step 1: Write failing store tests**

Test that `replace("a1=value; web_session=value", "manual_cookie")` persists an active credential, `get_status()` has source and timestamp but no secret field, and an invalid replacement leaves the previous active value unchanged.

- [x] **Step 2: Run the new store tests to verify they fail**

Run: `pytest -q tests/unit/test_xhs_credentials.py`

Expected: import failure because the store does not exist.

- [x] **Step 3: Implement migration and store**

Add `xhs_local_credentials` with one active record, secret Cookie value, source, status, and timestamps. Validate non-empty Cookie before transactionally replacing the active row. Expose only `authenticated`, `source`, `updated_at`, and a safe failure code through `XHSLoginStatus`.

- [x] **Step 4: Persist QR success and restore startup auth**

Inject the store into `XHSQRLoginSession`; on QR success serialize its Cookie header and replace the record. Add a constructor path that reconstructs `XHSPcAuth.from_cookie()` from the active record before `XHSSpiderClient` is created.

- [x] **Step 5: Verify focused persistence tests**

Run: `pytest -q tests/unit/test_xhs_credentials.py tests/unit/test_xhs_qr_auth.py`

Expected: all pass and no assertion reads a secret through a public projection.

### Task 3: Expose local-only credential actions

**Files:**
- Modify: `app/content_research/api_schemas.py`
- Modify: `app/api/routes/router.py`
- Modify: `app/main.py`
- Test: `tests/e2e/test_xhs_login_api.py`

**Consumes:** `XHSCredentialStore` and `XHSQRLoginSession` from Task 2.

**Produces:** redacted status, QR start/poll, manual save/replace, and clear endpoints.

- [x] **Step 1: Write failing API tests**

Cover unauthenticated status, manual Cookie save, restart-equivalent store reload, clear, and a response-body assertion that the submitted Cookie does not occur in any response or error text.

- [x] **Step 2: Run the API test to verify it fails**

Run: `pytest -q tests/e2e/test_xhs_login_api.py`

Expected: 404 or missing route failure.

- [x] **Step 3: Implement schemas and routes**

Add `GET /content-research/providers/xiaohongshu/login`, `PUT` for manual Cookie replacement, and `DELETE` for clear. Keep existing QR endpoints, but make their status read the durable credential after successful authentication. Wire one credential store into application lifespan and session creation.

- [x] **Step 4: Verify the API contract**

Run: `pytest -q tests/e2e/test_xhs_login_api.py tests/e2e/test_content_research_trace_api.py`

Expected: all pass without provider-network calls.

### Task 4: Add the Creator right-sidebar login card

**Files:**
- Modify: `frontend/src/lib/content-research-api.ts`
- Create: `frontend/src/components/content-research/XiaohongshuLoginCard.tsx`
- Modify: `frontend/src/app/creator/page.tsx`
- Test: `frontend/src/app/creator/page.test.tsx`

**Consumes:** Task 3's redacted status and credential actions.

**Produces:** a sidebar card rendered whenever Content Research mode is selected, before a topic is entered, with QR and Cookie controls simultaneously visible.

- [x] **Step 1: Write failing render tests**

Assert that activating Content Research renders Model Service and Xiaohongshu Login cards before presearch, that both `扫码登录` and `粘贴 Cookie` controls exist, and a saved status displays source/timestamp but never Cookie text.

- [x] **Step 2: Run the focused frontend test to verify it fails**

Run: `cd frontend && npm test -- --run src/app/creator/page.test.tsx`

Expected: missing login-card/control assertion.

- [x] **Step 3: Implement client API and card**

Use password input for Cookie, start/poll QR state, and Save/Replace/Clear actions. Clear the input value after every request. Render the card in the existing research sidebar regardless of run state; do not add a page, modal, or onboarding flow.

- [x] **Step 4: Verify frontend behavior**

Run: `cd frontend && npm test -- --run src/app/creator/page.test.tsx`

Expected: all pass.

### Task 5: Verify restart, upgrade, and release package behavior

**Files:**
- Modify: `tests/unit/test_runtime_config_migration.py`
- Modify: `tests/unit/test_package_metadata.py`
- Modify: `docs/user/getting-started.md`
- Modify: `docs/user/troubleshooting.md`

**Consumes:** Tasks 1–4 and the packaged Runtime build script.

**Produces:** a release artifact that never creates user data in the bundle and whose documentation points users to Creator's right sidebar.

- [x] **Step 1: Write failing built-Runtime boundary test**

Under a temporary HOME, seed an LLM configuration and Xiaohongshu credential in the Runtime database, restart from a second extracted Runtime folder, and assert both safe summaries remain present while the executable folders contain no `data/*.db`.

- [x] **Step 2: Run the test to verify the prior bundle behavior fails**

Run: `pytest -q tests/unit/test_runtime_config_migration.py`

Expected: current template-relative data configuration causes a bundle-local database assertion failure.

- [x] **Step 3: Update user documentation**

Replace file-edit setup instructions with: open Creator, enable Content Research, then configure the Model Service and Xiaohongshu Login cards in the right sidebar. Document Cookie as an alternative to QR, not a config file instruction.

- [x] **Step 4: Build and inspect the release archive**

Run: `bash scripts/build_runtime.sh && unzip -t dist/xhs-runtime.zip | tail -n 1`

Expected: build exits zero and the archive integrity check reports no errors.

- [x] **Step 5: Run the complete relevant regression suite**

Run: `pytest -q tests/unit/test_runtime_config_migration.py tests/unit/test_xhs_credentials.py tests/unit/test_xhs_qr_auth.py tests/e2e/test_xhs_login_api.py tests/e2e/test_content_research_creator_browser.py`

Expected: all pass.
