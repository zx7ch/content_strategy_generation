# Development Guide: P1-S2-6 - Demo Dataset And Test Case Entry

> Generated: 2026-05-03
> Architect: dev-helper / implementation stage
> Status: Ready for development
> Source: `docs/v2/development_tasks.md` §2.11.6, §2.12, `docs/v2/dev_spec.md` §3.2, §3.3, §3.4, §3.5, §3.6, §9.0, §9.7.2, §9.7.3, §11, `docs/testing_rules.md`

## 1. Task Context

### Scope Boundary
- Task ID: `P1-S2-6`
- Task Name: `Demo Dataset And Test Case Entry`
- Phase: `Phase 1 Stage 2`
- Dependencies:
  - `P1-1` foundation/master-data 已完成（`workspaces`、`brands`、`brand_channels`、`brand_policy_configs`、`brand_state_snapshots` 可由 service 创建）
  - `P1-2` ingestion 已完成（`POST /brands/{id}/source-syncs` 与 `IngestionService` 可作为正式入口）
  - `P1-3` topic-pool 生成 已完成（`TopicPoolService.refresh_topic_pool(...)` 是 deterministic 入口，无 LLM 调用）
  - `P1-S2-2` scorer 已落地（`ScorerService.ensureFresh(...)`、`BrandFitEvaluator`、`TopicPoolScorer` 已实现）
  - `P1-S2-3` second-batch 反馈闭环已有 acceptance 覆盖（`tests/acceptance/test_v2_phase1_release_gate.py`）
  - `P1-S2-4` Postgres 收敛已基本完成（所有模块的 `postgres_store.py` 与 `tests/acceptance/test_v2_p1_2_postgres_runtime.py` 已就位）
  - `P1-S2-5` 前端契约对齐已完成（`9.7.3` canonical TS 契约就绪）
- Task Goal:
  - 以一份固定的服饰品牌 fixture 数据集，让 Phase 1 闭环（ingestion -> topic pool -> decision -> publish -> performance -> second decision）在 extension capture 与文件上传产品化（`P1-S2-1`）完成之前可被一键加载、可演示、可回归
  - 数据是合成的，但写入路径必须复用正式 ingestion / topic-pool / decision / publish / performance 服务边界，前端展示走真实运行态读路径，`示例` 标签是真实数据上的可见标记，不是 mock 渲染分支

### In Scope
- 在 `brands` 表新增 `is_demo BOOLEAN NOT NULL DEFAULT FALSE` 字段，作为 demo provenance 的 canonical schema 标记
- 新增 `app/v2/demo` 模块，承载 `DemoDatasetService` 与 fixture 加载逻辑
- 在 `app/v2/demo/fixtures/apparel_brand_v1.json`（或等价 Python 常量）中预置一份固定的服饰品牌 demo 数据集
- 实现 demo loader endpoints：
  - `POST /workspaces/{workspace_id}/demo-datasets/load`
  - `DELETE /workspaces/{workspace_id}/demo-datasets`
  - `GET /workspaces/{workspace_id}/demo-datasets`
- `Brand` API 响应模型加 `is_demo` 字段
- topic-pool / decision-batch / publish-record / performance-snapshot 的列表/详情响应通过 brand 解析后附带 `is_demo` 字段
- 前端：在 `/brands` 列表页面 header 加 `加载示例数据集 / Test Case` 按钮组（含 `加载`、`重置`、`卸载` 三态）
- 前端：在所有 demo brand 派生行（topic-pool / decisions / publish / performance）渲染 `示例` 徽章
- 前端：生产 workspace 启用允许，但点击 `加载` / `卸载` 必须先经过 confirmation 对话框
- 后端 unit + API regression tests；前端 `next build` 验证
- `docs/v2/dev_spec.md` 同步：在 `3.2` 加 `is_demo` 字段定义，新增 `9.0` 兄弟节小节描述 demo loader API 与 `示例` 标签规则

### Out Of Scope
- 不实现真正的 browser-extension return path（属于 `P1-S2-1`）
- 不实现真实文件上传解析（属于 `P1-S2-1`）
- 不替换 `app/v2/foundation/bootstrap.py` 现有 `_ensure_default_demo_data`：保留它仅创建空白 default workspace；现有 `轻量户外` seed brand 在本任务中由 demo loader 接管，bootstrap 不再自动创建任何 brand
- 不自动触发 decision_batch / publish / performance：loader 只准备 brand + ingestion + topic-pool + 历史 publish/performance 基线，操作流由运营在 console 中走完
- 不实现 demo 数据的随机化或多套数据集切换；本任务只交付一份固定的服饰 fixture
- 不引入 demo-only 的 evaluation / learning_state 写入；evaluation 与 second batch 由运营通过现有正式接口触发
- 不改动 scorer / decision engine / publish service 内部逻辑

