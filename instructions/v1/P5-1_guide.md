# Development Guide: P5-1 - 单元测试补全

> Generated: 2026-03-21
> Architect: implementation skill
> Status: Ready for development
> Source: `dev_spec.md` §9.2/§9.3/§9.4, `docs/testing_strategy.md` `ts-p5-1`

## 1. Task Context

### Scope Boundary
- **Task ID**: `P5-1`
- **Task Name**: 单元测试补全
- **Phase**: Phase 5 测试交付
- **Dependencies**:
  - `P1-4` / `P1-5` / `P2-3` 已完成，可直接在现有实现上补 unit coverage
  - `P4-4` 已完成，router / worker / session / generation 主路径均可作为当前 unit 修复基础
- **Task Goal**:
  - 在 unit 层收口当前最明确、最影响后续开发的 contract 缺口，并为未能在本轮关闭的 backlog 留下准确 residual 记录

### In Scope
- 修复并锁定 `router` 的预算字段映射和 `Last-Event-ID` 错误契约
- 修复并锁定 `SessionManager` 的 `purged -> cancelled` 清理语义
- 将 `session_created/session_frozen/session_resumed/session_purged/stage_changed/sse_heartbeat` 及预算相关 required logs 接到真实业务路径
- 扩充 `tests/unit/test_router.py`、`tests/unit/test_logging.py`、`tests/unit/test_session_state.py`、`tests/unit/test_job_store.py`
- 新增 `tests/unit/test_job_worker.py`，覆盖最大重试耗尽后的终态分支
- 修复 `app/config.py` / `.env.example` 与现有 unit/acceptance 契约的漂移
- 将 legacy `tests/unit/test_content_strategy.py` 改造成当前 `ContentStrategyAgent` 的有效 unit 回归，而不是继续依赖失效的旧 patch 点
- 为 `llm_call_*` / `reindex_*` required logs 接入真实业务路径并补 unit 证据
- 落地最小可用 `alert_evaluator` 与 `tests/unit/test_alert_evaluator.py`，收口 `P5-1` 最后一个 unit backlog

### Out Of Scope
- 不承担 SSE 生命周期事件对外可见性的 integration / E2E 验证，这属于 `P5-2/P5-3`
- 不做与本轮 residual 无关的大规模重构

### Required Deliverables
- Production: `router.py`、`session_state.py`、`llm/client.py`、`alert_evaluator` 以及必要的日志埋点/观测模块修复
- Tests: 更新既有 unit tests，并新增/修复与 `config`、`ContentStrategyAgent`、`llm/reindex logs`、`alert_evaluator` 对应的 unit coverage
- Spec/Docs: 如本轮仍有未闭环 backlog，必须回写 `dev_spec.md` §9.4

### Acceptance Criteria (from dev_spec.md §9.3 + relevant residuals)
- [ ] AC1 `GET /sessions/{id}` 返回真实 `token_used/token_budget/budget_remaining/budget_degraded`
- [ ] AC2 非法 `Last-Event-ID` 返回稳定错误契约，不泄露 500
- [ ] AC3 `session_frozen/session_purged/sse_heartbeat` 以及本轮触达的相关 required logs 在真实业务路径被 emit，并有 unit 证据
- [ ] AC4 session 进入 `purged` 时，未完成 jobs 被统一置为 `cancelled`
- [ ] AC5 `tests/unit/test_job_worker.py` 存在并覆盖最大重试耗尽后的终态分支

### Residual Obligations (from dev_spec.md §9.4)
- **Relevant OPEN Residuals**:
  - `RES-P1-5-001`: `session_frozen/session_purged/sse_heartbeat` 等 required logs 未在真实路径 emit
  - `RES-P5-1-001`: `alert_evaluator` / `tests/unit/test_alert_evaluator.py` backlog 尚未落地
  - `RES-P5-1-002`: `app/config.py` / `.env.example` 与现有设置契约和测试不一致
  - `RES-P5-1-003`: legacy `tests/unit/test_content_strategy.py` 依赖已失效 patch 点，当前不能作为有效回归证据
- **Current-Phase Carry-Forward Items To Re-check**:
  - 若 `alert_evaluator` 只做最小实现，必须保证测试覆盖 open / resolve / suppression / threshold mapping 四类合同
  - 若 `llm_call_*` / `reindex_*` required logs 仍未在真实路径闭环，必须保留或细化 `RES-P1-5-001`
