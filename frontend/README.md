# XHS Growth Agent Frontend

`Phase 1-1` 前端控制台脚手架。

当前实现提供：

- `Next.js 14+` App Router
- `TypeScript` 严格模式
- `Tailwind CSS`
- 顶部导航与侧边栏
- brand selector / brand-aware shell
- 六个核心业务路由骨架
- `/brands/[id]` 品牌配置详情入口
- `/brands/[id]` 的 `P1-2` 数据入口触发面板
- 共享 `Button`、`Card`、`DataTable`、`StatCard`
- `lib/types.ts` 类型定义；测试夹具可独立使用 `lib/data.ts`，但正式页面运行态不得依赖 mock/fallback 数据
- `lib/api.ts` / `lib/server-api.ts` 统一 API 接入层，SSR 页面与客户端页面按各自运行时解析 workspace

## Phase 边界

当前前端实现遵循 “先有 route shell，再按 phase 打开 live integration” 的顺序。

| Route | 当前阶段归属 | 当前状态 | 说明 |
|---|---|---|---|
| `/brands` | `P1-1` | `live (SSR)` | 读取品牌列表与基础主数据；live 失败时显示明确错误态 |
| `/brands/[id]` | `P1-2` | `live (SSR)` | 读取品牌详情，并提供 source sync / historical import 入口与状态反馈；live 失败时显示明确错误态 |
| `/topic-pool` | `P1-3` | `route shell / pending live data` | API contract 已留出；在能力落地前只能显示清晰的 unavailable/loading/empty 状态，不得伪装 mock live 数据 |
| `/decisions` | `P1-4` | `route shell / pending live data` | 当前保留壳子；待决策 API 交付后接入真实读取与真实错误态 |
| `/publish` | `P1-5` | `route shell / pending live data` | 待 publish lineage API 落地；交付后不得保留 mock fallback |
| `/performance` | `P1-5` | `route shell / pending live data` | 待 performance/read model 落地；交付后必须展示真实接口结果 |
| `/evaluation` | `P1-5` | `route shell / pending live data` | 待 evaluation APIs 落地；交付后必须展示真实接口结果 |

约束：

- `P1-1` 允许创建后续页面路由骨架，但不允许把未交付 phase 的页面描述成 live integration
- 正式页面流转必须通过按钮点击与 App Router 路由完成，不得依赖用户手动修改 API 地址、请求体或地址栏
- `lib/api.ts` 中每个 route loader 都必须标明 phase 归属和真实契约依赖；测试替身必须隔离在测试环境
- 当后续 phase 的后端端点真正落地时，对应 loader 必须直接接入 `live`，并展示真实 loading / empty / error 状态，而不是保留 mock fallback

## 本地启动

```bash
cd frontend
npm install
npm run dev
```

默认首页会重定向到 `/brands`。

## Vercel 部署

前端正式部署时，Vercel 只托管 Next.js UI；Agent Runtime 仍运行在用户本机。

Vercel 项目配置：

- Root Directory: `frontend`
- Framework Preset: `Next.js`
- Install Command: `npm install`
- Build Command: `npm run build`
- Output Directory: 使用 Vercel 默认值

生产环境变量：

```bash
NEXT_PUBLIC_XHS_API_BASE_URL=http://127.0.0.1:8000
NEXT_PUBLIC_XHS_AUTH_TOKEN=
```

部署完成后，需要把 Vercel 生产域名加入本地后端 `.env`：

```bash
XHS_CORS_ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000,https://your-app.vercel.app
```

浏览器访问 Vercel 页面后，会从用户自己的机器访问 `http://127.0.0.1:8000`。因此 Vercel 服务端不能调用本地 runtime，依赖本地 runtime 的页面必须在浏览器端发起请求。

## 可选环境变量

若要让前端读取真实 V2 API，可设置：

```bash
NEXT_PUBLIC_XHS_API_BASE_URL=http://127.0.0.1:8000
NEXT_PUBLIC_XHS_AUTH_TOKEN=<optional-when-v2-auth-enabled>
```

说明：

- `/brands` 与 `/brands/[id]` 等 live 页面应通过浏览器端请求本地 runtime。
- 客户端页面通过 `WorkspaceProvider` 初始化 runtime workspace context。
- 若后端启动正常，前端本地联调不需要手工配置 `workspace_id` / `user_id`。
- 正式开发完成后的页面不应因为 API 失败而自动改用 mock 数据；应显示真实错误并提供可理解的重试路径。
