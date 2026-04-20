# Development Guide: P3-3 - 相似度处理

> Generated: 2026-03-18
> Architect: implementation skill
> Status: Ready for development
> Source: `dev_spec.md` §5/§9.3, `docs/testing_strategy.md` `ts-p3-3`

## 1. Task Context

### Scope Boundary
- **Task ID**: `P3-3`
- **Task Name**: 相似度处理
- **Phase**: Phase 3 生成引擎
- **Dependencies**:
  - `P3-2` 已完成，slot 并行生成已可产出 `GeneratedNote`
  - `P1-3` 已完成，`RAGService.query_similar()` 可作为相似内容来源
  - `P3-4` 尚未开始，本任务只做相似度判定与 proposal 重选，不收口完整 generation workflow

### Acceptance Criteria
- [ ] `embedding_similarity > 0.6` 触发 proposal 重选
- [ ] `0.3 < similarity <= 0.6` 仅 warning，不重选
- [ ] `lexical_overlap > 0.4` 仅 warning，不重选
- [ ] high-risk proposal 不会再次被选中
- [ ] 每个 slot 最多重选 2 次

### Test Requirements
- **Test File**: `tests/unit/test_generation_agent.py`
- **Test Scenarios**:
  1. `embedding_similarity > 0.6` 触发重选
  2. `0.3 < similarity <= 0.6` 仅 warning
  3. `lexical_overlap` 超阈值仅告警不重选
  4. proposal 被标记 high-risk 后不再被重复选中
  5. 每 slot 最多重试 2 次

## 2. Architecture Context

### System Position
- `ContentGenerationAgent` 负责 note 生成后的相似度检查
- `RAGService.query_similar()` 提供 embedding 主判定所需的相似内容
- `ProposalPool` 管理可用 proposal、已使用 proposal 与 high-risk proposal

### Constraints
- embedding 相似度是唯一触发重选的硬门槛
- lexical overlap 只做 warning，不触发重选
- proposal pool 必须并发安全，避免多个 slot 反复拿到同一个高风险 proposal

## 3. Technical Design

### 3.1 Files to Modify
- `app/agents/content_generation_agent.py`
- `tests/unit/test_generation_agent.py`

### 3.2 Required Interfaces
- `ProposalPool`
- `_check_similarity(...)`
- `_handle_high_similarity(...)`
- `_select_next_proposal(...)`

### 3.3 Core Logic
1. `ProposalPool`
- 保存可用 proposal、候补 proposal、high-risk proposal id、已使用 proposal id
- `select_proposal()` 必须加锁
- `mark_high_risk()` 后，该 proposal 不可再被选中

2. `_check_similarity()`
- embedding 主判定取相似内容中的最大 `similarity`
- lexical overlap 用 `difflib.SequenceMatcher` 计算最大字符重合率
- 规则：
  - `embedding > 0.6` -> `should_retry=True`, `status="rewritten"`
  - `0.3 < embedding <= 0.6` -> `should_retry=False`, `status="warning"`
  - `lexical > 0.4` -> `should_retry=False`, `status="warning"`
  - 否则 `status="safe"`

3. `_handle_high_similarity()`
- 将当前 proposal 标为 high-risk
- 立即从池中选下一个 proposal

4. slot 级重试
- 每个 slot 最多执行 1 次首轮 + 2 次重选
- 池耗尽时直接失败，不阻塞其他 slot
- 成功 note 需写回最终 `similarity_check`

## 4. Testing Strategy

### Layer
- `unit`

### Must Implement
1. 高 embedding 相似度会触发重选，并返回新 proposal
2. 中等 embedding 相似度只给 warning
3. lexical warning 不触发重选
4. `ProposalPool` 标记 high-risk 后不会再次返回该 proposal
5. slot 重选次数达到上限后停止，不无限循环

## 5. Assumptions
- `RAGService` 在本任务中通过依赖注入或 monkeypatch 假实现，不跑真实 embedding
- `GeneratedNote.similarity_check` 继续使用 dict 结构，等后续若需要再单独模型化
- `settings.GENERATION_MAX_RETRIES=2` 解释为“最多 2 次重选”，总尝试次数为 3
