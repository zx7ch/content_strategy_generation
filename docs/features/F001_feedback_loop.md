# F001 反馈驱动的内容策略 Spec(反馈闭环深化)

**状态**:设计中(讨论稿)
**日期**:2026-06-02
**范围**:v0.2 — 反馈闭环 & 交互优化

---

## 1. 背景与动机

v1 已发布给种子用户,v0.2 规划「反馈闭环 & 交互优化」(笔记渐进式展示、笔记「好/差」标记、选题库归档与状态、选题去重)。

我们希望把反馈闭环做得更深:不让「好/差」只是模糊地隐式调参,而是**沉淀进一份显式、可编辑、可追溯的内容策略 spec**。

理论依据:CHI'26《Social Media Feed Elicitation》(arXiv 2602.18594, Stanford)。其核心问题——**表达鸿沟 / gulf of envisioning**:用户想要定制内容,但自己描述时想不全关键决策与边界情况。论文方法 **feed elicitation interviews**(分阶段引导访谈,产出结构化自然语言 spec,生成端拆 relevance / ranking 两段)给我们的核心启发:

- 上游引导(elicitation)比下游过滤杠杆大;光把表单/模板做得更结构化**没用**,交互式追问才是有效成分。
- 副作用:访谈会让用户变得更挑剔。
- 已知短板:偏好之间的**相对优先级**问不出来——而这对内容创作(涨粉 vs 调性冲突)恰恰重要。

---

## 2. 核心概念

### 2.1 ContentStrategySpec(策略 spec)

落在现有 `brand_policy_config`(版本化,`is_active`)+ `brands.{brand_voice, target_audience, goals}` 上,不另起炉灶。一条偏好项:

```
{
  id,
  dimension,        // purpose | audience | topic_include | topic_exclude
                    // | voice_style | format | avoid
  statement,        // 自然语言,如「优先出有具体可执行步骤的干货,而非情绪鸡汤」
  importance,       // mild | preferred | essential(论文三档)
  provenance,       // onboarding | feedback_derived | usage_inferred | manual_edit
  confidence,       // 累积信号强度(写回门槛用)
  source_refs,      // 贡献过的事件 id 列表
  status            // active | proposed | retired
}
```

- statement 用**自然语言**(论文:表达力来自自然语言),dimension 负责分类供生成端取用。
- 映射到现有规则层(目前偏关键词):`hard_filter_rules{blocked_topic_types, blocked_terms}`、`brand_fit_rules{preferred_topic_types, required_terms, minimum_source_count, minimum_fit_score}`(见 `app/v2/topic_pool/scorer.py`)。
  - `topic_include/exclude` + `avoid` → 相关性/选题过滤(对应 v0.2 选题去重/筛选)
  - `voice_style/format/purpose/audience` + importance → 质量排序
  - 这正是论文 **relevance vs ranking 分离**。
- importance 是绝对重要度;相对优先级用 `tiebreak` 元偏好(见 §6.5)单独承载。

### 2.2 两个反馈回路(分开)

- **Loop A — 草稿级「好/差」(发布前,本期焦点)**:用户对生成的笔记草稿打标 → 写回 spec。
- **Loop B — 发布后绩效奖励(已有,本期不动)**:发布 → 观测指标 → reward → 喂 RL 策略,走现有 `feedback_events`(绑发布、带 reward_payload)。

两者最终都可影响 spec,但入口与触发时机完全不同。

### 2.3 DraftFeedbackEvent(新事件)

草稿「好/差」**新开事件类型**,不复用绩效 `feedback_event`(后者 `publish_record_id` 必填、语义是发布后奖励)。

```
DraftFeedbackEvent {
  candidate_id, note_id, thread_id, brand_id,   // brand_id 是跨库 join key
  rating(good/bad), reason_chip?,
  note_snapshot(title+content+tags)             // 点击当下快照,归因不受草稿后续修改影响
}
```

---

## 3. 数据流(Loop A)

