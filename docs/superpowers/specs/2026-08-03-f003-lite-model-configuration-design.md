# F003 Lite 模型配置与 pre-research 恢复设计

日期：2026-08-03

状态：待用户书面审阅

范围：F003 Content Research Lite

## 1. 背景与目标

Lite 的 pre-research 当前通过静态模型策略和 `.env` 凭证调用 LLM。真实验收中，旧 Kimi Coding 路由因会员权益返回 HTTP 402，用户只能修改 `.env` 并重启服务，页面没有配置、验证或恢复入口。

本设计增加一个最小的本地模型配置闭环：用户在 Creator 右侧栏填写 `base_url`、`model`、`api_key`，后端验证并保存配置，内容调研运行时优先使用该配置；若 LLM 失败，用户修复配置后在同一 run 从未完成阶段继续。

快速发布优先。本任务不建设通用模型管理平台、密钥托管系统或多协议适配框架。

## 2. 已确认的产品决策

### 2.1 配置字段

Lite 只允许用户配置：

- `base_url`
- `model`
- `api_key`

`temperature`、输出长度、结构化输出提示、超时和自动重试属于系统任务策略，不开放给用户。

### 2.2 支持范围

采用“协议受限、模型名开放”的边界：

- 只支持 OpenAI-compatible Chat Completions 协议；
- 不维护具体模型 ID 白名单；
- 运行时原样使用用户填写的 `base_url + model + api_key`；
- 不静默改写模型 ID，不在失败后自动切换 Provider 或 `.env` 模型；
- Anthropic 原生协议、Ollama 特殊协议、视觉、工具调用和流式能力不属于 Lite 首版。

模型必须能完成普通文本对话，并能按提示返回可被后端解析和校验的结构化文本。不要求原生 JSON Mode 或 JSON Schema。

### 2.3 系统参数

- `temperature` 由具体任务设置；目标模型拒绝该可选参数时，adapter 可省略后重试一次。
- 输出长度由任务设置上限；adapter 负责转换为兼容参数名，目标模型不支持时可以省略。
- JSON 文本由提示、解析、schema 校验和有限重试保证，不由用户配置。

## 3. UI 设计

### 3.1 入口位置

为减少 Lite 发布工作量，不开发头像菜单或独立设置中心。Creator 右侧栏顺序为：

1. 研究运行 / Trace
2. 证据与 Trace
3. 本次研究摘要
4. 模型服务

“模型服务”卡片是用户级配置的 Lite 临时入口，并不表示配置只对当前 run 生效。正式版可迁移到设置中心，而不改变后端合同。

### 3.2 收起态

卡片只展示安全摘要：

- 状态：`系统默认`、`已配置`、`验证失败` 或 `模型服务异常`
- 当前模型
- 生效来源：`用户配置` 或 `系统默认`
- API Key：仅在用户配置存在时显示末四位
- 操作：`配置模型`

未运行研究任务时卡片仍可见。

### 3.3 编辑态

点击“配置模型”后，在卡片内展开或打开轻量弹窗，包含三个输入框以及：

- `测试连接`
- `保存`
- `删除配置，恢复系统默认`
- `继续调研`：仅在当前 run 因模型错误进入可恢复等待且新配置验证成功后显示

API Key 输入框不回填完整旧值。用户不填写新 Key 时，允许只修改 Base URL 或模型并复用已保存 Key；填写新 Key 时整体替换。

### 3.4 失败态

pre-research 或后续 LLM 步骤因模型配置失败时，右侧卡片显示稳定、可操作的错误，例如：

- API Key 无效；
- 账户余额或套餐不可用；
- 模型不存在；
- 请求过频；
- 服务暂时不可用；
- 接口与 OpenAI-compatible 合同不兼容；
- 结构化结果无法解析。

错误卡和 Trace 可以提供“配置模型”快捷按钮，但都打开同一个模型服务卡片，不复制配置逻辑。

## 4. 后端设计

### 4.1 配置存储

新增本地 `LiteLLMConfiguration`，以 `(workspace_id, user_id)` 唯一定位，至少保存：

- `base_url`
- `model`
- `api_key`
- `validation_status`
- `validated_at`
- `last_validation_error_code`
- `created_at`
- `updated_at`

Lite 本地版允许 API Key 明文保存在本地 SQLite，不引入应用层加密。最低安全边界仍必须满足：

- 查询接口永不返回完整 API Key；
- 日志、Trace、异常和 usage event 永不记录 API Key 或 Authorization header；
- 安全摘要只返回 `api_key_configured` 和末四位；
- 删除用户配置后恢复 `.env` 系统默认。

### 4.2 配置优先级

运行时解析顺序为：

1. 当前 Workspace 中当前用户的已验证配置；
2. `.env` 系统默认配置。

Workspace 默认 Provider 管理不进入 Lite 首版，避免额外的权限和管理 UI。后续若增加 Workspace 默认配置，可在用户配置和 `.env` 之间插入，不改变调用接口。

用户配置一旦被选中，该次调用必须完整使用其 Base URL、模型和 Key。调用失败不得静默回退，否则 Trace 无法说明实际使用的模型，也会掩盖配置错误。

### 4.3 复用现有 LLM 服务

配置能力接入现有 `app/services/llm` 正式调用链：

