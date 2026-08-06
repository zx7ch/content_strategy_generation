# F003 产品营销：作者可用性元数据与 Q2 查询偏离

**日期**：2026-08-06  
**状态**：已确认修复规则，待实施  
**影响范围**：产品营销方向的 evidence packet、Lite Q2 查询与正式调研准入

## 发现依据

真实 run `run_770af525b4a84dbe87df3128dccc0532` 共保存 30 个真实笔记 packet、21 位不同作者。

| 观察项 | 实际结果 |
| --- | --- |
| `field_projection.author` | 已保存作者名，例如 `AllyWoo_`、`野生俊子` |
| `field_availability.author` | 30 个 packet 均未声明为 `present` |
| 冻结 Q1 | `T恤 凉感` |
| 冻结 Q2 | `T恤 识别营销话术和内容角度` |
| 准入漏斗 | 30 selected → 0 quote-relevant → 0 eligible → 0 admitted |

## Bug 1：作者值与可用性元数据不一致

`XiaohongshuSourceNormalizer.normalize_note_detail()` 将 `post.author` 写入 detail payload，但计算 `field_availability` 的输入遗漏 `author`。因此作者可以用于后续投影，却被元数据表示为未声明。

这不是本 run 的直接准入阻断：产品营销的 blocking fields 当前不含 author。但它破坏 packet 合约，也会误导后续依赖字段可用性的数据质量判断。

## Bug 2：Q2 以泛方向文案替代事实锚点

subject structure 正确解析为：核心对象 `T恤`，首要意图 `凉感`。但当用户未填写自定义焦点时，服务层把产品营销方向的旧静态问题“识别营销话术和内容角度”作为 Q2 facet；query compiler 只将其与核心对象拼接，因此丢失了 `凉感`。

这会召回泛 T 恤带货和内容方法论，而不是可直接支持“凉感 T 恤”的证据。硬准入随后正确拒绝这些引用，但搜索预算已被消耗。

## 已确认修复规则

正式产品营销查询必须始终保留一个核心对象与一个首要意图：

```text
Q1 = core + first_intent
Q2 = core + first_intent + facet
```

`facet` 的优先级是：用户在 Brief 确认时填写的可选焦点；否则为用户已选营销目标的受控默认 facet。Lite 不使用 LLM、embedding 或旧方向默认问题来产生 Q2。

示例：

```text
core=T恤, first_intent=凉感, goal=content_seeding
无自定义焦点：Q1=T恤 凉感；Q2=T恤 凉感 上身感受
自定义焦点=通勤：Q1=T恤 凉感；Q2=T恤 凉感 通勤
```