存储现状:**生成层与 v2 决策层是两个存储,靠 `brand_id` 关联**。
- 生成层(SQLite `creator_threads.db`):`creator_threads`(已带 workspace_id/brand_id)→ `creator_messages` → `publish_candidates`(= 一条生成笔记草稿)。用户点「好/差」的对象即 `publish_candidate`。
- v2 层:`topic_pool_items → decision_events → publish_records → performance_snapshots → feedback_events`(RL 决策/奖励)。

```
① 捕获信号
   用户对 publish_candidate 点 好/差 (+可选 reason_chip)
   写: 新增 DraftFeedbackEvent(含 note_snapshot 快照)
        │
② 归因 (Reflection, LLM)  —— 详见第 4 节
   读: note_snapshot + 当前 active ContentStrategySpec
   出: 候选 delta(未写库)
        │
③ 冲突检测  ★关键 elicitation 时刻
   判: 新 delta 是否与现有 active 偏好矛盾?
       (用户不清楚要什么 → 前后需求会互相冲突)
       ┌─ 不冲突 → ④
       └─ 冲突   → 不自动写,弹确认:"你之前说要 X,这条又偏向 Y,以后更想要哪种?"
                   用户取舍 → 存为 tiebreak 元偏好(优先级高于普通项)→ ④
        │
④ 累积 / 门槛(累积或确认后才改)
   低风险增量(加 avoid / 强化已有) → 可直接进 ⑤
   高影响(改写/削弱 essential、动 purpose/audience) → 达阈值 或 用户确认 → ⑤
        │
⑤ 物化新版本
   派生新 brand_policy_config 版本:旧版 retire(保留历史可回滚),新版 active
   每条 delta 带 provenance + source_refs
        │
⑥ 闭环消费(让用户看见生效)
   下次生成读 active spec → 拆 relevance prompt + ranking prompt → 新 publish_candidate
```

数据流要点:
- **跨库边界靠 `brand_id`**(`creator_threads` 已有该列);前提是 brand 这层真实存在且持久。
- **note_snapshot 必存**:归因/观测可能晚于改稿,必须当下快照,否则归因对错版本。
- **冲突分支(③)是主动 elicitation 的落点**:不是报错,而是把用户自己没意识到的前后矛盾翻出来确认。

---

## 4. ② 归因(Reflection)设计

原则:**归因(信号意味着什么)用 LLM,门槛判定(要不要改 spec)用确定性代码**。两者分开,门槛逻辑可测、不靠模型发挥。

### 4.1 输入

```
{
  signal:        { rating: good|bad, reason_chip?: 选题|角度|口吻|太营销|… },
  note_snapshot: { title, content, tags },
  current_spec:  [ { item_id, dimension, statement, importance }, … ],  // 带 id 供引用
  taxonomy:      { dimensions:[...], ops:[reinforce|add|weaken] }
}
```
- 必须带 item_id,让模型对已有项 reinforce/weaken,而非反复 add。
- 只喂当前 brand 的 active spec,不喂历史反馈流(避免噪声滚大);冲突由 ③ 代码检测。
- 一次处理一条信号,强制结构化 JSON。

### 4.2 输出(候选 delta,未写库)

```
{
  abstain: bool,            // ★ 归不出明确原因必须弃权,不臆造
  deltas: [
    {
      op:            reinforce | add | weaken,
      dimension:     purpose|audience|topic_include|topic_exclude|voice_style|format|avoid,
      target_item_id: <已有项 id> | null,    // reinforce/weaken 必填
      statement:      <自然语言>,
      rule_patch:     { field:"blocked_terms|required_terms|preferred_topic_types|blocked_topic_types", value:… } | null,
      evidence:       <笔记原文片段>,         // ★ ③ 给用户确认时展示
      attribution_confidence: 0..1            // 对"这条归因"的把握
    }
  ]
}
```
- **abstain**:信号稀疏,光秃秃的「差」归不出原因就弃权(事件仍记录,只是不产 delta)。
- **evidence**:接地素材,确认弹窗用("这条笔记里『…』让我觉得你想要 X")。
- **rule_patch**:能机器化的同时给结构化补丁立即生效;纯风格类置 null,只留 statement 喂排序 prompt。
- 「好」反馈:命中现有项 → reinforce;体现 spec 尚无的优点 → add(provenance=feedback_derived)。

