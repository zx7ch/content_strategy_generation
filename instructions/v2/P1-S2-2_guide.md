# Development Guide: P1-S2-2 - Topic Pool Explainability And Scorer Completion

> Generated: 2026-04-19
> Architect: dev-helper / implementation stage
> Status: Ready for development
> Source: `docs/v2/development_tasks.md` §2.11.2, `docs/v2/dev_spec.md` §5.4.2-§5.4.3, §9.7.2, `instructions/v2/P1-3_v2_guide.md`, `instructions/v2/UI-ALIGN-2_guide.md`, `docs/testing_rules.md`

## 1. Task Context

### Scope Boundary
- Task ID: `P1-S2-2`
- Task Name: `Topic Pool Explainability And Scorer Completion`
- Phase: `Phase 1 Stage 2`
- Dependencies:
  - `P1-1` foundation/master-data 已完成，可提供品牌、policy、workspace scope
  - `P1-2` ingestion 已完成，可提供 `content_items` 与 canonical `performance_snapshots` 上游数据
  - `P1-3` topic-pool generation 已完成，但当前 `final_score` 仍是 placeholder
  - `UI-ALIGN-2` 已完成 `/topic-pool` explainability surface，但当前 explainability 仍绑定 placeholder scorer 输出
- Task Goal:
  - 把 topic-pool 从“可生成、可展示”推进到“可正式打分、可刷新、可解释”，并让 `/topic-pool` 上展示的分项分数与 provenance 真正来自 scorer contract，而不是 P1-3 的临时公式

### In Scope
- 实现 `Brand Fit Evaluator`
- 实现 `Topic Pool Scorer`
- 实现 `ScorerService.ensureFresh(...)`
- 用真实 scorer contract 替换 `TopicPoolService` 中的 placeholder score 生成方式
- 让 `historical_reward_score` 仅基于 canonical `performance_snapshots`
- 保持 scorer refresh 为独立边界，不把刷新逻辑塞进 `Decision Engine`
- 保持 `/topic-pool` explainability UI，但将其数据源切换到 scorer-owned component fields
- 补齐 task-scoped backend tests，并验证前端编译

### Out Of Scope
- 不改 `Decision Engine` 的 selection/quota 逻辑
- 不实现反馈后第二批次闭环证明，那属于 `P1-S2-3`
- 不重构 `/topic-pool` 页面布局，只修正其数据契约和 explainability 来源
- 不把 `feedback_events` 并入 `historical_reward_score`
- 不做 Postgres-default runtime convergence，那属于 `P1-S2-4`

### Required Deliverables
- Production:
  - scorer domain/service boundary
  - topic-pool item score refresh/write-back path
  - brand-fit / scorer component persistence and list output
  - `/topic-pool` read path continuing to expose provenance + score breakdown, now backed by scorer-owned values
- Tests:
  - scorer service unit coverage
  - topic-pool API/service regression updates
  - frontend `next build`
- Spec/Docs:
  - 本轮只生成 guide；除非任务 clean，否则不要求同步修改 spec

### Acceptance Criteria
- [ ] AC1 `Brand Fit Evaluator` 仅基于 `brand_policy_configs.hard_filter_rules` 与 `brand_fit_rules` 计算 `fit_score` / violations / pass-fail，不读取 `brand_voice`、`fit_rationale`、`risk_flags`
- [ ] AC2 `Topic Pool Scorer` 计算并写回 `novelty_score`、`fit_score`、`trend_score`、`historical_reward_score`、`policy_score`、`final_score`
- [ ] AC3 `historical_reward_score` 只由 canonical `performance_snapshots` 驱动，且当前 Phase 1 聚合维度仅按 `topic_type`
- [ ] AC4 `ScorerService.ensureFresh(...)` 能识别 stale topic-pool item 并通过 scorer 边界刷新，不把刷新逻辑嵌入 `Decision Engine`
- [ ] AC5 `/brands/{id}/topic-pool` 返回的 `score_breakdown` 与 `final_score` 一致，且 breakdown 值来自 scorer-owned component fields，而不是 UI 端重算
- [ ] AC6 `/topic-pool` 继续支持 evidence provenance table，并在展示 `final_score` 时同时展示 scorer-owned component breakdown
- [ ] AC7 当没有 canonical `performance_snapshots` 时，`historical_reward_score` 可按 spec 回落为 `0` 或 `global_mean` 路径，但行为必须稳定且有测试锁定
- [ ] AC8 所有 scorer 读写继续保持 `workspace_id + brand_id` 作用域隔离