- **Resolved By This Task**:
  - 已关闭 `RES-P4-3-002`
  - 已关闭 `RES-P4-3-003`
  - 已关闭 `RES-P1-2-001`
  - 本轮继续尽量关闭 `RES-P1-5-001`
  - 本轮继续尽量关闭 `RES-P5-1-001`
  - 本轮继续尽量关闭 `RES-P5-1-002`
  - 本轮继续尽量关闭 `RES-P5-1-003`
- **Deferred / Blocked**:
  - `RES-P4-3-001` 需要 integration / E2E 证据，不在本轮关闭

### Contract Inventory
- Upstream contracts: `dev_spec.md` §1.5.9 Logging、§4.2 lifecycle、§4.3.7 SSE、§8.4.4 queue handling
- Downstream contracts: `P5-2` 的 SSE lifecycle / purge-job integration、`P5-3` 的 API / SSE E2E contract
- Files/interfaces with compatibility risk: `SessionStatusResponse`, `stream_session_events()`, `SessionManager.refresh_lifecycle_state()`, `JobWorker._execute_job()`

### Test Requirements (from docs/testing_strategy.md §12.x)
- **Primary Test Files**:
  - `tests/unit/test_router.py`
  - `tests/unit/test_logging.py`
  - `tests/unit/test_session_state.py`
  - `tests/unit/test_job_store.py`
  - `tests/unit/test_job_worker.py`
- **Test Scenarios**:
  1. router 预算字段映射、非法 `Last-Event-ID` 错误体、异常状态码覆盖
  2. session lifecycle 边界、`purged` 取消未完成 jobs、重复恢复路径
  3. required logs 在真实路径触发，尤其是 lifecycle / heartbeat / budget 事件
  4. worker 最大重试耗尽后进入 `failed/JOB_MAX_RETRIES_EXCEEDED`
- **Test Target**: 任务级 unit suite 必须覆盖 success / boundary / failure 路径，并给每个关闭的 residual 提供测试证据

---

## 2. Architecture Context

### System Position
```text
router.py -> SessionManager / JobStore -> SessionStatusResponse, SSE stream
SessionManager -> lifecycle transitions + session persistence
JobWorker -> queue failure semantics
ContentGenerationAgent -> budget accounting and generation result write-back
logging_config.py -> structured event contract facade
```

### Tech Stack
- Language/runtime: Python 3.10 + FastAPI + aiosqlite + pytest
- Primary libraries/services: `fastapi`, `pydantic`, `aiosqlite`, `structlog`
- Execution pattern: async service methods + deterministic unit tests with fake/mocked dependencies
- Key behavioral constraints:
  - unit tests must stay offline and deterministic
  - heartbeat 不写入 `session_events`，也不能推进 replay cursor
  - lifecycle / queue / error contract 必须优先保证对外可见行为正确

### Constraints
- 优先做最小生产代码修复，不把 P5-1 扩成新的架构阶段
- 不依赖真实 worker loop、真实 LLM 或真实网络
- 对未能在 unit 层证明的问题，必须明确 carry forward 到 `§9.4`

---

## 3. Technical Design

### 3.1 Module Structure (from dev_spec.md §9.3)

**Files to Create/Modify:**
```text
app/api/routes/router.py                 MODIFY
app/memory/session_state.py              MODIFY
app/agents/content_generation_agent.py   MODIFY
app/config.py                            MODIFY
app/llm/client.py                        MODIFY
app/observe/alert_evaluator.py           NEW
tests/unit/test_content_strategy.py      MODIFY
tests/unit/test_alert_evaluator.py       NEW
tests/unit/test_config.py                MODIFY
tests/unit/test_project_setup_acceptance.py MODIFY
tests/unit/test_router.py                MODIFY
tests/unit/test_logging.py               MODIFY
tests/unit/test_session_state.py         MODIFY
tests/unit/test_job_store.py             MODIFY
tests/unit/test_job_worker.py            NEW
```

