# Troubleshooting

本文聚焦当前实现里最常见的运行、接口和测试问题。若需要接口字段总览，请配合阅读 [docs/api_schemas.md](/Users/czx/Documents/agentic/xhs_note_generator/docs/api_schemas.md)。

## 启动与环境

### `python3 not found`

现象：

- 执行 `./setup_env.sh` 失败

原因：

- 本机没有可用的 Python 3.10+

处理：

```bash
python3 --version
```

确认本机已安装 Python 3.10+ 后重新执行：

```bash
./setup_env.sh
source .venv/bin/activate
```

### `.env` 配置了但仍无法运行

优先检查：

- 是否从 `.env.example` 复制得到 `.env`
- `LLM_PROVIDER` 是否与对应 API key 匹配
- `XHS_SPIDER_COOKIES` 是否已填写
- `XHS_SQLITE_DB_PATH` / `XHS_CHROMA_PERSIST_DIR` 是否指向可写目录

说明：

- `app/config.py` 会自动创建 SQLite / Chroma 的父目录
- `.env.example` 中同时存在 canonical 名称和 `XHS_*` alias，是为了兼容当前 `Settings`

## API 错误码

### `SESSION_NOT_FOUND` (`404`)

含义：

- 会话不存在，或者 `session_id` 输入错误

建议：

- 先调用 `POST /sessions`
- 确认 URL 中的 `session_id` 来自最新创建结果

### `INVALID_STAGE` (`409`)

含义：

- 当前 session 阶段不允许执行这个动作

常见场景：

- 在 `init` 阶段直接调用 `/generate`
- 同一 session 的 generate 任务仍在进行时重复提交

建议：

- 先 `GET /sessions/{id}` 查看 `stage` 和 `job_status`
- 确认 strategy 已完成后再 generate

### `SESSION_FROZEN` (`423`)

含义：

- 会话进入 `frozen` 生命周期，需先恢复

建议：

```bash
curl -X POST http://127.0.0.1:8000/sessions/<session_id>/resume
```

### `SESSION_PURGED` (`410`)

含义：

- 会话已被清理，不能再恢复或继续使用

建议：

- 重新创建 session
- 若这是非预期行为，检查 `last_user_activity_at` 与生命周期规则

### `SPIDER_COOLDOWN_ACTIVE` (`429`)

含义：

- Spider 当前处于冷却窗口

可观察字段：

- `error_details.spider_cooldown_until`
- `retryable=true`

建议：

- 等待冷却窗口结束后重试
- 不要在冷却期内持续重放同一个请求

### `INVALID_LAST_EVENT_ID` (`400`)

含义：

- SSE 重连时传入的 `Last-Event-ID` 不是非负整数

正确示例：

```bash
curl -N http://127.0.0.1:8000/sessions/<session_id>/events \
  -H 'Last-Event-ID: 12'
```

错误示例：

- `Last-Event-ID: abc`
- `Last-Event-ID: -1`

### `BUDGET_EXCEEDED`

含义：

- generation 阶段触发 session token budget 限制

建议：

- 查询 `GET /sessions/{id}` 中的：
  - `token_used`
  - `token_budget`
  - `budget_remaining`
  - `budget_degraded`
- 评估是否要减少并发生成、缩短上下文或新建 session

### `JOB_MAX_RETRIES_EXCEEDED`

含义：

- job 达到最大重试次数后失败

建议：

- 查看 `GET /jobs/{job_id}` 的：
  - `attempts`
  - `max_attempts`
  - `last_error_code`
  - `last_error_message`
- 若根因是上游 Spider / LLM 问题，优先修复外部依赖后再新建任务

## SSE 与实时流

### SSE 连上后没有事件

先检查：

- 是否已经为该 session 创建了 strategy 或 generate job
- 当前是否仅处于空闲状态，没有新的持久化事件
- 是否误把 heartbeat 当作业务事件过滤掉了

说明：

- 服务端会周期性发 `heartbeat`
- `heartbeat` 仅用于保活，不推进 reconnect cursor

### 断线重连后重复或漏事件

建议流程：

1. 记录上次收到的持久化 `event_id`
2. 重连时通过 `Last-Event-ID` 传回
3. 让服务端 replay `event_id` 之后的事件

当前契约：

- 只补发持久化事件
- replay 完成后自动进入 live stream

### `tests/e2e/test_sse_uvicorn.py` 被 skip

含义：

- 当前环境禁止绑定 localhost 端口

这通常发生在：

- 沙箱
- 更严格的 CI 容器

它表示：

- 当前环境无法完成“真实 Uvicorn socket 级 SSE”验证
- 不表示路由层或 `_event_stream` 本身失败

## 测试 warning

### Pydantic 弃用 warning

当前状态：

- 仓库里由 `Session` 模型触发的旧式 `Config/json_encoders` warning 已处理

若仍看到 Pydantic warning：

- 多半来自第三方依赖或其它尚未迁移的模型
- 先区分是本仓库代码还是依赖链

### `websockets` 弃用 warning

当前状态：

- 主要出现在 `tests/e2e/test_sse_uvicorn.py` 运行时
- 属于 Uvicorn / websockets 依赖链提示

影响：

- 当前不影响核心 SSE 功能通过
- 后续升级依赖时应再验证兼容性

## 建议排查顺序

当你不确定问题在哪一层时，建议按这个顺序看：

1. `GET /health`
2. `POST /sessions`
3. `GET /sessions/{id}`
4. `GET /jobs/{job_id}`
5. `GET /sessions/{id}/events`
6. 对应的 unit / integration / e2e 测试

如果问题仍不清楚，再回到：

- [README.md](/Users/czx/Documents/agentic/xhs_note_generator/README.md)
- [dev_spec.md](/Users/czx/Documents/agentic/xhs_note_generator/dev_spec.md)
