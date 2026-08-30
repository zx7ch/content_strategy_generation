# Content Research Refactor Mission Ledger

| Field | Content |
| --- | --- |
| Mission | 按已批准计划补齐启动动作并重构 Content Research，同时保证所有功能和 Task 3.1 当前发布 ZIP 的能力完全一致。 |
| Current slice | Slice 2：预检索、Brief、Scope、决策、重试和结束操作全部通过 Command interface；生命周期 authority 与事务边界保持不变。 |
| Contract IDs | `STATE-RF-04`, `AUTH-RF-01`, `AUTH-RF-02`, `AUTH-RF-04`, `INV-RF-01`, `INV-RF-04`, `FAIL-RF-01`, `FAIL-RF-04`, `FAIL-RF-07`, `ACC-RF-02`, `ACC-RF-07` |
| Acceptance RED | 已观察：`test_content_research_command_interface.py` 因 `app.content_research.command` 不存在而收集失败。 |
| Last green proof | 2026-08-30：完整 prebuild 通过——后端 `647 passed`、Creator browser `7/7`、前端 `81 passed`、TypeScript 通过；全量 Content Research 回归 `665 passed, 7 skipped`。 |
| Finding route | `IMPLEMENTATION_DEFECT / ACC-RF-06`：旧 contract test 仍导入 service 私有 capability helper；已将纯冻结规则迁到公开 contracts interface，原始收集失败与回归均恢复绿色。 |
| Return point | Slice 2 Command seam、命令家族迁移和 prebuild 部署安全检查完成；尚未提交或构建候选 ZIP。 |
| Next action | 形成干净 Slice 2 commit，构建内部候选 ZIP并运行 artifact gate 与 Task 3.1 frozen-runtime differential。 |
| Open risk | none；Router mutation 仅通过 Command interface，旧 service 仅保留兼容委托，所有 action 分支只有 Command module 一个可达实现；无 systemic-risk trigger。 |
