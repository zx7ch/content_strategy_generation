# Development Guide: P3-4 - Generation Agent

> Generated: 2026-03-18
> Architect: implementation skill
> Status: Ready for development
> Source: `dev_spec.md` §5/§8.2/§9.3, `docs/testing_strategy.md` `ts-p3-4`

## 1. Task Context

### Scope Boundary
- **Task ID**: `P3-4`
- **Task Name**: Generation Agent
- **Phase**: Phase 3 生成引擎
- **Dependencies**:
  - `P3-1` proposal 生成/评分/筛选 已完成
  - `P3-2` 并行 note 生成 已完成
  - `P3-3` 相似度处理与 proposal 重选 已完成
- **本任务目标**:
  - 把现有 generation 子能力整合成完整执行链路
  - 实现 session 驱动的 `execute(session_id)`
  - 实现 request 驱动的 `generate(request)`
  - 加入最小可用的会话级 budget guard
  - 写回 `generated_notes` 和 `similarity_report`

### Acceptance Criteria
- [ ] generation 主链路正确：proposal -> score -> select -> parallel generate -> similarity report
- [ ] 部分失败可容忍：有成功 notes 时返回 partial，不因单 slot 失败整体失败
- [ ] `similarity_report` 会写回 session
- [ ] 预算超限行为正确：超限时停止后续生成，并保留已完成结果（如有）

### Test Requirements
- **Unit File**: `tests/unit/test_generation_agent.py`
- **Integration File**: `tests/integration/test_generation_workflow.py`
- **Unit Scenarios**:
  1. proposal 耗尽 / 全部失败处理正确
  2. `BUDGET_EXCEEDED` 和 partial result 正确
  3. prompt 输出结构和 temperature hint 保持正确
- **Integration Scenarios**:
  1. generation 全流程成功
  2. 高相似重选成功
  3. 某 slot 失败后其他 slot 继续
  4. 最终写回 `similarity_report`

## 2. Architecture Context

### System Position
- `ContentGenerationAgent` 是 generation 阶段的执行内核
- `SessionManager` 提供 session 读取与写回
- `RAGService` 提供相似内容查询
- `LLMClient` 提供 proposal / note 生成

### Constraints
- 本任务不实现 workflow graph、API 层或 orchestrator 状态机
- 预算只做会话级硬预算 + 最小降级，不做复杂软预算路由
- `similarity_report` 先用 dict 写回，保持与当前 session schema 一致

## 3. Technical Design

### 3.1 Files to Modify
- `app/agents/content_generation_agent.py`
- `tests/unit/test_generation_agent.py`
- `tests/integration/test_generation_workflow.py`

### 3.2 Required Interfaces
- `execute(session_id: str) -> GenerationExecutionResult`
- `generate(request: ContentGeneratorRequest) -> ContentGenerationResult`
- `_collect_results(...) -> dict[str, Any]`
- `SessionTokenBudget`

### 3.3 Execution Flow
1. `execute(session_id)`
- 读取 session
- 校验 session 存在，且已有 `content_strategy` + `platform_preference`
- 生成 proposals（若 session 已有 proposals 可直接复用）
- 评分并选 top-k
- 应用 budget guard，必要时把并行数从 5 降到 3
- 调用 `_parallel_generate()`
- 汇总结果，生成 `similarity_report`
- 写回 session：`proposals`、`generated_notes`、`similarity_report`
- 有成功 notes 时：
  - 全成功 -> `success`
  - 部分失败或预算超限 -> `partial`
- 无成功 notes 时 -> `failed`

2. `generate(request)`
- 用 request 构造最小 `ContentStrategy` / `PlatformPreference` 回退值
- 走同一套 proposal + parallel generation 链路
- 返回首条成功 note 对应的 `ContentGenerationResult`

3. Budget Guard
- `SessionTokenBudget` 累计 proposal 和 note 生成的 token 使用
- 当前阶段允许估算 usage，并设置 `usage_estimated=true`
- 达到预算前先降级并行数（5 -> 3）
- 仍超限时标记 `budget_exceeded`
- 若已有成功 note，返回 partial；若一个都没生成出来，返回 failed + `BUDGET_EXCEEDED`

### 3.4 Result Contract
- `GenerationExecutionResult`
  - `success: bool`
  - `status: "success" | "partial" | "failed"`
  - `notes: list[GeneratedNote]`
  - `similarity_report: dict`
  - `message: str`
  - `error_code: str | None`

- `similarity_report`
  - `total_proposals`
  - `selected_count`
  - `notes_generated`
  - `notes_rewritten`
  - `failed_count`
  - `similarity_warnings`
  - `token_used`
  - `token_budget`
  - `budget_remaining`
  - `budget_degraded`
  - `budget_exceeded`
  - `usage_estimated`

## 4. Error Handling Strategy

- `SESSION_NOT_FOUND`: `execute()` 返回 failed
- `INVALID_STAGE`: session 缺少 strategy 数据时返回 failed
- `BUDGET_EXCEEDED`: 停止后续生成；若已有结果则 partial，否则 failed
- `GENERATION_PARTIAL_FAILURE`: 有部分 slot 失败但仍有 notes 时返回 partial
- 所有错误写回 session 的 `error` 字段时，`stage` 使用 `SessionStage.GENERATION`

## 5. Testing Strategy

### Unit
- 验证全失败路径
- 验证 budget exceeded + partial result
- 验证 `generate()` 不再抛 `NotImplementedError`

### Integration
- 用临时 SQLite + fake LLM + fake RAG 跑真实 session 写回
- 验证 `generated_notes` 和 `similarity_report` 持久化
- 验证高相似重选后仍可成功
- 验证部分 slot 失败时 session 仍保留成功 notes

## 6. Assumptions
- 本任务允许 `SessionTokenBudget` 使用字符长度近似估算 token
- session 表当前没有单独的 `selected_proposal_ids` 列，因此不在本任务写回该字段
- `generate(request)` 的回退 strategy/preference 仅为让公共接口可用，不追求最终产品语义完备
