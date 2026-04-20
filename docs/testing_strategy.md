
# Testing Strategy

For high-frequency execution, read `testing_rules.md` first. This document remains the full testing specification and source for deeper design, roadmap test matrix mapping, and higher-layer policy.

## 1. Goal

本项目测试体系必须同时回答 4 个问题：

1. 单个模块逻辑是否正确。
2. 多个模块协作时是否正确。
3. 用户通过 API 的主流程是否稳定可用。
4. 交付前接入真实 Spider / LLM / RAG 时是否真实可用。

因此测试体系采用 4 层分层设计：`unit -> integration -> e2e -> acceptance`。

---

## 2. Test Pyramid

| 层级 | 目标 | 是否接真实外部依赖 | 典型运行时机 | 失败是否阻塞合并 |
|------|------|--------------------|--------------|------------------|
| `unit` | 验证单模块逻辑、边界、错误码、日志、指标 | 否 | 每次提交 / PR | 是 |
| `integration` | 验证组件协作、状态流转、恢复、补偿 | 否，默认 fake Spider/LLM | 每次提交 / PR | 是 |
| `e2e` | 验证用户 API 主流程、SSE、并发、幂等 | 否，使用 deterministic fake Spider/LLM/RAG | 每次提交 / PR | 是 |
| `acceptance` | 验证预发环境真实可用性 | 是 | 发布前 / 定时 smoke | 是，阻塞发布 |

设计原则：

- `unit`、`integration`、`e2e` 必须稳定、可重复、可离线运行。
- `acceptance` 允许较慢，但必须严格控量、可观测、可回溯。
- 不允许用真实依赖替代 `e2e`。真实依赖波动会让 CI 随机失败，破坏回归价值。
- 不允许只做 fake 测试就直接发布。没有真实验收，无法证明交付可用。

---

## 2.1 Frontend Live-Route Verification Policy

凡是已经对外宣称 `live integration` 的前端页面，测试设计必须把“页面运行时如何拿到真实身份与真实数据”作为正式契约的一部分，而不是只验证 API helper 或静态渲染结果。

必须遵守：

- 如果页面是 `SSR`，测试必须验证服务端的数据路径是否能独立解析 workspace/auth/route context；不能只靠浏览器端 provider 初始化通过。
- 如果页面是 client page，测试必须验证 provider/context 初始化完成后才能发起 live 请求。
- 已交付 live 页面在 API 失败时必须展示真实 loading / empty / error 状态，不得以 mock 数据、demo 成功态或静默 fallback 掩盖失败。
- route shell / pending live data 页面可以保持 unavailable/empty guidance，但不得伪装成 live。

推荐最小覆盖：

1. 一条成功路径：证明页面在正确运行时上下文下能读到真实数据。
2. 一条失败路径：证明页面会显示真实错误，而不是回落到 mock。
3. 一条上下文一致性路径：证明 `SSR` 页面不依赖 client-only context，或 client 页面不会在 context 未初始化时提前取数。

---

## 3. Dependency Policy

### 3.1 Unit

- 仅测试一个模块。
- 所有跨模块依赖必须 mock / fake。
- 禁止真实网络。
- 禁止真实长耗时 embedding / LLM 调用。

### 3.2 Integration

- 允许真实 `SessionManager`、`JobStore`、`JobWorker`、`Workflow`、`FastAPI app factory`。
- 外部依赖默认 fake：
  - `XHSSpider`
  - `LLMClient`
  - `RAG embedding provider`
- SQLite 使用临时文件库，不使用共享生产库。
- Chroma 使用临时目录；允许用 lightweight fake/vector stub 替代真实 embedding。

### 3.3 E2E

- 运行真实 FastAPI app + router + worker + sqlite + SSE。
- `Spider`、`LLM`、`RAG` 必须可控，使用 deterministic fake。
- 所有断言围绕用户可见行为：
  - HTTP status
  - response schema
  - job state
  - session state
  - SSE event sequence

### 3.4 Acceptance

- 运行在 staging / pre-prod。
- 接入真实 Spider / LLM / RAG。
- 控制 query 样本、token 预算和并发，避免大规模真实调用。
- 所有验收测试必须输出：
  - request id
  - session id
  - provider/model
  - token used
  - latency
  - failure code

---

## 4. Fixtures and Test Utilities

`tests/conftest.py` 应提供以下基础夹具：

- `settings_test`: 测试配置，关闭真实外部网络。
- `temp_db_path`: 每测例独立 SQLite 文件。
- `temp_chroma_dir`: 每测例独立 Chroma 目录。
- `fake_spider_client`: 可配置返回成功、空结果、TransientError、PermanentError。
- `fake_llm_client`: 可配置返回固定 strategy / proposal / generated note / timeout / rate limit。
- `fake_rag_service`: 可配置相似度命中、warning、空结果、reindex pending。
- `session_factory`: 构造最小合法 session。
- `job_factory`: 构造 strategy / generation job。
- `event_collector`: 采集并断言 SSE 事件序列。
- `frozen_clock`: 冻结时间，验证 lease、cooldown、lifecycle、purge。

要求：

- fixture 默认 deterministic。
- 允许通过参数化切换异常模式。
- 任何测试不得依赖真实当前时间或真实外部服务响应格式。

---

## 5. Naming, Marker and Execution

推荐 `pytest` markers：

