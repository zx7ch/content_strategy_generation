# Development Guide: P1-3 - Agent + Topic Pool

> Generated: 2026-04-12
> Architect: dev-helper adapted for `docs/v2/development_tasks.md`
> Status: Ready for development
> Source: `docs/v2/development_tasks.md` §2.3, `docs/v2/dev_spec.md` §3.5, §5.1-§5.4, §8.4, §9, §9.7

## 1. Task Context

### Scope Boundary
- **Task ID**: `P1-3`
- **Task Name**: Agent + Topic Pool
- **Phase**: V2 Phase 1
- **Dependencies**:
  - `P1-1` foundation/master-data API 已完成，可提供品牌、channel、policy、state snapshot 的作用域与基础配置
  - `P1-2` ingestion 已完成，可提供 `authors/topics/content_items/content_metrics_snapshots/comments` 作为证据层输入
- **Task Goal**:
  - 让品牌的已入库证据能够通过一条确定性 topic-pool pipeline 生成候选选题，并通过 `/topic-pool` 页面完成“查看 + 刷新”闭环

### In Scope
- 后端新增 `topic-pool` 域，包含：
  - `Competitor Scan Agent`
  - `Pattern Insight Agent`
  - `Topic Hypothesis Agent`
  - `Topic Pool Normalizer / Enricher`
- 新增并暴露：
  - `POST /brands/{id}/topic-pool/refresh`
  - `GET /brands/{id}/topic-pool`
- 规范化持久化：
  - `topic_pool_items`
  - `topics` create-or-reuse
  - normative `evidence_summary`
  - `archive_candidates` status transitions
- 前端：
  - `/topic-pool` 页面接 live API
  - 显示品牌状态、目标受众、候选分数、来源、loading/empty/error
  - 刷新动作触发正式 refresh API
- 测试：
  - service 行为
  - API contract
  - schema/runtime smoke 回归

### Out Of Scope
- 不实现 `Brand Fit Evaluator`、`Topic Pool Scorer`、`Decision Engine`
- 不实现 `ScorerService.ensureFresh(...)` 与 `historical_reward_score` 真实计算
- 不在本轮实现 `/decisions`、`/publish`、`/performance`、`/evaluation` live integration
- 不引入真实 LLM 调用；Phase 1 当前轮以 deterministic rule-based pipeline 代替 agent runtime

### Required Deliverables
- Production:
  - `app/v2/topic_pool/*`
  - `app/api/routes/router.py`
  - `app/main.py`
  - `app/models/schemas.py`
  - `app/v2/ingestion/store.py` / `postgres_store.py` 必要扩展
  - `frontend/src/app/topic-pool/page.tsx`
  - `frontend/src/lib/api.ts`
  - `frontend/src/lib/types.ts`
- Tests:
  - `tests/unit/test_v2_topic_pool_service.py`
  - `tests/unit/test_v2_topic_pool_api.py`
  - 必要的 schema/runtime 相关回归补充
- Spec/Docs:
  - `instructions/P1-3_v2_guide.md`

### Acceptance Criteria
- [ ] AC1 `POST /brands/{id}/topic-pool/refresh` 能基于已入库 evidence 生成一个可追踪的 refresh 结果，并返回新增/归档数量
- [ ] AC2 `Topic Hypothesis Agent` 输出只包含 evidence / insight / proposal 层字段，不包含可执行评分字段
- [ ] AC3 `Topic Pool Normalizer / Enricher` 按 `normalized_name` 去重，create-or-reuse `topics`，并持久化 `topic_pool_items`
- [ ] AC4 `topic_pool_items.evidence_summary` 满足 spec 的 normative schema，`source_count`、`dominant_signal_type`、equal weights 规则正确
- [ ] AC5 `archive_candidates` 会把超出阈值的历史候选改为 `archived`，但不删除数据
- [ ] AC6 `GET /brands/{id}/topic-pool` 返回的列表可支持前端展示品牌状态、目标受众、假设、分数与来源
- [ ] AC7 `/topic-pool` 页面完成 live integration，并在 live 不可用时显示真实 unavailable / error / empty 状态，而不是回退 mock business data
- [ ] AC8 所有读写保持 `workspace_id + brand_id` 作用域隔离

### Residual Obligations
- **Relevant OPEN Residuals**:
  - 当前仓库没有单独维护 V2 residual tracker；本轮把 scorer / decision 相关未完成能力显式保留为后续 phase work
- **Current-Phase Carry-Forward Items To Re-check**:
  - `final_score` 在本轮只允许 deterministic placeholder 计算，不得冒充 P1-4 scorer contract
  - refresh 成功后需保证 `/topic-pool` 可直接读取正式候选库，而不是读取原始 agent 输出
- **Resolved By This Task**:
  - V2 evidence 到 topic pool 的正式生成链路
  - `/topic-pool` 页面 mock-only 边界
- **Deferred / Blocked**:
  - `Brand Fit Evaluator` / `Topic Pool Scorer` / decision selection 仍归属 `P1-4`

