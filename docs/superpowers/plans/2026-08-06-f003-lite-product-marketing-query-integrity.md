# F003 Lite 产品营销查询完整性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复产品营销笔记作者可用性元数据，并保证 Q2 永远同时研究确认的核心对象和首要意图。

**Architecture:** 来源 normalizer 以同一作者值同时生成 payload 与 availability。query compiler 接收冻结的营销目标；仅对 `product_marketing` 将 Q2 编译为 `core + first_intent + facet`，其中 facet 优先使用用户可选焦点，否则使用受控营销目标映射。不会调用 LLM、embedding 或旧方向默认问题。

**Tech Stack:** Python 3.11、FastAPI、Pydantic、SQLite、pytest / pytest-asyncio。

## Global Constraints

- 仅修改 Lite `product_marketing` 的 Q2；其他方向、query cap、排序和 fallback 行为保持不变。
- `core` 与 `first_intent` 来自已确认的 subject structure，Q1/Q2 都不得删除它们。
- 当前唯一有效营销目标 `content_seeding` 的默认 facet 是 `上身感受`。
- `custom_research_question` 可覆盖 facet，但绝不能覆盖 `core` 或 `first_intent`。
- 不新增 Spider 请求、LLM、embedding、营销目标 UI 或自动重跑历史 run。
- packet 不得编造作者身份：仅传播 provider 返回的 `author`；空值必须声明为 `missing`。
- 公共 Trace 不得增加原始 query、作者或 packet 内容。

## Execution Closure — 2026-08-07

Task 1–3 已实施并验证；Task 4 的真实 canary 未验收。真实 run
`run_fabdc32b145c4a6b81dd3a8ec35d947d` 在主体结构确认阶段发生 SQLite 锁冲突，未启动
Spider。该状态不是“证据不足”，不得通过重试搜索规避。

本计划的剩余 Task 4 被新的前置闭环取代：产品营销结构必须由用户确认后原子冻结，随后由唯一 compiler
产生唯一 snapshot，最后才能 dispatch。废弃的产品营销默认 query 规格必须删除而非兼容。
完整的架构、删除范围、当前执行状态和恢复顺序见
`docs/bugfix/20260807_f003_presearch_to_spider_closure.md`。

---

### Task 1: 修复笔记详情作者可用性元数据（已完成）

**Files:**
- Modify: `app/content_research/sources/xiaohongshu/normalizer.py:102-140`
- Modify: `tests/unit/test_content_research_source_payloads.py`

**Interfaces:**
- Produces: `normalize_note_detail(post, required_fields)` 中 `author` 与 `field_availability["author"]` 一致。
- Consumes: `XHSPost.author`，不新增 author ID 或昵称推断。

- [ ] **Step 1: 写入失败测试**

```python
def test_xhs_note_detail_declares_the_same_author_it_projects():
    payload = XiaohongshuSourceNormalizer().normalize_note_detail(
        _post(), required_fields=("title", "content_text", "author"),
    )

    assert payload["author"] == "户外作者"
    assert payload["field_availability"]["author"] == "present"
```

补充空作者用例，构造 `post.model_copy(update={"author": ""})`，断言 `author == ""` 与 `field_availability["author"] == "missing"`。

- [ ] **Step 2: 验证 RED**

Run: `pytest -q tests/unit/test_content_research_source_payloads.py -k note_detail`

Expected: FAIL；当前 detail payload 虽含作者，但 availability 未计算 `author`。

- [ ] **Step 3: 最小实现**

在 `normalize_note_detail()` 的 `availability_payload` 中加入：

```python
"author": post.author,
```

保留已有返回字段 `"author": post.author`；不修改来源 schema version、Spider client 或 author identity 算法。

- [ ] **Step 4: 验证 GREEN**

