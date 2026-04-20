# Development Guide: P4-1 - LangGraph State & Nodes

> Generated: 2026-03-18
> Architect: implementation skill
> Status: Ready for development
> Source: `dev_spec.md` §1.5.2/§7.4.1/§9.2/§9.3, `docs/testing_strategy.md` `ts-p4-1`

## 1. Task Context

### Scope Boundary
- **Task ID**: `P4-1`
- **Task Name**: LangGraph State & Nodes
- **Phase**: Phase 4 工作流集成
- **Dependencies**:
  - `P2-2` Strategy Agent 已完成
  - `P3-4` Generation Agent 已完成
  - `P4-3` 仍因 `P4-2` 阻塞，本任务不处理 API 层
- **本任务目标**:
  - 把当前占位的 `app/graph/state.py` 收敛为轻量 checkpoint state 契约
  - 新增 `app/graph/nodes.py`，实现 `init_node()` / `strategy_node()` / `generate_node()` / `error_node()`
  - 节点通过 `SessionManager` 按需读取完整 session 数据，不把大对象塞进 `AgentState`
  - 节点重复执行时保持幂等，不重复生成已存在结果

### Acceptance Criteria
- [ ] `AgentState` 仅保留轻量引用和流程元数据，不保存完整 spider/strategy/generation 大对象
- [ ] `init_node/strategy_node/generate_node/error_node` 输入输出正确
- [ ] 节点通过 `SessionManager` 按需加载 session 数据
- [ ] 节点重入幂等：已有 strategy / generated notes 时重复调用不重复执行对应 agent
- [ ] `error_node` 统一写回 `SessionError` 并产出失败状态

### Test Requirements
- **Unit File**: `tests/unit/test_nodes.py`
- **Unit Scenarios**:
  1. `init_node/strategy_node/generate_node/error_node` 输入输出正确
  2. 节点通过 `SessionManager` 按需加载数据
  3. 节点重入幂等
  4. `error_node` 记录异常并产出统一失败状态
- **Test Target**: 锁定节点输入输出、按需加载、幂等性和错误出口语义

---

## 2. Architecture Context

### System Position
- `AgentState` 是 LangGraph checkpoint 的轻量快照，只携带 session 进度和外部数据引用
- `SessionManager` / `SessionDataStore` 是完整业务数据读取入口
- `ContentStrategyAgent` 和 `ContentGenerationAgent` 继续负责各阶段核心业务，节点只做装配、幂等检查和状态映射

### Tech Stack
- Language/runtime: Python 3.10+, async/await
- Primary libraries/services: `langgraph`, `aiosqlite`, `pydantic`
- Execution pattern: Worker 出队后进入 workflow node，节点以 async 函数执行
- Key behavioral constraints:
  - checkpoint 仅存轻量 state
  - 大对象通过 `SessionManager.get_session()` 按需加载
  - 节点重复执行必须兼容 checkpoint 恢复

### Constraints
- 本任务不实现 `workflow.py` 的 StateGraph 编排
- 本任务不补 checkpoint 恢复集成测试，那属于 `P4-2`
- 不改已有 strategy/generation agent 的对外契约，只在 graph 层组装

---

## 3. Technical Design

### 3.1 Module Structure

**Files to Create/Modify:**
```text
app/graph/state.py                  MODIFY
app/graph/nodes.py                  NEW
tests/unit/test_nodes.py            NEW
```

### 3.2 Class & Interface Design

**Primary State Contract**: `AgentState`

```python
class AgentState(TypedDict, total=False):
    session_id: str
    stage: Literal["init", "strategy", "generation", "completed", "failed"]
    lifecycle_state: Literal["alive", "frozen", "purged"]
    user_query: str
    spider_note_ids: list[str]
    strategy_id: str | None
    proposal_ids: list[str]
    generated_note_ids: list[str]
    quality_score: float
    used_fallback: bool
    error_code: str | None
    error_message: str | None
```

**Node Entry Points**:

