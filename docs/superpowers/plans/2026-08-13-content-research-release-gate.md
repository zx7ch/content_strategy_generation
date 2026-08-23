# Content Research Release Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the failures discovered in real Creator usage reproducible release gates, with actionable UI recovery copy and bounded browser verification.

**Architecture:** The release script builds one frozen Runtime and runs deterministic artifact checks against it. Frontend library/component tests protect error-code mapping and in-flight controls; optional environment switches run real LLM and XHS network smoke checks without putting credentials in CI. The tag workflow runs only deterministic checks before uploading an asset.

**Tech Stack:** Bash, Python/pytest, PyInstaller, Node test runner, React/jsdom, GitHub Actions.

## Global Constraints

- No API key, Cookie, or other credential may appear in test output or build artifacts.
- Persistent user credentials survive an upgrade; release-owned feature flags must not preserve an obsolete disabled state.
- A browser release test must have a finite timeout and save diagnostics when it expires.
- `RELEASE_REAL_LLM_SMOKE=1` and `RELEASE_LIVE_XHS_LOGIN_SMOKE=1` are local/manual-only checks.

---

### Task 1: Preserve machine-readable API failures through the frontend client

**Files:**
- Create: `frontend/src/lib/content-research-error-feedback.ts`
- Create: `frontend/src/lib/content-research-error-feedback.test.ts`
- Modify: `frontend/src/lib/content-research-api.ts`

- [ ] Write a test asserting `F003_LITE_PREVIEW_DISABLED` maps to an upgrade/restart instruction and an unknown error retains a safe server message.
- [ ] Run `npm test -- src/lib/content-research-error-feedback.test.ts` and verify it fails because the module does not exist.
- [ ] Add the typed error code projection and the smallest mapping function required by the test.
- [ ] Re-run the test and verify it passes.

### Task 2: Make login and presearch errors actionable and prevent duplicate submissions

**Files:**
- Modify: `frontend/src/components/content-research/XiaohongshuLoginCard.tsx`
- Modify: `frontend/src/app/creator/page.tsx`
- Modify: `frontend/src/app/creator/page.test.tsx`

- [ ] Write component tests for Cookie validation feedback and disabled controls while Cookie/QR requests are in flight.
- [ ] Run `npm test -- src/app/creator/page.test.tsx` and verify the new assertions fail.
- [ ] Use the error feedback mapping in Cookie, QR, and presearch failures; preserve entered Cookie after failure and disable duplicate actions during requests.
- [ ] Re-run the focused frontend tests and verify they pass.

### Task 3: Add a deterministic frozen-artifact release gate

**Files:**
- Create: `tests/acceptance/test_runtime_release_artifact.py`
- Create: `scripts/run_release_gate.sh`
- Modify: `.github/workflows/release.yml`

- [ ] Write artifact tests that require the release zip, launcher, and frozen executable when `RELEASE_GATE_REQUIRE_ARTIFACT=1`.
- [ ] Run the focused test before creating the script and verify it fails when the archive requirement is enabled.
- [ ] Build, integrity-check the zip, run config migration/API/package regressions, and run the artifact test in `scripts/run_release_gate.sh`.
- [ ] Invoke the script from the release workflow before the asset upload.

### Task 4: Bound browser and live-provider release checks

**Files:**
- Create: `scripts/run_creator_browser_gate.py`
- Modify: `scripts/run_release_gate.sh`
- Modify: `pyproject.toml`

- [ ] Add a runner with a 300-second maximum and a captured log file on timeout.
- [ ] Add documented `RELEASE_REAL_LLM_SMOKE` and `RELEASE_LIVE_XHS_LOGIN_SMOKE` branches that cannot run accidentally in CI.
- [ ] Run deterministic gates and confirm they pass; report live gates separately when credentials/network are unavailable.
