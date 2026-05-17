# Development Guide: ALIGN-1 - Runtime 连接层

> Generated: 2026-05-16
> Source: docs/changes/2026-05-16-frontend-scope-v1-v2-alignment.md

## 1. Task Context

### Scope Boundary
- **Task ID**: ALIGN-1
- **Phase**: Frontend-Backend Connection
- **Dependencies**: None
- **Task Goal**: 建立云端/前端到本机 Agent Runtime 的最小稳定连接基础

### In Scope
- 修复 CORS 策略，允许任意 Origin（cloud-hosted 前端访问本机 runtime 需要）
- 统一 frontend 中 runtime base URL 读取到单一导出常量
- `initializeWorkspaceContext()` 增加 `/health` 检查作为第一步
- WorkspaceProvider 保留 `connected / offline / error` 三态（现有实现已基本满足，小幅完善即可）
- `frontend/src/lib/api.test.ts` 补充 workspace 初始化成功/失败单测

### Out Of Scope
- 端口扫描、自定义 runtime discovery
- 完整 HTTPS/cloud-to-localhost 安全加固
- 多租户 auth
- `server-api.ts` 的修改（ALIGN-2 负责消除 server-side 读取路径）

### Required Deliverables
- Production: `app/api/routes/router.py` CORS 修改
- Production: `frontend/src/lib/api.ts` base URL 统一 + health check
- Production: `frontend/src/components/providers/WorkspaceProvider.tsx` 状态完善（若需要）
- Tests: `frontend/src/lib/api.test.ts` 新增/更新单测

### Acceptance Criteria
- [ ] AC1: runtime 开启时，WorkspaceProvider 初始化成功（连接态）
- [ ] AC2: runtime 关闭时，WorkspaceProvider 显示可重试错误（不回退 mock 数据）
- [ ] AC3: 来自任意 Origin（含云端部署域名）的浏览器请求能通过 CORS preflight
- [ ] AC4: `initializeWorkspaceContext()` 先检查 `/health`，再获取 `/workspaces/default`
- [ ] AC5: runtime base URL 在 api.ts 中只定义一次（导出常量 `RUNTIME_BASE_URL`）
- [ ] AC6: 前端单测覆盖 workspace 初始化成功 / 失败两条路径

### Residual Obligations
- 无历史遗留 residual items
- 新发现：CORS 配置对 cloud-hosted origin 完全不通，为阻断性 bug，本任务必须修复

---

## 2. Architecture Context

### System Position
```
Browser (cloud domain)
  └─ WorkspaceProvider
       └─ initializeWorkspaceContext()   [api.ts]
            ├─ GET http://127.0.0.1:8000/health
            └─ GET http://127.0.0.1:8000/workspaces/default
                        ↑
              FastAPI (local, port 8000)
              └─ CORSMiddleware  ← 需修复
```

### Tech Stack
- Backend: Python / FastAPI / Starlette CORSMiddleware
- Frontend: Next.js 14 App Router / TypeScript / React

### Key Behavioral Constraints
- Local Agent Runtime URL 固定为 `http://127.0.0.1:8000`（可被 env var 覆盖）
- Cloud-hosted 前端 origin 不可预知 → CORS 必须允许任意 origin（不需要 credentials）
- WorkspaceProvider 必须在初始化完成前阻止子组件渲染（loading gate）

---

## 3. Technical Design

### 3.1 Module Structure

**Files to Modify:**
```
app/api/routes/router.py                    MODIFY  CORS allow_origins
frontend/src/lib/api.ts                     MODIFY  base URL 常量化 + health check
frontend/src/components/providers/
  WorkspaceProvider.tsx                     MODIFY  状态细化（minor）
frontend/src/lib/api.test.ts               MODIFY  新增单测
```

| Path | NEW/MODIFY | Required Change | Linked AC |
|------|-----------|-----------------|-----------|
| `app/api/routes/router.py` | MODIFY | `allow_origin_regex` → `allow_origins=["*"]` | AC3 |
| `frontend/src/lib/api.ts` | MODIFY | 导出 `RUNTIME_BASE_URL` 常量；统一三处 base URL 读取；`initializeWorkspaceContext` 加 health check | AC4, AC5 |
| `frontend/src/components/providers/WorkspaceProvider.tsx` | MODIFY | 将 loading 态文案从"正在连接…"改为区分 `connecting` vs `offline`（视 health 结果） | AC1, AC2 |
| `frontend/src/lib/api.test.ts` | MODIFY | 增加 `initializeWorkspaceContext` 成功/失败测试 | AC6 |

### 3.2 CORS Fix (Backend)

**当前代码（router.py L144-150）：**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**目标代码：**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # cloud-hosted 前端需要；local-first 阶段不需要 credentials
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

> 注：`allow_origins=["*"]` 与 `allow_credentials=True` 不兼容；这里 credentials=False，所以 `["*"]` 合法。

### 3.3 Base URL Centralization (Frontend)

**目标：** 在 `api.ts` 顶部导出单一常量，消除三处重复的 env var 读取。

```typescript
// 在 api.ts 顶部，imports 之后，接口定义之前
export const RUNTIME_BASE_URL: string =
  (typeof process !== "undefined" &&
    (process.env.NEXT_PUBLIC_XHS_API_BASE_URL?.trim() ||
     process.env.XHS_API_BASE_URL?.trim())) ||
  "http://127.0.0.1:8000";
```

