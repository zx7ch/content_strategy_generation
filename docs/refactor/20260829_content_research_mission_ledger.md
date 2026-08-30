# Content Research Refactor Mission Ledger

| Field | Content |
| --- | --- |
| Mission | 按已批准计划补齐启动动作并重构 Content Research，同时保证所有功能和 Task 3.1 当前发布 ZIP 的能力完全一致。 |
| Current slice | Slice 1：Router 的 Content Research 读取全部通过 Query interface；读取响应、历史选择和零写入语义保持不变。 |
| Contract IDs | `STATE-RF-01`, `STATE-RF-02`, `STATE-RF-04`, `AUTH-RF-01`, `AUTH-RF-05`, `FAIL-RF-03`, `FAIL-RF-04`, `FAIL-RF-07`, `ACC-RF-01`, `ACC-RF-07` |
| Acceptance RED | 已观察：`test_content_research_query_interface.py` 因 `app.content_research.query` 不存在而收集失败。 |
| Last green proof | 2026-08-30：完整 prebuild 通过——后端 `645 passed`、Creator browser `7/7`、前端 `81 passed`、TypeScript 通过；全量 Content Research 回归另有 `663 passed, 7 skipped`。 |
| Finding route | `DEBT`（测试确定性）已收口：统一等待 brand-scoped hydration 后浏览器门禁 7/7 绿色；真实极速点击竞态留给 Slice 5。Query 缓存 `IMPLEMENTATION_DEFECT` 已修复并回归。 |
| Return point | Slice 1 代码与 prebuild 部署安全检查完成；未提交、未构建候选 ZIP。 |
| Next action | 获得提交授权后形成干净 commit，构建 Slice 1 候选 ZIP并运行 Task 3.1 frozen-runtime differential。 |
| Open risk | local；presearch get 与 human-decisions get 仍经 Query interface 的兼容适配器，按计划随 Slice 2 命令家族迁移。品牌 hydration 极速点击竞态记录为 Slice 5 debt；无 systemic-risk trigger。 |