### Required Deliverables
- Production:
  - `brands.is_demo` schema migration 与对应 Pydantic / dataclass 字段
  - `app/v2/demo/service.py` (DemoDatasetService) + `app/v2/demo/fixtures/apparel_brand_v1.py` 或 .json
  - `app/v2/demo/store.py`：demo dataset state（loaded? brand_id? loaded_at?）的最小持久化（可复用 `brands.is_demo` + 单 brand 约束直接从 master-data 推导，避免新增表）
  - `app/api/routes/router.py`：3 个新端点 + 在派生行响应中暴露 `is_demo`
  - `app/models/schemas.py`：响应模型字段更新
  - `app/v2/foundation/bootstrap.py`：移除 `_ensure_default_demo_data` 中创建 demo brand / channel / policy / snapshot 的逻辑，保留仅创建 default workspace 行为
  - `frontend/src/components/brand/DemoDatasetPanel.tsx`：按钮组件 + confirmation 弹窗
  - `frontend/src/lib/api.ts`、`frontend/src/lib/server-api.ts`、`frontend/src/lib/types.ts`：endpoint 调用与 `isDemo` 字段映射
  - `frontend/src/components/ui/DemoBadge.tsx`：`示例` 徽章组件
  - 在 `frontend/src/app/topic-pool/page.tsx`、`/decisions/page.tsx`、`/publish/page.tsx`、`/performance/page.tsx` 与 brand list 行渲染 `示例` 徽章
- Tests:
  - `tests/unit/test_v2_demo_service.py`：fixture 加载、幂等重放、卸载、`is_demo` 写入
  - `tests/unit/test_v2_demo_api.py`：3 个 endpoint 的鉴权 / workspace scope / 重复加载 / 错误处理
  - 扩展 `tests/unit/test_v2_topic_pool_api.py`、`tests/unit/test_v2_decision_api.py`、`tests/unit/test_v2_feedback_api.py`：派生行响应携带 `is_demo`
  - 新增 `tests/acceptance/test_v2_demo_dataset_loop.py`：通过 demo loader 完整跑通 ingestion → topic pool → decision → publish → performance → second decision 链路（与现有 release-gate helpers 复用）
  - `cd frontend && npm run build`
- Spec/Docs:
  - `docs/v2/dev_spec.md` §3.2 brands schema 增加 `is_demo`
  - `docs/v2/dev_spec.md` 在 `9.0` 之后或 `9.7` 之前新增 `9.x Demo Dataset Loader` 节，描述 endpoints、`示例` 徽章规则、生产 workspace 二次确认要求
  - `docs/v2/development_tasks.md` §2.11.6 在完成时由 progress-tracker 标注 closure evidence

