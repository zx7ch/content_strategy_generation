# Runtime XHS Login Packaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a macOS Runtime that can save a Xiaohongshu Cookie and render a QR login image after PyInstaller packaging.

**Architecture:** Make the two lazily imported runtime dependencies explicit PyInstaller inputs. Add a focused packaging-contract test that protects the frozen dependency manifest, then rebuild the existing distribution and exercise its login endpoints under an isolated HOME.

**Tech Stack:** Python 3.11, PyInstaller, FastAPI, pytest, curl_cffi, qrcode.

## Global Constraints

- Never print, persist, or include user Cookie values or LLM API Keys in test output.
- Preserve the existing Spider direct-network behavior; this fix is dependency packaging only.
- Validate the generated `dist/xhs-runtime.zip`, not just development imports.

---

### Task 1: Lock the frozen login dependency contract

**Files:**
- Modify: `tests/unit/test_runtime_launcher.py`
- Modify: `runtime_main.spec`

**Interfaces:**
- Consumes: PyInstaller `Analysis.hiddenimports` in `runtime_main.spec`.
- Produces: a regression test requiring `qrcode` and `curl_cffi` in the frozen hidden-import list.

- [ ] **Step 1: Write the failing test**

```python
def test_runtime_bundle_declares_lazy_xhs_login_dependencies() -> None:
    spec = Path("runtime_main.spec").read_text(encoding="utf-8")

    assert '"qrcode"' in spec
    assert '"curl_cffi"' in spec
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest -q tests/unit/test_runtime_launcher.py::test_runtime_bundle_declares_lazy_xhs_login_dependencies`

Expected: FAIL because neither dynamic import is declared.

- [ ] **Step 3: Add the two explicit hidden imports**

```python
# XHS QR login and Chrome-impersonating Cookie transport are lazy imports.
"qrcode",
"curl_cffi",
```

- [ ] **Step 4: Run the launcher tests**

Run: `.venv/bin/pytest -q tests/unit/test_runtime_launcher.py`

Expected: PASS.

### Task 2: Rebuild and validate the release artifact

**Files:**
- Modify: `dist/xhs-runtime/` (generated)
- Modify: `dist/xhs-runtime.zip` (generated)

**Interfaces:**
- Consumes: `scripts/build_runtime.sh` and `runtime_main.spec`.
- Produces: a distribution containing the two runtime imports.

- [ ] **Step 1: Rebuild the Runtime**

Run: `bash scripts/build_runtime.sh`

- [ ] **Step 2: Validate artifact contents and archive integrity**

Run: `find dist/xhs-runtime/_internal -maxdepth 1 -iname 'qrcode*' -o -iname 'curl_cffi*'; unzip -t dist/xhs-runtime.zip`

Expected: both dependencies present and archive reports no errors.

- [ ] **Step 3: Exercise isolated packaged login endpoints**

Start the packaged executable under a temporary `HOME`, submit a synthetic Cookie (`a1=test`) to the Cookie endpoint, then start the QR endpoint. The Cookie response must be a successful redacted status; the QR response must not report `qr_render_failed` or a missing-module error.

### Task 3: Creator release acceptance audit

**Files:**
- Create: `docs/release/2026-08-13-content-research-release-audit.md`

**Interfaces:**
- Consumes: the deployed frontend, isolated Runtime, Playwright Creator browser tests.
- Produces: severity-ranked, reproducible user-impact findings and coverage boundaries.

- [ ] **Step 1: Run existing Creator browser regression suite**

Run: `.venv/bin/pytest -q tests/e2e/test_content_research_creator_browser.py`

- [ ] **Step 2: Manually exercise the Creator release path with Playwright**

Cover Runtime connection state, model validation result, Cookie save feedback, QR feedback, presearch failure/recovery, and visible error copy.

- [ ] **Step 3: Record findings**

For each finding state severity, steps, current behavior, user impact, and whether it is fixed in this release.
