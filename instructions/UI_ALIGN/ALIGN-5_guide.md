# Development Guide: ALIGN-5 - Rule-based Intent Router

> Generated: 2026-05-16
> Source: docs/changes/2026-05-16-frontend-scope-v1-v2-alignment.md

## 1. Task Context

### Scope Boundary
- **Task ID**: ALIGN-5
- **Dependencies**: ALIGN-4 (Done) — workflow endpoint, thread/job stores
- **Goal**: 让 `POST /threads/{id}/messages` 根据线程状态和文本关键词分类意图，并对 pause/resume/cancel/ask_status 意图执行真实 job-control 操作

### In Scope
- 新增 `app/services/creator_intent_router.py` — 纯函数规则分类器
- 修改 `app/api/routes/router.py` — `POST /threads/{id}/messages` 接入 intent router，执行 job-control，持久化 `linked_session_id`/`linked_job_id`
- 修改 `app/memory/thread_store.py` — 无 schema 变更；仅确保 `append_message` 调用时传入 `linked_session_id` 和 `linked_job_id`（字段已存在）
- 修改 `frontend/src/app/creator/page.tsx` — 处理 message response 中的 intent，根据 pause/resume/cancel 结果更新 task 状态
- 新增 `tests/unit/test_creator_intent_router.py` — 规则分类单测
- 新增 `tests/e2e/test_creator_message_intent_api.py` — message API e2e，覆盖 running job 场景

### Out Of Scope
- `new_workflow` intent（启动新 workflow 仍走 `POST /threads/{id}/workflow` 显式端点）
- 对 `running` 状态的 job 的即时强停：`pause_session_jobs` 只暂停 `queued/retrying` 状态，`running` 的 job 会在阶段边界自然感知（worker 侧 ALIGN-6 范围）
- ML/LLM-based 意图分类：当前规则是 MVP 方案，**不是正式方案**。正式方案（本地小模型/LLM 分类器）属于待排期的独立任务，不在 ALIGN-1 到 ALIGN-8 范围内
- 实时重规划：`add_constraint` 仅落库 + 关联 job，不重跑 strategy agent。正式重规划依赖 agent 支持"增量约束注入"，属于独立的 agent 增强任务，不在 ALIGN-1 到 ALIGN-8 范围内

### Acceptance Criteria
- [x] AC1: 任务运行中发送消息，message 持久化，intent 分类正确，linked_session_id/linked_job_id 被写入
- [x] AC2: 发送 pause 关键词 → `pause_job` intent → `pause_session_jobs(session_id)` 被调用，返回 job_action_result
- [x] AC3: 发送 resume 关键词 → `resume_job` intent → `resume_paused_jobs(session_id)` 被调用
- [x] AC4: 发送 cancel 关键词 → `cancel_job` intent → `cancel_session_jobs(session_id)` 被调用
- [x] AC5: 发送 status 关键词 → `ask_status` intent → 返回 job.status/job_type/session_id/job_id 摘要
- [x] AC6: 无 active job 时普通消息 → `free_chat` intent，不触发 job-control
- [x] AC7: 有 active job 且无关键词 → `add_constraint` intent，message 落库并关联 active session/job
- [x] AC8: 单测覆盖所有 6 种 intent 分类路径

### Residual Obligations (来自 ALIGN-4 技术债务)
- **TD-ALIGN4-1 [本任务关闭]**: session 复用缺失。ALIGN-5 的 intent router 使消息端点感知 active job，当 job 正在运行时消息不会触发新 session，而是路由到 `add_constraint`。实质上解决了"用户发消息不应意外创建新 session"的问题。`POST /threads/{id}/workflow` 仍是唯一创建新 session 的入口。
- **TD-ALIGN4-3 [本任务不处理]**: job 状态轮询，留 ALIGN-6 SSE 替换

### ⚠ 技术债务（本任务产生）
- **TD-ALIGN5-1 [worker 侧，ALIGN-6 处理]**: `pause_job` 只暂停 `queued/retrying` 状态 job；已进入 `running` 的 job 会继续执行到阶段边界。正式暂停需要 worker 在执行循环中轮询 `pause_requested` flag（JobStore schema 已有该字段）。carry into: ALIGN-6
- **TD-ALIGN5-2 [待排期]**: `add_constraint` 消息落库后，strategy agent 不会重新读取约束重规划。正式方案需要 agent 支持"增量约束注入"。carry into: 无对应 ALIGN 任务，需单独排期
- **TD-ALIGN5-3 [待排期]**: 关键词规则有误判风险（"继续努力" → resume_job）。正式方案为小模型/LLM 分类器，接口已预留 async + IntentContext，替换时调用方不需改动。carry into: 无对应 ALIGN 任务，需单独排期