### Residual Obligations
- Relevant OPEN / carry-forward items:
  - `P1-3` carry-forward: `final_score` 仍是 deterministic placeholder，必须在本任务内被真实 scorer contract 替换
  - `UI-ALIGN-2` carry-forward: `/topic-pool` 已展示 breakdown/provenance，但当前 breakdown 不能继续绑定 placeholder 公式
  - `development_tasks.md` §2.11.2: stale topic scores must refresh through `ScorerService.ensureFresh(...)`
- Current-Phase Carry-Forward Items To Re-check:
  - `historical_reward_score` 只允许读取 canonical `performance_snapshots`
  - `Decision Engine` 只能消费 topic-pool 当前分数，不能承担刷新职责
  - operator explainability 仍需从真实 API payload 驱动，不能退回前端 heuristics
- Resolved By This Task:
  - topic-pool placeholder scoring gap
  - scorer refresh boundary gap
  - explainability data source 与 scorer contract 脱节的问题
- Deferred / Blocked:
  - feedback import 后第二轮推荐变化证明 -> `P1-S2-3`
  - Postgres-default 收口 -> `P1-S2-4`
  - guide/spec canonical TS contract reconciliation -> `P1-S2-5`

### Contract Inventory
- Upstream contracts:
  - `brand_policy_configs.hard_filter_rules`
  - `brand_policy_configs.brand_fit_rules`
  - canonical `performance_snapshots`
  - `topic_pool_items` candidate inventory generated by `P1-3`
- Downstream contracts:
  - `GET /brands/{id}/topic-pool`
  - `/topic-pool` page score/provenance rendering
  - future `Decision Engine` consumption of fresh scores
- Files/interfaces with compatibility risk:
  - `app/v2/topic_pool/service.py`
  - `app/v2/topic_pool/models.py`
  - `app/v2/topic_pool/store.py`
  - `app/v2/topic_pool/postgres_store.py`
  - `app/v2/feedback/*`
  - `app/models/schemas.py`
  - `app/api/routes/router.py`
  - `frontend/src/lib/api.ts`
  - `frontend/src/lib/types.ts`
  - `frontend/src/app/topic-pool/page.tsx`

### Test Requirements
- Primary test files:
  - `tests/unit/test_v2_topic_pool_service.py`
  - `tests/unit/test_v2_topic_pool_api.py`
  - `tests/unit/test_v2_feedback_service.py` or nearest scorer-targeted file if score aggregation is colocated there
- Required scenarios:
  1. `Brand Fit Evaluator` respects only executable policy fields and emits deterministic fit outputs
  2. scorer computes all component scores and `final_score` deterministically
  3. `historical_reward_score` aggregates from canonical `performance_snapshots` by `topic_type`
  4. no-history fallback path is stable and explicit
  5. `ensureFresh(...)` refreshes stale items and skips fresh ones
  6. `/brands/{id}/topic-pool` returns scorer-backed breakdown and provenance
  7. cross-workspace access is rejected
  8. frontend compiles against the updated payload
- Test target:
  - backend `unit` + API regression, plus `frontend` build verification

## 2. Architecture Context

