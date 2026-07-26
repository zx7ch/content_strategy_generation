# F003 Gate 2：XHS Provider 路线复核（新版 Spider）

**日期**：2026-07-25
**状态**：路线已更新；Gate 2 仍在执行
**问题**：不安装 Extension、不读取用户日常 Chrome profile 的前提下，如何采集小红书笔记并进入 F003 的 citation 链路？

## 已验证结论

1. 早先判断基于仓库中的旧 Spider 版本，已失效。`cv-cat/Spider_XHS` 已更新至 `4267f898`；新版提供 `XHSPcAuth`，支持完整 Cookie、二维码和手机号三种认证来源，并使用自己的 HTTP/2 Chrome impersonation 会话和可变签名状态。
2. 在本机实际验证中，二维码登录后，同一新版 Spider 生命周期成功完成 `bootstrap → search`（20 张卡）→ `detail`（1 条详情）。这说明旧 `406 / code=-1` 并非 F003 selection/admission 规则导致，而是旧静态 facade 与已变化的 Spider 签名/会话协议不兼容。
3. 正式 F003 runtime 现改为 `XHSPcAuth → XHS_Apis.bootstrap()`：一个 facade 生命周期持有一个可变认证/签名状态，worker 线程串行调用 search/detail/comments。旧 CDP response bridge 已从该路径删除。
4. 后端仍不会、也不应读取用户日常 Chrome 的 profile、Cookie 或已有 tab。它不能“自动发现”日常 Chrome 登录态。用户需要显式提供完整 Cookie，或通过产品提供的 QR/手机登录入口建立 Spider 自己的会话。

## 路线对比

| 方案 | Extension | 读取日常 Chrome | 本次 Gate 2 路线 | 说明 |
|---|---:|---:|---:|---|
| 旧 bundled Spider（固定签名/逐请求 Cookie） | 否 | 否 | 淘汰 | 已实证被 provider 拒绝；代码已移除 |
| Chrome CDP response bridge | 否 | 需显式调试授权 | 淘汰 | 不再作为正式采集路径 |
| experiment Extension capture | 是 | 是 | 不接入 | 实验隔离，且改变用户安装要求 |
| **新版 Spider 独立认证会话** | **否** | **否** | **采用** | QR/手机/完整 Cookie，供后端后台采集 |

## 正式运行边界

```text
用户显式建立 Spider QR/手机/完整 Cookie 登录态
  -> XHSPcAuth（持有 Cookie、签名与 HTTP 会话）
  -> XHS_Apis.bootstrap（校验会话并初始化 user id）
  -> search → detail → comments
  -> SourceAdapter → Evidence → Admission → frozen citation → report/trace

认证缺失或失效
  -> typed auth_required
  -> workflow recovery card（不生成报告 artifact）
```

关键约束：

- Cookie、会话、签名、HAR 和原始 provider body 都不得写入 trace、日志、fixture 或仓库。
- 同一个 facade 不得并行使用可变 upstream auth/signing state；同步 provider 调用只能在 worker executor 中执行，不能阻塞 application event loop。
- QR 登录必须是用户主动触发的交互；worker 只使用已建立的会话，过期时返回 `auth_required`，不在后台自行拉起登录。
- 需要单独实现受保护的会话持久化或用户可见的 QR 登录入口；本次 facade 升级不把短期 QR 登录结果写入 `.env`。

## 对 Gate 2 的影响

`F003-G2-SPIDER-LIFECYCLE` 已完成新版 provider facade 的适配与 CDP 清理，并用隔离单测锁定新的认证、search/detail/comments 调用合同。Gate 2 仍为 `IP`：尚缺 Creator 公共入口的真实 Product Marketing 成功 run（含 frozen citation），以及真实 auth failure 的 recovery screenshot、checkpoint 和 reload/retry 去重证据。