---

## 2. Architecture Context

### System Position
```
POST /threads/{id}/messages
  └─ thread_store.get_thread()        → 获取 active_job_id, active_workflow_session_id
  └─ job_store.get_job(active_job_id) → 获取 job.status
  └─ classify_intent(text, has_active_job)  [NEW: creator_intent_router.py]
       ├─ pause_job   → job_store.pause_session_jobs(session_id)
       ├─ resume_job  → job_store.resume_paused_jobs(session_id)
       ├─ cancel_job  → job_store.cancel_session_jobs(session_id)
       ├─ ask_status  → build status summary dict
       ├─ add_constraint → (no action, just persist)
       └─ free_chat   → (no action, just persist)
  └─ thread_store.append_message(intent=..., linked_session_id=..., linked_job_id=...)
  └─ return CreatorMessageResponse(intent=..., job_action_result=...)
```

### Key Behavioral Constraints
- `has_active_job` = thread.active_job_id 非空 **且** `job.status in ('queued', 'running', 'retrying', 'paused')`
- 命令关键词优先级高于 has_active_job 状态（即：暂停关键词即使无 active job 也返回 `pause_job`，但 job-control 操作无效时返回空 action_result）
- `pause_session_jobs` / `resume_paused_jobs` / `cancel_session_jobs` 操作的是 `session_id`（来自 thread.active_workflow_session_id），不是 `job_id`
- `job_action_result` 结构：`{"action": str, "affected_jobs": int, "session_id": str, "job_id": str}`
- `ask_status` 的 `job_action_result`：`{"job_id": str, "job_status": str, "job_type": str, "session_id": str, "stage": str}`

---

## 3. Technical Design

### 3.1 Module Structure

| Path | NEW/MODIFY | Required Change | Linked AC |
|------|-----------|-----------------|-----------|
| `app/services/creator_intent_router.py` | NEW | 纯函数 `classify_intent(text, has_active_job) -> str` | AC1-8 |
| `app/api/routes/router.py` | MODIFY | `POST /threads/{id}/messages` 接入 intent router + job-control | AC1-7 |
| `app/memory/thread_store.py` | MODIFY | 无 schema 变更；`append_message` 调用时补传 `linked_session_id`/`linked_job_id` | AC1,7 |
| `frontend/src/app/creator/page.tsx` | MODIFY | 读取 message response 的 intent，更新 task 状态 | AC2-4 |
| `tests/unit/test_creator_intent_router.py` | NEW | 8 个单测覆盖所有分类路径 | AC8 |
| `tests/e2e/test_creator_message_intent_api.py` | NEW | 4 个 e2e 测试覆盖 running job 场景 | AC1-7 |

### 3.2 Intent Router: `app/services/creator_intent_router.py`

**接口设计原则**：函数签名从一开始就设计为 `async`，context 用 dataclass 而非裸 bool，以便未来替换实现体（小模型/LLM）时调用方不需要改动。

```python
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional

@dataclass
class IntentContext:
    has_active_job: bool
    active_job_status: Optional[str] = None  # 未来模型分类可用
    # 扩展点：recent_messages: list[str] = field(default_factory=list)

_PAUSE_RE  = re.compile(r"暂停|停止|先停|pause", re.IGNORECASE)
_RESUME_RE = re.compile(r"恢复|继续|重启|resume", re.IGNORECASE)
_CANCEL_RE = re.compile(r"取消|中断|算了|不要了|cancel", re.IGNORECASE)
_STATUS_RE = re.compile(r"进度|状态|怎么样|完成了吗|多久|status", re.IGNORECASE)

async def classify_intent(text: str, context: IntentContext) -> str:
    """
    Rule-based intent classifier. Async signature reserves room for
    model-based classification without changing callers.

    Evaluation order: pause > resume > cancel > ask_status
                      > add_constraint (active job) > free_chat
    """
    if _PAUSE_RE.search(text):
        return "pause_job"
    if _RESUME_RE.search(text):
        return "resume_job"
    if _CANCEL_RE.search(text):
        return "cancel_job"
    if _STATUS_RE.search(text):
        return "ask_status"
    if context.has_active_job:
        return "add_constraint"
    return "free_chat"
```

