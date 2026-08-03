# F003 Presearch OpenAI Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route Content Research presearch through the configured OpenAI model instead of the unavailable Kimi Coding membership.

**Architecture:** Keep the shared model router unchanged. Change only the presearch request from the `cheap_fast` policy (Kimi) to the existing `balanced` policy (OpenAI), so the request uses `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `OPENAI_MODEL` without affecting other LLM consumers.

**Tech Stack:** Python, pytest, shared `LLMService` model-policy router.

## Global Constraints

- Do not print or persist API keys.
- Do not change Xiaohongshu collection or report-admission behavior.
- Do not change the global meaning of `cheap_fast` for other consumers.
- Preserve presearch timeout and fallback behavior.

---

### Task 1: Route presearch to OpenAI

**Files:**
- Modify: `tests/unit/test_content_research_presearch.py`
- Modify: `app/content_research/presearch/service.py`

**Interfaces:**
- Consumes: `LLMRequest.model_policy` and the existing `balanced` route.
- Produces: presearch requests with `model_policy="balanced"`.

- [x] **Step 1: Add the failing routing assertion**

Add `assert service._presearch._llm.requests[0].model_policy == "balanced"` to the successful presearch test.

- [x] **Step 2: Verify RED**

Run `pytest -q tests/unit/test_content_research_presearch.py::test_presearch_success_creates_workflow_brief_trace_and_observation` and confirm it fails because the actual policy is `cheap_fast`.

- [x] **Step 3: Implement the minimal routing change**

Change the presearch `LLMRequest` to `model_policy="balanced"`.

- [x] **Step 4: Verify GREEN and regressions**

Run `pytest -q tests/unit/test_content_research_presearch.py tests/unit/test_llm_router.py tests/e2e/test_content_research_presearch_api.py`.

- [x] **Step 5: Verify the configured provider externally**

Restart the backend, send one minimal request through the default LLM service with `model_policy="balanced"`, and confirm the response reports provider `openai` without exposing credentials.
