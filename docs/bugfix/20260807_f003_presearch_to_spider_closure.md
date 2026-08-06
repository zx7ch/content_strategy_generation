# F003 Lite：Presearch 到 Spider 的闭环收敛

**日期**：2026-08-07  
**状态**：架构已确认；现有 canary 暂停，等待前置闭环实施  
**范围**：Lite `product_marketing` 从用户输入、结构确认、冻结查询到 Spider dispatch 的单向链路。

## 已确认的设计原则

1. Lite 是正式方案的子集：边界、状态和数据所有权必须与正式方案一致，只缩减能力与输出数量。
2. 在产生问题的边界完成闭环；不以兼容分支、fallback 或下游补救保留失效规格。
3. query 不承担二次来源审计：搜索前的结构确认必须可靠，compiler 只消费已冻结的权威结构。
4. 用户确认的关键字段只保留必要标记，不建设逐 token provenance 系统。

## 目标架构

```text
用户输入
  -> LLM 提议 SubjectStructure（非权威候选）
  -> 产品营销结构确认（core、first intent、context）
  -> 原子冻结 confirmed SubjectStructure
  -> 唯一确定性 Query Compiler
  -> RunPolicySnapshot.locked_query_plan
  -> Spider dispatch
```

任何结构候选、旧方向默认问题、页面展示文本或未冻结 brief 都不得绕过该链路直接影响 Spider query。

## 权威数据与最小字段标记

继续使用既有 `SubjectStructure`；不新增 `AnchoredTerm` 或每个字段的完整 provenance。

在 brief payload 中新增且仅新增下列字段：

```json
{
  "subject_structure_user_confirmed_fields": [
    "core_entities[0]",
    "research_intents[0]",
    "context_modifiers"
  ]
}
```

语义是“这些值在确认卡中已经由用户明确确认”，而不是“它们仅在被修改时才记录”。对产品营销，`core_entities[0]` 和 `research_intents[0]` 是必填确认项；`context_modifiers` 在确认卡中展示并确认。后续 compiler、admission 与报告均以冻结 structure 的值为准，不读取这一列表来重新解释 query。

## 旧规格删除

`识别营销话术和内容角度` 不再属于产品营销的 direction 默认问题、query facet、service 参数、测试期望或文档规格。它只可作为营销报告 `message` 轨道的分析主题，不能生成 Spider query。

新 run 的产品营销查询只有：

```text
Q1 = core + first_intent
Q2 = core + first_intent + facet
```

其中 `facet` 仅来自用户的 `custom_research_question`，否则来自已确认营销目标的受控映射。旧冻结 run 不回写，也不兼容其旧 Q2。

## 当前执行状态

原计划 `2026-08-06-f003-lite-product-marketing-query-integrity.md` 的状态：

| 任务 | 状态 | 结果 |
| --- | --- | --- |
| Task 1：作者 availability | 已完成 | `author` 投影值与 availability 一致。 |
| Task 2：产品营销 Q2 compiler | 已完成 | Q1/Q2 保留 `core + first_intent`，并处理同义重复合并。 |
| Task 3：冻结营销目标接线 | 已完成 | API 直接验证 frozen plan 的默认与自定义 facet。 |
| Task 4：真实 canary | 未验收 | run `run_fabdc32b145c4a6b81dd3a8ec35d947d` 在结构确认写入时被 SQLite 锁阻断；未启动 Spider，未产生 snapshot、packet、漏斗或报告。 |

Task 4 不能通过重试来完成；它必须等待以下前置闭环。

## 缺口及其正确边界

### 1. 产品属性的结构确认边界

现有 LLM 候选可将 `夏季凉感 T恤` 解析为 `core=凉感 T恤`、`intent=产品特性了解`，或在澄清后给出 `intent=产品营销`。现有 parser 仅校验 shape、原文 core mention 与少数重叠情形，不能保证首要意图是用户要研究的属性。

修复边界不是 compiler：产品营销的 LLM 输出一律是候选；在创建正式 plan 前，用户必须确认 core、first intent、context。确认成功前不允许 dispatch。

### 2. 结构确认的原子写入边界

`confirm_subject_structure()` 目前先经同步 `SQLiteContentResearchStore.save_brief()` 写入，再写 checkpoint 并调用异步 runtime。这与 `aiosqlite` / `WorkflowRunManager` 的写路径并存；真实 canary 在该同步写入得到 `sqlite3.OperationalError: database is locked`。

修复边界是一个 Content Research 统一异步写入协调者：确认 brief、subject-structure checkpoint、`resume_subject_clarification` 与 `mark_presearch_ready` 必须作为一个原子状态转换提交。锁冲突应返回明确的可恢复冲突状态，不得转为 HTTP 500 或留下半完成 brief。确认链路中旧的同步 store 写入必须删除，而非与新路径并存。

### 3. 冻结前的 dispatch 守卫

dispatch 必须验证：

- `subject_structure_state == "confirmed"`；
- `subject_structure_user_confirmed_fields` 包含产品营销的三个确认字段；
- 已存在有效 `locked_query_plan`；
- 每个产品营销 primary `QueryGroup` 都包含 frozen core 和 frozen first intent。

任一条件不满足时，拒绝 dispatch，不调用 Spider，不创建“补救 query”。

## 调整后的后续顺序

1. **重新定义并测试产品营销结构确认契约。** 补充 prompt 的明确示例与 parser/服务契约测试；产品营销没有显式结构确认时不得创建正式 plan。
2. **删除旧营销话术规格。** 从方向注册、service、测试与文档中删除 `识别营销话术和内容角度`，不保留产品营销兼容行为。
3. **统一结构确认写入。** 以单一异步事务/协调者替换确认链路的同步 `save_brief` 与后续分离写入；实现可恢复锁冲突语义和竞争测试。
4. **加入冻结前 dispatch 守卫。** 只允许 confirmed、用户确认字段齐全且 query plan 合法的产品营销 run 进入 Spider。
5. **重新执行 Task 4 真实 canary。** 同一输入 `夏季凉感 T恤`，以真实 packet 验证冻结 query、作者可用性、漏斗及 Lite report；证据不足不额外搜索。

## 完成标准

新 canary 必须证明以下顺序完整成立：用户确认的 `T恤 / 凉感 / 夏季` 原子冻结，随后 snapshot 中只有 `T恤 凉感` 与 `T恤 凉感 上身感受` 两个产品营销主查询，之后才允许 Spider 调用。
