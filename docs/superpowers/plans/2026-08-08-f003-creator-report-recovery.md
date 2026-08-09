# Creator Report Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Recover a previously published Lite report when the local runtime becomes reachable shortly after Creator restores its thread.

**Architecture:** Retry only transport failures from `getContentResearchLiteReport` with bounded delays. Preserve immediate handling for valid HTTP error responses so pending or malformed reports are never hidden.

**Tech Stack:** Next.js, TypeScript, Playwright.

### Task 1: Add bounded report-read recovery

**Files:**
- Modify: `frontend/src/app/creator/page.tsx`
- Test: `tests/e2e/test_content_research_creator_browser.py`

- [ ] Write a browser test where the first report request fails and a later retry returns the frozen report.
- [ ] Run it and confirm the current one-shot restore fails.
- [ ] Add a three-attempt, network-only report reader used by thread restore and direct-run restore.
- [ ] Re-run the browser test and production build.
