# Development Guide: WS-P1-ALIGN - Web Search Phase 1 Alignment

> Generated: 2026-04-06
> Architect: dev-helper / implementation stage
> Status: Ready for development
> Source: `dev_spec.md` §1.3, §1.5.8, §1.6, §11.4 and `docs/testing_rules.md`

## 1. Task Context

### Scope Boundary
- **Task ID**: `WS-P1-ALIGN`
- **Task Name**: Web Search Phase 1 Alignment
- **Phase**: Architecture Optimization / Phase 1
- **Dependencies**:
  - Current code already contains `web_search` Phase 1 foundation
  - `dev_spec.md` has open residual `RES-ARCH-WS-001`
- **Task Goal**:
  - Align implementation to Phase 1 only
  - Remove/undo Phase 2/3 runtime behavior that was started early
  - Update spec progress and residual state to match actual implementation

### In Scope
- Keep unified `web_search` core models, provider protocol, Spider provider, and minimal orchestrator/service
- Keep `ContentStrategyAgent` on the unified search entry instead of direct Spider usage
- Remove Phase 3 runtime features from production code paths:
  - `BrowserCaptureProvider`
  - `EvidenceStore`
  - capture API endpoint
  - imported evidence persistence and strategy merge behavior
- Remove Phase 2 runtime behavior not required for Phase 1:
  - config-driven multi-provider routing semantics
  - provider-order logic that implies multiple runtime providers are active
- Update `dev_spec.md` residual/progress text to state:
  - Phase 1 foundation is implemented
  - Phase 2/3 remain pending

### Out Of Scope
- Do not continue Phase 2 development
- Do not continue Phase 3 development
- Do not add capture/manual import behavior
- Do not add new public APIs beyond what Phase 1 already needs
- Do not implement Playwright or any browser automation runtime

### Required Deliverables
- Production:
  - `web_search` module reduced to Phase 1-compliant runtime scope
  - `ContentStrategyAgent` still working via unified search entry
  - removal of premature Phase 2/3 runtime paths
- Tests:
  - update/remove tests that covered Phase 3 behavior
  - keep/add tests proving Phase 1 behavior
- Spec/Docs:
  - `dev_spec.md` residual/progress synchronized with actual implementation state

### Acceptance Criteria
- [ ] AC1 `ContentStrategyAgent` no longer directly depends on `XHSSpiderClient`, but still retrieves data through the unified Phase 1 web search entry
- [ ] AC2 Production runtime no longer exposes `POST /sessions/{session_id}/web-search/capture`
- [ ] AC3 Production runtime no longer persists imported/capture evidence or merges it into strategy discovery
- [ ] AC4 Web search runtime provider set is Spider-only for now; future provider/capture work remains spec-only
- [ ] AC5 `dev_spec.md` no longer says the code is still Spider-direct, and residual state clearly records “Phase 1 done, Phase 2/3 pending”

### Residual Obligations
- **Relevant OPEN Residuals**:
  - `RES-ARCH-WS-001`: current code/spec mismatch around web search unification progress
- **Resolved By This Task**:
  - close the “Spider direct” part of `RES-ARCH-WS-001`
  - keep Phase 2/3 pending work visible as OPEN carry-forward

### Contract Inventory
- Upstream contracts:
  - `dev_spec.md` §1.6 Web Search 架构优化
  - `ContentStrategyAgent` execution contract
- Downstream contracts:
  - strategy unit/integration tests
  - session API contract
- Compatibility risks:
  - removing capture API/models without breaking unrelated router tests
  - keeping strategy regression behavior stable

### Test Requirements
- **Primary Test Files**:
  - `tests/unit/test_web_search.py`
  - `tests/unit/test_strategy_agent.py`
  - `tests/integration/test_strategy_workflow.py`
  - `tests/unit/test_router.py`
- **Test Scenarios**:
  1. Spider provider maps posts to normalized evidence
  2. Phase 1 orchestrator returns Spider discover results
  3. Strategy runs successfully through unified web search entry
  4. Router no longer exposes capture import path