Run: `pytest -q tests/unit/test_content_research_source_payloads.py`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add app/content_research/sources/xiaohongshu/normalizer.py tests/unit/test_content_research_source_payloads.py
git commit -m "fix(content-research): align XHS author availability"
```

### Task 2: 为产品营销确定性编译带事实锚点的 Q2（已完成）

**Files:**
- Modify: `app/content_research/workflow/query_planner.py:39-112`
- Modify: `tests/unit/test_content_research_query_planner.py`

**Interfaces:**
- Produces: `resolve_product_marketing_facet(*, primary_marketing_goal: str, custom_focus: str) -> str`。
- Extends: `compile_structured_query_plan(..., primary_marketing_goal: str = "")`。
- Contract: 对 `product_marketing`，Q2 是 `[core, first_intent, facet]`；对其他方向保持原有 `[core, focus]` 行为。

- [ ] **Step 1: 写入默认 facet 的失败测试**

```python
def test_product_marketing_q2_keeps_intent_and_uses_goal_facet():
    plan = compile_structured_query_plan(
        direction_id="product_marketing",
        subject_structure=_structure(),
        primary_marketing_goal="content_seeding",
        run_as_of_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )

    assert [group.query_group.query for group in plan.primary_groups] == [
        "防晒服饰 穿搭",
        "防晒服饰 穿搭 上身感受",
    ]
    assert plan.primary_groups[1].role == "goal_facet"
```

- [ ] **Step 2: 写入自定义焦点与隔离性失败测试**

```python
def test_product_marketing_custom_focus_replaces_only_the_facet():
    plan = compile_structured_query_plan(
        direction_id="product_marketing",
        subject_structure=_structure(),
        explicit_focus="通勤",
        primary_marketing_goal="content_seeding",
        run_as_of_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )
    assert plan.primary_groups[1].query_group.query == "防晒服饰 穿搭 通勤"
    assert plan.primary_groups[1].role == "user_focus"


def test_non_product_marketing_query_compilation_is_unchanged():
    plan = compile_structured_query_plan(
        direction_id="content_performance",
        subject_structure=_structure(),
        explicit_focus="通勤",
        second_facet="使用场景",
        run_as_of_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )
    assert plan.primary_groups[1].query_group.query == "防晒服饰 通勤"
```

- [ ] **Step 3: 验证 RED**

Run: `pytest -q tests/unit/test_content_research_query_planner.py`

Expected: FAIL；当前 compiler 不接收营销目标，且 Q2 缺少 `first_intent`。

- [ ] **Step 4: 最小实现**

在 query planner 定义受控常量并实现 resolver：

```python
PRODUCT_MARKETING_GOAL_FACETS = {"content_seeding": "上身感受"}

def resolve_product_marketing_facet(*, primary_marketing_goal: str, custom_focus: str) -> str:
    focus = _display_term(custom_focus)
    if focus:
        return focus
    try:
        return PRODUCT_MARKETING_GOAL_FACETS[primary_marketing_goal]
    except KeyError as exc:
        raise ValueError("unknown product-marketing goal") from exc
```

在 `direction_id == "product_marketing"` 分支中，使用 `(core, primary_intent, facet)` 创建 Q2；角色分别为 `user_focus` 或 `goal_facet`。不读取 `second_facet`。保留现有非产品营销逻辑与 `_append_or_merge()`。

- [ ] **Step 5: 验证 GREEN**

Run: `pytest -q tests/unit/test_content_research_query_planner.py`

Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add app/content_research/workflow/query_planner.py tests/unit/test_content_research_query_planner.py
git commit -m "fix(content-research): anchor product marketing Q2 intent"
```

### Task 3: 将冻结营销目标传入正式计划，并验证 API 合约（已完成）

**Files:**
- Modify: `app/content_research/service.py:1114-1132`
- Modify: `tests/e2e/test_content_research_brief_confirm_api.py`

**Interfaces:**
- Consumes: `BriefConfirmation.primary_marketing_goal`，该字段已在 Brief 确认 API 校验并冻结。
- Produces: `effective_policy.locked_query_plan.directions.product_marketing.query_groups` 使用 Task 2 的输出。

- [ ] **Step 1: 写入 Brief 确认 API 的失败测试**

在现有产品营销确认用例中，提交：

```json
{
  "primary_marketing_goal": "content_seeding",
  "custom_research_question": ""
}
```

断言 product-marketing primary queries 恰为：

```python
assert [item["normalized_query"] for item in primary_groups] == [
    "T恤 凉感",
    "T恤 凉感 上身感受",
]
```

新增自定义焦点 `通勤` 用例，断言 Q2 是 `T恤 凉感 通勤`。测试使用 API 返回的冻结 plan，不调用 Spider。

- [ ] **Step 2: 验证 RED**

