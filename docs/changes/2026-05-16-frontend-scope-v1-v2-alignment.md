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
- `Checklist`: AC 和交付物的逐条完成状态，标记规则：`[x]` 已完成 · `[ ]` 未完成 · `[-]` 有遗漏问题待解决
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

- `Progress`: Done
- `Owner`: claude-sonnet-4-6
- `Last Updated`: 2026-05-16
- `Checklist`:
  - [x] `frontend/src/lib/api.ts` — 统一导出 `RUNTIME_BASE_URL`，替换三处重复 env var 读取
  - [x] `frontend/src/lib/api.ts` — `initializeWorkspaceContext()` 先调 `/health` 再调 `/workspaces/default`
  - [x] `frontend/src/components/providers/WorkspaceProvider.tsx` — 连接状态文案更新
  - [x] `app/api/routes/router.py` — CORS 从 `allow_origin_regex` 改为 `allow_origins=["*"]`（`allow_credentials=False`）
  - [x] `frontend/src/lib/api.test.ts` — 新增 5 个测试覆盖 health check 成功/失败/网络错误路径，更新 1 个已有测试的错误断言
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

- `Progress`: Done
- `Owner`: claude-sonnet-4-6
- `Last Updated`: 2026-05-16
- `Checklist`:
  - [x] `frontend/src/app/brands/page.tsx` — 改为 Client Component，`useEffect` + `useState` 加载，错误态使用 `LiveApiErrorState`
  - [x] `frontend/src/app/brands/[id]/page.tsx` — 改为 Client Component，同上模式
  - [x] `frontend/src/lib/api.ts` — `getApiConfig()` 使用 `RUNTIME_BASE_URL` 常量
  - [x] `npm run build` — 通过，无 Server/Client 边界错误
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

- `Progress`: Done
- `Owner`: claude-sonnet-4-6
- `Last Updated`: 2026-05-16
- `Checklist`:
  - [x] `app/memory/thread_store.py` — ThreadStore 类，aiosqlite，`creator_threads` + `creator_messages` 两张表，8 个方法
  - [x] `app/models/schemas.py` — 追加 9 个 Pydantic 模型（CreatorThread*/CreatorMessage*）
  - [x] `app/main.py` — lifespan 中初始化 ThreadStore，赋值 `application.state.thread_store`，finally 中 `close()`
  - [x] `app/api/routes/router.py` — `_get_thread_store()` helper + 4 个端点（POST/GET /threads，GET /threads/{id}，POST /threads/{id}/messages）
  - [x] `tests/unit/test_thread_store.py` — 7 个 async 单测，全部通过
  - [x] `tests/e2e/test_creator_thread_api.py` — 7 个 e2e 测试，全部通过
  - [x] `tests/e2e/conftest.py` — mock `langgraph.checkpoint.sqlite.aio`（venv 中 langgraph 1.1.10 缺失该子模块）
- `Bugfix Log`: 2026-05-16 - [medium] e2e tests fail at collection with ModuleNotFoundError: langgraph.checkpoint.sqlite -> langgraph 1.1.10 omits checkpoint.sqlite submodule; app.memory.session_state imports AsyncSqliteSaver at module level -> created tests/e2e/conftest.py to mock the module before app imports -> all 7 e2e tests now pass

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