**Per-file Change Intent**:
| Path | NEW/MODIFY | Required Change | Linked AC / Residual |
|------|------------|-----------------|----------------------|
| `app/api/routes/router.py` | `MODIFY` | 使用 session `similarity_report` 映射预算字段；校验 `Last-Event-ID`；记录 session/stage/heartbeat logs | `AC1`, `AC2`, `AC3`, `RES-P4-3-002`, `RES-P4-3-003`, `RES-P1-5-001` |
| `app/memory/session_state.py` | `MODIFY` | lifecycle 进入 `purged` 时取消未完成 jobs，并记录 frozen/purged logs | `AC3`, `AC4`, `RES-P1-2-001`, `RES-P1-5-001` |
| `app/agents/content_generation_agent.py` | `MODIFY` | 预算降级/超限路径 emit `budget_*` logs | `AC3`, `RES-P1-5-001` |
| `app/config.py` | `MODIFY` | 路径与复杂环境变量解析行为需与 `.env.example`/测试契约一致 | `RES-P5-1-002` |
| `app/llm/client.py` | `MODIFY` | LLM 成功/失败路径 emit `llm_call_completed` / `llm_call_failed` | `RES-P1-5-001` |
| `app/observe/alert_evaluator.py` | `NEW` | 基于 SQLite 事实表评估窗口指标，支持 open / resolve / suppression，并按阈值写入 `alerts` 表 | `RES-P5-1-001` |
| `tests/unit/test_content_strategy.py` | `MODIFY` | 用当前 `ContentStrategyAgent` 契约重写过期 legacy suite，移除失效 patch 假设 | `RES-P5-1-003` |
| `tests/unit/test_alert_evaluator.py` | `NEW` | 覆盖阈值命中、恢复、抑制和指标阈值映射 | `RES-P5-1-001` |
| `tests/unit/test_config.py` | `MODIFY` | 锁定路径校验和复杂 env 值解析行为 | `RES-P5-1-002` |
| `tests/unit/test_project_setup_acceptance.py` | `MODIFY` | 锁定 `.env.example` 可被 `Settings` 直接解析 | `RES-P5-1-002` |
| `tests/unit/test_router.py` | `MODIFY` | 增加预算字段、非法 `Last-Event-ID`、stage/session logs 相关断言 | `AC1`, `AC2`, `AC3` |
| `tests/unit/test_logging.py` | `MODIFY` | 锁定 lifecycle / heartbeat / budget 日志在真实路径出现 | `AC3` |
| `tests/unit/test_session_state.py` | `MODIFY` | 增加 `purged` 取消 job、lifecycle 边界与日志路径断言 | `AC3`, `AC4` |
| `tests/unit/test_job_store.py` | `MODIFY` | 增加 `cancel_session_jobs()` 终态边界断言 | `AC4` |
| `tests/unit/test_job_worker.py` | `NEW` | 锁定最大重试耗尽后的终态 | `AC5` |

### 3.2 Class & Interface Design

**Primary Class Or Entry Point**:
- `router._build_session_status()`
- `router.stream_session_events()`
- `SessionManager.refresh_lifecycle_state()`
- `ContentGenerationAgent.execute()`
- `JobWorker._execute_job()`

Expected public behavior:
- `SessionStatusResponse` 预算字段来源于 `session.similarity_report`，无值时才回退默认值
- `stream_session_events()` 遇到非法 `Last-Event-ID` 返回 `APIError`
- `refresh_lifecycle_state()` 在 `purged` 转换时完成 job 清理并记录生命周期日志
- `ContentGenerationAgent.execute()` 在预算降级/超限时记录结构化日志，不改变既有返回 contract

### 3.3 Algorithm & Logic Flow

**Core Flow**:
```text
read session -> map similarity_report budget fields -> build session status response
  -> validate Last-Event-ID
  -> replay persisted SSE events
  -> emit heartbeat without id and log sse_heartbeat

refresh session lifecycle
  -> compute target state
  -> if target is purged: cancel unfinished jobs
  -> persist lifecycle transition
  -> emit session_frozen/session_purged log when state changed

generation execute
  -> compute degraded slots
  -> emit budget_degrade_applied when slots reduced
  -> emit budget_exceeded when budget guardrail trips
```

### 3.4 Implementation Checklist
- [ ] 修正 `router` 的预算字段映射逻辑
- [ ] 为 `Last-Event-ID` 增加显式校验和 4xx 错误响应
- [ ] 为 `session_created/stage_changed/session_resumed/sse_heartbeat` 接入日志
- [ ] 为 `session_frozen/session_purged` 接入真实 lifecycle 日志
- [ ] 为 `budget_degrade_applied/budget_exceeded` 接入真实 generation 日志
- [ ] 在 `purged` 进入路径上取消未完成 jobs
- [ ] 补齐 / 新增对应 unit tests，并把不能关闭的项回写 residual

**Error Classification Rules**:
- 非法请求头/输入契约错误 -> `APIError` 4xx
- queue / lifecycle 未满足 spec -> 视为 contract_violation
- 尚无法在 unit 层闭环的问题 -> 保留 `OPEN` residual，不能伪装为完成

### 3.5 Error Handling Strategy

**Failure Mapping**:
```text
APIError
├── INVALID_LAST_EVENT_ID (400)
├── SESSION_NOT_FOUND (404)
├── SESSION_FROZEN (423)
└── SESSION_PURGED (410)
```

