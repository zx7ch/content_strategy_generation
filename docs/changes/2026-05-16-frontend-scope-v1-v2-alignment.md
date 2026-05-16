# 2026-05-16 Frontend Scope Alignment for V1/V2

## Status

Accepted.

## Context

The product now has two primary frontend surfaces:

- `Creator Workbench` (`/creator`) for conversation-first content creation.
- `Workspace Console` (`/brands`, `/topic-pool`, `/decisions`, `/publish`, `/performance`, `/evaluation`, etc.) for the brand growth operating loop.

The existing V1 spec contains both `EDITING MODE` and `EXPLORATION MODE`. The current frontend Creator Workbench, however, is designed around chat + long-running workflow execution, not the exploration branch/roll/candidate workspace.

The existing V2 spec defines the full brand growth loop and a future collaborative runtime profile. The current deployment direction is local-first: cloud-hosted frontend, local Agent Runtime, and cloud LLM inference.

## Decision

UI experience is the target contract. Frontend and backend implementation must
follow the Creator Workbench and Workspace Console experience instead of
reducing the UI to the current backend surface.

V1 maps to the Creator Workbench. The Creator Workbench MVP will use V1 `EDITING MODE` first:

```text
user message
-> workflow session
-> strategy job
-> generation job
-> SSE progress
-> generated notes
```

V1 `EXPLORATION MODE` is deferred. It remains a designed capability, but it is not part of the initial Creator Workbench MVP. If introduced later, it must become an explicit Creator Workbench sub-mode with:

- topic exploration entrypoint
- candidate cards
- refine / refresh actions
- candidate confirmation
- handoff into strategy generation

V2 maps to the Workspace Console. The Workspace Console MVP focuses on the operator loop:

```text
brand setup
-> ingestion
-> topic pool
-> decision
-> publish record
-> performance feedback
-> evaluation
```

The local-first deployment profile is the first real deployment target. The collaborative cloud runtime profile remains a future migration path.

## Implementation Scope

This alignment is an implementation priority document, not a full replacement
for the formal V1/V2 specs. The current implementation pass must complete only
the highest-priority path described here:

- local-first runtime connection from the browser to the local Agent Runtime
- browser-side reads for local runtime APIs
- Creator Workbench wired to V1 `EDITING MODE`
- task controls and follow-up messages backed by real backend state
- generated outputs surfaced into the Workspace Console as publish candidates

The implementation should not expand into the full formal spec unless the item
is required to satisfy the UI experience above.

## Fixed Product Decisions

- Local Agent Runtime URL is fixed to `http://127.0.0.1:8000` for this pass.
- The first priority is to make the end-to-end flow work; complete HTTPS/cloud
  security hardening for browser-to-localhost access is not a blocking scope item.
- Creator thread/message/workflow linkage uses SQLite and the existing
  local-first persistence profile.
- The Creator Workbench MVP uses a rule-based intent router, not LLM
  classification.
- `完成` means the user manually ends and accepts the current Creator task.
- Creator generated outputs enter the Workspace Console as publish draft /
  publish record candidates. They do not directly write into the full Topic Pool
  or decision loop.

## Consequences

- `/creator` should first be wired to existing V1 strategy/generation/session/job/SSE APIs.
- `/creator` should not claim exploration branch support until the exploration UI and backend contracts are implemented.
- The chat input remains independent from workflow task controls; running jobs should not block follow-up messages in the same thread.
- The Workspace Console should prioritize real local runtime data access before advanced cloud collaboration features.
- Postgres remains important for the collaborative cloud runtime, but it is not required for the local-first MVP.

## Deferred Capabilities

V1 deferred:

- `ExplorationPlanner`
- `ExplorationStateStore`
- exploration branches / rolls / candidates
- exploration candidate cards
- refine / refresh / confirm handoff UI

V2 deferred:

- full multi-user workspace membership and RBAC
- Postgres as default system of record for the collaborative cloud runtime
- complete contextual bandit policy surface
- full offline replay diagnostics beyond the MVP evaluation view

## Next Implementation Priority

1. Make cloud-hosted frontend detect and connect to the local Agent Runtime.
2. Move local runtime API reads from Server Components to browser-side clients.
3. Wire `/creator` to V1 Editing Mode: sessions, strategy, generation, jobs, and SSE.
4. Surface generated outputs into the Workspace Console where appropriate.
5. Introduce V1 Exploration Mode only after the core creation workflow is reliable.

## Task Schedule

Each task below must record progress with:

- `Progress`: `Pending | In Progress | Done | Blocked`
- `Owner`: `Unassigned` until assigned
- `Last Updated`: empty until work starts
- `Bugfix Log`: `None` until a bugfix entry is needed

Bugfix entries must use this format:

```text
YYYY-MM-DD - [severity] symptom -> root cause -> fix -> regression test
```

### ALIGN-1 Runtime 连接层

任务目标：

- 建立云端/前端到本机 Agent Runtime 的最小稳定连接基础。

满足的 UI 体验：

- 用户打开 Console 或 Creator 时，前端能自动连接本机 runtime。
- runtime 未启动时，页面显示明确错误和重试入口，而不是空白、崩溃或 mock 数据。
- 所有页面使用一致的连接状态和错误提示。

任务范围：

- 固定连接 `http://127.0.0.1:8000`。
- 使用 `/health` 和 `/workspaces/default` 完成启动检查。
- 不实现端口扫描、自定义 discovery、完整 HTTPS 安全模型。

修改/新增文件：

- `frontend/src/lib/api.ts`
- `frontend/src/components/providers/WorkspaceProvider.tsx`
- `app/api/routes/router.py`
- `frontend/src/lib/api.test.ts`

关键修改点：

- 前端统一 runtime base URL。
- `WorkspaceProvider` 初始化时区分 `connected / offline / error`。
- 后端保证 `/health`、CORS、错误格式可被 browser-side frontend 消费。

验收标准：

- runtime 开启时 Console/Creator 初始化成功。
- runtime 关闭时前端显示可重试错误。
- 不回退 mock 数据。

测试设计：

- 前端单测覆盖 workspace 初始化成功/失败。
- 后端单测覆盖 `/health`、CORS、错误契约。

完成进展：

- `Progress`: Pending
- `Owner`: Unassigned
- `Last Updated`:
- `Bugfix Log`: None

### ALIGN-2 Server Component API 读取迁移

任务目标：

- 消除云端 Next server 访问用户本机 runtime 的路径，让本地 runtime 读取都发生在浏览器侧。

满足的 UI 体验：

- 用户访问 `/brands`、品牌详情等 Console 页面时，页面从用户浏览器直连本机 runtime。
- 云端部署前端时，Console 仍能读取本机数据。
- 数据加载失败时保持统一 live API 错误态和重试体验。

任务范围：

- 迁移仍由 Server Component 读取本机 API 的页面。
- 优先 `/brands`、`/brands/[id]`。
- 不重做 Console 信息架构或 V2 operator loop。

修改/新增文件：

- `frontend/src/app/brands/page.tsx`
- `frontend/src/app/brands/[id]/page.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/lib/server-api.ts`

关键修改点：

- `/brands`、`/brands/[id]` 改为 client-side loader。
- 保留现有 V2 Console UI 和 live data 行为。
- `server-api.ts` 不再作为本机 runtime 读取路径使用。

验收标准：

- `/brands` 和品牌详情页在浏览器侧读取 live API。
- 断开 runtime 时显示统一错误态。
- `npm run build` 通过。

测试设计：

- 前端 loader 单测覆盖成功、失败、重试。
- 构建测试覆盖 Next client/server 边界。

完成进展：

- `Progress`: Pending
- `Owner`: Unassigned
- `Last Updated`:
- `Bugfix Log`: None

### ALIGN-3 SQLite Thread / Message Store

任务目标：

- 为 Creator Workbench 建立真实 conversation thread 层，作为 workflow session 之上的用户体验模型。

满足的 UI 体验：

- 左侧对话列表不再是 mock。
- `新建对话`、`切换对话`、`保留历史消息` 有后端数据支撑。
- 同一个聊天窗口能挂载一个后台 workflow session/job。

任务范围：

- 新增轻量 thread/message SQLite 存储。
- 支持创建 thread、列出 thread、读取 thread、追加 message。
- 支持记录 active workflow session/job。
- 不实现分享、置顶、重命名、删除的完整能力；菜单能力可保留为后续任务。

修改/新增文件：

- 新增 `app/memory/thread_store.py`
- 修改 `app/models/schemas.py`
- 修改 `app/api/routes/router.py`
- 新增 `tests/unit/test_thread_store.py`
- 新增 `tests/e2e/test_creator_thread_api.py`

关键修改点：

- SQLite 表：`creator_threads`、`creator_messages`。
- thread 保存 `active_workflow_session_id`、`active_job_id`、`status`、`accepted_at`。
- message 保存 `role`、`text`、`intent`、`linked_session_id`、`linked_job_id`。