### Acceptance Criteria
- [ ] AC1 `brands` 表新增 `is_demo BOOLEAN NOT NULL DEFAULT FALSE`，所有现存 brand 默认 `is_demo = false`，迁移脚本可重放
- [ ] AC2 `POST /workspaces/{ws}/demo-datasets/load` 创建（或重置）一个 `is_demo = true` 的服饰示例品牌、对应 channel、`brand_policy_configs` active 行、`brand_state_snapshots` 行，并通过正式 `IngestionService` 的 `source_sync` / `data_import` 流程写入约 30 条 `content_items`、对应 `authors`、`comments`、`content_metrics_snapshots`
- [ ] AC3 demo loader 调用 `TopicPoolService.refresh_topic_pool(...)` 走正式 normalizer + scorer 通路生成 `topic_pool_items`，不直接 INSERT 越过该边界
- [ ] AC4 demo loader 写入 2-3 条历史 `publish_records`（`decision_event_id IS NULL` 标记为 manual）和对应 `performance_snapshots`，作为 second-batch 反馈对比的 baseline
- [ ] AC5 重复调用 `POST /demo-datasets/load` 必须幂等：先级联删除已存在的 demo brand 及其全部派生行，再重新加载，不出现重复或孤立记录
- [ ] AC6 `DELETE /workspaces/{ws}/demo-datasets` 删除 demo brand 及全部派生行（`brand_channels`、`brand_policy_configs`、`brand_state_snapshots`、`content_items`、`authors`*、`comments`、`content_metrics_snapshots`、`ingestion_runs`、`topic_pool_items`、`publish_records`、`performance_snapshots`）。`authors` 仅删除唯一仅被 demo brand 引用的；如被多 brand 引用则保留
- [ ] AC7 `GET /workspaces/{ws}/demo-datasets` 返回 `{ loaded: bool, brand_id?: str, loaded_at?: str, dataset_version: str }`，其中 `dataset_version` 与 fixture 文件版本一致
- [ ] AC8 demo loader 路径上所有写操作必须通过 `MasterDataService` / `IngestionService` / `TopicPoolService` / `FeedbackService`（或现有 publish / performance 等价 service）调用，禁止任何对 store 的越权直写以绕过业务校验
- [ ] AC9 `Brand` API 响应（list、detail）携带 `is_demo` 字段
- [ ] AC10 `/brands/{id}/topic-pool`、`/brands/{id}/decisions/...`、`/publish-records`、`/performance/...` 派生行响应均携带可由前端用于渲染徽章的 `is_demo` 字段（可由后端 JOIN 解析）
- [ ] AC11 前端 `/brands` 页面顶部展示 `加载示例数据集 / Test Case` 按钮组：未加载态显示 `加载`，已加载态显示 `重置` 和 `卸载`；加载中态显示进度；按钮通过真实 API 调用，不出现 `alert(...)` 占位
- [ ] AC12 任何点击 `加载` / `重置` / `卸载` 的操作必须先经过 confirmation modal，文案明确告知会写入或删除一个示例品牌的全部数据
- [ ] AC13 `/topic-pool`、`/decisions`、`/publish`、`/performance` 列表与详情页面，对 `isDemo === true` 的行渲染 `示例` 徽章；`/brands` 列表对 `isDemo === true` 的行同样渲染徽章
- [ ] AC14 demo loader 在生产 workspace 也可用（不被 env flag 限制），但二次确认是必经路径
- [ ] AC15 端到端 acceptance：通过 demo loader 加载后，无需任何 extension/upload，按 `tests/acceptance/v2_phase1_helpers.py` 现有助手运行 `decision_batch`、`publish`、`performance import`、`second decision_batch`，且 `detect_second_batch_effect(...)` 返回非空效果
- [ ] AC16 spec 同步：`docs/v2/dev_spec.md` `3.2` 与新增 `Demo Dataset Loader` 章节在 PR 中一并更新；`docs/v2/development_tasks.md` 不需新增内容（原 `2.11.6` 已就位）
- [ ] AC17 不引入任何 mock fallback / demo-only 渲染分支：demo brand 在前端展示与真实 brand 走相同 loader、相同组件，仅多出 `示例` 徽章
- [ ] AC18 移除 `app/v2/foundation/bootstrap.py` 中创建 demo brand 的逻辑，仅保留 default workspace 创建；`tests/unit/test_v2_bootstrap.py` 同步更新

### Residual Obligations
- Relevant OPEN / carry-forward items:
  - `P1-S2-1` data-intake productization 仍是 `2.12` Final Closure Gate 的拦截项；本任务不替代它，仅提供合成数据下的端到端验证
  - `UI-ALIGN-3` no-fallback cleanup：必须保持，本任务不能因引入 demo data 而退回到 mock-render 分支
  - `Spec` `9.7.2` "正式开发完成后的应用运行态不得保留 mock/fallback 数据分支"：通过"真实写入 + 真实读路径 + 仅徽章标注"的方式遵守
- Current-Phase Carry-Forward Items To Re-check:
  - `app/v2/foundation/bootstrap.py` 的 demo seed 行为收敛到本 loader 后，`tests/unit/test_v2_bootstrap.py` 必须同步
  - `Brand` 类型的 `is_demo` 字段需要前后端 canonical TS contract 同步，否则违反 `9.7.3` 字段一一对应规则
- Resolved By This Task:
  - "Phase 1 闭环无法在不接入 extension 与上传文件的前提下被端到端验证" 这一缺口
  - bootstrap 自动 seed demo brand 与正式 demo loader 之间的职责重叠
- Deferred / Blocked:
  - 真实 extension capture / 真实文件上传产品化 -> `P1-S2-1`
  - 多套 demo dataset 切换 / 随机化生成 -> 后续视需要再开 backlog
  - demo dataset 的多语言版本 / 国际化 -> 不在 Phase 1 范围

### Contract Inventory
- Upstream contracts:
  - `MasterDataService` brand / channel / policy / state snapshot writes
  - `IngestionService.create_source_sync(...)` / `create_data_import(...)`
  - `TopicPoolService.refresh_topic_pool(...)`
  - publish service / `POST /publish-records` 服务层
  - feedback service / `POST /performance/import` 服务层
  - Postgres 迁移 runner（`app/v2/db/runner.py` / `app/v2/db/migrations.py`）
