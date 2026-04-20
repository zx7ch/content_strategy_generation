# Development Guide: P4-2 - Workflow 编排

> Generated: 2026-03-18
> Architect: implementation skill
> Status: Ready for development
> Source: `dev_spec.md` §1.5.2/§5.3.1/§6.2.2/§9.3, `docs/testing_strategy.md` `ts-p4-2`

## 1. Task Context

### Scope Boundary
- **Task ID**: `P4-2`
- **Task Name**: Workflow 编排
- **Phase**: Phase 4 工作流集成
- **Dependencies**:
  - `P4-1` LangGraph State & Nodes 已完成
- **本任务目标**:
  - 实现 `app/graph/workflow.py` 的 `create_workflow()`
  - 实现 `app/graph/edges.py` 的条件边函数 `should_expand_query()` / `should_regenerate()`
  - 补齐 `tests/unit/test_edges.py`
  - 补齐 `tests/integration/test_checkpoint_recovery.py`，验证 interrupt + checkpoint resume

### Acceptance Criteria
- [ ] `create_workflow()` 能编排 `init_node -> strategy_node -> generate_node` 主链路和统一错误出口
- [ ] `should_expand_query()` 条件判断和 `0.35/10` 边界正确
- [ ] `should_regenerate()` 条件判断和 `0.6/0.3` 边界正确，且 retry 优先级高于 warning
- [ ] workflow 可在中断后从 checkpoint 恢复
- [ ] checkpoint 保持轻量，不写入完整 strategy / note 大对象

### Test Requirements
- **Unit File**: `tests/unit/test_edges.py`
- **Integration File**: `tests/integration/test_checkpoint_recovery.py`
- **Unit Scenarios**:
  1. `should_expand_query()` 条件判断正确
  2. `should_regenerate()` 条件判断正确
  3. 边分支优先级正确
  4. 边界值 `0.35/10/0.6/0.3` 行为正确
- **Integration Scenarios**:
  1. generation 中断后恢复
  2. 恢复结果与一次性执行一致
  3. checkpoint 大小保持轻量

---

## 2. Architecture Context

### System Position
- `workflow.py` 负责把 graph nodes 编排成可恢复的 LangGraph `StateGraph`
- `edges.py` 负责封装阈值型决策逻辑，便于单测和后续路由扩展
- `SessionManager` + `AsyncSqliteSaver` 共同承担业务数据与 checkpoint 的分层持久化

### Constraints
- 不在本任务里扩展 API / Orchestrator
- 不把完整业务数据塞进 graph state
- 不要求在 `P4-2` 中重写 strategy/generation agent 内部逻辑

---

## 3. Technical Design

### 3.1 Files to Modify
```text
app/graph/workflow.py                        MODIFY
app/graph/edges.py                           MODIFY
tests/unit/test_edges.py                     NEW
tests/integration/test_checkpoint_recovery.py NEW
```

### 3.2 Required Interfaces

```python
def should_expand_query(quality_score: float, doc_count: int) -> bool:
    ...

def should_regenerate(
    embedding_similarity: float,
    lexical_overlap: float = 0.0,
) -> Literal["retry", "warn", "accept"]:
    ...

def create_workflow(
    *,
    checkpointer: Checkpointer | None = None,
    interrupt_before: list[str] | None = None,
    session_manager: SessionManager | None = None,
    strategy_agent: ContentStrategyAgent | None = None,
    generation_agent: ContentGenerationAgent | None = None,
) -> CompiledStateGraph:
    ...
```

### 3.3 Logic Flow

**Edge Rules**:
```text
should_expand_query = (quality_score < 0.35 and doc_count < 10)

should_regenerate:
  embedding_similarity > 0.6 -> "retry"
  embedding_similarity > 0.3 or lexical_overlap > 0.4 -> "warn"
  else -> "accept"
```

**Workflow Topology**:
```text
START
  -> init_node
  -> strategy_node
  -> generate_node
  -> END

failure at init/strategy/generate
  -> error_node
  -> END
```

**Checkpoint Strategy**:
- `create_workflow()` 支持注入 `AsyncSqliteSaver`
- 测试中使用 `interrupt_before=["generate_node"]` 模拟 generation 前中断
- 恢复时用同一 `thread_id` 再次 `ainvoke(None, config=...)`

### 3.4 Error Handling Strategy

- 任一 node 返回 `stage == "failed"` 时进入 `error_node`
- `error_node` 按 `P4-1` 已有逻辑统一写回 `SessionError`
- workflow 编排层不吞异常；仅通过 edge 路由失败 state

---

## 4. Testing Strategy

### 4.1 Test Pyramid Mapping

| Level | File | Count | Focus | Mock Strategy |
|-------|------|-------|-------|---------------|
| Unit | `tests/unit/test_edges.py` | 6-8 | 阈值判断、边界值、优先级 | 纯函数，无外部依赖 |
| Integration | `tests/integration/test_checkpoint_recovery.py` | 2-3 | checkpoint interrupt/resume、一致性、轻量状态 | 临时 SQLite + fake strategy/generation agent |
| E2E | `N/A` | 0 | 不在本任务范围 | N/A |

### 4.2 Critical Test Scenarios

**Must Implement**:
1. `should_expand_query()` 在 `<0.35 and <10` 时返回 `True`
2. `should_expand_query()` 在 `0.35`、`10` 边界时不触发扩展
3. `should_regenerate()` 在 `>0.6` 时优先返回 `retry`
4. `should_regenerate()` 在 `0.3 < similarity <= 0.6` 或 lexical 超阈值时返回 `warn`
5. checkpoint 中断恢复结果与一次性执行一致
6. checkpoint payload 不包含 `content_strategy` / `generated_notes` 等大对象字段

## 5. Assumptions

- `should_regenerate()` 作为 workflow 级策略纯函数存在，即使当前 generation agent 已在内部完成 slot 级重选
- integration test 可用 stub agent 写入 session，避免真实外部依赖
- checkpoint 轻量性的验证以字段缺失和 JSON 尺寸明显受控为准