### 4.3 门槛判定(确定性代码,输出 auto_apply | accumulate | confirm)

```
def gate(delta, spec):
    if delta.attribution_confidence < τ_low:               return "accumulate"
    if conflicts_with_existing(delta, spec):               return "confirm"   # → ③
    if delta.dimension in {purpose, audience}:             return "confirm"   # 身份级
    if delta.op == "weaken":                               return "confirm"
    if target_item(delta, spec).importance == "essential": return "confirm"
    if delta.op in {"add","reinforce"} and additive_only(delta):
        return "auto_apply" if delta.attribution_confidence >= τ_high else "accumulate"
    return "accumulate"
```
判定维度:op(增量低/削弱高)、dimension(avoid/exclude 低,purpose/audience 高)、目标项 importance(essential 高)、冲突(高→③)、置信度。

### 4.4 去重 & 累积

跨多条笔记产出相似 delta 时**不新建重复项**,按 `(dimension, 归一 statement | target_item_id)` 归并,追加 source_refs、累加 confidence。单条「差」因此改不动 essential——必须多条同向信号或用户确认。

---

## 5. 归因 Prompt(拟放 `app/prompts/feedback.py`)

风格对齐现有 `app/prompts/*`(中文、`【】`分节、内联 JSON schema、`.format` 模板)。

### System prompt

```python
ATTRIBUTION_SYSTEM_PROMPT = """\
你是小红书内容策略的「反馈归因分析师」。
用户对一条已生成的笔记草稿打了「好」或「差」。你的任务**不是**评价笔记本身，
而是推断这条反馈意味着用户的内容偏好应该如何调整，并指向具体的策略偏好项。

【可用维度 dimension】
- purpose       账号/内容的目的与目标（身份级）
- audience      目标受众（身份级）
- topic_include 想要的选题方向
- topic_exclude 不想要的选题方向
- voice_style   口吻、语气、情绪基调
- format        结构、长度、排版、呈现形式
- avoid         需要规避的具体元素（措辞、套路、雷点）

【可用操作 op】
- reinforce 强化一条**已存在**的偏好项（必须给 target_item_id）
- add       新增一条当前 spec 里没有的偏好项
- weaken    削弱/修正一条**已存在**的偏好项（必须给 target_item_id）

【核心原则】
1. 一切结论必须由笔记原文支撑：每条 delta 都要给出触发它的原文片段 evidence。
2. **归不出明确原因时必须弃权**：若「差」只是模糊不满、无法定位到具体维度，
   返回 abstain=true、deltas=[]。宁可弃权，绝不臆造偏好。
3. 「好」反馈：找出这条笔记**命中了哪些现有偏好项**并 reinforce；
   若体现了 spec 里尚无的优点，用 add 补充。
4. 优先 reinforce 已有项（引用 target_item_id），不要重复造新项。
5. 能落到具体规则的，额外给出 rule_patch；纯风格类给不出就置 null。
6. attribution_confidence 是你对「这条归因」本身的把握，不是对笔记质量的评价。

【输出规则】
只返回合法 JSON，不要 Markdown、不要解释。schema：
{{
  "abstain": <bool>,
  "deltas": [
    {{
      "op": "reinforce|add|weaken",
      "dimension": "purpose|audience|topic_include|topic_exclude|voice_style|format|avoid",
      "target_item_id": "<已有项 id 或 null>",
      "statement": "<自然语言描述该偏好；add/重述时给>",
      "rule_patch": {{"field": "blocked_terms|required_terms|preferred_topic_types|blocked_topic_types", "value": <字符串或字符串数组>}} 或 null,
      "evidence": "<触发该判断的笔记原文片段>",
      "attribution_confidence": <0~1>
    }}
  ]
}}

【示例】

示例1（差，原因明确 → 新增规避项 + 规则补丁）
输入要点：用户点「差」，原因chip=太营销；笔记正文含「点击主页链接立即抢购，9.9包邮」。
输出：
{{"abstain": false, "deltas": [
  {{"op": "add", "dimension": "avoid",
    "statement": "规避硬广式促销话术与直接导流（如限时抢购、9.9包邮）",
    "rule_patch": {{"field": "blocked_terms", "value": ["9.9包邮", "立即抢购"]}},
    "evidence": "点击主页链接立即抢购，9.9包邮", "attribution_confidence": 0.86}}
]}}

示例2（好 → 强化已有偏好项）
输入要点：用户点「好」；现有 spec 含 item s7 = voice_style「真实第一人称体验，少说教」；
笔记以「我连续踩了三周坑才发现…」开头、全程个人口吻。
输出：
{{"abstain": false, "deltas": [
  {{"op": "reinforce", "dimension": "voice_style", "target_item_id": "s7",
    "statement": "真实第一人称体验，少说教", "rule_patch": null,
    "evidence": "我连续踩了三周坑才发现", "attribution_confidence": 0.78}}
]}}

示例3（差，原因模糊 → 弃权）
输入要点：用户点「差」，无原因chip；笔记结构、口吻、选题均无明显偏离 spec 的线索。
输出：
{{"abstain": true, "deltas": []}}
"""
```