- Downstream contracts:
  - `GET /brands`, `GET /brands/{id}` 响应增字段
  - `GET /brands/{id}/topic-pool` 响应增 `is_demo`
  - `GET /brands/{id}/decisions/...`, `GET /publish-records`, `GET /performance/...` 响应增 `is_demo`
  - `frontend/src/lib/types.ts` `Brand`、`Topic`、`DecisionItem`、`PublishRecord`、`PerformanceSnapshot` 类型增 `isDemo`
- Files/interfaces with compatibility risk:
  - `app/v2/foundation/postgres_store.py` 与 `app/v2/foundation/store.py`（brand DDL/dataclass 变更）
  - `app/v2/db/migrations.py` 必须新增可重放迁移
  - `app/v2/foundation/bootstrap.py`（移除 demo seed）
  - `tests/unit/test_v2_bootstrap.py`、`tests/acceptance/test_v2_phase1_console_walkthrough.py` 等可能依赖默认 demo brand 的测试
  - 已有 acceptance helpers `tests/acceptance/v2_phase1_helpers.py` 可继续复用

### Test Requirements
- Primary test files:
  - `tests/unit/test_v2_demo_service.py` (NEW)
  - `tests/unit/test_v2_demo_api.py` (NEW)
  - `tests/acceptance/test_v2_demo_dataset_loop.py` (NEW)
  - `tests/unit/test_v2_topic_pool_api.py`、`test_v2_decision_api.py`、`test_v2_feedback_api.py`（MODIFY：覆盖 `is_demo` 字段）
  - `tests/unit/test_v2_bootstrap.py`（MODIFY：移除 demo brand 假设）
  - `tests/unit/test_v2_schema_contract.py`（MODIFY：覆盖 `brands.is_demo` 列）
- Required scenarios:
  1. `DemoDatasetService.load(...)` 在空 workspace 中创建预期数量的 brand/channel/policy/state/content_items/topic_pool_items/publish_records/performance_snapshots，所有 brand 标记 `is_demo=true`
  2. 对已加载的 workspace 重复 load，必须先卸载再加载，最终行数与首次一致（幂等）
  3. `unload(...)` 删除全部派生行；非 demo brand 不受影响（同 workspace 多品牌隔离）
  4. `state(...)` 返回 `loaded=false` 与 `loaded=true` 两种形态及正确的 `dataset_version`
  5. `is_demo=false` brand 上的 ingestion / topic-pool 行响应 `is_demo=false`
  6. workspace scope 隔离：不同 workspace 的 demo loader 互不影响
  7. API 鉴权：缺失 workspace header 时 401/403 行为符合现有约定
  8. acceptance：load → run decision_batch → review accept → publish_record → performance import → second decision_batch；`detect_second_batch_effect` 返回非空效果
  9. 前端：build 通过；`/brands` 上 demo brand 渲染徽章；点击 `加载` 触发 confirmation 后调用真实 API
- Test target:
  - backend `unit` + `acceptance`，frontend build verification

## 2. Architecture Context

### System Position
```
Operator clicks "加载示例数据集"
  -> POST /workspaces/{ws}/demo-datasets/load
  -> DemoDatasetService.load(workspace_id)
       -> (idempotent) DemoDatasetService.unload(workspace_id) if existing demo brand
       -> MasterDataService.create_brand(is_demo=True) + channel + policy + state
       -> IngestionService.create_source_sync(payload=fixture.competitor_capture)
       -> IngestionService.create_data_import(rows=fixture.owned_history)
       -> TopicPoolService.refresh_topic_pool(brand_id) [deterministic, no LLM]
       -> for record in fixture.baseline_publishes:
            PublishService.create(record)  [decision_event_id=NULL, manual]
            FeedbackService.import_performance(record_id, snapshot)
  -> response: { brand_id, loaded_at, dataset_version, summary_counts }

Operator continues normal flow:
  /topic-pool -> 选 candidate
  /decisions -> POST /brands/{id}/decisions/run
  /decisions -> PATCH accept slot
  /publish -> POST /publish-records
  /performance -> POST /performance/import
  /decisions -> POST /brands/{id}/decisions/run (second batch)
  Each list/detail row from demo brand renders `示例` badge.
```

### Technical Constraints
- demo loader 必须复用现有 service 层，禁止直接写 store 越过校验
- demo loader 必须幂等：重复 load 等价于一次 unload + 一次 load
- `is_demo` 是真实 schema 字段（spec canonical），不是前端 derived flag
- 前端徽章是真实数据上的视觉装饰，不是 mock 渲染分支
- 生产 workspace 启用，但 confirmation 必须存在以避免误触
- demo dataset 文件 `apparel_brand_v1` 为固定内容，回归与演示均可重现
- spec sync：`is_demo` 与 demo loader API 必须先在 spec 落定，再实现

