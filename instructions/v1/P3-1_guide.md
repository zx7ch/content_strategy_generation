# Development Guide: P3-1 - 提案生成与管理

> Generated: 2026-03-18
> Architect: implementation skill
> Status: Ready for development
> Source: `dev_spec.md` §5/§9.3, `docs/testing_strategy.md` `ts-p3-1`

## 1. Task Context

### Scope Boundary
- **Task ID**: `P3-1`
- **Task Name**: 提案生成与管理
- **Phase**: Phase 3 生成引擎
- **Dependencies**:
  - `P2-1` 已完成，`EngagementAnalyzer.score_proposals()` 可直接复用
  - `P3-5` prompt 已完成，可直接消费 proposal prompt 渲染入口
  - `P3-2/P3-3/P3-4` 仍未开始，本任务只做 proposal 子流程，不实现完整 generation 主链路

### Acceptance Criteria
- [ ] `generate_proposals()` 能稳定生成 10 条 proposal，并映射到内部 `Proposal` 结构
- [ ] `score_proposals()` 的排序结果与平台偏好评分逻辑一致
- [ ] `select_top_k()` 能按分数选出 top-k proposal

### Test Requirements
- **Test File**: `tests/unit/test_generation_agent.py`
- **Test Scenarios**:
  1. proposal 生成 10 条且结构完整
  2. top-k 选择正确
  3. proposal 评分排序正确

## 2. Architecture Context

### System Position
- `app/agents/content_generation_agent.py` 是 generation 子流程聚合点
- `app/prompts/generation.py` 提供 proposal prompt 渲染
- `app/services/engagement_analyzer.py` 提供平台偏好导向评分
- `Proposal` 是后续并行生成、相似度筛选和持久化的统一内部结构

### Constraints
- 不在本任务里实现 note generation、并发、多轮重试或 similarity
- 不改 `Proposal` 持久化结构，统一做 LLM 输出到 `Proposal` 的归一化
- 单测只验证 proposal 生成/评分/筛选契约，不接真实 LLM

## 3. Technical Design

### 3.1 Files to Modify
- `app/agents/content_generation_agent.py`
- `tests/unit/test_generation_agent.py`

### 3.2 Required Interfaces
- `generate_proposals(...)`
- `score_proposals(...)`
- `select_top_k(...)`

### 3.3 Core Logic
1. `generate_proposals()`
- 调用 proposal prompt 渲染器
- 通过 `LLMClient.chat()` 获取 JSON array
- 验证数量必须为 `n`
- 将每个 item 归一化为内部 `Proposal`
- 必须兼容 `content_outline` 为列表或字符串

2. `Proposal` 归一化映射
- `proposal_id` -> 直接使用；缺失时按索引补默认值
- `angle` -> 直接映射
- `title_concept` -> 映射到 `hook`
- `content_outline` -> 映射到 `outline`
- `target_emotion` -> 直接映射
- `content_pillars` -> 优先取策略里的 `content_pillars`
- `suggested_tags` -> 优先取 LLM 返回值；缺失时可回退到部分 `content_pillars`
- `expected_engagement` -> 作为初始 `score`

3. `score_proposals()`
- 直接复用 `EngagementAnalyzer.score_proposals()`
- 返回按分数降序排序后的 proposal 列表

4. `select_top_k()`
- 从已评分 proposal 中按 `score desc` 选前 `k`
- `k` 默认使用 `settings.NUM_FINAL_NOTES`
- 只负责筛选，不改 proposal 内容

## 4. Testing Strategy

### Layer
- `unit`

### Must Implement
1. fake LLM 返回 10 条 proposal 时，`generate_proposals()` 输出 10 个 `Proposal`
2. 每条 proposal 至少具备 `proposal_id/angle/hook/outline/target_emotion`
3. `score_proposals()` 对高匹配 proposal 排名更靠前
4. `select_top_k()` 只返回分数最高的前 `k` 条

## 5. Assumptions
- proposal prompt 输出仍以 JSON array 为唯一合法格式
- 本任务允许 `score` 先承载 LLM 的 `expected_engagement` 初值，之后再被平台偏好评分覆盖
- `ContentGenerationAgent.generate()` 仍保持未实现，等后续 `P3-4` 再整合完整流程