### System Position
`performance_snapshots`
-> `ScorerService.ensureFresh(...)`
-> `Brand Fit Evaluator`
-> `Topic Pool Scorer`
-> write back `topic_pool_items.*_score`, `last_scored_at`
-> `GET /brands/{id}/topic-pool`
-> `/topic-pool` explainability surface

### Technical Constraints
- scorer refresh is a dedicated deterministic service boundary, not a concern of `Decision Engine`
- `historical_reward_score` reads only canonical `performance_snapshots`
- current Phase 1 scorer aggregates by `topic_type` only; angle-level granularity is reserved for future extension
- operator-facing breakdown must be derived from persisted scorer component fields, not recomputed ad hoc in the frontend
- preserve current in-memory runtime and keep Postgres store compatibility

## 3. Technical Design

### 3.1 Files To Create Or Modify

| Path | NEW/MODIFY | Required Change | Linked AC |
|------|------------|-----------------|-----------|
| `app/v2/topic_pool/service.py` | MODIFY | remove placeholder score ownership from refresh path; delegate to scorer service / ensureFresh boundary | AC2, AC4 |
| `app/v2/topic_pool/models.py` | MODIFY | formalize scorer-backed component fields and freshness metadata in list/read models | AC2, AC5 |
| `app/v2/topic_pool/store.py` | MODIFY | add scorer read/write helpers if needed for item refresh | AC2, AC4 |
| `app/v2/topic_pool/postgres_store.py` | MODIFY | keep scorer field persistence aligned with in-memory contract | AC2 |
| `app/v2/topic_pool/__init__.py` / `bootstrap.py` | MODIFY | wire scorer service bootstrap | AC4 |
| `app/v2/feedback/service.py` or new `app/v2/topic_pool/scorer.py` | NEW/MODIFY | implement `Brand Fit Evaluator`, `Topic Pool Scorer`, `ScorerService.ensureFresh(...)` | AC1-AC4 |
| `app/v2/feedback/store.py` / Postgres equivalent if needed | MODIFY | expose canonical performance snapshot reads required for scoring | AC3 |
| `app/models/schemas.py` | MODIFY | keep topic-pool response schema aligned with scorer-backed breakdown fields | AC5 |
| `app/api/routes/router.py` | MODIFY | ensure topic-pool list path returns scorer-refreshed breakdown fields | AC5, AC6 |
| `frontend/src/lib/api.ts` | MODIFY | keep mapping aligned with scorer-backed payload | AC5, AC6 |
| `frontend/src/lib/types.ts` | MODIFY | tighten `Topic.scoreBreakdown` around scorer-owned fields | AC5, AC6 |
| `frontend/src/app/topic-pool/page.tsx` | MODIFY | continue rendering breakdown/provenance with scorer-backed values and no fake fallback | AC6 |
| `tests/unit/test_v2_topic_pool_service.py` | MODIFY | add scorer/refresh coverage | AC2-AC5 |
| `tests/unit/test_v2_topic_pool_api.py` | MODIFY | assert API returns scorer-backed breakdown | AC5, AC8 |

### 3.2 Core Design Rules
- `TopicPoolService.refresh_topic_pool(...)` remains responsible for candidate generation and normalization, not long-lived score freshness policy
- scorer should run after candidate persistence through a dedicated service boundary; practical Phase 1 implementation may invoke `ensureFresh(...)` immediately after refresh/list, but the ownership must remain explicit
- `Brand Fit Evaluator` should output:
  - `brand_fit_check`
  - `brand_fit_violations`
  - `fit_score`
- `Topic Pool Scorer` should compute and persist:
  - `novelty_score`
  - `fit_score`
  - `trend_score`
  - `historical_reward_score`
  - `policy_score`
  - `final_score`
  - `last_scored_at`
- `score_breakdown` returned to operators should be a rendering projection of the persisted scorer component fields

### 3.3 Suggested Module Split
- Option A, preferred:
  - `app/v2/topic_pool/scorer.py`
    - `BrandFitEvaluator`
    - `TopicPoolScorer`
    - `ScorerService`