**已知关键词误判风险**（规则方案的局限，记录为 TD-ALIGN5-3）：
- `"继续努力"` → 误触 `resume_job`
- `"取消这条约束"` → 误触 `cancel_job`
- `"停下来想想"` → 误触 `pause_job`

这些 case 只能通过上下文感知（模型分类）解决，规则 MVP 阶段接受此风险。

### 3.3 router.py 调用方式变化

`classify_intent` 现在是 async，调用改为：
```python
intent = await classify_intent(body.text, IntentContext(
    has_active_job=has_active_job,
    active_job_status=active_job.status if active_job else None,
))
```

### 3.3 `POST /threads/{id}/messages` 新实现

```python
@app.post("/threads/{thread_id}/messages", status_code=201)
async def append_thread_message(
    thread_id: str, body: CreatorMessageCreateRequest, request: Request
) -> CreatorMessageResponse:
    thread_store = _get_thread_store(request)
    job_store = _get_job_store(request)

    thread = await thread_store.get_thread(thread_id)
    if thread is None:
        raise APIError(status_code=404, error_code="THREAD_NOT_FOUND", ...)

    # Determine active job state
    active_session_id = thread["active_workflow_session_id"]
    active_job_id = thread["active_job_id"]
    active_job = None
    if active_job_id:
        active_job = await job_store.get_job(active_job_id)

    has_active_job = (
        active_job is not None
        and active_job.status in ("queued", "running", "retrying", "paused")
    )

    intent = classify_intent(body.text, has_active_job)

    # Execute job-control action
    job_action_result: Optional[dict] = None
    if intent == "pause_job" and active_session_id:
        count = await job_store.pause_session_jobs(active_session_id)
        job_action_result = {"action": "pause", "affected_jobs": count,
                             "session_id": active_session_id, "job_id": active_job_id}
    elif intent == "resume_job" and active_session_id:
        count = await job_store.resume_paused_jobs(active_session_id)
        job_action_result = {"action": "resume", "affected_jobs": count,
                             "session_id": active_session_id, "job_id": active_job_id}
    elif intent == "cancel_job" and active_session_id:
        count = await job_store.cancel_session_jobs(active_session_id, reason="user_cancelled")
        job_action_result = {"action": "cancel", "affected_jobs": count,
                             "session_id": active_session_id, "job_id": active_job_id}
    elif intent == "ask_status":
        job_action_result = {
            "job_id": active_job_id,
            "job_status": active_job.status if active_job else None,
            "job_type": active_job.job_type if active_job else None,
            "session_id": active_session_id,
        }

    # Persist message with intent + linked context
    msg_row = await thread_store.append_message(
        thread_id=thread_id,
        role="user",
        text=body.text,
        intent=intent,
        linked_session_id=active_session_id,
        linked_job_id=active_job_id,
    )

    message_record = CreatorMessageRecord(
        message_id=msg_row["id"], thread_id=msg_row["thread_id"],
        role=msg_row["role"], text=msg_row["text"], intent=msg_row["intent"],
        linked_session_id=msg_row["linked_session_id"],
        linked_job_id=msg_row["linked_job_id"], created_at=msg_row["created_at"],
    )
    return CreatorMessageResponse(message=message_record, intent=intent,
                                  job_action_result=job_action_result)
```

**需要在 router.py 顶部补充 import：**
```python
from app.services.creator_intent_router import classify_intent
```

### 3.4 Frontend: creator/page.tsx 更新

`sendMessage` 里的 `appendThreadMessage` 调用改为读取返回值；根据 intent 更新 task 状态：

```typescript
// api.ts: 修改 appendThreadMessage 返回类型
export async function appendThreadMessage(
  threadId: string,
  text: string
): Promise<{ intent: string; job_action_result: unknown }> {
  const res = await creatorFetch<{ message: unknown; intent: string; job_action_result: unknown }>(
    `/threads/${threadId}/messages`,
    { method: "POST", body: { text } }
  );
  return { intent: res.intent, job_action_result: res.job_action_result };
}
```