新增 API：

- `POST /threads`
- `GET /threads`
- `GET /threads/{thread_id}`
- `POST /threads/{thread_id}/messages`

验收标准：

- 可创建、列出、读取 thread。
- 可追加 user/assistant/system message。
- thread 可保存 active session/job 关联。

测试设计：

- Store 单测覆盖 create/list/get/append。
- API e2e 覆盖 thread 生命周期。

完成进展：

- `Progress`: Pending
- `Owner`: Unassigned
- `Last Updated`:
- `Bugfix Log`: None

### ALIGN-4 Creator Workflow API

任务目标：

- 把 `/creator` 从前端 mock 变成真实 V1 Editing Mode 工作流入口。

满足的 UI 体验：

- 用户在 Creator 输入 `生成内容 / 策略 / 笔记` 后，真实启动后台任务。
- 顶部任务条显示真实 session/job 状态。
- 用户能看到从 strategy 到 generation 再到 generated notes 的完整链路。

任务范围：

- 从 thread message 启动 V1 session 和 strategy job。
- strategy 完成后继续 generation job。
- 只做 Editing Mode，不做 Exploration Mode/candidate cards。

修改/新增文件：

- `app/api/routes/router.py`
- `app/memory/thread_store.py`
- `app/models/schemas.py`
- `frontend/src/lib/api.ts`
- `frontend/src/app/creator/page.tsx`
- 新增 `tests/e2e/test_creator_workflow_api.py`

关键修改点：

- 新增 `POST /threads/{thread_id}/workflow`。
- workflow API 创建或复用 V1 session，入队 strategy job，并回写 thread active session/job。
- MVP 采用前端收到 strategy succeeded 后调用 generate，复用现有 `/sessions/{id}/generate`。

验收标准：

- `/creator` 用户输入生成需求后真实创建 V1 session。
- strategy job 和 generation job 能顺序执行。
- 最终能在 Creator 里读取并展示 generated notes。
- 页面不承诺 Exploration/candidate workflow。

测试设计：

- e2e 覆盖 thread -> workflow -> strategy -> generation。
- 回归现有 strategy/generation API 测试。

完成进展：

- `Progress`: Pending
- `Owner`: Unassigned
- `Last Updated`:
- `Bugfix Log`: None

### ALIGN-5 Rule-based Intent Router

任务目标：

- 让运行中聊天输入保持可用，并把用户后续消息路由到可执行意图。

满足的 UI 体验：

- 后台任务运行时，输入框不会被锁死。
- 用户可以继续补充要求、询问进度、暂停、恢复、取消。
- 用户消息会留在同一个 conversation thread 中，而不是丢失或只存在前端状态。

任务范围：

- MVP 使用规则化 intent router。
- 支持 `add_constraint`、`ask_status`、`pause_job`、`resume_job`、`cancel_job`、`free_chat`。
- `add_constraint` 先落库并关联 active job，不要求 running job 实时重规划。

修改/新增文件：

- 新增 `app/services/creator_intent_router.py`
- 修改 `app/api/routes/router.py`
- 修改 `app/memory/thread_store.py`
- 新增 `tests/unit/test_creator_intent_router.py`

关键修改点：

- 包含 `暂停 / 停止` -> `pause_job`。
- 包含 `恢复 / 继续` -> `resume_job`。
- 包含 `取消 / 中断` -> `cancel_job`。
- 包含 `进度 / 状态` -> `ask_status`。
- 运行中其他用户消息 -> `add_constraint`。
- 非运行中普通消息 -> `free_chat`。

验收标准：

- 任务运行中发送消息，message 持久化且带 intent。
- pause/resume/cancel intent 会触发真实 job-control。
- ask_status 能返回当前 thread/session/job 状态摘要。

测试设计：

- 规则分类单测。
- message API e2e 覆盖 running job 场景。

完成进展：

- `Progress`: Pending
- `Owner`: Unassigned
- `Last Updated`:
- `Bugfix Log`: None

### ALIGN-6 Job Control API

任务目标：

- 补齐 Creator 任务条需要的真实后端控制面。

满足的 UI 体验：

- 用户点击 `停止 / 恢复 / 取消` 时，任务状态真实变化。
- UI 不再只是本地改状态。
- cancel 后任务不会继续写入成功结果，避免用户看到已取消任务又完成。

任务范围：