## 3. Technical Design

### 3.1 Files To Create Or Modify

| Path | NEW/MODIFY | Required Change | Linked AC |
|------|------------|-----------------|-----------|
| `docs/v2/dev_spec.md` | MODIFY | §3.2 brands 新增 `is_demo`；新增 `Demo Dataset Loader` 小节 | AC1, AC11-AC14, AC16 |
| `app/v2/db/migrations.py` | MODIFY | 新增 `ALTER TABLE brands ADD COLUMN is_demo BOOLEAN NOT NULL DEFAULT FALSE` 迁移 | AC1 |
| `app/v2/db/schema.py` | MODIFY | 同步 schema 文档 / canonical DDL | AC1 |
| `app/v2/foundation/models.py` | MODIFY | `BrandRecord` 增 `is_demo: bool = False` | AC1, AC9 |
| `app/v2/foundation/store.py` | MODIFY | in-memory store 支持 `is_demo` 读写 | AC1 |
| `app/v2/foundation/postgres_store.py` | MODIFY | postgres store 读写 `is_demo` | AC1 |
| `app/v2/foundation/service.py` | MODIFY | `create_brand(...)` 支持 `is_demo`，新增 `delete_brand_cascade(...)` 或在 demo 模块内拼装 | AC2, AC6, AC8 |
| `app/v2/foundation/bootstrap.py` | MODIFY | 移除 `_ensure_default_demo_data` 中 brand/channel/policy/snapshot 的 seed 行为，仅保留 workspace 创建 | AC18 |
| `app/v2/demo/__init__.py` | NEW | module init | - |
| `app/v2/demo/fixtures/__init__.py` | NEW | export `APPAREL_BRAND_V1` | AC2-AC4 |
| `app/v2/demo/fixtures/apparel_brand_v1.py` | NEW | 固定服饰品牌 fixture：1 brand + 1 channel + policy + state + ~30 content_items + authors + comments + metrics + 2-3 historical publishes + performance | AC2-AC4 |
| `app/v2/demo/service.py` | NEW | `DemoDatasetService(load/unload/state)` 调用 master-data/ingestion/topic-pool/publish/performance services | AC2-AC8 |
| `app/v2/demo/bootstrap.py` | NEW | wire DemoDatasetService 到 runtime（依赖 master-data + ingestion + topic-pool + publish + performance services） | AC2 |
| `app/api/routes/router.py` | MODIFY | 注册 3 个 demo endpoints；在 brand / topic-pool / decision / publish / performance 响应模型中输出 `is_demo` | AC2, AC5, AC6, AC7, AC9, AC10 |
| `app/models/schemas.py` | MODIFY | `Brand`, `TopicPoolListItem`, `DecisionBatchItem`, `PublishRecord`, `PerformanceSnapshot` 等响应模型增 `is_demo: bool` | AC9, AC10 |
| `frontend/src/lib/types.ts` | MODIFY | `Brand`、`Topic`、`DecisionItem`、`PublishRecord`、`PerformanceSnapshot` 增 `isDemo: boolean`；`DemoDatasetState` 类型 | AC9, AC10, AC11 |
| `frontend/src/lib/api.ts` | MODIFY | `loadDemoDataset()`、`unloadDemoDataset()`、`getDemoDatasetState()`；mapper 同步 `is_demo -> isDemo` | AC11 |
| `frontend/src/lib/server-api.ts` | MODIFY | SSR 端读取 demo state；mapper 同步 | AC11 |
| `frontend/src/components/brand/DemoDatasetPanel.tsx` | NEW | 按钮组件：未加载/已加载/加载中三态；调用 confirmation modal | AC11, AC12, AC14 |
| `frontend/src/components/ui/DemoBadge.tsx` | NEW | `示例` 徽章组件 | AC13 |
| `frontend/src/components/ui/ConfirmDialog.tsx` | NEW or MODIFY | 复用或新增 confirmation modal | AC12 |
| `frontend/src/app/brands/page.tsx` | MODIFY | header 嵌入 `DemoDatasetPanel`；列表行对 `isDemo` 渲染徽章 | AC11, AC13 |
| `frontend/src/app/topic-pool/page.tsx` | MODIFY | 对 `isDemo` 渲染徽章 | AC13 |
| `frontend/src/app/decisions/page.tsx` | MODIFY | 同上 | AC13 |
| `frontend/src/app/publish/page.tsx` | MODIFY | 同上 | AC13 |
| `frontend/src/app/performance/page.tsx` | MODIFY | 同上 | AC13 |
| `tests/unit/test_v2_demo_service.py` | NEW | 单元测试 | AC2-AC8 |
| `tests/unit/test_v2_demo_api.py` | NEW | API 集成测试 | AC2, AC5-AC7, AC10 |
| `tests/unit/test_v2_topic_pool_api.py` | MODIFY | 覆盖 `is_demo` 字段透传 | AC10 |
| `tests/unit/test_v2_decision_api.py` | MODIFY | 同上 | AC10 |
| `tests/unit/test_v2_feedback_api.py` | MODIFY | 同上 | AC10 |
| `tests/unit/test_v2_bootstrap.py` | MODIFY | 移除 demo brand 默认存在的断言 | AC18 |
| `tests/unit/test_v2_schema_contract.py` | MODIFY | 断言 `brands.is_demo` 列 | AC1 |
| `tests/acceptance/test_v2_demo_dataset_loop.py` | NEW | 端到端 demo loader → second batch effect | AC15 |