```python
async def init_node(
    state: AgentState,
    *,
    session_manager: SessionManager | None = None,
) -> AgentState:
    ...

async def strategy_node(
    state: AgentState,
    *,
    session_manager: SessionManager | None = None,
    strategy_agent: ContentStrategyAgent | None = None,
) -> AgentState:
    ...

async def generate_node(
    state: AgentState,
    *,
    session_manager: SessionManager | None = None,
    generation_agent: ContentGenerationAgent | None = None,
) -> AgentState:
    ...

async def error_node(
    state: AgentState,
    *,
    session_manager: SessionManager | None = None,
) -> AgentState:
    ...
```

### 3.3 Algorithm & Logic Flow

**Core Flow**:
```text
init_node
  -> load session by session_id
  -> validate session exists
  -> project full Session into lightweight AgentState

strategy_node
  -> load session on demand
  -> if strategy already exists, return projected state unchanged
  -> else execute ContentStrategyAgent.execute(session_id)
  -> reload session and project lightweight state

generate_node
  -> load session on demand
  -> if generated notes already exist, return projected state unchanged
  -> else execute ContentGenerationAgent.execute(session_id)
  -> reload session and project lightweight state

error_node
  -> read error_code/error_message from state
  -> write SessionError(stage=current_stage_or_failed) into session
  -> force Session.stage = failed
  -> return projected failed state
```

**Idempotency Rules**:
- `strategy_node`: session 已有 `strategy_id`、`content_strategy`、`platform_preference` 时直接返回，不重复调用 StrategyAgent
- `generate_node`: session 已有 `generated_note_ids` 或 `generated_notes` 时直接返回，不重复调用 GenerationAgent
- `init_node`: 只做读取和投影，无副作用
- `error_node`: 可重复调用；重复写同类错误允许覆盖 `error`，但状态保持 `failed`

### 3.4 Error Handling Strategy

**Failure Mapping**:
```text
Session missing
  -> state.stage = "failed"
  -> error_code = "SESSION_NOT_FOUND"

Agent result failure
  -> state.stage = "failed"
  -> error_code = agent_result.error_code or fallback code

Unhandled exception
  -> caller可进入 error_node
  -> error_node 统一写回 SessionError
```

**State / Persistence Notes**:
- 节点返回值必须来自最新 session 的轻量投影，而不是手工拼大对象
- `error_node` 使用 `SessionStage.FAILED` 写回 session，但错误记录的 stage 优先取当前 state.stage 对应阶段
- `AgentState` 中不新增完整 `spider_notes` / `content_strategy` / `generated_notes`

---

## 4. Testing Strategy

### 4.1 Test Pyramid Mapping

| Level | File | Count | Focus | Mock Strategy |
|-------|------|-------|-------|---------------|
| Unit | `tests/unit/test_nodes.py` | 6-8 | 节点输入输出、按需加载、幂等、错误出口 | fake strategy/generation agent + 临时 SQLite `SessionManager` |
| Integration | `N/A` | 0 | `P4-2` 再补 checkpoint 恢复 | N/A |
| E2E | `N/A` | 0 | 不在本任务范围 | N/A |

### 4.2 Critical Test Scenarios

**Must Implement**:
1. `init_node()` 从 session 投影出轻量 state，且不携带大对象
2. `strategy_node()` 在 session 已有 strategy 时保持幂等，不调用 agent
3. `strategy_node()` 在缺少 strategy 时执行 agent，并返回更新后的引用字段
4. `generate_node()` 在 session 已有 generated notes 时保持幂等，不调用 agent
5. `generate_node()` 在缺少 notes 时执行 agent，并返回更新后的引用字段
6. `error_node()` 把错误统一写入 session 并返回 failed state

**Mock Requirements**:
- `strategy_node` / `generate_node` 使用 stub agent，避免真实调用外部依赖
- 使用临时 SQLite 文件验证节点是通过 `SessionManager` 按需读 session，而非依赖输入大对象

---

## 5. Assumptions

- `AgentState.stage` 继续使用 session 的主阶段字符串：`init/strategy/generation/completed/failed`
- `init_node()` 不负责创建 session，只负责把已有 session 装载进 workflow state
- 节点暂不引入 LangGraph `RunnableConfig`；先用普通 async node 形式，供 `P4-2` 编排时接入
