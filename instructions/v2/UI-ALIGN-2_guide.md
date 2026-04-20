# Development Guide: UI-ALIGN-2 - Topic Pool Explainability Surface

> Generated: 2026-04-15
> Architect: dev-helper / implementation stage
> Status: Ready for development
> Source: `docs/v2/dev_spec.md` §3.5, §5.3, §9.7.2, `docs/v2/development_tasks.md` §2.10, `docs/testing_rules.md`

## 1. Task Context

### Scope Boundary
- Task ID: `UI-ALIGN-2`
- Task Name: `Topic Pool Explainability Surface`
- Phase: `Post-Phase-1 Alignment Backlog`
- Dependencies:
  - V2 topic-pool refresh/list backend already exists
  - `/topic-pool` frontend route already exists
  - current API already returns `evidence_summary`, but not a productized explainability shape
- Task Goal:
  - make `/topic-pool` explainable in-product by exposing evidence provenance details and score-breakdown details for each candidate

### In Scope
- extend the V2 topic-pool list contract to carry operator-facing explainability fields already derivable from backend state
- map those explainability fields into frontend `Topic` types and topic-pool page data
- render expandable or per-row explainability UI on `/topic-pool`
- show both evidence provenance and score breakdown without leaving the console
- add targeted automated tests for the backend/frontend mapping behavior changed by this task

### Out Of Scope
- redesigning the full topic-pool page layout
- adding a new scorer system or changing the core scoring formula semantics
- changing decision-engine behavior
- broad removal of all frontend mock fallback behavior outside the touched topic-pool path

### Required Deliverables
- Production:
  - expanded topic-pool response model and mapping for explainability
  - updated frontend topic model and `/topic-pool` rendering
- Tests:
  - unit coverage for topic-pool service/API explainability payload
  - targeted frontend/unit coverage for API mapping logic if existing frontend test tooling supports it; otherwise extend the nearest deterministic unit coverage on the TS mapping path or existing backend tests to lock the contract
- Spec/Docs:
  - no spec rewrite required for this task; implementation must conform to existing `docs/v2/dev_spec.md` and `docs/v2/development_tasks.md`

### Acceptance Criteria
- [ ] AC1 `/brands/{id}/topic-pool` returns enough explainability data for the UI to render evidence provenance rows and score breakdown details
- [ ] AC2 `/topic-pool` shows an operator-visible explainability surface for each candidate, including evidence provenance rows with source link, original title, interaction metrics, signal type, and contribution weight or relative contribution
- [ ] AC3 `/topic-pool` does not render `final_score` as an opaque number; it exposes scorer-owned component breakdown beside or under the displayed score
- [ ] AC4 explainability rendering works from real API payloads and preserves explicit error behavior rather than masking failures

### Residual Obligations
- Relevant OPEN Residuals:
  - `UI-ALIGN-2`: add expandable evidence provenance views and `final_score` breakdown
- Current-Phase Carry-Forward Items To Re-check:
  - topic-pool page already has real-data/error-state work in progress and must remain compatible
- Resolved By This Task:
  - topic-pool explainability visibility gap
- Deferred / Blocked:
  - broader frontend no-fallback cleanup remains under `UI-ALIGN-3`

### Contract Inventory
- Upstream contracts:
  - `TopicPoolService.list_topic_pool(...)`
  - router response mapping for `/brands/{brand_id}/topic-pool`
- Downstream contracts:
  - `frontend/src/lib/api.ts::getTopicPoolPageData`
  - `frontend/src/lib/types.ts::Topic`
  - `frontend/src/app/topic-pool/page.tsx`
- Files/interfaces with compatibility risk:
  - `app/models/schemas.py`
  - `app/api/routes/router.py`
  - `app/v2/topic_pool/models.py`
  - `app/v2/topic_pool/service.py`
  - `frontend/src/lib/api.ts`
  - `frontend/src/lib/types.ts`
  - `frontend/src/app/topic-pool/page.tsx`

### Test Requirements
- Primary test files:
  - `tests/unit/test_v2_topic_pool_service.py`
  - `tests/unit/test_v2_topic_pool_api.py`
- Required scenarios:
  1. topic-pool list includes explainability payload with score-breakdown and provenance entries
  2. provenance rows expose source URL/title/metrics/signal/contribution fields
  3. score breakdown values are stable and traceable to the stored `final_score`
  4. frontend mapping consumes the enriched payload without breaking current topic fields
- Test target:
  - task-scoped `unit` coverage for backend and mapping logic

## 2. Architecture Context

### System Position
`/brands/{brand_id}/topic-pool`
-> `app/api/routes/router.py`
-> `TopicPoolService.list_topic_pool(...)`
-> `TopicPoolStore`
-> frontend `getTopicPoolPageData(...)`
-> `/topic-pool` page

### Tech Stack
- Language/runtime:
  - Python backend
  - TypeScript / Next.js frontend