- 新增 job-level pause/resume/cancel API。
- queued/retrying job 可立即 pause/cancel。
- running job 在阶段边界响应 pause/cancel。
- 不实现复杂抢占式中断。

修改/新增文件：

- `app/api/routes/router.py`
- `app/memory/job_store.py`
- `app/workers/job_worker.py`
- `app/models/schemas.py`
- `tests/unit/test_job_store.py`
- `tests/integration/test_job_worker.py`
- 新增 `tests/e2e/test_job_control_api.py`

新增 API：

- `POST /jobs/{job_id}/pause`
- `POST /jobs/{job_id}/resume`
- `POST /jobs/{job_id}/cancel`

关键修改点：

- pause 后 job 进入 `paused` 或设置 pause requested。
- resume 后 paused job 回到 `queued`。
- cancel 后 unfinished job 进入 `cancelled`。
- worker 在执行前、阶段边界、写成功结果前检查 cancel state。

验收标准：

- UI 任务控制按钮调用真实 API。
- `/jobs/{id}` 返回真实状态。
- cancel 后不写 succeeded result。
- 状态变化进入事件流。

测试设计：

- job store 单测覆盖 pause/resume/cancel。
- worker 集成测试覆盖 cancelled job 不执行、不写成功结果。
- e2e 覆盖 API 幂等性和非法状态错误。

完成进展：

- `Progress`: Pending
- `Owner`: Unassigned
- `Last Updated`:
- `Bugfix Log`: None

### ALIGN-7 Thread-scoped Events

任务目标：

- 让 Creator 以 thread 为中心消费实时任务进度，而不是直接暴露 V1 session event 细节。

满足的 UI 体验：

- 同一个聊天窗口内，消息、任务条、生成结果能随后台事件更新。
- 刷新或断线重连后，能 replay 已发生的任务事件。
- 用户不需要理解 session/job 内部模型，也能看到 `策略中 / 生成中 / 已完成 / 失败 / 已暂停` 等状态。

任务范围：

- 新增 thread-scoped SSE。
- MVP 复用现有 session event replay，把 session/job event 映射成 thread event。
- 不重建完整事件系统。

修改/新增文件：

- `app/api/routes/router.py`
- `app/memory/thread_store.py`
- `frontend/src/lib/api.ts`
- `frontend/src/app/creator/page.tsx`
- 新增 `tests/integration/test_thread_events.py`

新增 API：

- `GET /threads/{thread_id}/events`

关键修改点：

- 支持事件：`message_created`、`workflow_stage_changed`、`workflow_task_progress`、`workflow_task_failed`、`workflow_task_completed`、`workflow_paused`、`workflow_resumed`、`workflow_cancelled`、`workflow_accepted`。
- EventSource handler 更新 Creator task strip 和 generated notes 区域。

验收标准：

- `/creator` 任务条由真实事件更新。
- completed/failed/cancelled/paused/resumed 都能反映到 UI。
- SSE 重连至少 replay 已持久化 session/job 事件。

测试设计：

- SSE replay 集成测试。
- 前端 EventSource handler 单测覆盖 progress/completed/failed/cancelled。

完成进展：

- `Progress`: Pending
- `Owner`: Unassigned
- `Last Updated`:
- `Bugfix Log`: None

### ALIGN-8 Manual Complete / Publish Candidate

任务目标：

- 定义并实现 `完成` 作为用户手动结束/采纳任务的真实语义，并把 Creator 产物带入 Console。

满足的 UI 体验：

- 用户看到 generated notes 后，可以点击 `完成` 表示采纳当前任务。
- 完成后结果不只留在聊天气泡里，而是能进入 Workspace Console 的发布工作流。
- `/publish` 页面能看到来自 Creator 的 publish draft / publish record 候选。

任务范围：

- complete 标记 thread/workflow accepted。
- generated notes 转为 publish candidate。
- 不写入完整 Topic Pool，不触发 decision/bandit。

修改/新增文件：

- `app/api/routes/router.py`
- `app/memory/thread_store.py`
- `app/models/schemas.py`
- `frontend/src/app/creator/page.tsx`
- `frontend/src/app/publish/page.tsx`
- `frontend/src/lib/api.ts`
- 新增 `tests/e2e/test_creator_publish_candidate.py`

新增 API：

- `POST /threads/{thread_id}/complete`
- `GET /publish-candidates`
- 可选：`POST /publish-records/from-candidate`

关键修改点：

- complete 幂等：重复完成不重复生成候选。
- candidate 记录 thread/session/generated note 来源。
- `/publish` 增加 Creator candidates 读取和展示入口。