- 配置 resolver 根据 `LLMCallContext.tenant_id/user_id` 解析用户配置；
- `ModelRouter` 在存在用户配置时生成 OpenAI-compatible 的 resolved model；
- OpenAI-compatible adapter 使用本次解析出的 Base URL、模型和 Key；
- pre-research 继续声明自己的任务策略，不直接读取数据库或拼装客户端。

不得在 Creator route、pre-research service 或 Lite workflow 中新增第二套 LLM client。

现有 provider adapter 若在进程启动时冻结 Base URL，需要改为按请求接收解析后的 endpoint，确保保存配置后无需重启前后端即可生效。

### 4.4 配置 API

提供最小接口：

- `GET /content-research/llm-config`：返回安全配置摘要和当前生效来源；
- `POST /content-research/llm-config/validate`：验证候选配置，不持久化；
- `PUT /content-research/llm-config`：验证成功后保存；
- `DELETE /content-research/llm-config`：删除用户配置并恢复系统默认。

所有接口使用 Creator 已有的 Workspace/User principal，不接受客户端自行指定其他用户或 Workspace。

### 4.5 验证语义

验证不依赖 `/models`，因为兼容代理可能未实现该接口。后端向用户指定的 Chat Completions endpoint 发起最小请求并验证：

1. URL 和协议合法；
2. 服务可连接；
3. 鉴权通过；
4. 指定模型可调用；
5. 返回非空文本；
6. 最小结构化文本可解析。

验证失败的候选值不替换当前有效配置。`PUT` 必须使用同一验证逻辑，避免“只测试未验证地保存”。

### 4.6 错误合同

上游错误统一映射为稳定安全码：

| 上游现象 | 稳定错误码 | 是否可由用户恢复 |
|---|---|---|
| 401 / 403 | `llm_auth_invalid` | 是 |
| 402 / 账户权益或余额限制 | `llm_account_unavailable` | 是 |
| model not found | `llm_model_unavailable` | 是 |
| 429 | `llm_rate_limited` | 是，可等待或换配置 |
| 超时 / 5xx | `llm_service_unavailable` | 是 |
| 响应协议不兼容 | `llm_protocol_incompatible` | 是 |
| 结构化结果多次解析失败 | `llm_structured_output_invalid` | 是，可换模型 |

原始响应仅用于服务端安全诊断，不进入 Creator 消息、Trace 或持久化的公开错误字段。

## 5. Workflow 恢复语义

模型错误不得伪装为成功或永久报告结果。可恢复错误使父 workflow 收敛到 durable `waiting_user`，前端显示“等待配置模型”。

用户保存并验证新配置后，通过现有的同 run 恢复入口继续执行：

- 从最早的未完成 LLM checkpoint 继续；
- 保留已完成且仍有效的 Brief、查询计划、packet、admission、报告和 Trace artifact；
- Spider checkpoint 已完成时不得重新采集；
- 若失败发生在 Spider 之前的 pre-research，则只重试 pre-research，之后正常进入首次采集；
- 成功或已发布 run 仍拒绝重复恢复；
- 恢复动作沿用现有 specialist/workflow 用户恢复预算，不新增无限重试通道。

Trace 记录配置来源、Provider/模型安全名称、错误码、等待和恢复边界，但不记录 Base URL 查询参数、API Key、请求头、prompt 或原始响应。

## 6. 实现边界

### 6.1 Lite 首版包含

- 本地保存与删除三个配置字段；
- OpenAI-compatible 自定义 endpoint 和自由模型 ID；
- 测试连接与保存时验证；
- 用户配置优先、`.env` 回退；
- 错误分类与安全投影；
- 同 run checkpoint-aware 恢复；
- Creator 右侧栏“本次研究摘要”下方的模型服务卡片。

### 6.2 明确不做

- 应用层加密、云密钥托管或密钥轮换；
- 具体模型白名单；
- Anthropic 原生协议及其他专用 SDK；
- Workspace 默认配置管理 UI；
- 用户自定义 temperature、输出长度、超时和重试；
- 自动 Provider fallback、多模型路由或费用优化；
- `/models` 模型目录同步；
- 独立设置中心或头像菜单。

## 7. 验收矩阵

1. 用户可在右侧栏填写三个字段，测试、保存、刷新后仍可读取安全摘要。
2. 保存后无需重启服务，pre-research 使用用户填写的 endpoint、模型和 Key。
3. 用户未配置时继续使用 `.env`；删除配置后恢复 `.env`。
4. 自定义模型 ID 不在本地白名单中也可通过真实验证并使用。
5. 401、402、model not found、429、5xx/timeout、协议不兼容和解析失败均显示稳定安全错误。
6. 失败验证不会覆盖当前有效配置，接口和日志不返回完整 Key。
7. 模型错误使 workflow 进入可恢复等待，而不是发布伪成功报告。
8. 修复配置后在同一 run 继续；已有 Spider packet/checkpoint 时 Provider operation 数不增加。
9. 失败发生在 pre-research 且尚未采集时，只重试 pre-research，之后执行首次 Spider 采集。
10. Trace 能解释使用的配置来源、模型、失败和恢复时间线，同时通过敏感字段递归检查。

## 8. 交付顺序

该能力登记为 `Task 5H`，与 `Task 5G-2B` 的 Trace 时间语义解耦。因为无可用模型配置会直接阻断 Lite 真实运行，Task 5H 按快速发布的最小闭环优先实施；5G-2B 不作为其前置条件。