### User prompt

```python
ATTRIBUTION_USER_PROMPT = """\
【本次反馈】
评分：{rating}
用户原因标签：{reason_chip}

【笔记草稿快照】
标题：{note_title}
正文：{note_content}
标签：{note_tags}

【当前内容策略偏好项（可被 reinforce / weaken 的已有项）】
{spec_items}

请按 system 中的 schema 输出归因结果。
"""
```

`spec_items` 由代码渲染,每行 `[item_id] (dimension, importance) statement`。

### 调用约定
- 强制 JSON(JSON mode);失败重试一次,再失败按 `abstain=true` 兜底。
- `.format` 转义:system prompt 内 schema 的 `{}` 已双写;建议 system prompt 不走 `.format`(无变量),只 format user prompt。
- 低温度(0~0.3),归因要稳定可复现。
- 只喂 active spec;冲突检测与门槛判定全在代码侧。

---

## 6. ③ 冲突检测与确认交互

冲突分支是"主动 elicitation"的落点:用户不清楚自己要什么 → 前后反馈互相矛盾,系统把它翻出来让用户确认。`gate` 中 `conflicts_with_existing` 即指向本节。

### 6.1 判定:什么算"冲突"

先结构化(代码、确定性)再语义(LLM、限定范围),只比对相关维度,不全表扫:

```
作用范围(配对维度):
  topic_include  ↔  topic_exclude / avoid
  voice_style    ↔  voice_style
  format         ↔  format
  purpose        ↔  purpose        (身份级,本就 confirm)
  audience       ↔  audience
```

- **第 1 层 结构化矛盾(confidence=1.0)**:规则层集合判定,无歧义——同一词/选题同时在 include 与 exclude(或 `required_terms` 与 `blocked_terms`);同一 topic_type 同时进 `preferred_topic_types` 与 `blocked_topic_types`;新 `avoid` 词命中 `required_terms`。
- **第 2 层 语义对立(LLM,限定候选项)**:对同维度/配对维度的现有 active 项跑轻量"矛盾判定",返回对立项 + 类型 + 分数。类型决定弹窗形态:`negation`(直接对立)/ `tradeoff`(都想要但不能同时最大化)。

```python
def detect_conflict(delta, spec):
    cands = items_in_paired_dims(delta.dimension, spec)
    if s := structural_contradiction(delta, cands):
        return Conflict("structural", against=s, score=1.0)
    if cands and (sem := llm_contradiction_check(delta, cands)).score >= θ:
        return Conflict(sem.type, against=sem.item, score=sem.score)  # negation|tradeoff
    return None
```

> `weaken` op 本身已在 gate 直接 `confirm`;③ 主要补 `add`/`reinforce` 引入的 include/exclude 矛盾与 tradeoff 张力。

### 6.2 不打断、累积后再问

- 「好/差」当下 `DraftFeedbackEvent` 立即落库;冲突解决**异步、批量**,不阻塞打标。
- **低置信冲突先静默累积**,不提醒;置信度过阈值 **或** 同向冲突反复出现才升级为待确认。
- 去重:已解决的冲突对记录结果,后续相同 delta 自动抑制(除非显著新证据)。