### 3.2 Demo Dataset Fixture Shape

文件位置：`app/v2/demo/fixtures/apparel_brand_v1.py`

```python
APPAREL_BRAND_V1_VERSION = "apparel_brand_v1.0"

APPAREL_BRAND_V1 = {
    "brand": {
        "name": "示例品牌 - 都市轻装研究所",
        "category": "apparel",
        "stage": "growth",
        "target_audience": {
            "age_ranges": ["25-34"],
            "gender_skew": "female",
            "interests": ["通勤穿搭", "极简风", "场景叠穿"],
            "consumption_level": "mid_to_high",
            "geographic_focus": ["华东", "华南"],
        },
        "brand_voice": {
            "tone": ["authentic", "informative"],
            "preferred_formats": ["测评", "场景故事", "攻略"],
        },
        "goals": {"goals": [{"type": "engagement_rate", "target_value": 5, "unit": "%", "window_days": 90, "priority": 1}]},
    },
    "channel": {
        "platform": "xiaohongshu",
        "account_name": "示例 都市轻装研究所",
        "profile_url": "https://www.xiaohongshu.com/user/profile/demo-apparel-studio",
    },
    "policy": {
        "policy_name": "baseline_rule_v1",
        "policy_version": "v1",
        "topic_type_targets": {
            "targets": [
                {"topic_type": "scenario", "min_ratio": 0.34, "max_ratio": 1.0, "priority_boost": 0.12},
                {"topic_type": "problem", "min_ratio": 0.0, "max_ratio": 0.5, "priority_boost": 0.04},
            ]
        },
        "brand_fit_rules": {
            "preferred_topic_types": ["scenario", "problem"],
            "minimum_source_count": 2,
        },
    },
    "state_snapshot": {"state_version": "state_v1", "stage": "growth", "state_features": {"audience_focus": "urban commuting"}},
    "source_sync_capture": {
        # competitor + market posts (~20)
        # apparel-domain titles e.g. "通勤穿搭 5 套保暖叠穿"、"梨形身材冬季外套挑选"、"四季可穿基础款衣橱搭配"
        ...
    },
    "data_import_rows": [
        # owned brand historical posts (~10) used as historical evidence
        ...
    ],
    "baseline_publishes": [
        # 2-3 manual publish records with corresponding performance_snapshots
        # composite_reward 设计为 topic_type 上有差异，便于 second-batch 反馈对比
        ...
    ],
}
```

固定要求：
- 所有标题必须是服饰相关、可识别的中文文案
- `source_url`、`platform_content_id`、`platform_author_id` 全部带 `demo-` 前缀，避免与真实抓取数据潜在冲突
- 数量：~30 条 content_items（细分 owned/competitor/market），~3 个 authors/类，~3 条评论/帖
- baseline_publishes 中至少包含两个不同 `topic_type` 的发布记录，且 reward 值有可识别差异

### 3.3 DemoDatasetService Interface

```python
class DemoDatasetService:
    def __init__(
        self,
        master_data_service: MasterDataService,
        ingestion_service: IngestionService,
        topic_pool_service: TopicPoolService,
        publish_service,  # existing
        feedback_service,  # existing
    ) -> None: ...

    def load(self, *, workspace_id: str) -> DemoDatasetState:
        """Idempotent: unload existing demo brand if present, then load fresh fixture."""

    def unload(self, *, workspace_id: str) -> DemoDatasetState:
        """Cascade-delete demo brand and all FK-derived rows."""

    def state(self, *, workspace_id: str) -> DemoDatasetState:
        """Return current loaded state."""
```