Run: `pytest -q tests/e2e/test_content_research_brief_confirm_api.py -k 'marketing_goal or locked_query_plan'`

Expected: FAIL；service 当前将 `direction.default_questions[1]` 传给 compiler。

- [ ] **Step 3: 最小实现**

在 `_build_and_persist_confirmed_plan()` 调用 compiler 时传入：

```python
primary_marketing_goal=(
    confirmation.primary_marketing_goal
    if direction.id == "product_marketing"
    else ""
)
```

移除该调用中针对产品营销的 `direction.default_questions[1]` 传递；非产品营销方向继续使用当前 `second_facet` 逻辑。

- [ ] **Step 4: 验证 GREEN**

Run: `pytest -q tests/e2e/test_content_research_brief_confirm_api.py tests/unit/test_content_research_query_planner.py`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add app/content_research/service.py tests/e2e/test_content_research_brief_confirm_api.py
git commit -m "fix(content-research): freeze goal-aware marketing queries"
```

### Task 4: 使用真实笔记完成端到端 canary 与漏斗复核（已阻断，待前置闭环）

**Files:**
- No production code changes.
- Verify: Creator UI、`/content-research/workflows/{run_id}/lite-report`、SQLite persisted packets/checkpoints。

**Interfaces:**
- Consumes: 已登录的本地 XHS Spider、Task 1-3 的冻结 plan。
- Produces: 一个新的真实 run ID 与仅由该 run persisted packets 计算的漏斗结论。

- [ ] **Step 1: 通过 Creator 发起真实调研**

使用输入 `夏季凉感 T恤`；确认结构为 `核心对象=T恤`、`首要意图=凉感`、`场景=夏季`；选择 `产品营销` 与 `内容种草`，不填写自定义焦点。记录 run ID；不得导入或构造虚假笔记。

- [ ] **Step 2: 验证冻结查询而非仅查看页面文案**

查询该 run 的 `content_research_run_policy_snapshots.effective_policy_json`，断言 product-marketing 主查询为：

```text
T恤 凉感
T恤 凉感 上身感受
```

同时确认产品营销 primary query 仅为上述两个冻结查询。

- [ ] **Step 3: 验证真实 packet 的作者一致性**

对该 run 的 `content_research_directional_evidence_packets` 统计：

```sql
SELECT
  json_extract(payload_json, '$.field_projection.author') AS author,
  json_extract(payload_json, '$.field_availability.author') AS author_availability
FROM content_research_directional_evidence_packets
WHERE workflow_run_id = :run_id;
```

每条记录必须满足：非空 `author` 对应 `present`，空 `author` 对应 `missing`。

- [ ] **Step 4: 复核漏斗与报告**

从 `admission` checkpoint 读取 `computed_metrics` 的 selected、quote-relevant、eligible、independent-author 计数，并读取 Lite report。验收条件：

- 若有营销结论，三个轨道均显示其真实支持数与引用；
- 若无营销结论，报告显示精简的逐轨证据不足原因；
- 不得将作者 availability 漏报或 Q2 偏离作为原因；
- 不因证据不足启动额外 Spider 搜索。

- [ ] **Step 5: Commit canary evidence only if it is a durable, safe test artifact**

不提交真实笔记正文、作者名、URL、cookie、原始 Spider 响应或数据库。若新增自动化测试，只提交脱敏的断言和 fixture；否则在任务报告中记录 run ID、冻结查询、漏斗计数和报告状态。

## Plan Self-Review

- Spec coverage: Task 1 覆盖作者值与 availability；Task 2-3 覆盖确定性 facet、服务传递与 API 冻结 plan；Task 4 覆盖真实 packet canary 与报告诊断。
- Scope: 未增加 provider、LLM、embedding、UI 或重跑策略。
- Type consistency: `primary_marketing_goal` 已存在于 `BriefConfirmation`；Task 2 只扩展 compiler 可选参数，Task 3 负责传入。
- Placeholder scan: 无 TBD/TODO 或未定义的实现步骤。

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-06-f003-lite-product-marketing-query-integrity.md`.

Two execution options:

1. **Subagent-Driven（推荐）**：每个任务一个新 subagent，逐项审查，适合并行检查与快速迭代。
2. **Inline Execution**：在当前会话按任务执行，并在每个任务后停下来复核。