### 6.3 交互:两个"面" + 静音降级

- **面 1 主动卡片**:出现在 chatbot 主流程的自然决策点(发起下一批生成前 / 打开 thread 时)。**内联确认卡片,非硬性 modal**(论文:别惹烦用户),显眼但可跳过。
- **面 2 被动收件箱**:策略面板里的「策略·N 待确认」列表,所有 pending 沉淀于此,用户可主动随时处理。
- **静音降级**:高风险冲突默认主动提醒,但被跳过 3 次后从"面 1"撤下、**仅留"面 2"**——不再打断,但随时可在面板处理。即"从推给你降级为等你来"。

**素材**(每个确认都展示,帮判断):新反馈的 `evidence` 原文片段 + 现有项 `statement` + 其 provenance/重要度/年龄("onboarding·必须" vs "上周 3 条反馈形成·偏好")。

**形态 A — negation(直接对立),四选一**:保留原来的 / 改成新的 / 两个都要分场景 / 先跳过。

**形态 B — tradeoff(取舍),定优先级**:优先 A / 优先 B / 看情况(跳过)。

### 6.4 解决结果如何写回

| 用户选择 | 写回动作 |
|---|---|
| 保留原来的 | 丢弃 delta;记 `rejected` provenance,抑制再问;可降低同类未来 delta 权重 |
| 改成新的 | 旧项 `weaken`/`retire`,应用新 delta → 派生新 policy_config 版本 |
| 两个都要分场景 | 两项各加 `scope/condition`,转条件偏好(较复杂,可放二期) |
| tradeoff 定优先级 | 新增 `tiebreak` 元偏好(优先级高于普通项) |
| 跳过 | 仅丢弃本次 delta,两项都留,允许之后再问 |

### 6.5 tiebreak 元偏好:存储与触发

**存储(跟 spec 同源同版本,不另起表)**:
- 概念层(可见可编辑):`dimension="tiebreak"` 的偏好项,留在 ContentStrategySpec,可在策略面板查看/编辑/回滚。
- 机器消费层:投影进当前 active `brand_policy_config.brand_fit_rules.tiebreaks[]`(本质是排序优先级规则,归 ranking 侧):
  ```
  brand_fit_rules.tiebreaks: [
    { higher_item_id, lower_item_id, scope?,
      provenance: "user_resolved_conflict", source_refs: [事件id, 冲突请求id] }
  ]
  ```
- 为何放 policy_config 而非独立表:必须跟 spec 一起版本化、回滚、被 ranking 读到,避免"策略回滚了优先级没回滚"的不一致。

**触发(只在生成消费阶段、且某候选上两项真的反向拉扯时)**:
- ranking:候选强满足 A 但违反 B 且有 tiebreak `A>B` → 不因 B 扣分(或 A 权重压过 B),A 赢。
- relevance/选题:include 与 exclude/avoid 在同一选题打架时同理。
- 无实际冲突的候选不参与;带 `scope` 的只在特定情境生效。优先级高于普通 importance。**平时不动,冲突点才仲裁。**

### 6.6 异常兜底:待确认但用户无法确认

原则:**待确认冲突绝不阻塞产品、绝不静默改 spec、绝不丢信号。**

1. **原始信号先落库**:`DraftFeedbackEvent`(含 note_snapshot)在点击当下持久化,后续任何异常都不丢这条反馈。
2. **冲突是持久待办记录**(非内存弹窗状态),重启后仍在收件箱:
   ```
   ConflictResolutionRequest {
     id, brand_id, candidate_delta, against_item_id,
     conflict_type, evidence, status: pending|resolved|expired, created_at }
   ```
3. **未解决期间安全默认**:两项都保持原样,新 delta 不生效,生成继续用当前 active spec(最坏只是"暂时没学到")。
4. **应用原子化**:⑤ 派生新版本 + 翻 `is_active` 同一事务;中途崩溃则旧版本仍 active(幂等),请求保持 pending 待重试。resolution 仅在新版本提交成功后标 resolved。
5. **归因/语义服务挂掉**:不丢反馈——原始事件已落库,进 `needs_attribution` 队列,恢复后重处理,期间 spec 不变。