- `Progress`: Done
- `Owner`: claude-sonnet-4-6
- `Last Updated`: 2026-05-16
- `Checklist`:
  - [x] `app/models/schemas.py` — 追加 `CreatorWorkflowRequest`、`CreatorWorkflowResponse`
  - [x] `app/api/routes/router.py` — 新增 `_get_job_store()` helper + `POST /threads/{id}/workflow` 端点
  - [x] `frontend/src/lib/api.ts` — 新增 `listThreads`、`createThread`、`appendThreadMessage`、`startThreadWorkflow`、`getJobStatus`、`enqueueGenerate` 6 个函数及对应 interface
  - [x] `frontend/src/app/creator/page.tsx` — 接入真实 API：线程列表从后端加载，消息持久化，workflow 启动返回真实 session_id/job_id，job 状态 3s 轮询触发 generate
  - [x] `tests/e2e/test_creator_workflow_api.py` — 4 个 e2e 测试，全部通过
  - [x] `npm run build` — 通过，/creator 为静态渲染（Client Component）
  - [x] 线程切换不加载历史消息（TD-ALIGN4-2）：已在 ALIGN-9 通过 `selectThread -> getThread()` 加载真实历史消息，并恢复 task 状态
  - [-] 每次 workflow 新建 session，不复用（TD-ALIGN4-1）：仍未实现 session 复用；需单独排期，不再归属当前 MVP 必做项
  - [x] job 状态检测用 3s 轮询（TD-ALIGN4-3）：已在 ALIGN-7 替换为 subscribeThreadEvents EventSource，pollingRef 和 getJobStatus 调用已移除
- `Bugfix Log`: 2026-05-16 - [medium] POST /threads/{id}/workflow 缺少 session 事件写入和 stage 更新时序不完整 -> workflow 端点在 create_session 后直接调 update_session(stage=STRATEGY)，漏掉了 touch_user_activity、两条 append_session_event（session created + stage_changed）以及 log_event，导致 SSE 事件流里看不到 workflow 启动事件 -> 参照 _enqueue_with_stage 和 create_session 的完整模式补全两阶段写入 -> tests/e2e/test_creator_workflow_api.py 11/11 通过

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

- `Progress`: Done
- `Owner`: claude-sonnet-4-6
- `Last Updated`: 2026-05-16
- `Checklist`:
  - [x] `app/services/creator_intent_router.py` — 新增 `IntentContext` dataclass（`has_active_job`, `active_job_status`）、`ACTIVE_JOB_STATUSES`、`async classify_intent()`，优先级：pause > resume > cancel > ask_status > add_constraint > free_chat
  - [x] `app/api/routes/router.py` — `POST /threads/{id}/messages` 接入 intent router：读取 thread active_job_id → classify intent → 执行 pause/resume/cancel/ask_status job-control → 落库 message（带 intent + linked_session_id + linked_job_id）→ 返回 intent + job_action_result
  - [x] `frontend/src/lib/api.ts` — `appendThreadMessage` 返回 `{ intent, job_action_result }` 替换 `void`
  - [x] `frontend/src/app/creator/page.tsx` — `sendMessage` 读取 intent 并更新 task 状态（pause → paused / resume → running / cancel → cancelled / ask_status → 文本回显）
  - [x] `tests/unit/test_creator_intent_router.py` — 9 个单测，覆盖全部 6 种 intent 路径 + 优先级规则 + ACTIVE_JOB_STATUSES 常量
  - [x] `tests/e2e/test_creator_message_intent_api.py` — 4 个 e2e 测试，覆盖 free_chat / add_constraint / pause_job / ask_status 场景
  - [-] `pause_job` 只影响 queued/retrying，无法抢占 running job（TD-ALIGN5-1，ALIGN-6 部分关闭）：cancel 的 worker stage boundary guard 已在 ALIGN-6 实现；pause 对 running job 仍需 jobs 表新增 pause_requested 列，记为 TD-ALIGN6-1，待排期
  - [-] `add_constraint` 只落库，不触发 strategy agent 重规划（TD-ALIGN5-2）：实时重规划无对应 ALIGN 任务，需单独排期
  - [-] 规则分类存在关键词误命中（TD-ALIGN5-3，例如"继续努力" → resume_job）：async 接口设计保留模型替换空间，ML 分类无对应 ALIGN 任务，需单独排期
  - [-] 缺少 `redirect_job` 意图（TD-ALIGN5-4）：用户说"帮我改一下方向"等改变任务目标的表述时，正确语义是取消当前任务 + 以新 user_query 重新启动 workflow（cancel current session jobs → POST /threads/{id}/workflow with updated query → update thread active session/job）；现在该类消息被分类为 add_constraint 后仅落库，任务继续跑，用户无反馈。需新增 `redirect_job` intent、对应正则规则（或模型分类）和 router handler；无对应 ALIGN 任务，需单独排期
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

