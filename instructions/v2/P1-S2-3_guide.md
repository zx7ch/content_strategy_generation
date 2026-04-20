# Development Guide: P1-S2-3 - Feedback-Driven Second-Batch Closure

> Generated: 2026-04-19
> Architect: dev-helper / implementation stage
> Status: Ready for development
> Source: `docs/v2/development_tasks.md` §2.9, §2.11.3, `instructions/v2/P1-S2-2_guide.md`, `instructions/v2/V2-P1-ACCEPT_guide.md`, `docs/testing_rules.md`

## 1. Task Context

### Scope Boundary
- Task ID: `P1-S2-3`
- Task Name: `Feedback-Driven Second-Batch Closure`
- Phase: `Phase 1 Stage 2`
- Dependencies:
  - `P1-S2-1` 已完成品牌详情 ingestion workspace
  - `P1-S2-2` 已完成 scorer refresh boundary 与 scorer-backed topic-pool contract
  - 现有 acceptance / release-gate 已能跑完整 Phase 1 闭环，但“第二批次变化”仍未成为统一 launch gate
- Task Goal:
  - 把“导入反馈后第二轮推荐发生变化”从隐含能力升级成正式 acceptance 和 release-gate 证明

### In Scope
- 扩展 acceptance API 路径，使 full-loop proof 包含：
  - 第一批 recommendation batch
  - publish record
  - performance import
  - 第二批 recommendation batch
- 对第二批次增加显式断言：至少一个 downstream update 必须发生在
  - candidate eligibility
  - topic scores
  - ranking order
  - archive state
- 更新 release-gate evidence，使第二批次效果成为 Phase 1 closure 的正式组成部分
- 如有必要，抽取 acceptance helper 以复用“batch diff / topic-pool diff”断言逻辑
- 如有必要，补一条 console walkthrough 级验证，证明前端路径也能走到 feedback 后再回到 decision 观察更新

### Out Of Scope
- 不重新设计 scorer 公式
- 不扩展新的业务页面或新 API 资源类型，除非 acceptance 证明确实缺少必要读路径
- 不做 Postgres-default runtime convergence
- 不做前端 contract/guide reconciliation 之外的大规模文案重构

### Required Deliverables
- Production:
  - 若 acceptance 证明所需数据不足，则补最小必要读路径或 helper
- Tests:
  - `tests/acceptance/test_v2_phase1_full_loop.py`
  - `tests/acceptance/test_v2_phase1_release_gate.py`
  - 如需要则更新 `tests/acceptance/test_v2_phase1_console_walkthrough.py`
  - 如需要则更新 `tests/acceptance/v2_phase1_helpers.py`
- Spec/Docs:
  - 本轮以 acceptance/release-gate 收口为主，不要求额外 spec 改写

### Acceptance Criteria
- [ ] AC1 full-loop acceptance proof 在 performance import 之后运行第二个 recommendation batch
- [ ] AC2 acceptance 明确断言至少一种 downstream change：eligibility、score、ranking 或 archive state
- [ ] AC3 release-gate 失败条件中包含“第二批次没有变化”这一类闭环不成立的情形
- [ ] AC4 acceptance artifact 记录 first batch、second batch、变化类型和变化前后关键数值
- [ ] AC5 若 console walkthrough 被纳入本任务，则前端路径也必须证明 feedback 后重新进入决策页时观察到更新，而不是只证明后端 API 可行

### Residual Obligations
- Relevant OPEN / carry-forward items:
  - `docs/v2/development_tasks.md` §2.9：Phase 1 acceptance 已明确要求“receive a later recommendation batch that reflects the recorded feedback”
  - `docs/v2/development_tasks.md` §2.11.3：需要把 second-batch effect 纳入 release gate，而不是只留在局部测试
  - `P1-S2-2` carry-forward：既然 scorer refresh 已正式存在，本任务必须消费这个能力来证明 downstream update
- Current-Phase Carry-Forward Items To Re-check:
  - 第二批次变化不能依赖手工改 store 或测试直接篡改 runtime 状态
  - acceptance 断言必须围绕用户可见结果，而不是只断言内部字段被写过
  - 若 second batch 仍无变化，必须把阻塞点作为真实 residual 留下，而不是降低断言
- Resolved By This Task:
  - full-loop acceptance 缺少 second-batch proof
  - release gate 未强制 second-batch change
- Deferred / Blocked:
  - Postgres runtime convergence -> `P1-S2-4`
  - guide/frontend canonical 名称收口 -> `P1-S2-5`