验收标准：

- 点击完成后 thread 状态为 `accepted`。
- generated notes 生成 publish candidate。
- `/publish` 可看到 Creator 候选。
- 候选不会进入 Topic Pool。

测试设计：

- e2e 覆盖 Creator complete 后 publish 页面可读取候选。
- API 单测覆盖候选生成和重复 complete 幂等。

完成进展：

- `Progress`: Pending
- `Owner`: Unassigned
- `Last Updated`:
- `Bugfix Log`: None

## Backend API Contract Additions

The MVP backend contracts are fixed as follows:

- `POST /threads`
  - request: `{ "title"?: string }`
  - response: `{ "thread_id": string, "title": string, "status": "active", "active_workflow_session_id"?: string, "active_job_id"?: string }`

- `GET /threads`
  - response: `{ "items": ThreadSummary[] }`

- `GET /threads/{thread_id}`
  - response: `{ "thread": ThreadDetail, "messages": CreatorMessage[] }`

- `POST /threads/{thread_id}/messages`
  - request: `{ "text": string }`
  - response: `{ "message": CreatorMessage, "intent": CreatorIntent, "job_action_result"?: object }`

- `POST /threads/{thread_id}/workflow`
  - request: `{ "message_id"?: string, "user_query": string, "platform"?: string }`
  - response: `{ "thread_id": string, "session_id": string, "job_id": string, "job_type": "strategy" }`

- `GET /threads/{thread_id}/events`
  - SSE events map session/job events into thread-level names.

- `POST /jobs/{job_id}/pause`
- `POST /jobs/{job_id}/resume`
- `POST /jobs/{job_id}/cancel`
  - response: `{ "job_id": string, "session_id": string, "status": string }`

- `POST /threads/{thread_id}/complete`
  - response: `{ "thread_id": string, "status": "accepted", "publish_candidate_count": number }`

- `GET /publish-candidates`
  - response: `{ "items": PublishCandidate[] }`

## Testing and Acceptance Gate

Backend unit tests:

- `pytest tests/unit/test_thread_store.py`
- `pytest tests/unit/test_creator_intent_router.py`
- `pytest tests/unit/test_job_store.py`

Backend e2e/integration tests:

- `pytest tests/e2e/test_creator_thread_api.py`
- `pytest tests/e2e/test_creator_workflow_api.py`
- `pytest tests/e2e/test_job_control_api.py`
- `pytest tests/e2e/test_creator_publish_candidate.py`
- `pytest tests/integration/test_thread_events.py`

Existing V1 regression tests:

- `pytest tests/e2e/test_session_flow.py tests/e2e/test_strategy_api.py tests/e2e/test_generation_api.py tests/e2e/test_sse_api.py`

Frontend tests:

- Run existing `frontend/src/lib/*.test.ts` tests.
- Run `npm run build`.
- Manually check `/creator`, `/brands`, `/brands/[id]`, and `/publish`.

Global acceptance gate:

- `/creator` can run a real V1 Editing Mode flow from user message to generated notes.
- The chat input remains usable while workflow jobs run.
- task controls call backend APIs and persist state changes.
- generated notes can be accepted and surfaced as publish candidates.
- Workspace Console local runtime reads happen from the browser side.
- No UI copy or route claims V1 Exploration Mode support.

## Completion Progress

- `ALIGN-1 Runtime 连接层`: Pending
- `ALIGN-2 Server Component API 读取迁移`: Pending
- `ALIGN-3 SQLite Thread / Message Store`: Pending
- `ALIGN-4 Creator Workflow API`: Pending
- `ALIGN-5 Rule-based Intent Router`: Pending
- `ALIGN-6 Job Control API`: Pending
- `ALIGN-7 Thread-scoped Events`: Pending
- `ALIGN-8 Manual Complete / Publish Candidate`: Pending

## Bugfix Log

- None

## Assumptions

- This document update defines the execution plan; code implementation happens in follow-up tasks.
- Runtime URL remains fixed to `http://127.0.0.1:8000`.
- Complete HTTPS cloud-to-localhost security hardening is deferred.
- Thread/message/publish candidate storage uses SQLite.
- Intent routing uses deterministic rules.
- Generated outputs only enter publish draft / publish record candidate flow.
- V1 Exploration Mode, candidate cards, refine/refresh/confirm handoff, multi-user
  RBAC, Postgres-as-default, complete contextual bandit policy surface, and full
  replay diagnostics remain outside this implementation pass.