`DemoDatasetState`:
```python
@dataclass
class DemoDatasetState:
    loaded: bool
    brand_id: str | None
    loaded_at: datetime | None
    dataset_version: str
    summary_counts: dict[str, int] | None  # {"content_items": 30, "topic_pool_items": 12, ...}
```

### 3.4 Cascade Delete Strategy
- 通过 `brand_id` FK 反向遍历所有派生表
- `authors` 是 workspace 级共享，仅当某 author 仅被本次 demo brand 引用时删除（用 `content_items.author_id` 计数判定）
- 删除顺序遵守 FK 约束：
  1. `performance_snapshots` (FK -> publish_records)
  2. `publish_records` (FK -> brand, content_items, channel)
  3. `decision_*` (FK -> brand, batch)
  4. `topic_pool_items`
  5. `comments` (FK -> content_items)
  6. `content_metrics_snapshots`
  7. `content_items`
  8. `topics`
  9. `ingestion_runs`
  10. `extension_capture_sessions` / `data_import_previews`
  11. `authors`（按上述判定条件）
  12. `brand_state_snapshots`
  13. `brand_policy_configs`
  14. `brand_channels`
  15. `brands`
- in-memory store 与 postgres store 都需要等价的 cascade，统一在 service 层实现，store 提供必要的 list/delete 原语

### 3.5 API Endpoints

#### `POST /workspaces/{workspace_id}/demo-datasets/load`
- Auth: 标准 workspace header
- Idempotent
- Response 200:
  ```json
  {
    "loaded": true,
    "brand_id": "uuid",
    "loaded_at": "2026-05-03T10:00:00+08:00",
    "dataset_version": "apparel_brand_v1.0",
    "summary_counts": {
      "content_items": 30,
      "authors": 9,
      "topic_pool_items": 12,
      "publish_records": 3,
      "performance_snapshots": 3
    }
  }
  ```

#### `DELETE /workspaces/{workspace_id}/demo-datasets`
- Response 200:
  ```json
  { "loaded": false, "brand_id": null, "loaded_at": null, "dataset_version": "apparel_brand_v1.0" }
  ```

#### `GET /workspaces/{workspace_id}/demo-datasets`
- Same shape as load response，`loaded=false` 时 `brand_id`/`loaded_at`/`summary_counts` 为 null

### 3.6 Frontend Behavior

- `/brands` 顶部 header 增 `DemoDatasetPanel`，展示当前状态：
  - `loaded=false` → 显示按钮 `加载示例数据集` + 提示文案 `加载固定的服饰示例品牌（约 30 条样本数据）以便端到端体验`
  - `loaded=true` → 展示当前 brand_id link、`重置` 与 `卸载` 按钮、`loaded_at`
- 点击任一动作弹出 confirmation modal：
  - 加载文案：`确认加载示例数据集？将创建一个新的示例品牌及其 30 条样本数据。`
  - 重置文案：`确认重置示例数据集？将清空当前示例品牌的全部数据并重新加载。`
  - 卸载文案：`确认卸载示例数据集？将删除该示例品牌及其全部派生数据。`
- 调用真实 API；成功后通过 `router.refresh()` 让 SSR 数据重新拉取
- 任何派生页面渲染列表项时，对 `row.isDemo === true` 显示 `<DemoBadge />`
- `DemoBadge` 视觉建议：浅色背景 pill，文案 `示例`，与现有 badge 组件风格一致

### 3.7 Error Handling
- demo loader 内部失败必须把已经写入的部分回滚（service 层使用同一事务/上下文，失败时自动 unload）
- API 在 fixture 文件不存在 / version mismatch 时返回 500 + 结构化错误
- 重复 load 不视为错误，是幂等行为
- 卸载不存在的 demo brand 应返回 200 + `loaded=false`，而非 404
- 生产 workspace 与非生产 workspace 在后端无差别，限制仅在前端 confirmation；后端不做 env 判断

## 4. Implementation Checklist

- [ ] 在 spec `3.2` 加 `is_demo` 字段定义；新增 `Demo Dataset Loader` 小节
- [ ] 后端 schema：迁移、`BrandRecord`、in-memory + postgres store 读写 `is_demo`
- [ ] `MasterDataService.create_brand` 支持 `is_demo`；新增/抽取 `delete_brand_cascade(...)` 由 demo service 协调
- [ ] 移除 `bootstrap._ensure_default_demo_data` 中 brand 创建逻辑；只保留 workspace 创建
- [ ] 新建 `app/v2/demo/fixtures/apparel_brand_v1.py` 固定 fixture
- [ ] 实现 `DemoDatasetService`：load / unload / state / cascade delete
- [ ] 注册 3 个 endpoints；响应模型中携带 `is_demo`
- [ ] 在 topic-pool / decision / publish / performance 列表与详情响应中 join brand 暴露 `is_demo`
- [ ] 前端：`DemoBadge` 组件、`DemoDatasetPanel` 组件、`/brands` header 集成
- [ ] 前端：在 4 个派生页面渲染 `示例` 徽章
- [ ] 前端类型契约同步、API 调用同步
- [ ] 后端 unit + acceptance 测试
- [ ] 前端 `next build` 验证
- [ ] 更新 `docs/v2/dev_spec.md`，progress-tracker 写回 `2.11.6` closure evidence