### Test Requirements
- **Primary Test Files**:
  - `tests/unit/test_v2_topic_pool_service.py`
  - `tests/unit/test_v2_topic_pool_api.py`
  - `tests/unit/test_v2_schema_contract.py`
- **Test Scenarios**:
  1. refresh 从 content evidence 生成 deterministic proposals
  2. invalid proposal fields 在落库前被拒绝
  3. `normalized_name` 去重与 topic reuse 正确
  4. `evidence_summary` schema 与 equal weights 规则正确
  5. archive threshold 命中时旧候选转为 `archived`
  6. cross-workspace brand 访问被拒绝
  7. API 返回结构满足前端 live path 所需字段
- **Test Target**:
  - unit + API-level tests only

## 2. Architecture Context

- `P1-3` 处于 ingestion evidence 与后续 decision/scorer 之间，职责是把“证据”转成“可重复读取的候选库存”
- 不把 raw agent output 直接存成 executable candidate，而是先经过 deterministic normalizer
- 新域服务需要同时依赖：
  - `MasterDataService` 读取品牌与 workspace scope
  - `IngestionStore` 读取 content evidence、metrics、canonical topics
  - 新增 `TopicPoolStore` 持久化 `topic_pool_items`

### Constraints
- 不得把 scorer refresh 逻辑塞进 `Decision Engine`
- 不得让 explanation-only fields 变成执行输入
- 必须兼容 in-memory runtime，且为 Postgres store 预留接口

## 3. Technical Design

### 3.1 Files to Create/Modify

| Path | NEW/MODIFY | Intent |
|------|------------|--------|
| `app/v2/topic_pool/models.py` | NEW | typed records + refresh/list result models |
| `app/v2/topic_pool/store.py` | NEW | topic pool store protocol + in-memory implementation |
| `app/v2/topic_pool/postgres_store.py` | NEW | Postgres-backed topic pool store |
| `app/v2/topic_pool/service.py` | NEW | deterministic agent pipeline + normalizer/enricher |
| `app/v2/topic_pool/bootstrap.py` | NEW | runtime bootstrap |
| `app/v2/topic_pool/__init__.py` | NEW | exports |
| `app/v2/ingestion/store.py` | MODIFY | add topic lookup/list helpers |
| `app/v2/ingestion/postgres_store.py` | MODIFY | add topic lookup/list helpers |
| `app/models/schemas.py` | MODIFY | add topic-pool request/response schemas |
| `app/api/routes/router.py` | MODIFY | add topic-pool refresh/list endpoints |
| `app/main.py` | MODIFY | initialize shared topic-pool store/service |
| `frontend/src/lib/types.ts` | MODIFY | topic-pool live response types |
| `frontend/src/lib/api.ts` | MODIFY | topic-pool read/refresh client helpers |
| `frontend/src/app/topic-pool/page.tsx` | MODIFY | live refresh flow + states |
| `tests/unit/test_v2_topic_pool_service.py` | NEW | service coverage |
| `tests/unit/test_v2_topic_pool_api.py` | NEW | API contract coverage |

### 3.2 Deterministic Pipeline

1. Load brand-scoped content evidence from ingestion store
2. `Competitor Scan Agent`
   - rank high-signal items by lightweight engagement proxy
   - extract candidate evidence pool
3. `Pattern Insight Agent`
   - summarize repeated tags, topic phrases, and comment pain points
4. `Topic Hypothesis Agent`
   - emit `new_items[]` only with:
     - `normalized_name`
     - `display_name`
     - `topic_type`
     - `title`
     - `angle`
     - `hypothesis`
     - `supporting_evidence_ids`
     - optional `fit_rationale`
     - optional `risk_flags`
5. `Topic Pool Normalizer / Enricher`
   - validate required fields
   - create or reuse canonical `topics`
   - assemble `evidence_summary`
   - upsert `topic_pool_items`
   - run archive transitions

### 3.3 Scoring Boundary

- 本轮 `final_score` 允许使用 deterministic placeholder 公式，仅用于 UI 排序
- placeholder 只能依赖当前 evidence，可用：
  - engagement proxy
  - evidence count
  - source diversity
- 不得读取或模拟 `performance_snapshots` 的正式 scorer contract
- `historical_reward_score` 固定保留为 `0`

### 3.4 Frontend Behavior

- `/topic-pool` live path:
  - 当选择品牌且 API 可用时读取正式 topic pool
  - 点击“刷新选题”触发 refresh endpoint，完成后 revalidate 当前列表
- 页面应显示：
  - 当前品牌
  - brand stage / target audience
  - source mode
  - candidate table
  - empty state
  - loading/submitting state
  - request failure message
- 测试设计必须锁定：
  - 页面运行时 brand/workspace context 已初始化后才发起 live 请求
  - live 请求失败时显示真实错误，不得回退 mock 选题列表

## 4. Validation Notes

- 本任务完成标准不是“agent 智能性足够高”，而是“topic pool 生成链路正式存在、可重复、可追踪”
- 下一推荐任务：`P1-4 决策与评分`