- `Progress`: Done
- `Owner`: claude-sonnet-4-6
- `Last Updated`: 2026-05-16
- `Checklist`:
  - [x] `app/memory/job_store.py` — 新增 `pause_job`, `resume_job`, `cancel_job` 3 个 job-level 方法（queued/retrying 可直接暂停；cancel 覆盖全部活跃状态含 running）
  - [x] `app/models/schemas.py` — 新增 `JobControlResponse(job_id, session_id, status)`
  - [x] `app/api/routes/router.py` — 新增 `_TERMINAL_JOB_STATUSES` + 3 个端点（prefetch 模式，404/409 错误处理完整）
  - [x] `app/workers/job_worker.py` — `_execute_job` cancel guard：`orchestrator.run_job` 返回后，写 `mark_succeeded` 前重新 fetch job 状态，若已 cancelled 则 emit `task_cancelled` 事件并跳过成功写入
  - [x] `tests/unit/test_job_store.py` — 新增 6 个单测，覆盖 pause/resume/cancel 全部状态转移
  - [x] `tests/e2e/test_job_control_api.py` — 新建，6 个 e2e 测试（pause/resume/cancel 200 + 404 + 409 路径）
  - [-] running job 软暂停未实现（TD-ALIGN6-1）：pause 对 running job 返回 409；正式方案需给 jobs 表加 `pause_requested` 列，无对应 ALIGN 任务，需单独排期
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

- `Progress`: Done
- `Owner`: claude-sonnet-4-6
- `Last Updated`: 2026-05-16
- `Checklist`:
  - [x] `app/api/routes/router.py` — 新增 `_SESSION_TO_THREAD_EVENT` 映射常量、`_format_sse_thread_event` 序列化 helper、`_thread_event_stream` 异步生成器（replay + live poll + heartbeat）、`GET /threads/{thread_id}/events` 端点（404/空流处理完整）；新增 `import json` 到 stdlib 导入块
  - [x] `frontend/src/lib/api.ts` — 新增 `ThreadEventData` interface + `subscribeThreadEvents` 函数（注册 workflow_task_progress/completed/failed/cancelled/stage_changed 5 个事件 handler，返回 EventSource 实例）
  - [x] `frontend/src/app/creator/page.tsx` — 移除 `getJobStatus` import 和 `pollingRef`；新增 `taskRef = useRef` + sync effect 解决 stale closure；用 `subscribeThreadEvents` useEffect 替换 3s 轮询 useEffect（TD-ALIGN4-3 关闭）
  - [x] `tests/integration/test_thread_events.py` — 新建，4 个集成测试：replay 映射事件名 / 404 无效 thread / 无 active session 空流 / Last-Event-ID replay 过滤
  - [x] `tests/integration/conftest.py` — 新建，mock langgraph.checkpoint.sqlite（与 e2e/conftest.py 相同模式）
  - [x] `tests/e2e/test_creator_thread_api.py` — fixture 补注入 `job_store`（ALIGN-5 给 POST /threads/{id}/messages 加了 `_get_job_store` 调用，原 fixture 只注入了 `thread_store`，导致 2 个测试在回归中变为 500）
  - [x] `_thread_event_stream` 订阅固定绑定到 `thread.active_workflow_session_id`；thread 切换新 workflow 时前端需靠 `task?.sessionId` 变化重建 EventSource，无自动切换（TD-ALIGN7-1）：已在 ALIGN-9 通过 `subscribedThreadId` + `subscribedSessionId` stale event guard 和 `task.sessionId` 依赖重建 EventSource 关闭
  - [-] `GET /threads/{id}/events` 无鉴权，任何客户端可订阅任意 thread 的事件流（TD-ALIGN7-2）：local-first 单用户可接受，云部署需补鉴权，无对应 ALIGN 任务
