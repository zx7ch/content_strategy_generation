# F003 Lite Gate 2 最终验收记录

**状态**：CLEAN
**日期**：2026-07-26
**范围**：`product_marketing` 单方向真实 Creator 垂直切片。

## 已关闭的验收证据

1. 受控无效本地凭据使同一 `workflow_run_id` 进入真实 `auth_required` 分支。两个 discover operation 记录为认证失败；此时 `ReportPublication=0`、Creator `artifact_result=0`。
2. 用户显式 QR 认证后，`retry_formal_research` 将**同一 run**重新入队。恢复后真实 provider 完成 discover/detail，workflow 到达 `succeeded`。
3. 失败期的 4 个 operation/checkpoint 记录被 `superseded`；恢复期写入 completed operation。恢复没有新建 Brief、Plan 或 workflow run。
4. 最终只 materialize 一份 `complete_verified_report`、一条 Creator `artifact_result`，并冻结 8 个 citation group。
5. 已完成 run 的显式重复重试被拒绝；已发布报告与 citation 在刷新后保持同一版本。

本记录不包含 QR 图像、Cookie、会话、签名或原始 provider payload。

## 非阻塞遗留任务

- **真实 timeout/transient 用户恢复演练**：构造可安全恢复的 timeout 或 transient 错误，验证用户重试、已完成 operation 去重、唯一报告及浏览器可见证据。此前只完成了结构化失败记录与局部自动化覆盖。
- **失败态刷新后的 Creator 恢复入口**：认证失败但尚未发布 artifact 时，刷新页面可能丢失当前 run 的恢复上下文；应从持久化 workflow/trace 恢复 QR 与重试入口，而不是依赖前端瞬时状态。
- **终态 dispatch job 归档/TTL**：保留当前审计行；归档与清理由正式 runtime release 处理。
- **其余 Content Research repository 的异步化**：worker/dispatcher 路径已使用异步持久化；非运行时业务 read/write repository 的全面迁移另行安排。

这些项是后续稳定性与正式发布工作，不改变本次 Gate 2 的关闭结论。
