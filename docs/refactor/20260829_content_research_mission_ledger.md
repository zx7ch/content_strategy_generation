# Content Research Refactor Mission Ledger

| Field | Content |
| --- | --- |
| Mission | 按已批准计划补齐启动动作并重构 Content Research，同时保证所有功能和 Task 3.1 当前发布 ZIP 的能力完全一致。 |
| Current slice | 发布收口：功能/异常矩阵已补齐并固化为机器可校验 manifest；完整 prebuild gate 已通过，打包仍冻结。 |
| Contract IDs | `STATE-SQL-06`, `STATE-SQL-10`, `STATE-SQL-11`, `AUTH-SQL-12`, `AUTH-SQL-13`, `INV-SQL-08`, `INV-SQL-11`, `INV-SQL-12`, `FAIL-SQL-08`, `FAIL-SQL-15`, `FAIL-SQL-16`, `ACC-SQL-11`, `ACC-SQL-12`, `ACC-SQL-13`, `ACC-SQL-14`, `ACC-SQL-15`, `ACC-SQL-16`，以及全部既有回归。 |
| Acceptance RED | 对每个用户可见功能建立 happy path、异常操作、恢复/并发边界和 reload/history 覆盖；缺少对应自动化证据的功能不得进入 Release Gate 完成态。 |
| Last green proof | 完整 prebuild gate：backend/API/integration/acceptance `721 passed, 1 skipped`；隔离真实 Chromium `22/22 passed`；frontend `87/87 passed`；`tsc --noEmit` passed。浏览器状态证据：`returncode=0`、`timed_out=false`。 |
| Finding route | R7 live logs found an `IMPLEMENTATION_DEFECT`：analysis failure first committed canonical `recovery_required`, then the transitional workflow runtime attempted the same recovery transition and raised `waiting_user` conflict。A red integration seam reproduced the exact duplicate write；the second authority was deleted and recovery regression is `14 passed`。Full-suite failures were then traced to stale `app.state` dependencies surviving `app.main` shutdown plus API unit tests depending on import order；lifespan now removes installed runtime dependencies and injected unit APIs explicitly suppress production startup。Discarded endpoint/in-memory/env/extension request-shape tests were removed or rewritten to current contracts。The consolidated release gate also exposed three infrastructure gaps：package metadata test attempted online build isolation，现使用 `--no-index --no-build-isolation`；Creator helper把 thread-history 200 错当作品牌 hydration，现以品牌下拉可用且有值为权威并增加 history 503 浏览器回归；SQLite 锁验收的 12 秒小于冷启动导入时间，现保留“不得变为 healthy”的业务判定并提供 30 秒启动预算。 |
| Return point | 取消会原子清理 active run 并 fence 晚到 worker；三方向 Scope/admission 覆盖修复；四种可发布报告状态使用不同文案，integrity-flagged 内容 fail-closed；evidence-only 候选审计真实 API/下载闭环已覆盖。 |
| Next action | 建立 clean commit 候选后才允许生成 ZIP，并以同一 commit SHA 执行 artifact restart/parity 与生产前端浏览器验收。 |
| Open risk | 当前 ZIP 和历史 Gate 结果仍冻结、不可发布；artifact gate 与同 SHA 生产前端浏览器验收必须等 clean commit 的候选包生成后执行。 |