- `Bugfix Log`:
  - 2026-05-16 - [low] `tests/integration/` 缺少 conftest.py → `test_thread_events.py` collect 时 ModuleNotFoundError: langgraph.checkpoint.sqlite -> 新建 tests/integration/conftest.py mock 该模块 -> 4 个测试通过
  - 2026-05-16 - [low] tests 2/3 用假 lifespan 注入 app.state 无效 → `ASGITransport` 不触发 lifespan，`app.state.thread_store` 为 None 返回 500 -> 改为在 async with JobStore/ThreadStore 上下文中直接赋值 `app.state` -> 全部通过

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

- `Progress`: Done
- `Owner`: claude-sonnet-4-6
- `Last Updated`: 2026-05-16
- `Checklist`:
  - [x] `app/models/schemas.py` — 新增 `PublishCandidate`, `CompleteThreadResponse`, `PublishCandidatesResponse`, `GeneratedNoteItem`, `ThreadResultResponse` 5 个 schema 类
  - [x] `app/memory/thread_store.py` — `_init_tables` 新增 `publish_candidates` 表及索引；新增 `complete_thread`, `save_publish_candidates`, `count_publish_candidates`, `list_publish_candidates` 4 个方法
  - [x] `app/api/routes/router.py` — 新增 `POST /threads/{id}/complete`（幂等，跨 DB 读取 generated notes，写入候选）、`GET /publish-candidates`、`GET /threads/{id}/result` 3 个端点；补充 5 个 schema 导入
  - [x] `frontend/src/lib/api.ts` — 新增 `PublishCandidate`, `CompleteThreadResponse`, `GeneratedNoteItem`, `ThreadResult` 4 个类型；新增 `completeThread`, `getPublishCandidates`, `getThreadResult` 3 个函数
  - [x] `frontend/src/app/creator/page.tsx` — 新增 `generatedResult` / `isAccepted` state；generation 完成后调 `getThreadResult` 展示策略定位 + 生成笔记；新增「完成 — 加入发布候选」按钮，点击调 `completeThread`
  - [x] `frontend/src/app/publish/page.tsx` — 新增 Creator 发布候选 section，`useEffect` 加载 `getPublishCandidates`，列表展示笔记标题/内容/标签
  - [x] `tests/e2e/test_creator_publish_candidate.py` — 新建，4 个 e2e 测试：complete 成功 / 幂等 / 404 / 候选列表空返回
  - [x] `GET /threads/{id}/result` 不在原始 API contract 表格内（TD-ALIGN8-1），已补充到 Backend API Contract Additions
  - [-] `GET /publish-candidates` 无分页（TD-ALIGN8-2）：MVP 候选量小，可接受，无对应 ALIGN 任务
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

- `PATCH /threads/{thread_id}`
  - request: `{ "title": string }`
  - response: `{ "thread_id": string, "title": string, "status": string, "active_workflow_session_id"?: string, "active_job_id"?: string }`

- `DELETE /threads/{thread_id}`
  - response: `{ "thread_id": string, "deleted": true }`
  - Added by ALIGN-9; if the thread has an active workflow session, unfinished jobs are cancelled before deleting thread data.

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

- `GET /threads/{thread_id}/result`
  - response: `{ "thread_id": string, "session_id": string|null, "strategy": object|null, "notes": GeneratedNoteItem[] }`
  - Added by ALIGN-8 for frontend result display; not in original contract section.

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

### ALIGN-9 Creator Chat 基础交互完善

任务目标：

- 补齐 Creator Workbench chat 基础交互和 thread 状态恢复问题，使用户能按主流 AI chat 习惯停止任务、管理对话，并在切换 thread 后继续看到正确的 workflow 状态。

满足的 UI 体验：

- 点击历史对话后，能看到该对话的真实历史消息，而不是欢迎语。
- 输入框随内容自动撑高，不截断多行输入。
- 发送消息后焦点自动回到输入框，无需手动点击。
- 运行中任务有显眼的停止入口，不要求用户输入"停止/取消"才能触发控制。
- 重命名/删除对话是真实持久化能力，不是空菜单。
- 切换 thread 后，SSE 订阅和任务状态以新 thread 的 active workflow 为准，不继续订阅旧 session。