## 5. Testing Plan

### Backend
- `pytest tests/unit/test_v2_demo_service.py tests/unit/test_v2_demo_api.py`
- `pytest tests/unit/test_v2_topic_pool_api.py tests/unit/test_v2_decision_api.py tests/unit/test_v2_feedback_api.py tests/unit/test_v2_bootstrap.py tests/unit/test_v2_schema_contract.py`
- `pytest tests/acceptance/test_v2_demo_dataset_loop.py`
- 全量回归（必要时）：`pytest tests/unit tests/acceptance -k "v2"`

### Frontend
- `cd frontend && npm run build`
- 如已存在 `*.test.ts` runner：`node --test frontend/src/lib/api.test.ts frontend/src/lib/server-api.test.ts`

### Acceptance Loop（自动化）
- create empty workspace
- POST /demo-datasets/load
- assert summary_counts 与 fixture 一致
- run decision_batch
- accept slot
- create publish_record
- import performance
- run second decision_batch
- assert detect_second_batch_effect 非空
- DELETE /demo-datasets，assert loaded=false 且品牌完全清空

## 6. Risk & Notes

### 风险点
- cascade delete 在 in-memory 与 postgres 两种 store 上必须等价，否则 acceptance 与 unit 表现不一致
- `brands.is_demo` 迁移如对正在运行的 demo workspace 留有遗留数据，需要明确：现有 `轻量户外` seed 在迁移后默认 `is_demo=false`；本任务通过移除 bootstrap seed + 推荐 reset workspace 解决
- demo loader 写入操作多，单事务边界要足够大避免半成品状态；in-memory store 需要 try/except + 自动 rollback (调用 `unload`)
- `tests/unit/test_v2_bootstrap.py` 与依赖默认 demo brand 的其他测试需要识别并改造

### 架构决策
- demo provenance 标记选择 `brands.is_demo` 而非 ingestion-run 级标记，原因：
  - 一个 demo brand 的所有派生数据本质上是同源的；brand-level 标记是最自然的真理来源
  - 派生行的 `is_demo` 通过 brand_id JOIN 即可得出，不需要每张表加列
  - 与 spec `3.2` brands 节自然契合
- 前端徽章选择以 `isDemo` 字段驱动，不引入 url-based / cookie-based demo mode 标志位

### Spec 对齐
- `is_demo` 必须先在 `docs/v2/dev_spec.md` `3.2` 中作为 canonical schema 字段公开；前端 TS 契约同步
- demo loader API 必须在 spec `9` 章节中作为正式 API 公开，不能仅存在于 instructions
- 生产 workspace 启用 + 二次确认必须在 spec 中明文化以避免被理解为 dev-only 工具

### 跨任务依赖
- 与 `P1-S2-1` 无 hard 依赖；二者独立工作
- `P1-S2-3` second-batch 的 acceptance helpers 可复用，避免重复造轮子
- 未来若引入真正多租户 auth (`AUTH-1`)，demo loader 仍按 workspace 隔离，无须改动

## 7. Spec Sync Expectations

- 完成 PR 中必须同步：
  - `docs/v2/dev_spec.md` `3.2 Brands` 加 `is_demo` 列定义、规则、示例
  - `docs/v2/dev_spec.md` 新增 `Demo Dataset Loader` API 小节（建议放在 `9.0` 之后或 `9.7` 之前）
  - `docs/v2/dev_spec.md` `9.7.2` 在 brands / topic-pool / decisions / publish / performance 模块描述中追加一句 `带 is_demo=true 的派生行渲染 示例 徽章`
- 若有任何 guide-scoped 义务（如 confirmation 文案细节、fixture 数据规模）未能在本任务内完成，必须以 `OPEN` 形式回写 `docs/v2/development_tasks.md` §2.11.6 或建立等价跟踪条目
- 若现有 `轻量户外` bootstrap demo 被移除导致其他文档中举例失效，需要相应更新举例为新 demo brand 名或抽象表述