- Primary libraries/services:
  - FastAPI + Pydantic response schemas
  - existing V2 topic-pool service/store
  - React client page + shared `DataTable`
- Execution pattern:
  - synchronous backend list assembly + client fetch and render
- Key behavioral constraints:
  - preserve existing route path and base response semantics
  - keep explainability data derived from real persisted state
  - do not invent fake detail rows in runtime

### Constraints
- explainability payload should be derived from `evidence_summary` and persisted topic-pool item fields, not from frontend heuristics alone
- score breakdown must remain consistent with the current scoring formula implemented in `TopicPoolService._build_topic_pool_item(...)`
- frontend changes should fit current page/component patterns and avoid unrelated route refactors

## 3. Technical Design

### 3.1 Module Structure

Files to modify:

| Path | NEW/MODIFY | Required Change | Linked AC |
|------|------------|-----------------|-----------|
| `app/v2/topic_pool/models.py` | MODIFY | enrich list item model with explainability payloads | AC1 |
| `app/v2/topic_pool/service.py` | MODIFY | assemble provenance entries and score breakdown from stored evidence/scoring inputs | AC1, AC3 |
| `app/models/schemas.py` | MODIFY | extend topic-pool response schema for explainability fields | AC1 |
| `app/api/routes/router.py` | MODIFY | map explainability fields into API response | AC1 |
| `frontend/src/lib/types.ts` | MODIFY | extend `Topic` with explainability data | AC2, AC3 |
| `frontend/src/lib/api.ts` | MODIFY | map enriched topic-pool API payload into frontend types | AC2, AC3, AC4 |
| `frontend/src/app/topic-pool/page.tsx` | MODIFY | render explainability UI for provenance and score breakdown | AC2, AC3, AC4 |
| `tests/unit/test_v2_topic_pool_service.py` | MODIFY | validate explainability assembly behavior | AC1, AC3 |
| `tests/unit/test_v2_topic_pool_api.py` | MODIFY | validate API response contract includes explainability fields | AC1 |

### 3.2 Class & Interface Design

Backend additions should stay within the existing topic-pool list contract shape by introducing nested fields such as:

```python
class V2TopicPoolItemResponse(BaseModel):
    ...
    score_breakdown: Dict[str, Any] = Field(default_factory=dict)
    evidence_provenance: List[Dict[str, Any]] = Field(default_factory=list)
```

Frontend `Topic` additions should stay additive:

```ts
interface Topic {
  ...
  scoreBreakdown?: {
    base: number;
    avgSignal: number;
    evidenceVolume: number;
    sourceDiversity: number;
    trendBonus: number;
    finalScore: number;
  };
  evidenceProvenance?: Array<{
    sourceUrl?: string;
    originalTitle: string;
    signalType: string;
    contribution: number;
    likes?: number;
    comments?: number;
    collects?: number;
    shares?: number;
  }>;
}
```

### 3.3 Algorithm & Logic Flow

Core backend flow:

1. keep the existing `final_score` computation unchanged
2. at score-build time, store or reconstruct a deterministic score-breakdown object that matches the formula inputs
3. build evidence provenance rows from deduplicated evidence signals:
   - source URL from content item
   - original title from content item
   - interaction metrics from content item stats/fields
   - signal type from evidence signal
   - contribution weight from evidence signal score or normalized relative share
4. return both structures in `list_topic_pool(...)`

Core frontend flow:

1. fetch enriched `/topic-pool` payload
2. map nested explainability fields into `Topic`
3. render score as before, but add a clear breakdown view
4. render provenance rows in an operator-readable expand/collapse or inline details surface
5. keep explicit loading/error states unchanged

### 3.4 Error Handling

- preserve existing fetch error behavior on `/topic-pool`
- do not synthesize fallback explainability rows when live payload is absent
- if explainability arrays are empty, render an honest empty detail state such as “暂无证据明细”

## 4. Implementation Checklist

- [ ] Extend topic-pool list models/schemas with additive explainability fields
- [ ] Build deterministic `score_breakdown` in the backend from the current formula
- [ ] Build deterministic `evidence_provenance` rows from evidence signals/content items
- [ ] Map new fields through router and frontend API client
- [ ] Extend frontend `Topic` type
- [ ] Render operator-facing breakdown/provenance UI on `/topic-pool`
- [ ] Add/update unit tests for service and API contract

## 5. Testing Plan

- `python3 -m pytest tests/unit/test_v2_topic_pool_service.py tests/unit/test_v2_topic_pool_api.py -q`

Expected coverage focus:
- success path for enriched list response
- evidence provenance row completeness
- score-breakdown to `final_score` consistency
- existing topic-pool list behavior remains intact

## 6. Notes

- Keep this task additive and localized. Do not use it as the vehicle for a wider mock-fallback cleanup across unrelated routes.
- If the existing frontend test stack does not support a small TS unit easily, prefer locking the contract in backend unit tests plus deterministic `frontend/src/lib/api.ts` mapping changes with minimal surface-area risk.