在 `sendMessage` 中处理 intent 响应：
```typescript
const result = await appendThreadMessage(activeThread.thread_id, text);
if (result.intent === "pause_job") {
  setTask((t) => t ? { ...t, status: "paused" } : t);
} else if (result.intent === "resume_job") {
  setTask((t) => t ? { ...t, status: "running" } : t);
} else if (result.intent === "cancel_job") {
  setTask((t) => t ? { ...t, status: "cancelled" } : t);
}
```

---

## 4. Testing Strategy

### 4.1 Unit Tests: `tests/unit/test_creator_intent_router.py`

| # | Test name | Scenario |
|---|-----------|---------|
| 1 | `test_pause_keywords` | "暂停" / "停止" / "先停" / "pause" → `pause_job` |
| 2 | `test_resume_keywords` | "恢复" / "继续" / "重启" → `resume_job` |
| 3 | `test_cancel_keywords` | "取消" / "中断" / "算了" → `cancel_job` |
| 4 | `test_ask_status_keywords` | "进度" / "状态" / "完成了吗" → `ask_status` |
| 5 | `test_add_constraint_when_job_active` | 普通文本 + has_active_job=True → `add_constraint` |
| 6 | `test_free_chat_when_no_job` | 普通文本 + has_active_job=False → `free_chat` |
| 7 | `test_command_priority_over_active_job` | "暂停" + has_active_job=True → `pause_job`（不变成 add_constraint）|
| 8 | `test_empty_text_no_job` | "" + has_active_job=False → `free_chat` |

### 4.2 E2E Tests: `tests/e2e/test_creator_message_intent_api.py`

Fixture 同 `test_creator_workflow_api.py`（ThreadStore + JobStore + SessionManager tmp 隔离）。

| # | Test name | Scenario |
|---|-----------|---------|
| 1 | `test_message_free_chat_no_active_job` | 无 active job → intent=free_chat，no job_action_result |
| 2 | `test_message_add_constraint_when_job_queued` | 先 workflow，再发普通消息 → intent=add_constraint，linked_job_id 非空 |
| 3 | `test_message_pause_job` | workflow 后发"暂停" → intent=pause_job，job_action_result.action=pause |
| 4 | `test_message_ask_status` | workflow 后发"进度" → intent=ask_status，job_action_result 含 job_id |

---

## 5. Implementation Checklist

1. [x] 新建 `app/services/__init__.py`（若不存在）
2. [x] 新建 `app/services/creator_intent_router.py`
3. [x] 修改 `app/api/routes/router.py` — import classify_intent + 替换 `append_thread_message` 实现
4. [x] 修改 `frontend/src/lib/api.ts` — `appendThreadMessage` 返回 intent + job_action_result
5. [x] 修改 `frontend/src/app/creator/page.tsx` — `sendMessage` 读取 intent 更新 task 状态
6. [x] 新建 `tests/unit/test_creator_intent_router.py`
7. [x] 新建 `tests/e2e/test_creator_message_intent_api.py`

---

## 6. Risk & Notes

**`pause_session_jobs` 只暂停 queued/retrying（TD-ALIGN5-1）**:
- 正式暂停 running job 需要 worker 在循环中检查 `pause_requested` flag（JobStore schema 已有）
- ALIGN-5 MVP 行为：用户发"暂停"→ queued job 立即暂停；running job 继续跑到当前阶段结束
- 这是非正式方案，必须在 spec 中记录为 OPEN 残留

**命令关键词优先于 add_constraint**:
- 若 text 同时含"继续" 和"生成"，`resume_job` 优先（regex 顺序决定，pause > resume > cancel > status > add_constraint/free_chat）

**前端 sendMessage 不再调 startWorkflow**:
- 所有用户消息都走 `appendThreadMessage`，backend 负责分类
- 启动新 workflow 仍需用户显式操作（按钮 / 首次消息 intent 检测），不在本任务范围

---

## 7. Spec Sync Expectations

- TD-ALIGN4-1 在本任务关闭：message 端点有 active job 时不触发新 session，`[-]` 改为 `[x]` 写回 ALIGN-4 进展
- 以下残留写回 spec 为 OPEN：

| 残留 ID | 问题 | carry into |
|---------|------|-----------|
| TD-ALIGN5-1 | pause 只暂停 queued/retrying，running job 不能即时停止 | ALIGN-6 |
| TD-ALIGN5-2 | add_constraint 不触发 strategy agent 重规划 | 后续 agent 增强 |
