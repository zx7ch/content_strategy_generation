# Content Research Refactor Mission Ledger

| Field | Content |
| --- | --- |
| Mission | 按已批准计划补齐启动动作并重构 Content Research，同时保证所有功能和 Task 3.1 当前发布 ZIP 的能力完全一致。 |
| Current slice | Slice 2.1：10 个 P0 workflow action 由固定 Handler 与注册表分派；Command interface、生命周期 authority 与事务边界保持不变。 |
| Contract IDs | `STATE-RF-04`, `AUTH-RF-01`, `AUTH-RF-02`, `AUTH-RF-04`, `INV-RF-01`, `INV-RF-04`, `FAIL-RF-01`, `FAIL-RF-04`, `FAIL-RF-07`, `ACC-RF-02`, `ACC-RF-07` |
| Acceptance RED | 已观察：Dispatcher contract test 因 `app.content_research.commands` 不存在而收集失败。 |
| Last green proof | 2026-08-30：全量 Content Research `670 passed, 8 skipped`；prebuild 后端 650 项均有通过证据；Creator browser `7/7`；前端 `81 passed`；TypeScript 通过。 |
| Finding route | `IMPLEMENTATION_DEFECT / ACC-RF-02`：422 测试最初误用 FastAPI 默认 `detail`，已按既有 `error_code/error_message` 契约修正；package metadata 与浏览器首次执行失败均为沙箱/工作树依赖环境限制，沙箱外复验通过。 |
| Return point | Slice 2.1 Handler registry、命令分派迁移和 prebuild 部署安全检查完成；尚未提交或构建候选 ZIP。 |
| Next action | 形成干净 Slice 2.1 commit，构建内部候选 ZIP并运行 artifact gate 与 Task 3.1 frozen-runtime differential。 |
| Open risk | none；Dispatcher 只选择 Handler，所有持久化状态、revision、幂等与事务仍由 lifecycle coordinator 独占；无 systemic-risk trigger。 |