- `@pytest.mark.unit`
- `@pytest.mark.integration`
- `@pytest.mark.e2e`
- `@pytest.mark.acceptance`
- `@pytest.mark.slow`
- `@pytest.mark.real_dependency`

推荐命令：

```bash
pytest tests/unit -q
pytest tests/integration -q
pytest tests/e2e -q
pytest tests/acceptance -q -m acceptance
pytest --cov=app tests/unit
```

CI 建议：

- PR 必跑：`unit + integration + e2e`
- Nightly：`unit + integration + e2e + acceptance smoke`
- Release gate：最近一次 `acceptance` 全绿，且无 blocker 缺陷

测试覆盖率目标：

单元测试：核心逻辑覆盖率 ≥ 80%
集成测试：关键路径覆盖率 100%（如 Ingestion、Hybrid Search）
E2E 测试：核心用户场景覆盖率 100%（至少 3 个关键流程）


### 5.1 Shift-left Cadence

测试执行时机必须前置到功能开发阶段，而不是集中到最后统一补写。

执行规则：

- `P0` 建立 pytest 基座、fixtures、markers、测试数据目录。
- `P1` 基础设施模块完成后，立即补齐对应 `unit`；涉及组件协作的能力在同一 phase 内补齐 `integration`。
- `P2` Strategy 相关功能完成后，立即补齐 `test_strategy_agent.py` 与 `test_strategy_workflow.py`。
- `P3` Generation 相关功能完成后，立即补齐 `test_generation_agent.py` 与 `test_generation_workflow.py`。
- `P4` API / Orchestrator 完成后，立即补齐 `test_router.py` 与 `tests/e2e/*`。
- `P5` 只保留真实依赖 `acceptance` 和发布前文档收口，不再承担补写 unit / integration / e2e 的职责。
- `unit` / `integration` / `e2e` 在相关模块开发完成后必须立刻编写并立刻执行；不允许累计到 phase 末尾统一补写或统一补跑。
- 一个任务如果引入了新状态机、新错误码、新 SSE 事件或新的持久化字段，必须在同一任务内补齐至少一层自动化测试锁定行为。

---

## 6. Directory Convention

```text
tests/
├── conftest.py
├── fixtures/
│   ├── sample_documents/
│   ├── sample_sessions/
│   ├── spider_payloads/
│   ├── llm_outputs/
│   └── events/
├── unit/
│   ├── test_smoke_imports.py
│   ├── test_config.py
│   ├── test_spider.py
│   ├── test_rag_service.py
│   ├── test_engagement.py
│   ├── test_session_state.py
│   ├── test_job_store.py
│   ├── test_job_worker.py
│   ├── test_strategy_agent.py
│   ├── test_generation_agent.py
│   ├── test_nodes.py
│   ├── test_edges.py
│   ├── test_router.py
│   ├── test_logging.py
│   ├── test_logging_config.py
│   └── test_alert_evaluator.py
├── integration/
│   ├── test_strategy_workflow.py
│   ├── test_generation_workflow.py
│   ├── test_checkpoint_recovery.py
│   ├── test_job_worker.py
│   ├── test_sse_replay.py
│   └── test_reindex_compensation.py
├── e2e/
│   ├── test_session_flow.py
│   ├── test_strategy_api.py
│   ├── test_generation_api.py
│   ├── test_resume_and_lifecycle.py
│   ├── test_sse_api.py
│   ├── test_idempotency_and_concurrency.py
│   └── test_error_contracts.py
└── acceptance/
    ├── test_real_spider_smoke.py
    ├── test_real_llm_strategy.py
    ├── test_real_llm_generation.py
    ├── test_real_rag_roundtrip.py
    └── test_full_chain_smoke.py
```

---

## 7. Unit Test Design

### 7.1 Goal

单元测试用于锁定单个模块内部逻辑、边界条件、错误分流、日志字段和状态机行为。

### 7.2 Scope

- 每个单测文件只验证一个模块或一组紧耦合纯函数。
- 所有跨模块依赖必须 mock / fake，不跨真实组件边界。
- 单测必须离线、稳定、可重复执行。

### 7.3 Mapping Rule