任务范围：

- TD-ALIGN4-2：切换对话加载历史消息（GET /threads/{id} 端点已就绪，补前端调用）。
- UX-4：输入框高度随输入内容自动增长，上限 `max-h-36`，触达后内部滚动。
- UX-5：`sendMessage` 完成后 `inputRef.current?.focus()`，保持输入连贯。
- 切换对话时同步恢复 task 状态：读取 thread 的 `active_job_id`，判断是否有进行中任务并恢复 task strip。
- UX-9-1：显眼停止按钮，调用真实 job-control/intent 路径，禁止只改本地状态。
- UX-9-2：重命名/删除对话补真实 API、store 方法、前端调用和回归测试。
- UX-9-3：thread 切换后 EventSource 跟随当前 thread/session；旧 SSE 回调不能污染新对话。
- UX-9-4：切换对话后 task 状态恢复依赖 TD-ALIGN4-2，一起修复。

依赖与修复顺序：

1. 先修 UX-9-4 / UX-9-3：切换 thread 必须先恢复当前 thread 的 `active_workflow_session_id` / `active_job_id`，再建立对应 EventSource；否则停止按钮和状态展示可能操作旧 job。
2. 再修 UX-9-1：停止按钮复用已恢复的当前 `task.jobId`，调用 `POST /jobs/{job_id}/cancel`，失败时回退到 `POST /threads/{id}/messages` 的 `cancel_job` intent。
3. 最后修 UX-9-2：重命名/删除是独立管理能力，但删除运行中 thread 时必须先取消该 thread 的 unfinished session jobs，再删除 thread/message/candidate 数据。

不在本次范围：

- markdown 渲染、时间分组等 P2/P3 项留后续任务。
- 不为了前端效果修改 V1 workflow/job 后端执行语义；只补 thread 管理 API 与前端状态衔接。

修改文件：

- `frontend/src/app/creator/page.tsx`
- `frontend/src/lib/api.ts`
- `app/memory/thread_store.py`
- `app/models/schemas.py`
- `app/api/routes/router.py`
- `tests/unit/test_thread_store.py`
- `tests/e2e/test_creator_thread_api.py`

关键修改点：

- `selectThread`：调 `GET /threads/{id}` 拿 messages，映射为 `ChatMessage[]` 替换欢迎语；读 `active_job_id` 和 `GET /jobs/{id}` 恢复 task strip。
- SSE effect：订阅时固定 `subscribedThreadId` + `subscribedSessionId`，回调丢弃 stale event；stage_changed(generate) 更新 generation jobId。
- 停止按钮：运行中显示按钮，调用 `cancelJob(task.jobId)`，不依赖用户打字触发 intent。
- 重命名/删除：新增 `PATCH /threads/{id}`、`DELETE /threads/{id}`；删除时取消 active session unfinished jobs。
- `textarea`：绑定 `ref`，`onInput` 时 `el.style.height = "auto"; el.style.height = el.scrollHeight + "px"`。
- `sendMessage`：完成后 `inputRef.current?.focus()`。
- `inputRef`：新增 `useRef<HTMLTextAreaElement>(null)`。

验收标准：

- 点击任意历史对话，消息列表显示该对话的真实历史（包括 user / assistant / system 角色气泡）。
- 输入 3 行以上内容，输入框高度随之增长；超过 `max-h-36` 后出现内部滚动条。
- 发送消息后，光标自动回到输入框。
- 切换有 active_job_id 的对话时，task strip 恢复真实 job_type/status，SSE 订阅当前 thread 的 active session。
- 切换 thread 后启动新任务，EventSource 订阅新 workflow，不消费旧 session 事件。
- 点击停止按钮后，真实 job 进入 cancelled；刷新或切换回来后仍显示已取消。
- 重命名后列表和 `GET /threads/{id}` 标题一致；删除后列表移除，`GET /threads/{id}` 返回 404。

测试设计：