**State / Persistence Notes**:
- heartbeat 不能写入 `session_events`
- `purged` job cleanup 必须覆盖 `queued/paused/retrying/running`
- 预算字段优先读取 `session.similarity_report`，默认值仅作兜底

---

## 4. Testing Strategy (from docs/testing_strategy.md §12.x, §2, §3)

### 4.1 Test Pyramid Mapping

| Level | File | Count | Focus | Mock Strategy |
|-------|------|-------|-------|---------------|
| Unit | `tests/unit/test_router.py` | 3+ new scenarios | API 单层 contract | `TestClient` + 临时 SQLite |
| Unit | `tests/unit/test_logging.py` | 3+ new scenarios | 真实路径 required logs | patch `log_event` / capture logger |
| Unit | `tests/unit/test_session_state.py` | 2+ new scenarios | purge cleanup + lifecycle logs | 临时 SQLite + real `SessionManager/JobStore` |
| Unit | `tests/unit/test_job_store.py` | 1+ new scenario | cancelled 终态边界 | real `JobStore` |
| Unit | `tests/unit/test_job_worker.py` | new file | max retries exhausted | fake orchestrator |

### 4.2 Critical Test Scenarios (from docs/testing_strategy.md §12.x)

**Must Implement**:
1. `GET /sessions/{id}` 预算字段读取真实 `similarity_report`
2. 非法 `Last-Event-ID` 返回稳定错误体
3. `session_frozen/session_purged/sse_heartbeat` 在真实路径 emit
4. `purged` 转换时未完成 jobs 变为 `cancelled`
5. worker 最大重试耗尽后进入 `failed` 且错误码为 `JOB_MAX_RETRIES_EXCEEDED`

**Mock Requirements**:
- 对日志验证优先 patch `log_event`，避免依赖 stdout 格式
- 对 worker 测试使用 fake orchestrator，不接真实 agent
- 对 router/session 测试使用临时 SQLite，不接真实外部依赖

### 4.3 Test Data Fixtures (from docs/testing_strategy.md §4)

```python
# Existing fixtures/helpers to reuse:
isolated_db
_create_session_via_api()
_set_session_state()
SessionManager(db_path)
JobStore(db_path)
```

### 4.4 Shift-left Cadence (from docs/testing_strategy.md §5.1)

Per shift-left requirement:
- unit tests must be written together with the production fix
- every closed residual must map to at least one concrete test
- coverage target: core logic `>= 80%`

---

## 5. Implementation Checklist

### Coding Sequence (Order Matters)
1. [ ] 先修 `router` 预算字段和 `Last-Event-ID` 契约
2. [ ] 再修 lifecycle / purge cleanup / logging 路径
3. [ ] 接入 generation 的预算日志
4. [ ] 扩充既有 unit tests
5. [ ] 新建 `tests/unit/test_job_worker.py`

### Dependencies to Install/Verify
```text
No new runtime dependencies expected.
Use existing pytest / fastapi / aiosqlite stack.
```

### Configuration Required
```yaml
SESSION_TOKEN_BUDGET: use monkeypatch in tests
SSE_HEARTBEAT_SECONDS: lower in tests for deterministic heartbeat assertions
```

---

## 6. Risk & Notes

**Technical Debt Warning**:
- `alert_evaluator` 测试矩阵仍指向一个当前仓库未落地的模块，若本轮未补齐必须登记 residual
- `llm_call_*` required logs 可能仍未完全落地；如果本轮未处理，不能假关闭 logging backlog

**Architecture Decision**:
- 生命周期 purge cleanup 优先保证 contract 正确，采用最小实现而非重构整个 queue/lifecycle 编排
- 预算字段读取 session 写回结果，不在 router 重算 generation usage

**Spec Alignment**:
- `DONE` residual 只有在代码和测试证据同时具备时才关闭
- 任何未能在 unit 层闭环的问题都必须回写 `§9.4`

**Cross-task Dependencies**:
- `P5-2` 需要基于本轮 unit 修复继续做 lifecycle / SSE integration 验证
- `P5-3` 需要在真实 API 入口下重新验证本轮关闭的 router residual

## 7. Spec Sync Expectations

- 如果 `RES-P4-3-002`、`RES-P4-3-003`、`RES-P1-2-001` 有充分测试证据，应在 progress-tracker 中标记 `DONE`
- 如果 `RES-P1-5-001` 仍有部分 required logs 未接入，必须保留 `OPEN` 或拆分出更精确的新 residual
- 若 `tests/unit/test_job_worker.py` 或 `alert_evaluator` backlog 仍未闭环，必须登记到 `dev_spec.md` §9.4
