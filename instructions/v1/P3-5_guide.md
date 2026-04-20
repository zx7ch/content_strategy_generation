# Development Guide: P3-5 - Generation Prompts

> Generated: 2026-03-16
> Architect: implementation skill
> Status: Ready for development
> Source: `dev_spec.md` §9.2/§9.3, `docs/testing_strategy.md` `ts-p3-5`

## 1. Task Context

### Scope Boundary
- **Task ID**: `P3-5`
- **Task Name**: Generation Prompts
- **Phase**: Phase 3 生成引擎
- **Dependencies**:
  - `P3-4` 主链路尚未完成，因此本任务应独立完成 prompt 模板与 prompt 渲染稳定性，不依赖真正的 generation agent

### Acceptance Criteria
- [ ] proposal prompt 输出稳定可解析
- [ ] note prompt 符合 XHS 风格约束
- [ ] 不同 temperature 产生明确差异化提示

### Test Requirements
- **Test File**: `tests/unit/test_generation_agent.py`
- **Scenarios**:
  1. proposal prompt 输出稳定可解析
  2. note prompt 输出符合 XHS 风格
  3. 不同 temperature 有明显风格差异

## 2. Architecture Context

### System Position
- `app/prompts/generation.py` 是 Generation Agent 后续将直接消费的 prompt 模板模块
- 本任务只负责模板和渲染辅助，不实现生成主链路

### Constraints
- 不引入新的业务状态机
- 尽量保持模板文本不大改，只补稳定渲染入口和测试
- 单测只锁定 prompt 契约，不模拟真实 LLM 调用

## 3. Technical Design

### 3.1 Files to Modify
- `app/prompts/generation.py`
- `tests/unit/test_generation_agent.py`

### 3.2 Required Changes
1. 为 proposal prompt 提供稳定渲染入口
2. 为 note prompt 提供稳定渲染入口
3. 增加 temperature hint 选择函数，输入任意 float 时返回最近的已定义 hint
4. 若 `content_outline` 传入列表，渲染成可读文本，避免未来调用方重复处理

### 3.3 Public Interfaces
- `get_temperature_hint(temperature: float) -> str`
- `render_proposal_generation_prompts(...) -> tuple[str, str]`
- `render_note_generation_prompts(...) -> tuple[str, str]`

## 4. Testing Strategy

### 4.1 Test Layer

| Level | File | Focus |
|-------|------|-------|
| Unit | `tests/unit/test_generation_agent.py` | prompt 契约、temperature 差异、渲染稳定性 |

### 4.2 Must Implement
1. proposal prompt 渲染后保留 JSON array 契约、`n`、目标用户信息
2. note prompt 渲染后保留 XHS 风格要求、输出 schema、line break / hashtag 约束
3. `get_temperature_hint()` 对低/中/高温返回不同风格描述
4. note prompt 在 `content_outline` 为列表时可稳定渲染
