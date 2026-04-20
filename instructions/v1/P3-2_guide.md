# Development Guide: P3-2 - 并行笔记生成

> Generated: 2026-03-18
> Architect: implementation skill
> Status: Ready for development
> Source: `dev_spec.md` §5/§9.3, `docs/testing_strategy.md` `ts-p3-2`

## 1. Task Context

### Scope Boundary
- **Task ID**: `P3-2`
- **Task Name**: 并行笔记生成
- **Phase**: Phase 3 生成引擎
- **Dependencies**:
  - `P3-1` 已完成，可直接消费已选 proposal
  - `P3-5` prompt 已完成，可直接消费 note prompt 渲染入口
  - `P3-3/P3-4` 尚未开始，本任务只做并行 note 生成和失败隔离，不做相似度重选与完整主链路

### Acceptance Criteria
- [ ] `_parallel_generate()` 按 5 路 slot 运行，并使用 temperature 映射
- [ ] `temperature_to_hint()` 能稳定映射到 prompt 提示
- [ ] 单 slot 失败不阻塞其他 slot

### Test Requirements
- **Test File**: `tests/unit/test_generation_agent.py`
- **Test Scenarios**:
  1. 5 路并发与 temperature 映射正确
  2. temperature hint 正确注入 prompt
  3. 单 slot 失败不阻塞其他 slot

## 2. Architecture Context

### System Position
- `ContentGenerationAgent` 负责 generation 子流程聚合
- `_parallel_generate()` 是 `P3-4` 完整流程里生成阶段的核心子步骤
- `GeneratedNote` 是并行生成的稳定输出结构

### Constraints
- 不在本任务里做 proposal 重选、similarity 检查或 budget 降级
- 并发调用采用 `asyncio.gather`，LLM 并发使用 `Semaphore`
- 单测不走真实 LLM，只用 fake/mocking 验证并行与 prompt 注入

## 3. Technical Design

### 3.1 Files to Modify
- `app/agents/content_generation_agent.py`
- `tests/unit/test_generation_agent.py`

### 3.2 Required Interfaces
- `temperature_to_hint(temperature: float) -> str`
- `_generate_single(...) -> GeneratedNote`
- `_parallel_generate(...) -> list[GeneratedNote]`

### 3.3 Core Logic
1. `temperature_to_hint()`
- 直接复用 `get_temperature_hint()`
- 保持单一映射入口，避免后续调用方重复依赖 prompt 模块细节

2. `_generate_single()`
- 接收 slot 对应 proposal、temperature、content strategy、target audience、language
- 渲染 note prompt
- 调用 `LLMClient.chat()`
- 将 JSON 输出解析为 `GeneratedNote`
- `generation_params` 至少包含 `temperature`、`proposal_id`、`slot_id`
- `similarity_check` 在本任务里先填安全默认值：`{"max_similarity": 0.0, "status": "safe"}`

3. `_parallel_generate()`
- 默认消费 `settings.PARALLEL_TEMPERATURES`
- slot 数默认 `settings.GENERATION_PARALLEL_SLOTS`
- 每个 slot 对应一个 proposal + 一个 temperature
- 使用 `asyncio.Semaphore(4)` 限制实际并发 LLM 调用
- 使用 `asyncio.gather(..., return_exceptions=True)` 或等价方式保证单 slot 失败不影响其他 slot
- 返回成功生成的 `GeneratedNote` 列表，失败 slot 直接过滤掉

## 4. Testing Strategy

### Layer
- `unit`

### Must Implement
1. `_parallel_generate()` 在 5 个 proposal 输入下触发 5 个 slot，并生成 5 条 note
2. FakeLLM 能观察到不同 temperature 对应的 hint 被注入到 prompt
3. 某个 slot 抛异常时，其余 slot 仍然返回成功结果

## 5. Assumptions
- `GeneratedNote.note_id` 在本任务里可由 agent 本地生成，不依赖外部存储
- 本任务允许 `similarity_check` 暂用安全默认值，等待 `P3-3` 接入真实判定
- `_parallel_generate()` 先按传入 proposal 顺序和 temperature 顺序一一对应，不引入 proposal pool
