# Development Guide: P4-3 - API 路由实现

> Generated: 2026-03-17
> Architect: implementation skill
> Status: Ready for development
> Source: `dev_spec.md` §4.3/§9.3, `docs/testing_strategy.md` `ts-p4-3`

> Historical Note (2026-03-18): this guide was generated before `P4-2` completed.
> It is still valid for router-layer REST/SSE contract work, but it does not mean
> the generation API main chain is fully integrated. True execution of queued
> generation jobs belongs to `P4-4 Orchestrator 集成`.

## 1. Task Context

### Scope Boundary
- **Task ID**: `P4-3`
- **Task Name**: API 路由实现
- **Phase**: Phase 4 工作流集成
- **Dependencies**:
  - `P1-5`、`P1-6` 已完成，`router.py` 已有部分 enqueue/resume/job/health 能力
  - 本 guide 的原始版本生成时 `P4-2` 尚未完成；现阶段应将其理解为“仅覆盖 router 契约层”
  - queued strategy/generate job 的真实执行、worker/main 集成与 generation 主链路落地，已转移到 `P4-4`

### Acceptance Criteria
- [ ] `POST /sessions` 可创建 session 并返回稳定 `201` 响应
- [ ] `GET /sessions/{id}` 可返回 session 状态、job 摘要和规范错误码
- [ ] `POST /sessions/{id}/strategy` / `generate` 具备阶段校验、生命周期校验和统一错误响应
- [ ] `POST /sessions/{id}/resume` 继续保持幂等且错误响应契约统一
- [ ] `GET /jobs/{job_id}` 返回稳定 schema，404 时使用统一错误体
- [ ] `GET /sessions/{id}/events` 返回标准 SSE 响应，支持 `Last-Event-ID` 补发已有事件，并至少发送 heartbeat

### Test Requirements
- **Test File**: `tests/unit/test_router.py`
- **Test Scenarios**:
  1. `POST /sessions` 返回 `201`
  2. `POST /strategy` 返回 `202`，错误阶段返回 `409`
  3. `POST /generate` 返回 `202`，前置条件不满足返回 `409`
  4. `POST /resume` 返回 `200` 且幂等
  5. `GET /sessions/{id}` 返回 `200/404/410`
  6. `GET /jobs/{job_id}` 返回 `200/404`
  7. `GET /sessions/{id}/events` 返回 SSE 响应并可读取事件帧
  8. 错误响应 schema 符合规范

## 2. Architecture Context

### System Position
- `app/api/routes/router.py` 是 HTTP/SSE 协议入口，只负责参数校验、状态机前置校验、响应序列化
- Session 真相来源于 `SessionManager`
- Job 真相来源于 `JobStore`
- SSE 历史事件存储已经有 `session_events` 表，但缺少 router 可直接消费的读写辅助

### Tech Stack
- Language/runtime: Python 3.10 + FastAPI
- Primary libraries/services: `fastapi`, `pydantic`, `aiosqlite`
- Execution pattern: HTTP 入队 + 后台 worker，SSE 为只读流
- Key behavioral constraints:
  - `strategy/generate` 只入队，不同步执行真实 agent
  - `frozen` 与 `purged` 的处理必须与生命周期规范一致
  - SSE 重连只使用 `Last-Event-ID`

### Constraints
- 不在本任务里实现完整 workflow / LangGraph / generation 主链路
- 不应据此 guide 将 generation API 视为“已集成完成”；这部分以后续 `P4-4` 为准
- 不引入新的公开生命周期状态
- 不把前端轮询 / SSE 保活算作用户交互

## 3. Technical Design

### 3.1 Files to Modify
- `app/api/routes/router.py`
- `app/models/schemas.py`
- `app/memory/job_store.py`
- `tests/unit/test_router.py`

### 3.2 Public Interfaces

**新增/补齐响应模型**
- `InitSessionRequest`
- `SessionStatusResponse`
- `ErrorResponse`
- `SessionEventPayload`
- `SessionEvent`

**router 入口**
- `create_session()`
- `enqueue_strategy()`
- `enqueue_generate()`
- `resume_session()`
- `get_session_status()`
- `get_job_status()`
- `stream_session_events()`

**JobStore 辅助接口**
- `append_session_event(...)`
- `list_session_events(...)`
- `get_latest_job_for_session(...)`

### 3.3 Core Flow

**Create Session**
1. 校验请求体
2. 生成 `session_id`
3. 调用 `SessionManager.create_session(...)`
4. 返回 `201` + session 核心字段

**Strategy / Generate**
1. 读取 session
2. 映射 `404 / 410 / 423 / 429`
3. 做阶段合法性判断
4. 记录用户触达 `touch_user_activity()`
5. 入队并返回 `202`
6. 写入一条 `session_events` 事件，供 SSE / replay 消费

**Get Session**
1. 读取 session
2. `None -> 404`，`purged -> 410`
3. 查询当前活跃或最近 job 摘要
4. 返回 `SessionStatusResponse`

**SSE**
1. 校验 session 是否存在，`purged -> 410`
2. 解析 `Last-Event-ID`
3. 先补发 `event_id > Last-Event-ID` 的历史事件，受 `SSE_REPLAY_LIMIT` 限制
4. 若当前无事件，也立即发一条 heartbeat
5. 保持标准 `text/event-stream` 响应头

### 3.4 Error Handling Strategy

**统一错误体**
- 所有 `/sessions/*` 和 `/jobs/*` 错误返回 `ErrorResponse`
- 字段固定：
  - `error_code`
  - `error_message`
  - `error_details`
  - `retryable`
  - `suggested_action`

**状态映射**
- `SESSION_NOT_FOUND -> 404`
- `JOB_NOT_FOUND -> 404`
- `INVALID_STAGE -> 409`
- `SESSION_PURGED -> 410`
- `SPIDER_COOLDOWN_ACTIVE -> 429`
- `SESSION_FROZEN -> 423`

**阶段规则**
- `strategy` 仅允许 `stage=init`
- `generate` 仅允许 `stage=strategy`
- `resume` 对 `purged` 返回 `410`，否则始终 `200`

## 4. Testing Strategy

### Layer
- 以 `unit` 为主，使用 `TestClient` + 临时 SQLite，验证 router 对真实内部组件的契约输出

### Must Implement
1. create session 成功返回 `201` 和 `stage=init`
2. strategy 在 `init` 可入队；重复或错误阶段返回 `409` + `INVALID_STAGE`
3. generate 在 `strategy` 可入队；`init` 直接调用返回 `409` + `INVALID_STAGE`
4. frozen / purged / missing session 的错误体稳定
5. get session 返回 job 摘要字段；purged 时返回 `410`
6. get job 404 返回统一错误体
7. SSE 返回 `text/event-stream`，可读到 replay 或 heartbeat 帧
8. `Last-Event-ID` 存在时仅补发更大的事件 ID

## 5. Assumptions

- 本任务只实现最小可用 SSE：历史补发 + heartbeat；更完整的实时 worker 事件集成留给后续阶段
- `token_used / budget_remaining / budget_degraded` 先按保守默认值返回，不在本任务里接真实预算统计
- 为了保证当前文档和实现一致，错误响应优先对齐 `docs/api_schemas.md` 的 `ErrorResponse`