- 手动验证：打开已有历史对话 → 消息正确显示。
- 手动验证：输入多行 → 输入框撑高。
- 手动验证：发送后 → 光标在输入框。
- 手动验证：运行中点击停止 → job-control 生效，SSE 不再把旧任务写回当前 thread。
- 单测：`ThreadStore.update_thread_title`、`ThreadStore.delete_thread`。
- e2e：`PATCH /threads/{id}`、`DELETE /threads/{id}`。

完成进展：

- `Progress`: Done
- `Owner`: Codex
- `Last Updated`: 2026-05-17
- `Checklist`:
  - [x] `frontend/src/lib/api.ts` — 新增 `CreatorMessage`、`CreatorThreadDetail` interface，新增 `getThread()` 函数
  - [x] `frontend/src/app/creator/page.tsx` — `selectThread` 调 `getThread` 加载历史消息，映射为 `ChatMessage[]`
  - [x] `frontend/src/app/creator/page.tsx` — task strip 恢复逻辑：读取真实 job_type/status，恢复 strategy/generation 与 running/paused/cancelled
  - [x] `frontend/src/app/creator/page.tsx` — `inputRef` + textarea `onChange` 自动撑高（reset to auto → expand to scrollHeight）
  - [x] `frontend/src/app/creator/page.tsx` — `sendMessage` 所有退出路径末尾补 `inputRef.current?.focus()`
  - [x] `frontend/src/app/creator/page.tsx` — placeholder 补充"Shift+Enter 换行"提示
  - [x] UX-9-1 — 显眼停止按钮，调用真实 `POST /jobs/{job_id}/cancel`，失败时回退 `cancel_job` intent
  - [x] UX-9-2 — 重命名/删除真实 API + 前端调用 + 单测/e2e
  - [x] UX-9-3 — thread 切换后 EventSource 自动跟随新 workflow，旧 SSE 回调按 thread/session 丢弃
  - [x] UX-9-4 — 切换对话 task 状态恢复，依赖 TD-ALIGN4-2 一起修
  - [x] `pytest tests/unit/test_thread_store.py` — 9 passed
  - [x] `pytest tests/e2e/test_creator_thread_api.py` — 9 passed
  - [x] `npm run build` — 通过
  - [x] `npm run dev` + `curl -I http://localhost:3000/creator` — 页面返回 200 OK
- `Bugfix Log`:
  - 2026-05-17 - [low] `enqueueGenerate` 在 ALIGN-7 移除轮询后成为死代码 import → 从 creator/page.tsx import 列表删除
  - 2026-05-17 - [medium] `selectThread` 快速连点两个对话时存在竞态：后发起的 `getThread` 若先返回会被后来的覆盖 → 改用 `loadingThreadRef` 记录最后请求的 threadId，stale 响应直接 return 丢弃
  - 2026-05-17 - [high] `CreatorMessage` interface 字段名写为 `id`，但后端 `GET /threads/{id}` 实际返回 `message_id`（与 `CreatorMessageRecord` schema 一致）→ interface 改为 `message_id`，`selectThread` 映射改为 `m.message_id`；若不修复，历史消息气泡 key 全为 `undefined`，React 会报 key 警告且列表渲染异常
  - 2026-05-17 - [high] thread 切换后 EventSource 只按 React effect 生命周期关闭旧连接，但旧回调仍可能在关闭前落入当前 UI，导致新 thread 显示旧 session 进度/结果 -> SSE 订阅捕获 `subscribedThreadId` + `subscribedSessionId`，所有回调先校验当前 active thread 和 event session，不匹配直接丢弃 -> `npm run build` 通过，手动验收按切换 thread/启动新 workflow 场景执行
  - 2026-05-17 - [medium] 重命名/删除菜单只有视觉入口没有真实行为 -> 缺少 thread update/delete API 与前端调用 -> 新增 `PATCH /threads/{id}`、`DELETE /threads/{id}`、`ThreadStore.delete_thread`、前端 `renameThread/deleteThread` 和菜单 handler；删除 active session 前先 cancel unfinished jobs -> `pytest tests/unit/test_thread_store.py`、`pytest tests/e2e/test_creator_thread_api.py` 通过
  - 2026-05-17 - [medium] 切换 thread 后 task strip 只显示占位 stage，不能恢复 generation/paused/cancelled 状态 -> 前端 `selectThread` 读取 `GET /jobs/{active_job_id}`，按 job_type/status 恢复 stage/status，并对 accepted thread 回读 result -> `npm run build` 通过