### Contract Inventory
- Upstream contracts:
  - `POST /brands/{id}/decisions/run`
  - `POST /publish-records`
  - `POST /performance/import`
  - `GET /brands/{id}/topic-pool`
- Downstream contracts:
  - acceptance artifacts
  - Phase 1 release gate conclusions
- Files/interfaces with compatibility risk:
  - `tests/acceptance/test_v2_phase1_full_loop.py`
  - `tests/acceptance/test_v2_phase1_release_gate.py`
  - `tests/acceptance/v2_phase1_helpers.py`
  - optionally `tests/acceptance/test_v2_phase1_console_walkthrough.py`

### Test Requirements
- Primary layer: `acceptance`
- Required scenarios:
  1. first batch is produced before publish/performance
  2. performance import writes feedback
  3. second batch runs after feedback
  4. at least one observable change is detected and recorded
  5. release gate fails closed if no second-batch change exists
- Test target:
  - targeted acceptance path and release-gate proof

## 2. Architecture Context

### System Position
first `decision_batch`
-> `publish_record`
-> `performance_snapshot`
-> scorer refresh on topic-pool inventory
-> second `decision_batch`
-> acceptance / release-gate diff assertion

### Technical Constraints
- second-batch proof must use shipped API surfaces rather than direct service calls where possible
- artifacts must remain audit-friendly and show before/after evidence
- tests should prefer deterministic helper-based assertions so future scorer tuning does not create brittle false negatives

## 3. Technical Design

### 3.1 Files To Modify

| Path | NEW/MODIFY | Required Change | Linked AC |
|------|------------|-----------------|-----------|
| `tests/acceptance/test_v2_phase1_full_loop.py` | MODIFY | extend the canonical full-loop proof with second-batch run and change assertions | AC1, AC2, AC4 |
| `tests/acceptance/test_v2_phase1_release_gate.py` | MODIFY | make second-batch effect an explicit release-gate criterion and artifact field | AC2, AC3, AC4 |
| `tests/acceptance/v2_phase1_helpers.py` | MODIFY | optionally add reusable diff helpers for topic-pool / batch changes | AC2, AC4 |
| `tests/acceptance/test_v2_phase1_console_walkthrough.py` | OPTIONAL MODIFY | if practical, add a frontend path that returns to decisions/topic-pool after feedback and observes updated state | AC5 |

### 3.2 Assertion Design

- Capture before-feedback state:
  - topic-pool inventory snapshot
  - first decision batch items and ordering
- Perform publish + performance import
- Capture after-feedback state:
  - topic-pool inventory snapshot
  - second decision batch items and ordering
- Compute change categories:
  - `score_changed`
  - `ranking_changed`
  - `eligibility_changed`
  - `archive_state_changed`
- Assert:
  - at least one category is true
  - artifact records which category triggered success

### 3.3 Recommended Helper Shape

If helper extraction is useful:

```python
def summarize_topic_pool(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    ...

def detect_second_batch_effect(
    *,
    topic_pool_before: dict[str, Any],
    topic_pool_after: dict[str, Any],
    first_batch: dict[str, Any],
    second_batch: dict[str, Any],
) -> dict[str, Any]:
    ...
```

Return payload should include booleans plus before/after identifiers so artifacts can reuse it directly.

### 3.4 Error Handling

- If no downstream change is detected, fail with a clear assertion explaining:
  - first batch ids
  - second batch ids
  - before/after scores
  - detected change flags
- Do not weaken the assertion to “performance import succeeded”; that is already covered by earlier tasks

## 4. Implementation Checklist

- [ ] Extend full-loop acceptance with second-batch run
- [ ] Add reusable diff helper if it reduces duplication
- [ ] Update release-gate to require second-batch change evidence
- [ ] Record artifacts with before/after batch ids and change flags
- [ ] Optionally extend console walkthrough if needed for frontend parity
- [ ] Execute targeted acceptance tests

## 5. Testing Plan

- `pytest -q tests/acceptance/test_v2_phase1_full_loop.py`
- `pytest -q tests/acceptance/test_v2_phase1_release_gate.py`
- if console walkthrough changes:
  - `pytest -q tests/acceptance/test_v2_phase1_console_walkthrough.py -k walkthrough`

## 6. Assumptions

- 当前 shipped API surface 已足够支撑 second-batch proof，主要缺口在 acceptance / release-gate 断言与 artifact，而不是业务 API 本身
- 允许“任一一种 downstream change 成立即通过”，不强制同时发生 score + ranking + eligibility 多种变化