### 6.7 过期自动消解策略

按"曝光次数 + 活跃时间"组合,按严重度分档(默认值,做成可配置常量)。计时用活跃天/生成会话而非日历天("曝光"= 用户进入了能看到它的场景却跳过)。

| 档位 | 典型情形 | 过期条件 | 消解方式 |
|---|---|---|---|
| 低(mild/低置信/纯增量) | 风格小偏好 | 曝光 2 次 **或** 7 活跃天 | 直接丢弃 delta |
| 中(preferred/topic 取舍) | 选题方向冲突 | 曝光 3 次 **或** 14 活跃天 | 保守消解为「保留现有」,记 `auto_expired_keep_existing`,可回滚 |
| 高(essential/身份级 purpose·audience) | 账号定位级矛盾 | **不自动过期** | 永久留收件箱;曝光 3 次后**静音**(不再主动弹) |

- 满足"曝光 N 次"或"天数"任一即过期,兼顾活跃/不活跃用户。
- 高风险绝不自动决定;过期消解一律可审计 + 可回滚;原始 `DraftFeedbackEvent` 永不因过期删除(将来同向证据多了可重新触发)。

---

## 7. 身份与 onboarding(local-first)

- **不做登录。所有用户相关数据本地持久化保存**(决定于 2026-06-02)。
- 复用现有 `workspace → brand → brand_policy_config` 抽象。首次启动本地生成 workspace_id/user_id 存数据目录,默认建一个 brand(=「创作者账号」单位;将来可多 brand)。
- **首次进入可选「深入 onboarding」或「直接开始」,之后每次进入都直接开始**;但 onboarding 可后补,spec 始终可见可编辑。两条路统一写同一个 `brand_policy_config`:
  - 深入 onboarding = 现在跑引导访谈,产出「厚」config;
  - 直接开始 = 建最小默认 config,靠 usage_inferred + feedback_derived 慢慢长出来。

---

## 8. 现状盘点与前置依赖(落地必读)

- **持久化前置(第一道坎)**:v2 组件在 local 模式跑 **in-memory**(`app/v2/runtime.py` 的 `resolve_v2_backend` 只有 postgres / in_memory 两条路),重启即丢。`foundation/` 下曾有 `sqlite_store`,源码已删、仅剩 `.pyc` 残留。**需先补一个持久化本地后端(SQLite)**,否则 spec 不持久,写回无从谈起。
- spec 骨架已存在:`brand_policy_config`(版本化)+ `brands` 字段,但规则层偏关键词,自然语言偏好层是新增工作。
- 对话/草稿层(SQLite)已持久,但与 v2 spec 层是两个存储,靠 `brand_id` 串联。

---

## 9. 关键决策记录

| 决策 | 结论 | 理由 |
|---|---|---|
| 是否登录 | 否,local-first | 单机产品;用本地生成 id 区分与保存 |
| 用户数据持久化 | 全部本地持久化 | 反馈写回的前置 |
| 草稿「好/差」事件 | 新开 DraftFeedbackEvent | 现有 feedback_event 绑发布、语义是奖励 |
| 写回时机 | 累积 / 确认后才改,非自动 | 单样本稀疏,防过拟合 + 论文"用户变挑剔"效应 |
| 冲突处理 | 提醒用户确认,存 tiebreak 元偏好 | 用户前后需求矛盾 = 主动 elicitation 时刻 |
| 归因 vs 应用 | 归因用 LLM,门槛/写库用代码 | 可测,不靠模型发挥;对齐 relevance/ranking 分离 |

---

## 10. 待定 / 下一步

- 归因 prompt few-shot 扩充(format / purpose / audience 等维度覆盖)。
- 持久化本地后端(SQLite foundation/feedback store)落地方案。
- spec → 生成端两段 prompt(relevance / ranking)的对接。

## 参考
- 《Social Media Feed Elicitation》, Popowski et al., Stanford, CHI'26. arXiv: https://arxiv.org/abs/2602.18594