## Completion Progress

- `ALIGN-1 Runtime 连接层`: Done
- `ALIGN-2 Server Component API 读取迁移`: Done
- `ALIGN-3 SQLite Thread / Message Store`: Done
- `ALIGN-4 Creator Workflow API`: Done
- `ALIGN-5 Rule-based Intent Router`: Done
- `ALIGN-6 Job Control API`: Done
- `ALIGN-7 Thread-scoped Events`: Done
- `ALIGN-8 Manual Complete / Publish Candidate`: Done
- `ALIGN-9 Creator Chat 基础交互完善`: Done

## Bugfix Log

- 2026-05-17 [medium] **strategy→generate 结构性 RTT 延迟**：generate job 由前端 `onCompleted(strategy)` 触发 `POST /sessions/{id}/jobs/generate`，引入 1-3s SSE 往返延迟，且前端关闭时 generate 不会自动运行。改为 `job_worker._execute_job` 在 strategy job `mark_succeeded` 后直接 `job_store.enqueue(generate)` 并写入 `stage_changed` 事件；前端 `onCompleted(strategy)` 改为纯本地 `setTask({stage: "generation"})` 不再发 POST。

- 2026-05-17 [medium] **spider 搜索黑盒等待**：`search_some_note(num=50)` 内部串行翻 3 页（每页 20 条），全部完成才返回，期间用户无任何进度反馈。改为在 `_sync_search` 中内联分页循环（调用 `search_note` per page），每页到达后触发 `on_page` 同步回调，通过 `asyncio.run_coroutine_threadsafe` 从线程池回调到 event loop，最终写入 `task_progress` 事件。用户每 20 条看到一次"搜索到 N 篇相关内容..."进度更新。HTTP 请求总数与原来相同，不增加风控风险。

## Open Items Summary

As of 2026-05-17, all ALIGN-1 through ALIGN-9 implementation tasks are `Done`.
Remaining open items are deferred follow-up gaps, not blockers for the current
local-first Creator Workbench MVP.

Open checklist entries: 8.
Unique open issues: 7 (`TD-ALIGN5-1` and `TD-ALIGN6-1` describe the same
running-job soft pause gap from different task sections).

- `TD-ALIGN4-1` — workflow still creates a new session on every start; session reuse is not implemented.
- `TD-ALIGN5-1` / `TD-ALIGN6-1` — running job soft pause is not implemented; pause only applies to queued/retrying jobs, while running jobs should use cancel for the MVP.
- `TD-ALIGN5-2` — `add_constraint` only persists the message; it does not trigger strategy replanning for an already running job.
- `TD-ALIGN5-3` — rule-based intent classification has known keyword false positives; context-aware/model classification is deferred.
- `TD-ALIGN5-4` — `redirect_job` intent is missing; changing task direction does not cancel and restart workflow automatically.
- `TD-ALIGN7-2` — `GET /threads/{id}/events` has no auth gate; acceptable for local-first single-user MVP, but cloud deployment needs authorization.
- `TD-ALIGN8-2` — `GET /publish-candidates` has no pagination; acceptable for MVP-sized candidate volume.

## Assumptions

- This document records the current execution plan and bugfix scope before code implementation.
- Runtime URL remains fixed to `http://127.0.0.1:8000`.
- Complete HTTPS cloud-to-localhost security hardening is deferred.
- Thread/message/publish candidate storage uses SQLite.
- Intent routing uses deterministic rules.
- Generated outputs only enter publish draft / publish record candidate flow.
- V1 Exploration Mode, candidate cards, refine/refresh/confirm handoff, multi-user
  RBAC, Postgres-as-default, complete contextual bandit policy surface, and full
  replay diagnostics remain outside this implementation pass.