- `tests/unit/*` 的具体文件、场景和测试目标统一维护在 [§12 Roadmap Test Matrix](#12-roadmap-test-matrix) 的对应任务中。
- 当某个任务引入新的状态字段、错误码、日志事件或阈值规则时，必须在同一任务下补齐对应单测场景。
- 当某个任务交付前端 live 页面或把 route shell 提升为 live integration 时，必须增加一条覆盖其真实运行时上下文的测试：
  - SSR route: server-side loader / resolver test
  - client route: provider/context initialized fetch test

---

## 8. Integration Test Design

### 8.1 Goal

集成测试验证“真实内部组件协作”，重点关注状态流转、恢复、补偿和多组件边界契约。

### 8.2 Scope

- 允许真实内部组件协作，不接真实外部 Spider / LLM / embedding 服务。
- 优先覆盖 workflow、checkpoint、job recovery、SSE replay、reindex compensation 等跨模块路径。
- 断言重点是状态一致性、恢复语义、结果写回和补偿结果，而不是单个函数内部实现。

### 8.3 Mapping Rule

- `tests/integration/*` 的具体文件、场景和测试目标统一维护在 [§12 Roadmap Test Matrix](#12-roadmap-test-matrix) 的对应任务中。
- 需要跨多个 roadmap 任务才能形成完整协作链路时，以最后一个闭环任务作为测试归属点。

---

## 9. E2E Test Design

### 9.1 Goal

E2E 的目标是验证“用户从 API 视角看到的系统行为”。

E2E 不做真实 Spider / LLM 场景。理由：

- 需要稳定回归，不受第三方抖动影响。
- 需要可重复构造边界，如高相似、预算超限、SSE 补发。
- 需要在 CI 中快速执行。

### 9.2 Required Scenarios

必须覆盖的用户可见行为：

- session 创建、查询、生命周期流转、purge 后访问。
- exploration 入队、`stage=exploring + turn_status=awaiting_user_input` 结果可见、candidate confirm handoff。
- exploration branch list、old branch 保留、新 branch 创建、previous roll 只读回看。
- queued-next-round banner、zero-result suggestion chips、stale candidate hard rejection。
- strategy / generation 入队、完成、失败、预算超限。
- SSE 订阅、心跳、重连补发。
- idempotency、并发隔离、单 session 串行约束。
- 错误码、HTTP 状态码、响应 schema。

### 9.3 E2E Pass Criteria

- 所有端点状态码、响应 schema、错误码、事件序列符合 spec。
- 用户主链路稳定通过：
  - create session
  - explore
  - confirm candidate
  - strategy
  - generate
  - poll jobs
  - read session
  - subscribe events

---

## 10. Acceptance Test Design

### 10.1 Goal

Acceptance 负责证明“交付前真实可用”。

执行环境：

- staging / pre-prod
- 真实环境变量
- 真实 Spider
- 真实 LLM provider
- 真实 embedding / Chroma

### 10.2 Rules

- 仅使用少量固定 query 样本。
- 每次只跑 smoke 级最小量，控制 token 成本。
- 所有验收失败必须保留日志、响应、生成产物与失败码。

### 10.3 Required Scenarios

必须覆盖的真实依赖链路：

- 真实 Spider 字段契约与抓取可用性。
- 真实 exploration 检索降级与候选可追溯性。
- 真实 Strategy / Generation 输出结构与可用性。
- 真实 RAG 索引、检索、metadata filter。
- 真实 API 全链路与最小故障注入。

### 10.4 Acceptance Test Files

- 测试文件：`tests/acceptance/test_real_spider_smoke.py`
- 测试场景：
  1. 固定 query 可抓到非空结果。
  2. 结果字段未漂移。
  3. 响应时间在可接受范围。
- 测试目标：验证真实 Spider 可用性和字段契约稳定性。
- 测试文件：`tests/acceptance/test_real_llm_strategy.py`
- 测试场景：
  1. 真实 strategy 可解析为 `ContentStrategy`。
  2. 数据稀疏 query 下 fallback 仍可用。
  3. provider/model/token/latency 被记录。
- 测试目标：验证真实策略生成能力及验收可观测性。
- 测试文件：`tests/acceptance/test_real_exploration_smoke.py`
- 测试场景：
  1. `mode=exploration` query 可返回至少 1 个候选。
  2. 候选包含 evidence refs / rewrite suggestions / degraded 标记中的有效组合。
  3. provider/model/token/latency 被记录。
  4. branch/roll/candidate 链路可追溯。
- 测试目标：验证真实 `ExplorationPlanner + SearchWorker + SynthesisWorker` 的检索、降级、可追溯性与可观测性。
- 测试文件：`tests/acceptance/test_real_llm_generation.py`
- 测试场景：
  1. 真实 generation 产出 3-5 条可用笔记。
  2. 输出结构和风格符合预期。
  3. 成本和延迟在预算内。
- 测试目标：验证真实生成质量与成本可控性。
- 测试文件：`tests/acceptance/test_real_rag_roundtrip.py`
- 测试场景：
  1. 真实索引成功。
  2. 真实检索成功。
  3. metadata 过滤和相似度判断正确。
- 测试目标：验证真实 RAG 链路和隔离规则。
- 测试文件：`tests/acceptance/test_full_chain_smoke.py`
- 测试场景：
  1. `POST /sessions -> strategy -> generate -> GET /session -> SSE` 全链路成功。
  2. 真实 Spider / LLM / RAG 联调成功。
  3. 至少一个故障注入路径被验证。
- 测试目标：验证真实依赖下的全链路可用性和最小恢复能力。

---

## 11. Cross-cutting Verification and Release Criteria

### 11.1 Traceability Matrix

| 关键规则 | Unit | Integration | E2E | Acceptance |
|----------|------|-------------|-----|------------|
| Spider 3+2 重试 | 必测 | 必测 | 观察 job 状态 | 真实 smoke |
| `quality_score < 0.35 and doc_count < 10` expansion | 必测 | 必测 | 必测 | 观察真实 query |
| `embedding_similarity > 0.6` 重选 | 必测 | 必测 | 必测 | 可选 smoke |
| `BUDGET_EXCEEDED` | 必测 | 必测 | 必测 | 仅限小规模验证 |
| `Idempotency-Key` 去重 | 必测 | 必测 | 必测 | 不要求 |
| `lease_expires_at` 恢复 | 必测 | 必测 | 必测 | 不要求 |
| `Last-Event-ID` SSE 补发 | 可选 | 必测 | 必测 | 真实 smoke |
| 真实 Spider / LLM 可用性 | 否 | 否 | 否 | 必测 |

### 11.2 Non-functional Verification

除功能外，测试还必须覆盖以下非功能约束：

- 日志完整性：关键路径必须有结构化日志。
- 指标口径一致性：测试可复算 `job_success_rate`、`llm_p95_latency_ms`。
- 成本可控：acceptance 必须记录 token 使用量。
- 可恢复性：worker crash / checkpoint recovery / reindex compensation 必须被测试。
- 并发安全：同 session 串行约束、跨 session 并发不串数据。

### 11.3 Release Gate

发布前必须满足：

1. `unit + integration + e2e` 全部通过。
2. 最近一次 `acceptance smoke` 通过。
3. 无 blocker 级缺陷。
4. staging 监控指标正常：
   - `job_success_rate`
   - `job_recovery_success_rate`
   - `llm_p95_latency_ms`
   - `budget_exceeded_count`

### 11.4 Summary

- `unit` 保证模块逻辑正确。
- `integration` 保证系统内部协作正确。
- `e2e` 保证稳定的用户流程正确。
- `acceptance` 保证交付前真实依赖可用。

四层缺一不可；其中 `acceptance` 不是 `e2e` 的替代品，而是发布前真实可用性的最终证明。

---

## 12. Roadmap Test Matrix

本章统一承接原先“文件级测试矩阵”和“任务级测试映射”的职责。每个任务只保留 3 类信息：测试文件、测试场景、测试目标。测试场景使用编号列表列出必须覆盖的路径、分支和边界。

### 12.1 P0 Project Setup

<a id="ts-p0-1"></a>
#### `P0-1` Git 子模块配置

- 测试文件：环境验证脚本或最小导入检查
- 测试场景：
  1. `git submodule update --init` 成功。
  2. Spider 模块可导入。
  3. 子模块锁定到具体 commit。
- 测试目标：确认上游 Spider 依赖可稳定拉取、导入和固定版本。

<a id="ts-p0-2"></a>
#### `P0-2` 依赖配置

- 测试文件：环境安装验证
- 测试场景：
  1. 干净环境 `pip install -r requirements.txt` 成功。
  2. `.env.example` 覆盖全部必要配置。
  3. `setup_env.sh` 可一键初始化。
- 测试目标：确认项目依赖、配置模板和初始化脚本可支持新环境快速启动。

<a id="ts-p0-3"></a>
#### `P0-3` 项目骨架

- 测试文件：环境结构检查
- 测试场景：
  1. 目录结构与架构文档一致。
  2. `import app` 无报错。
  3. `.gitignore` 排除运行时产物。
- 测试目标：确认项目骨架、包入口和仓库结构约定稳定可用。

<a id="ts-p0-4"></a>
#### `P0-4` 测试单元准备

- 测试文件：`tests/unit/test_smoke_imports.py`
- 测试场景：
  1. `app.config`、`app.services.*`、`app.agents.*`、`app.memory.*`、`app.llm.*` 可正确导入。用于提前拦截错误的绝对/相对导入路径。
  2. 关键模块导入时不触发真实网络副作用。
  3. pytest 的 `testpaths` 与 markers 能正常工作。
- 测试目标：建立可离线执行、可持续扩展的 pytest 基座。

<a id="ts-p0-5"></a>
#### `P0-5` 环境验证

- 测试文件：README 演练验证
- 测试场景：
  1. 按 README 完成环境搭建。
  2. `pytest` 能运行。
  3. 配置文件可正确加载。
- 测试目标：确认开发文档足以支持新成员完成环境初始化并进入测试流程。

### 12.2 P1 Infrastructure

<a id="ts-p1-1"></a>
#### `P1-1` Spider 服务封装

- 测试文件：`tests/unit/test_spider.py`
- 测试场景：
  1. 首次成功返回标准化 `XHSPost[]`。
  2. 3 次自动重试后成功。
  3. 用户重试阶段成功。
  4. 5 次失败后写入 `spider_cooldown_until`。
  5. `TransientError` / `PermanentError` 分类正确。
  6. backoff 为 `2/4/8/16/32s`。
- 测试目标：锁定 Spider 输出契约、错误分类、3+2 重试和 cooldown 机制。

<a id="ts-p1-2"></a>
#### `P1-2` Session 状态管理

- 测试文件：`tests/unit/test_session_state.py`
- 测试场景：
  1. create / update / get / delete 正常。
  2. UPSERT 幂等。
  3. `alive/frozen/purged` 生命周期判定正确。
  4. `active_jobs` / `paused_jobs` 对生命周期的影响正确。
  5. Checkpoint 仅保存轻量引用。
  6. `SessionDataStore` 与主 session 存储一致。
  7. Chroma 失败时补偿状态可追踪。
- 测试目标：锁定 session 生命周期、双存储一致性和轻状态设计。
- 测试文件：`tests/integration/test_checkpoint_recovery.py`
- 测试场景：
  1. strategy 后可恢复到 generation。
  2. checkpoint 不保存完整大对象。
- 测试目标：验证状态持久化与 workflow 恢复协作正确。

<a id="ts-p1-3"></a>
#### `P1-3` RAG 服务

- 测试文件：`tests/unit/test_rag_service.py`
- 测试场景：
  1. `chunk_posts()` 输出字段齐全。
  2. embedding 接口被正确调用。
  3. `index_documents()` 写入复合 ID。
  4. `query_similar()` 仅返回当前 session 文档。
  5. `quality_score` 计算稳定。
  6. 删除 / 清理逻辑生效。
- 测试目标：锁定 RAG 的 chunk、索引、检索、session 隔离和清理行为。

<a id="ts-p1-4"></a>
#### `P1-4` 配置系统

- 测试文件：`tests/unit/test_config.py`
- 测试场景：
  1. 默认配置加载成功。
  2. `.env` / 环境变量覆盖默认值。
  3. 非法数值或缺失必填项时报错。
  4. 路径类配置校验存在性和创建策略。
- 测试目标：锁定配置加载、覆盖、校验和路径处理行为。

<a id="ts-p1-5"></a>
#### `P1-5` Logging 模块

- 测试文件：`tests/unit/test_logging.py`
- 测试场景：
  1. 结构化 JSON 日志字段完整。
  2. `session_id/job_id/stage/error_code` 关联正确。
  3. 关键事件名固定且大小写一致。
  4. 异常日志包含可追踪上下文。
- 测试目标：锁定关键日志字段和事件契约。
- 测试文件：`tests/unit/test_logging_config.py`
- 测试场景：
  1. 开发环境与生产环境 formatter 切换正确。
  2. log level 覆盖规则正确。
  3. 敏感字段脱敏策略生效。
- 测试目标：锁定日志配置在不同环境下的稳定输出。

<a id="ts-p1-6"></a>
#### `P1-6` SQLite 持久任务队列

- 测试文件：`tests/unit/test_job_store.py`
- 测试场景：
  1. `enqueue()` 支持 `Idempotency-Key` 去重。
  2. `lease_one()` 只抢占一个可运行任务。
  3. `lease_expires_at` 写入正确。
  4. `recover_expired_running_jobs()` 将超时任务转为 `retrying`。
  5. 同一 session 不出现多个 `running` job。
  6. retry 次数和 `not_before` 计算正确。
- 测试目标：锁定 JobStore 的入队、抢占、恢复和串行约束。
- 测试文件：`tests/unit/test_job_worker.py`
- 测试场景：
  1. `run_loop()` 可消费 strategy / generation 任务。
  2. 可重试错误进入 retry。
  3. 不可重试错误进入 failed。
  4. 任务重放不产生重复业务结果。
- 测试目标：锁定 worker 的任务执行、失败分流和幂等行为。
- 测试文件：`tests/integration/test_job_worker.py`
- 测试场景：
  1. worker 处理 strategy / generation job。
  2. crash 后 lease 回收。
  3. retry backoff 生效。
  4. 幂等重放无重复结果。
- 测试目标：验证 JobStore、Worker、Orchestrator 协作下的真实恢复链路。

### 12.3 P2 Strategy Engine

<a id="ts-p2-1"></a>
#### `P2-1` Engagement 分析器

- 测试文件：`tests/unit/test_engagement.py`
- 测试场景：
  1. `engagement_rate = λ*norm_likes + (1-λ)*norm_collects` 正确。
  2. Min-Max 归一化边界正确。
  3. 平台偏好统计正确。
  4. proposal 评分排序正确。
- 测试目标：锁定评分公式、归一化和偏好分析输出。

<a id="ts-p2-2"></a>
#### `P2-2` Strategy Agent

- 测试文件：`tests/unit/test_strategy_agent.py`
- 测试场景：
  1. data-driven strategy 分支。
  2. `quality_score < 0.35 and doc_count < 10` 触发 expansion。
  3. `new_unique_docs < 3` 停止 expansion。
  4. `quality_gain < 0.05` 停止 expansion。
  5. generic fallback 触发。
  6. Spider cooldown 生效后拒绝抓取。
  7. prompt 输出结构符合 `ContentStrategy` schema。
- 测试目标：锁定 StrategyAgent 的分支决策、停止条件和 fallback 语义。
- 测试文件：`tests/integration/test_strategy_workflow.py`
- 测试场景：
  1. strategy 全流程成功。
  2. Spider 返回空结果后 expansion。
  3. expansion 停止条件命中。
  4. fallback generic strategy 成功写入 session。
- 测试目标：验证 Strategy 端到端协作链路和结果持久化行为。

<a id="ts-p2-3"></a>
#### `P2-3` Strategy Prompts

- 测试文件：`tests/unit/test_strategy_agent.py`
- 测试场景：
  1. query expansion 输出 3-5 个可用替代 query。
  2. data-driven strategy 输出可通过 `ContentStrategy` schema。
  3. generic strategy 在无数据下也可解析。
- 测试目标：锁定策略 prompt 的结构稳定性和可消费性。

### 12.4 P3 Generation Engine

<a id="ts-p3-1"></a>
#### `P3-1` 提案生成与管理

- 测试文件：`tests/unit/test_generation_agent.py`
- 测试场景：
  1. proposal 生成 10 条且结构完整。
  2. top-k 选择正确。
  3. proposal 评分排序正确。
- 测试目标：锁定 proposal 的数量、结构和筛选逻辑。

<a id="ts-p3-2"></a>
#### `P3-2` 并行笔记生成

- 测试文件：`tests/unit/test_generation_agent.py`
- 测试场景：
  1. 5 路并发与 temperature 映射正确。
  2. temperature hint 正确注入 prompt。
  3. 单 slot 失败不阻塞其他 slot。
- 测试目标：锁定生成并发、温度映射和失败隔离行为。

<a id="ts-p3-3"></a>
#### `P3-3` 相似度处理

- 测试文件：`tests/unit/test_generation_agent.py`
- 测试场景：
  1. `embedding_similarity > 0.6` 触发重选。
  2. `0.3 < similarity <= 0.6` 仅 warning。
  3. `lexical_overlap` 超阈值仅告警不重选。
  4. proposal 被标记 high-risk 后不再被重复选中。
  5. 每 slot 最多重试 2 次。
- 测试目标：锁定相似度阈值、warning 语义和 proposal 重选规则。

<a id="ts-p3-4"></a>
#### `P3-4` Generation Agent

- 测试文件：`tests/unit/test_generation_agent.py`
- 测试场景：
  1. proposal 耗尽 / 全部失败处理正确。
  2. `BUDGET_EXCEEDED` 和 partial result 正确。
  3. prompt 输出结构和 temperature hint 正确。
- 测试目标：锁定 GenerationAgent 的边界处理和预算行为。
- 测试文件：`tests/integration/test_generation_workflow.py`
- 测试场景：
  1. generation 全流程成功。
  2. 高相似重选成功。
  3. 某 slot 失败后其他 slot 继续。
  4. 最终写回 `similarity_report`。
- 测试目标：验证生成主链路、部分失败容忍和结果写回行为。

<a id="ts-p3-5"></a>
#### `P3-5` Generation Prompts

- 测试文件：`tests/unit/test_generation_agent.py`
- 测试场景：
  1. proposal prompt 输出稳定可解析。
  2. note prompt 输出符合 XHS 风格。
  3. 不同 temperature 有明显风格差异。
- 测试目标：锁定生成 prompt 的结构稳定性、可用性和差异化输出。

### 12.5 P4 Workflow and API

<a id="ts-p4-1"></a>
#### `P4-1` LangGraph State & Nodes

- 测试文件：`tests/unit/test_nodes.py`
- 测试场景：
  1. `init_node/strategy_node/generate_node/error_node` 输入输出正确。
  2. node 通过 `SessionDataStore` 按需加载数据。
  3. node 重入幂等。
  4. error node 能记录异常并产出统一状态。
- 测试目标：锁定节点输入输出、按需加载、幂等性和错误出口语义。

<a id="ts-p4-2"></a>
#### `P4-2` Workflow 编排

- 测试文件：`tests/unit/test_edges.py`
- 测试场景：
  1. `should_expand_query()` 条件判断正确。
  2. `should_regenerate()` 条件判断正确。
  3. 边分支优先级正确。
  4. 边界值 `0.35/10/0.6/0.3` 行为正确。
- 测试目标：锁定 workflow 条件边路由和阈值边界行为。
- 测试文件：`tests/integration/test_checkpoint_recovery.py`
- 测试场景：
  1. generation 中断后恢复。
  2. 恢复结果与一次性执行一致。
  3. checkpoint 大小保持轻量。
- 测试文件：`tests/unit/test_session_state.py`
- 测试场景：
  1. exploration 三表关系与 `sessions` 轻量指针一致。
  2. latest-two retention 生效，old branch 保留。
  3. orphan roll / candidate 不进入 session snapshot。
  4. 关键指针失效时进入 `EXPLORATION_STATE_INCONSISTENT`。
- 测试目标：验证 exploration 存储模型、指针真相源和 fail-safe 恢复语义。
- 测试文件：`tests/integration/test_checkpoint_recovery.py`
- 测试场景：
  1. worker 恢复时先读 `jobs`，再读 `sessions`，不从 exploration 三表倒推当前状态。
  2. 某轮结果写入中断时，四类事务整体回滚，不留下可见半状态。
  3. confirm 后 strategy job 与 `candidate_selected` 状态同事务可见。
- 测试目标：验证 checkpoint 恢复语义和状态精简设计。

<a id="ts-p4-3"></a>
#### `P4-3` API 路由实现

- 测试文件：`tests/unit/test_router.py`
- 测试场景：
  1. `POST /sessions` 返回 `201`。
  2. `POST /explore` 返回 `202/409/423`。
  3. `POST /exploration/confirm` 返回 `200/404/409`。
  4. `POST /strategy` 返回 `202/503`。
  5. `POST /generate` 返回 `202/409`。
  6. `POST /resume` 返回 `200` 且幂等。
  7. `GET /sessions/{id}` 返回 `200/404/410`。
  8. `GET /jobs/{job_id}` 返回 `200/404`。
  9. `GET /sessions/{id}/events` 返回 SSE 响应。
  10. 错误响应 schema 符合规范。
- 测试目标：锁定所有核心 REST / SSE 端点的状态码和响应契约。

<a id="ts-p4-4"></a>
#### `P4-4` Orchestrator 集成

- 测试文件：`tests/e2e/test_session_flow.py`
- 测试场景：
  1. create session 成功。
  2. get session 成功。
  3. session 不存在 `404`。
  4. purged session `410`。
- 测试目标：锁定 session 主入口和基础查询链路。
- 测试文件：`tests/e2e/test_exploration_api.py`
- 测试场景：
  1. `mode=exploration` happy path。
  2. exploration 返回 candidates 或 zero-result rewrite suggestions。
  3. `stage=exploring + turn_status=awaiting_user_input` 后 confirm candidate 成功进入 `candidate_selected`，随后可进入 `strategy`。
  4. 部分 provider 失败时返回 degraded 结果而非 500。
  5. old branch 保留，`action=initial` 创建新 branch。
  6. `GET /sessions/{id}` 只返回 active branch 的 current/previous roll。
  7. stale candidate confirm 返回硬错误。
- 测试目标：锁定 exploration 接口主链路、branch 语义、handoff 和降级交付语义。
- 测试文件：`tests/e2e/test_strategy_api.py`
- 测试场景：
  1. strategy happy path。
  2. `Idempotency-Key` 幂等。
  3. Spider 失败进入 retry / failed。
  4. strategy 完成后 session 中可见 strategy 结果。
- 测试目标：锁定策略接口的主链路、幂等性和失败可见性。
- 测试文件：`tests/e2e/test_generation_api.py`
- 测试场景：
  1. generate happy path。
  2. 无 strategy 直接 generate 返回 `409`。
  3. generation 结果包含 notes 和 `similarity_report`。
  4. budget exceeded 时返回契约正确。
- 测试目标：锁定生成接口的主链路、前置条件冲突和预算失败语义。
- 测试文件：`tests/e2e/test_resume_and_lifecycle.py`
- 测试场景：
  1. `alive -> frozen -> resume -> alive`。
  2. `paused -> queued` 恢复。
  3. 生命周期超时变 `purged`。
- 测试目标：锁定用户可见生命周期流转。
- 测试文件：`tests/e2e/test_sse_api.py`
- 测试场景：
  1. 订阅事件流成功。
  2. 收到 `stage_changed/task_progress/task_completed/task_failed`。
  3. `Last-Event-ID` 补发成功。
- 测试目标：锁定 SSE 连接、心跳和重连补发能力。
- 测试文件：`tests/integration/test_sse_stream.py`
- 测试场景：
  1. replay 完成后连接保持打开，不因 heartbeat 自动结束。
  2. 同一条连接在空闲期间持续收到多个 heartbeat。
  3. replay 之后追加的新事件会在同一条连接上实时送达。
  4. heartbeat 不推进重连游标；客户端用最近一次持久化 `event_id` 重连仍能收到后续事件。
  5. heartbeat frame 不要求输出 SSE `id:`，且不得被测试视为可重放业务事件。
- 测试目标：锁定 spec 定义的常驻 SSE 语义；若框架内置 test transport 不能稳定暴露流式 chunk，应使用更贴近真实用户场景的 harness，而不是修改生产实现来迎合测试工具。
- 测试文件：`tests/e2e/test_idempotency_and_concurrency.py`
- 测试场景：
  1. 多 session 并发互不污染。
  2. 同 session 并发 generate 不出现双 `running`。
  3. 重复 resume 幂等。
  4. 重复 strategy / generate 请求不会写出重复结果。
- 测试目标：锁定并发隔离、串行约束和高频重试下的数据安全。
- 测试文件：`tests/e2e/test_error_contracts.py`
- 测试场景：
  1. `SESSION_NOT_FOUND`、`JOB_NOT_FOUND`、`BUDGET_EXCEEDED`、`JOB_MAX_RETRIES_EXCEEDED` 契约正确。
  2. 429/404/409/410/503 状态码映射正确。
  3. 错误响应 `message/details/error_code` 完整。
- 测试目标：锁定对外错误模型和 HTTP 契约。

### 12.6 P5 Verification and Delivery

<a id="ts-p5-1"></a>
#### `P5-1` 单元测试补全

- 测试文件：`tests/unit/test_alert_evaluator.py`
- 测试场景：
  1. 阈值命中后打开告警。
  2. 恢复后告警转为 resolved。
  3. suppression 窗口内不重复报警。
  4. `budget_exceeded_count` 等指标阈值映射正确。
- 测试目标：锁定告警阈值、恢复和抑制逻辑。
- 测试文件：`tests/unit/test_spider.py`
- 测试场景：
  1. 补齐连续失败后 cooldown 临界时刻等边界场景。
- 测试目标：提高 Spider 错误恢复分支覆盖率。
- 测试文件：`tests/unit/test_session_state.py`
- 测试场景：
  1. 补齐生命周期边界时刻与重复恢复场景。
- 测试目标：提高 session 状态边界覆盖率。
- 测试文件：`tests/unit/test_job_store.py`
- 测试场景：
  1. 补齐 lease 边界、重复入队、`not_before` 边界值。
- 测试目标：提高队列状态机覆盖率。
- 测试文件：`tests/unit/test_job_worker.py`
- 测试场景：
  1. 补齐最大重试次数耗尽后的终态分支。
- 测试目标：提高 worker 失败路径覆盖率。
- 测试文件：`tests/unit/test_rag_service.py`
- 测试场景：
  1. 补齐空文档、空检索结果、删除后查询场景。
- 测试目标：提高 RAG 边界覆盖率。
- 测试文件：`tests/unit/test_config.py`
- 测试场景：
  1. 补齐非法路径、非法数值、别名环境变量场景。
- 测试目标：提高配置健壮性覆盖率。
- 测试文件：`tests/unit/test_engagement.py`
- 测试场景：
  1. 补齐零值、极值、单样本场景。
- 测试目标：提高评分器边界覆盖率。
- 测试文件：`tests/unit/test_strategy_agent.py`
- 测试场景：
  1. 补齐无文档、低质量、扩展仍失败等极端路径。
- 测试目标：提高策略分支覆盖率。
- 测试文件：`tests/unit/test_generation_agent.py`
- 测试场景：
  1. 补齐预算耗尽、相似度边界、池耗尽边界场景。
- 测试目标：提高生成边界覆盖率。
- 测试文件：`tests/unit/test_nodes.py`
- 测试场景：
  1. 补齐异常恢复和空输入状态。
- 测试目标：提高节点容错覆盖率。
- 测试文件：`tests/unit/test_edges.py`
- 测试场景：
  1. 补齐多个阈值边界的临界值组合。
- 测试目标：提高条件边路由可靠性。
- 测试文件：`tests/unit/test_router.py`
- 测试场景：
  1. 补齐异常状态码和非法请求体场景。
  2. `Last-Event-ID` 非法值返回稳定错误契约，不泄露 500。
  3. session 预算字段映射使用真实运行值而非占位值。
- 测试目标：提高 API 单层契约覆盖率，并为 `RES-P4-3-002/003` 提供 unit 级关闭证据。
- 测试文件：`tests/unit/test_logging.py`
- 测试场景：
  1. 补齐失败日志和预算事件日志。
  2. 补齐 `session_frozen/session_purged/sse_heartbeat` 等 required logs 的真实路径断言。
- 测试目标：提高关键路径日志覆盖率，并为 `RES-P1-5-001` 提供 unit 级可验证部分。
- 测试文件：`tests/unit/test_logging_config.py`
- 测试场景：
  1. 补齐多环境切换和覆盖顺序场景。
- 测试目标：提高日志配置覆盖率。

<a id="ts-p5-2"></a>
#### `P5-2` 集成测试

- 测试文件：`tests/integration/test_sse_replay.py`
- 测试场景：
  1. 事件落库顺序正确。
  2. `Last-Event-ID` 补发正确。
  3. 补发后进入实时流。
  4. 客户端按 `event_id` 去重仍能拿到完整关键状态。
- 测试目标：锁定 SSE 重放与实时流衔接行为。
- 测试文件：`tests/integration/test_reindex_compensation.py`
- 测试场景：
  1. Chroma 写失败后 session 标记 `pending`。
  2. 补偿任务重试成功。
  3. 达到上限后进入 deadletter。
  4. 主流程在 pending 状态下仍可返回用户结果。
- 测试目标：锁定 reindex 补偿链路和故障降级能力。
- 测试文件：`tests/integration/test_strategy_workflow.py`
- 测试场景：
  1. 补齐多轮 expansion 和 fallback 组合场景。
- 测试目标：提高策略协作链路覆盖率。
- 测试文件：`tests/integration/test_generation_workflow.py`
- 测试场景：
  1. 补齐预算接近上限、部分 slot 成功的混合场景。
- 测试目标：提高生成协作链路覆盖率。
- 测试文件：`tests/integration/test_checkpoint_recovery.py`
- 测试场景：
  1. 补齐不同中断点恢复场景。
- 测试目标：提高恢复链路覆盖率。
- 测试文件：`tests/integration/test_job_worker.py`
- 测试场景：
  1. 补齐 worker 重启后不同 job_type 的恢复顺序场景。
  2. session 进入 `purged` 后，未完成 jobs 被统一 `cancelled`，不得残留 active jobs。
- 测试目标：提高任务恢复链路覆盖率，并验证 `RES-P1-2-001` 的跨模块一致性。

<a id="ts-p5-3"></a>
#### `P5-3` E2E 测试

- 测试文件：`tests/e2e/test_session_flow.py`
- 测试场景：
  1. 补齐重复查询与 purge 后重复访问场景。
- 测试目标：提高 session 用户路径回归覆盖率。
- 测试文件：`tests/e2e/test_strategy_api.py`
- 测试场景：
  1. 补齐重复提交和策略失败重试后的可见状态。
- 测试目标：提高策略 API 回归覆盖率。
- 测试文件：`tests/e2e/test_generation_api.py`
- 测试场景：
  1. 补齐 partial result、预算超限、无策略多次请求场景。
- 测试目标：提高生成 API 回归覆盖率。
- 测试文件：`tests/e2e/test_resume_and_lifecycle.py`
- 测试场景：
  1. 补齐冻结期间多次 resume 与超时边界场景。
- 测试目标：提高生命周期回归覆盖率。
- 测试文件：`tests/e2e/test_sse_api.py`
- 测试场景：
  1. 补齐长连接重连和多事件连续补发场景。
  2. 验证 `session_frozen/session_purged` 生命周期事件在真实 API 入口下可观察。
- 测试目标：提高 SSE 回归覆盖率，并验证 `RES-P4-3-001` 在真实入口下闭环。
- 测试文件：`tests/e2e/test_idempotency_and_concurrency.py`
- 测试场景：
  1. 补齐高并发重复提交场景。
- 测试目标：提高并发回归覆盖率。
- 测试文件：`tests/e2e/test_error_contracts.py`
- 测试场景：
  1. 补齐新增错误码与状态码映射场景。
  2. 覆盖非法 `Last-Event-ID`、预算字段错误映射等此前 residual 相关 contract。
- 测试目标：提高对外契约回归覆盖率，并验证前序 residual 在 E2E 层面真实关闭。

<a id="ts-p5-4"></a>
#### `P5-4` 文档交付

- 测试文件：README / API 文档演练
- 测试场景：
  1. README 的安装、配置、运行步骤可复现。
  2. API 示例与真实接口契约一致。
  3. 故障排除指南可覆盖高频失败路径。
  4. 已关闭 residual 的证据和仍 `OPEN` 的限制在文档中有清晰说明。
- 测试目标：确认交付文档能支撑部署、调用、常见问题排查，并准确反映 residual backlog 状态。
