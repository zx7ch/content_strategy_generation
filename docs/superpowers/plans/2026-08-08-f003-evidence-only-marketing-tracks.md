# F003 Evidence-Only Marketing Tracks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the three governed product-marketing tracks for an evidence-only Lite report without promoting unsupported evidence to a conclusion.

**Architecture:** Keep the immutable report artifact unchanged. The Lite read model projects terminal marketing-track decisions and the safe next-step action even when the publication is evidence-only; Creator renders those terminal cards while continuing to hide non-governed findings and weak signals.

**Tech Stack:** Python, pytest, FastAPI read model, Next.js/React, Playwright browser E2E.

## Global Constraints

- Do not replay reports or mutate persisted report artifacts.
- Do not call Spider, LLM, or embedding services.
- `insufficient_evidence` renders reasons and a verification direction only; it has no statement, citations, or direct-investment recommendation.
- Evidence-only main findings and weak signals remain hidden.

---

### Task 1: Preserve terminal marketing tracks in the Lite projection

**Files:**
- Modify: `tests/integration/test_content_research_lite_read_model.py`
- Modify: `app/content_research/reporting/lite_read_model.py:232-257`

**Interfaces:**
- Consumes: materialized `evidence_only_report` containing terminal product-marketing decisions.
- Produces: `sections.marketing_conclusions` with `need`, `value`, and `message`, and a safe `sections.priority_action`.

- [ ] **Step 1: Write the failing test**

Add an evidence-only fixture with three `insufficient_evidence` marketing decisions and assert the public reader result contains all three terminal tracks, contains each verification direction, contains no `statement` or `citation_group_ids` on a terminal track, and retains `main_findings == []` and `weak_signals == []`.

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `pytest tests/integration/test_content_research_lite_read_model.py -q`

Expected: failure because the evidence-only branch returns `{}` for `marketing_conclusions` and `null` for `priority_action`.

- [ ] **Step 3: Write the minimal implementation**

In `LiteReportReader._published_projection`, retain the projected marketing tracks and priority action for an evidence-only publication. Keep only `main_findings` and `weak_signals` suppressed by `is_evidence_only`.

- [ ] **Step 4: Run the focused test to verify it passes**

Run: `pytest tests/integration/test_content_research_lite_read_model.py -q`

Expected: PASS.

### Task 2: Render terminal cards in Creator for evidence-only reports

**Files:**
- Modify: `tests/e2e/test_content_research_creator_browser.py`
- Modify: `frontend/src/app/creator/page.tsx:1229-1287`

**Interfaces:**
- Consumes: Lite `marketing_conclusions` terminal-track objects.
- Produces: three visible report cards with terminal reasons and verification direction.

- [ ] **Step 1: Write the failing browser test**

Seed an `evidence_only_report` with the three terminal marketing decisions. Assert the published report shows all three track headings, `暂无可验证结论`, and the verification direction; assert it does not show a citation-detail button or a conclusion statement; retain the existing assertions that core findings, observations, and leads remain hidden.

- [ ] **Step 2: Run the focused browser test to verify it fails**

Run: `pytest tests/e2e/test_content_research_creator_browser.py -q -k evidence_only`

Expected: failure because the Creator condition `!evidenceOnly && hasMarketingTracks` suppresses all marketing cards.

- [ ] **Step 3: Write the minimal implementation**

Change only the marketing-track and safe-priority-action render guards to use `hasMarketingTracks`, not `!evidenceOnly`. Keep evidence-only suppression for findings, observations, weak signals, direction status, and citation buttons outside supported track states.

- [ ] **Step 4: Run the focused browser test to verify it passes**

Run: `pytest tests/e2e/test_content_research_creator_browser.py -q -k evidence_only`

Expected: PASS.

### Task 3: Verify the regression boundary

**Files:**
- Verify: `tests/integration/test_content_research_lite_read_model.py`
- Verify: `tests/e2e/test_content_research_creator_browser.py`

- [ ] **Step 1: Run API/read-model regressions**

Run: `pytest tests/integration/test_content_research_lite_read_model.py -q`

Expected: PASS.

- [ ] **Step 2: Run Creator browser regressions**

Run: `pytest tests/e2e/test_content_research_creator_browser.py -q`

Expected: PASS.

- [ ] **Step 3: Run frontend type/lint validation**

Run: `npm run lint --prefix frontend`

Expected: PASS with no lint errors.

- [ ] **Step 4: Commit the bugfix**

```bash
git add app/content_research/reporting/lite_read_model.py frontend/src/app/creator/page.tsx tests/integration/test_content_research_lite_read_model.py tests/e2e/test_content_research_creator_browser.py
git commit -m "fix: show evidence-only marketing tracks"
```
