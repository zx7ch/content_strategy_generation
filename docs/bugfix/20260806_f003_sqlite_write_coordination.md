# F003 SQLite 写入路径未统一

**日期**：2026-08-06  
**状态**：已完成定点缓解；基础设施治理待排期  
**影响范围**：Content Research 正式调研、调度队列、工作流运行时与证据持久化

## 现象

一次正常的“确认 brief → 开始正式调研”在尚未调用数据源前失败：

```text
sqlite3.OperationalError: database is locked
task_router._ensure_trace() -> SQLiteContentResearchStore.save_trace()
```

失败的 dispatch job 被写为 `failed`，但子任务仍是 `queued`，因此没有发生数据源调用或证据丢失。

## 根因

同一个 `data/xhs_agent.db` 同时存在多条独立写入路径：

- 同步 `sqlite3`：`SQLiteContentResearchStore` 与部分运行时记录；
- 异步 `aiosqlite`：formal dispatch、`WorkflowRunManager`、方向 pipeline；
- 各路径的事务边界、`busy_timeout` 与失败恢复语义不统一。

SQLite 同一时刻仅允许一个 writer。正式调研启动时，worker 又尝试插入一条新的 subagent trace；这条非必要写入与其他事务竞争，最终超出等待时间并使整个 run 失败。

## 本次定点修复

正式调研现在将该 workflow 已持久化的 presearch trace ID 传给每个方向任务。任务路由复用已有 trace，不再在 worker 启动时插入第二条 trace。

真实 run `run_770af525b4a84dbe87df3128dccc0532` 的验证结果：

- 首次因锁失败后，以原 scope 重新入队；
- 第二次进入 `running` 并完成，dispatch `last_error` 为空；
- workflow 仅有 1 条 trace；
- 子任务完成为 `partial_completed`，没有重复创建 run。

## 未解决的技术债

这不是全局 SQLite 并发治理。其他写入边界仍可能竞争，因此不能将本次修复视为“同步/异步混用已消除”。

后续基础设施任务应：

1. 为 Content Research 建立统一的异步写入协调入口，覆盖 dispatch、workflow runtime、trace 与 pipeline flush；
2. 统一连接初始化参数（WAL、`busy_timeout`、事务模式）；
3. 定义锁冲突的可恢复语义：不得把安全可重试的本地写冲突伪装成 provider 失败；
4. 增加并发确认、dispatch、pipeline flush 与 trace 读取/写入的竞争测试；
5. 评估若后续需要多进程 worker 或更高并发，迁移到具备多 writer 能力的数据库。