- `app/v2/topic_pool/service.py`
  - candidate generation / normalization only
  - calls scorer service where needed through explicit dependency
- `app/v2/feedback/service.py`
  - remains owner of performance import and canonical snapshot writes

This keeps scorer ownership near topic-pool semantics while still reading canonical feedback data.

### 3.4 Scoring Logic Contract
- `fit_score`
  - derived from executable policy rules only
  - must not inspect presentation-only fields such as `fit_rationale`, `risk_flags`, `brand_voice`
- `historical_reward_score`
  - aggregate canonical `performance_snapshots.composite_reward`
  - grouping key in Phase 1: candidate `topic_type`
  - compute `historical_reward_mean`, `global_mean`, `sample_count`, `confidence_weight`
  - fall back to `global_mean` if no `topic_type` samples exist
  - fall back to `0` if no eligible brand-owned samples exist
- `final_score`
  - explicit deterministic composition of component scores
  - formula/version must be centralized in scorer code, not duplicated in router/frontend

### 3.5 Freshness Contract
- `ensureFresh(...)` should accept at least:
  - `workspace_id`
  - `brand_id`
  - target topic-pool items or brand-scope inventory
  - freshness timestamp / max-age config
- Refresh policy:
  - if `last_scored_at` is missing -> stale
  - if `last_scored_at` older than configured `max_age` -> stale
  - if item is fresh -> no-op
- Config source:
  - read `max_age` from scorer config contract if already present
  - if the config surface is not yet implemented in runtime, use a single deterministic Phase 1 default and record it in tests

### 3.6 API / Frontend Behavior
- `/brands/{id}/topic-pool` should either:
  - guarantee listed items are already fresh, or
  - trigger `ensureFresh(...)` before list assembly
- `/topic-pool` page should not need to know freshness policy
- frontend remains display-only:
  - render score breakdown
  - render evidence provenance rows
  - surface empty/error honestly
  - do not recalculate component scores client-side

### 3.7 Error Handling
- scorer config missing:
  - acceptable to use deterministic default in Phase 1, but behavior must be explicit and tested
- canonical performance snapshot missing:
  - do not fail the whole topic-pool list; use no-history fallback and expose stable score values
- invalid policy rule shape:
  - raise deterministic validation error at evaluator boundary rather than silently treating every candidate as fit

## 4. Implementation Checklist

- [ ] Extract placeholder score calculation out of `TopicPoolService` ownership
- [ ] Implement `Brand Fit Evaluator`
- [ ] Implement `Topic Pool Scorer`
- [ ] Implement `ScorerService.ensureFresh(...)`
- [ ] Persist scorer component fields and `last_scored_at`
- [ ] Wire topic-pool list/refresh paths through scorer freshness boundary
- [ ] Keep evidence provenance output intact while switching breakdown data source to scorer-backed fields
- [ ] Add/update tests for no-history fallback, type-level reward aggregation, stale refresh, and API contract
- [ ] Run backend tests
- [ ] Run `cd frontend && npm run build`

## 5. Testing Plan

- Backend scorer/task scope:
  - `pytest tests/unit/test_v2_topic_pool_service.py tests/unit/test_v2_topic_pool_api.py`
- If scorer logic is split into a dedicated module:
  - add and run `pytest tests/unit/test_v2_topic_pool_scorer.py`
- Frontend verification:
  - `cd frontend && npm run build`

## 6. Assumptions

- 当前仓库还没有完整独立的 scorer config runtime；本任务可先使用 deterministic Phase 1 default `max_age`，只要 boundary 与测试明确锁定
- `UI-ALIGN-2` 的 explainability surface 已可复用，本任务不需要再重做交互，只需要把数据来源替换为真实 scorer contract
- 如果实现过程中发现 `performance_snapshots` 的读取接口不足，应在本任务内一并补齐最小必要 store/service 读取能力，而不是继续沿用 placeholder