然后将原有的三处 env var 读取（`getDefaultWorkspace`, `getApiConfig`, 以及其他地方）全部替换为 `RUNTIME_BASE_URL`。

### 3.4 Health Check in initializeWorkspaceContext

**目标逻辑：**
```
initializeWorkspaceContext()
  1. fetch GET /health
     - 失败 → throw RuntimeOfflineError（message: "Agent Runtime 未启动"）
  2. fetch GET /workspaces/default
     - 失败 → throw（原有逻辑）
  3. setWorkspaceContext(workspace_id, user_id)
  4. return workspace
```

**代码变更：**
```typescript
export async function initializeWorkspaceContext(): Promise<{ workspace_id: string; user_id: string }> {
  // Step 1: health check
  try {
    const healthRes = await fetch(`${RUNTIME_BASE_URL}/health`, { cache: "no-store" });
    if (!healthRes.ok) {
      throw new Error(`Agent Runtime 返回异常状态: ${healthRes.status}`);
    }
  } catch (err) {
    throw new Error(
      `Agent Runtime 未启动或不可达 (${RUNTIME_BASE_URL})。请先启动本地 runtime。`
    );
  }

  // Step 2: workspace
  const workspace = await getDefaultWorkspace();
  setWorkspaceContext(workspace.workspace_id, workspace.user_id);
  return workspace;
}
```

**注意：** `getDefaultWorkspace()` 现在改用 `RUNTIME_BASE_URL` 常量，不再重复读取 env var。

### 3.5 WorkspaceProvider State

当前实现已有 loading / error / ready 三态，基本满足需求。只需：
- 将 loading 文案从"正在连接..."改为"正在连接 Agent Runtime..."
- error 消息已包含重试入口（`LiveApiErrorState` with `onRetry`）

无需引入新的 state enum；保持现有结构。

### 3.6 Error Handling

| 错误场景 | 现象 | 处理 |
|---------|------|------|
| Runtime 未启动，health 失败 | fetch throws (network error) | `initializeWorkspaceContext` 抛出，WorkspaceProvider 展示错误+重试 |
| Runtime 启动但 workspace 接口失败 | response.status != 200 | 原有 error throw，WorkspaceProvider 展示错误+重试 |
| Runtime 健康，workspace 正常 | 全部成功 | `setReady(true)` |

---

## 4. Testing Strategy

### 4.1 Test Pyramid Mapping

| Level | File | Focus | Mock Strategy |
|-------|------|-------|--------------|
| Unit | `frontend/src/lib/api.test.ts` | `initializeWorkspaceContext` 成功/失败；`RUNTIME_BASE_URL` 导出 | `global.fetch` mock |

### 4.2 Critical Test Scenarios

1. `test_initializeWorkspaceContext_success`: health ok + workspace ok → sets workspace context, returns workspace_id
2. `test_initializeWorkspaceContext_health_fails`: health fetch throws network error → throws with "Agent Runtime 未启动" message
3. `test_initializeWorkspaceContext_health_status_error`: health returns 500 → throws
4. `test_initializeWorkspaceContext_workspace_fails`: health ok, workspace returns 404 → throws
5. `test_RUNTIME_BASE_URL_exported`: `RUNTIME_BASE_URL` 是 string，以 "http" 开头

### 4.3 Mock Pattern

```typescript
const mockFetch = vi.fn();
global.fetch = mockFetch;

// success case
mockFetch
  .mockResolvedValueOnce({ ok: true, json: async () => ({ status: "healthy" }) })
  .mockResolvedValueOnce({ ok: true, json: async () => ({ workspace_id: "ws-1", user_id: "u-1" }) });
```

---

## 5. Implementation Checklist

### Coding Sequence
1. [ ] `app/api/routes/router.py`: 修改 CORS `allow_origin_regex` → `allow_origins=["*"]`
2. [ ] `frontend/src/lib/api.ts`: 在文件顶部添加 `export const RUNTIME_BASE_URL`
3. [ ] `frontend/src/lib/api.ts`: 将三处 env var 读取替换为 `RUNTIME_BASE_URL`
4. [ ] `frontend/src/lib/api.ts`: 修改 `initializeWorkspaceContext` 加入 health check
5. [ ] `frontend/src/components/providers/WorkspaceProvider.tsx`: 更新 loading 文案
6. [ ] `frontend/src/lib/api.test.ts`: 添加 5 个新测试场景

---

## 6. Risk & Notes

**CORS change**: `allow_origins=["*"]` 在本地 runtime 场景安全——用户的 runtime 只监听 localhost，只有本机浏览器能访问。不涉及跨用户数据泄露。

**Health check 竞态**: health check 和 workspace fetch 是顺序执行，不存在竞态。

**`server-api.ts` 不在本任务范围内修改**: 它也有重复的 base URL 读取，但消除它的职责属于 ALIGN-2。

## 7. Spec Sync Expectations

- 本任务完成后，`ALIGN-1` 在 alignment doc 中状态从 `Pending` 改为 `Done`。
- CORS fix 是本任务新发现的必要修复，已纳入 scope。