- **Test Target**:
  - unit + targeted integration only

---

## 2. Architecture Context

### System Position
- `StrategyAgent` should consume the Phase 1 unified web search entry
- Spider is the only active runtime provider in Phase 1
- capture/evidence-store/provider-order work stays in spec as future phases

### Constraints
- Preserve current strategy user-visible behavior
- Minimize scope: remove premature runtime paths instead of expanding them
- Keep code aligned with `dev_spec.md` Phase 1 boundary

---

## 3. Technical Design

### 3.1 Module Structure

**Files to Modify**

| Path | Change | Intent |
|------|--------|--------|
| `app/services/web_search/orchestrator.py` | MODIFY | Reduce runtime behavior to Phase 1 Spider-only flow |
| `app/services/web_search/__init__.py` | MODIFY | Export only Phase 1 runtime components |
| `app/agents/content_strategy_agent.py` | MODIFY | Keep unified search entry, no imported evidence assumptions |
| `app/api/routes/router.py` | MODIFY | Remove capture API route |
| `app/models/schemas.py` | MODIFY | Remove capture request/response models |
| `app/memory/session_data_store.py` | MODIFY | Remove imported evidence persistence tables/methods |
| `tests/unit/test_web_search.py` | MODIFY | Keep Spider + orchestrator Phase 1 tests only |
| `tests/unit/test_router.py` | MODIFY | Remove capture API test and add absence/404 coverage if useful |
| `tests/integration/test_strategy_workflow.py` | MODIFY | Remove imported evidence integration scenario |
| `dev_spec.md` | MODIFY | Sync residual/progress to actual Phase 1 status |

### 3.2 Interface Design

**Phase 1 runtime public entry**

- `SearchOrchestrator.discover(intent, limit=50) -> EvidenceBatch`
- Active runtime provider set: `XhsSpiderDiscoverProvider` only

**Interfaces not active in runtime**

- `BrowserCaptureProvider`
- `EvidenceStore`
- capture API request/response contracts

These should be removed from current code paths rather than left half-active.

### 3.3 Algorithm & Logic Flow

**Phase 1 flow**

`ContentStrategyAgent`
-> `SearchOrchestrator.discover()`
-> `XhsSpiderDiscoverProvider`
-> normalize to `Evidence`
-> convert to `XHSPost`
-> existing strategy / RAG / expansion logic

No imported evidence merge.
No capture/manual path.
No multi-provider runtime selection.

### 3.4 Implementation Checklist
- [ ] Remove capture API endpoint and schemas
- [ ] Remove imported evidence storage and related code paths
- [ ] Reduce orchestrator/provider runtime to Spider-only Phase 1 behavior
- [ ] Keep strategy green on unified entry
- [ ] Update tests to Phase 1 scope
- [ ] Update `dev_spec.md` residual/progress text

### 3.5 Error Handling Strategy

- Preserve existing Spider failure mapping used by strategy:
  - provider/auth/rate-limit/permanent failures -> `SPIDER_SERVICE_UNAVAILABLE`
  - empty discover result -> `INSUFFICIENT_DATA`
- Do not introduce new API errors in this task

---

## 4. Testing Strategy

### Required Commands

```bash
python3 -m pytest tests/unit/test_web_search.py tests/unit/test_strategy_agent.py tests/unit/test_router.py tests/integration/test_strategy_workflow.py -q
```

### Required Scenario Mapping

- `AC1`: strategy tests + web search unit tests
- `AC2`: router tests
- `AC3`: integration/unit tests should no longer reference imported evidence
- `AC4`: web search unit tests verify Spider-only runtime path
- `AC5`: manual spec audit after code changes

---

## 5. Progress Tracking Requirements

- Update `dev_spec.md` `RES-ARCH-WS-001` to reflect:
  - Phase 1 foundation implemented
  - Phase 2/3 pending
  - no longer claim “current code still Spider direct”
- If any premature Phase 2/3 code remains intentionally deferred, keep it visible as OPEN carry-forward
- Do not mark the full web search architecture effort as complete
