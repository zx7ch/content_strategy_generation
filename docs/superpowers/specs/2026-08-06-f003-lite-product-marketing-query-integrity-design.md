# F003 Lite 产品营销查询完整性设计

## 状态

部分被替代。本文的作者字段合约与对应验收继续有效；Brief 中的
`primary_marketing_goal`、`custom_research_question`、固定“上身感受” facet
以及两组主查询编译规则，已由
[`2026-08-15-lite-research-scope-contract-design.md`](./2026-08-15-lite-research-scope-contract-design.md)
中的用户确认查询组合与 Scope 执行权威替代。新 run 不得继续把这些旧字段
解释为可执行检索词。

## 目标

保证产品营销正式调研的所有主查询都研究用户确认的核心对象与首要意图；修复作者值与 `field_availability` 的合约不一致，使后续结论门槛和数据质量诊断可信。

## 非目标

- 不增加 Spider 调用次数、provider 能力或新的营销目标 UI。
- 不改动“3 篇笔记 / 2 位独立作者”的营销结论门槛。
- 不使用 LLM、embedding 或语义检索生成 Lite 查询 facet。
- 不自动重跑历史 run；历史 packet 只用于诊断和 replay。

## 已冻结输入与职责

| 输入 | 产生时间 | 职责 |
| --- | --- | --- |
| `core` | presearch 后的主体确认 | 每条主查询的对象锚点 |
| `first_intent` | presearch 后的主体确认 | 每条主查询的事实意图锚点 |
| `primary_marketing_goal` | Brief 确认卡片，必选 | 选择受控默认 facet |
| `custom_research_question` | Brief 确认卡片，可选 | 覆盖默认 facet，不覆盖 core/intent |

当前 Lite 只有 `content_seeding` 目标。它的默认 facet 固定为 `上身感受`，该映射属于版本化的领域常量，而非模型输出。

## 查询编译

为 `product_marketing` 方向增加独立、确定性的 facet 解析：

```python
PRODUCT_MARKETING_GOAL_FACETS = {
    "content_seeding": "上身感受",
}

def resolve_product_marketing_facet(*, primary_marketing_goal: str, custom_focus: str) -> str:
    return normalize(custom_focus) or PRODUCT_MARKETING_GOAL_FACETS[primary_marketing_goal]
```

服务层将 `primary_marketing_goal` 传入 query compiler；compiler 生成：

```text
Q1: [core, first_intent]                 role=core_intent
Q2: [core, first_intent, resolved_facet] role=goal_facet 或 user_focus
```

若 facet 与 `first_intent` 规范化后相同，现有 query-group 合并逻辑将只保留一条主查询并合并角色。Q2 永远不得仅由 `core + facet` 组成，也不得读取 `ResearchDirectionDefinition.default_questions`。

## 作者字段合约

`normalize_note_detail()` 必须以同一 `post.author` 同时构造：

```python
availability_payload = {
    # existing fields...
    "author": post.author,
}
```

packet 的 `field_projection.author` 非空时，`field_availability.author` 必须为 `present`；为空时必须为 `missing`。不新增 `author_id` 的猜测或由昵称反推身份。

## 失败与可观测性

- 若缺少或未知营销目标，Brief 确认保持现有边界校验，不能创建正式计划。
- 不向公共 trace 暴露 query、作者、facet 或原始 packet；冻结 policy 仍保存完整 query plan，供内部审计与 replay 使用。
- 正式 run 的漏斗诊断继续以安全计数呈现：selected、quote-relevant、eligible、admitted；不要将 `field_availability.author` 漏报解释为作者缺失。

## 验收标准

1. detail payload 中有作者时，packet projection 与 availability 都声明作者存在；无作者时两者都为空/`missing`。
2. `content_seeding`、`T恤`、`凉感`、无自定义焦点时，冻结主查询依次为 `T恤 凉感` 与 `T恤 凉感 上身感受`。
3. 同一输入带自定义焦点 `通勤` 时，Q2 为 `T恤 凉感 通勤`。
4. 任意产品营销 Q2 都包含确认的 core 与 first intent；已废弃的默认营销 query 不参与冻结 query plan。
5. 现有 query cap、排序、fallback 与非产品营销方向行为不变。
6. 用真实 persisted packet 的 canary 证明：作者 availability 与投影一致；若仍无合格结论，报告准确归因为 quote-relevance 或样本门槛，而非作者缺失或 query 偏离。
