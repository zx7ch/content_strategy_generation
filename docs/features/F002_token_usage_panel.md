# F002 Token Usage Panel Spec(LLM 抽象层与用量展示)

**状态**:P0/P1 已完成
**日期**:2026-06-06
**范围**:v0.2 补丁 — P0/P1 only

---

## 1. 背景与动机

第一版发布后,用户反馈:生成任务过程中没有 token / cost 可见性,很容易在不知情的情况下产生较多模型调用费用。

本功能不应只做一个前端临时估算组件。更稳的做法是:在 Agent 与具体模型供应商之间增加统一 **LLM Abstraction Layer**,把模型路由、Provider 适配、usage 标准化、cost 计算和落库统一放在 LLM Service 层。

一句话目标:

> Agent 只依赖稳定的生成接口,不感知底层 provider / model / API key / usage 字段差异;系统在统一 LLM Service 层记录每次调用的 token、estimated cost、latency 和 status,并在前端按 Job / Step / Agent 展示。

---

## 2. 本期范围

本期只做 **P0 + P1**,不做 P2。

### 2.1 P0

目标:统一模型调用入口,并让用户看到一次任务总消耗。

交付:

- 新增 LLMService 统一调用入口。
- 新增 OpenAICompatibleAdapter。
- 新增简单 ModelRouter。
- 新增 CredentialResolver,第一版只从环境变量读取 API key。
- 新增 pricing 配置与 cost 计算。
- 新增 `llm_usage_events` 表。
- 在 LLMService 层记录 usage event。
- 新增 Job / Session usage summary API。
- 前端显示半透明 Usage Summary 面板。

### 2.2 P1

目标:让用户知道“钱花在哪一步”。

交付:

- LLMCallContext 补齐 `step_id` / `step_name` / `agent_name`。
- 新增 Step 级 usage 聚合接口。
- 新增 LLM call 明细接口。
- Workflow / Creator 任务展示 Step Usage Badge。
- 新增半透明 LLM Calls 折叠明细面板。

### 2.3 P2 延后

以下能力本期不做:

- workspace / user 自定义 API key。
- API key 加密存储。
- fallback model。
- retry attempt 详细关联。
- daily usage dashboard。
- budget warning。
- provider / model 维度统计。
- cached token / reasoning token / audio token 等细分 usage。

---

## 3. 设计原则

- **usage 记录必须发生在 LLM Service 层**,Agent 不参与计费逻辑。
- **Provider Adapter 只负责调用模型和标准化 response**,不负责落库。
- **前端展示 Estimated cost / 预估费用**,避免误导为真实账单。
- **价格不写死在业务逻辑里**,第一版使用 pricing config。
- **失败调用也要记录**,至少记录 status、latency、error_message。
- **先迁移真实产生费用的主链路**,不在本期强制全量替换所有历史调用。

---

## 4. 整体架构

新的调用链路:

```text
Agent / Workflow Step
        ↓
LLM Service
        ↓
Model Router
        ↓
Credential Resolver
        ↓
Provider Adapter
        ↓
OpenAI / DeepSeek / Qwen / Claude / Gemini
        ↓
Normalized LLM Response
        ↓
Usage Tracker
        ↓
llm_usage_events
        ↓
Usage API
        ↓
前端半透明 Usage Panel
```

分层职责:

```text
Agent 层:只关心任务目标与上下文
LLM 抽象层:选模型、拿 key、调 provider、统一返回
Usage 层:记录 token、cost、latency、status
前端层:展示任务总消耗、步骤拆分、调用明细
```

---

## 5. 模块划分

新增目录:

```text
app/services/llm/
  ├── service.py              # Agent 调用入口
  ├── router.py               # 模型路由
  ├── credentials.py          # API key 解析
  ├── pricing.py              # 价格配置和 cost 计算
  ├── usage_tracker.py        # token/cost/latency 落库
  ├── types.py                # 统一类型定义
  └── providers/
      ├── base.py             # Provider 基类
      └── openai_compatible.py
```

本期优先只做 `OpenAICompatibleAdapter`,覆盖 OpenAI / DeepSeek / Kimi / Qwen OpenAI-compatible endpoint 等兼容接口。

旧 `app/llm/client.py` 不必立即删除。第一阶段可以:

- 作为 adapter 内部兼容实现。
- 或保留给未迁移链路使用。

验收时只要求核心策略生成、内容生成链路走新 LLMService。

---

## 6. Agent 调用方式

Agent 不再直接依赖具体 SDK 或模型名。

推荐调用:

```python
response = await llm_service.generate(
    messages=messages,
    task_type="topic_generation",
    model_policy="balanced",
    temperature=0.7,
    context=LLMCallContext(
        session_id=session_id,
        job_id=job_id,
        step_id=step_id,
        step_name="选题生成",
        agent_name="TopicHypothesisAgent",
        tenant_id=tenant_id,
        user_id=user_id,
    ),
)
```

Agent 只表达:

```text
我要生成什么
当前属于哪个 session / job / step
当前是哪个 agent
希望使用 cheap / balanced / quality 哪类模型
```

Agent 不关心:

```text
provider 是谁
API key 从哪里来
usage 字段如何解析
费用如何计算
失败后如何记录
```

---

## 7. 统一类型

### 7.1 LLMRequest

```python
@dataclass
class LLMRequest:
    messages: list[Message]
    task_type: str
    model_policy: str | None = None
    model_id: str | None = None
    provider: str | None = None
    temperature: float = 0.7
    max_tokens: int | None = None
    stream: bool = False
    response_format: dict | None = None
    context: LLMCallContext | None = None
```

### 7.2 LLMCallContext

usage tracking 的关键上下文:

```python
@dataclass
class LLMCallContext:
    session_id: str | None = None
    job_id: str | None = None
    step_id: str | None = None
    step_name: str | None = None
    agent_name: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None
```

说明:

- `session_id` 用于 Creator / workflow run 汇总。
- `job_id` 用于 Job detail 汇总。
- `step_id` / `step_name` / `agent_name` 用于 P1 拆分。

### 7.3 LLMResponse

```python
@dataclass
class LLMResponse:
    content: str
    provider: str
    model: str
    usage: TokenUsage
    latency_ms: int
    raw_response_id: str | None = None
```

### 7.4 TokenUsage

```python
@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
```

本期只支持这三个字段。cached tokens、reasoning tokens、audio tokens 等推迟到 P2。

---

## 8. Provider Adapter

统一接口:

```python
class BaseLLMProvider(Protocol):
    async def generate(
        self,
        request: LLMRequest,
        api_key: str,
        model: str,
    ) -> LLMResponse:
        ...
```

P0 只实现:

```text
OpenAICompatibleAdapter
```

适配对象:

```text
OpenAI
DeepSeek
Moonshot / Kimi
Qwen OpenAI-compatible endpoint
其他兼容 OpenAI Chat Completions 的模型网关
```

Provider Adapter 输出必须标准化为 `LLMResponse`。

---

## 9. Model Router

Agent 使用模型策略,不直接写死模型。

策略:

```text
cheap
balanced
quality
long_context
json_strict
```

配置示例:

```json
{
  "cheap": {
    "provider": "deepseek",
    "model": "deepseek-chat"
  },
  "balanced": {
    "provider": "openai",
    "model": "gpt-4o-mini"
  },
  "quality": {
    "provider": "openai",
    "model": "gpt-4o"
  },
  "long_context": {
    "provider": "qwen",
    "model": "qwen-long"
  }
}
```

任务建议:

| 任务 | 推荐策略 |
| --- | --- |
| 数据清洗 | `cheap` |
| 竞品总结 | `cheap` / `balanced` |
| 选题生成 | `balanced` |
| 内容策略生成 | `balanced` |
| 最终文案生成 | `quality` |
| 长文档分析 | `long_context` |
| JSON 结构化输出 | `json_strict` |

---

## 10. Credential Resolver

P0 只从环境变量读取:

```env
OPENAI_API_KEY=xxx
DEEPSEEK_API_KEY=xxx
QWEN_API_KEY=xxx
ANTHROPIC_API_KEY=xxx
```

接口:

```python
class CredentialResolver:
    def resolve(
        self,
        provider: str,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ) -> str:
        ...
```

第一版映射:

```text
openai   -> OPENAI_API_KEY
deepseek -> DEEPSEEK_API_KEY
qwen     -> QWEN_API_KEY
kimi     -> KIMI_API_KEY
```

workspace / user 自定义 API key 属于 P2,本期不做。

---

## 11. Usage Tracking 数据表

新增表:

```sql
CREATE TABLE llm_usage_events (
    id TEXT PRIMARY KEY,

    session_id TEXT,
    job_id TEXT,
    step_id TEXT,
    step_name TEXT,
    agent_name TEXT,

    tenant_id TEXT,
    user_id TEXT,

    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    model_policy TEXT,

    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,

    input_cost REAL DEFAULT 0,
    output_cost REAL DEFAULT 0,
    total_cost REAL DEFAULT 0,
    currency TEXT DEFAULT 'USD',

    latency_ms INTEGER,
    status TEXT DEFAULT 'success',
    error_message TEXT,

    created_at TEXT NOT NULL
);
```

索引建议:

```sql
CREATE INDEX idx_llm_usage_events_session_id ON llm_usage_events(session_id);
CREATE INDEX idx_llm_usage_events_job_id ON llm_usage_events(job_id);
CREATE INDEX idx_llm_usage_events_created_at ON llm_usage_events(created_at);
```

聚合维度:

```text
Session 级:这一轮创作总共花了多少
Job 级:这个任务总共花了多少
Step 级:哪一步最贵
Agent 级:哪个 Agent 最耗 token
Event 级:每次模型调用明细
```

---

## 12. Pricing 配置

模型价格不要写死在业务逻辑中。

配置示例:

```json
{
  "openai:gpt-4o-mini": {
    "input_per_1m_tokens": 0.15,
    "output_per_1m_tokens": 0.60,
    "currency": "USD"
  },
  "openai:gpt-4o": {
    "input_per_1m_tokens": 2.50,
    "output_per_1m_tokens": 10.00,
    "currency": "USD"
  },
  "deepseek:deepseek-chat": {
    "input_per_1m_tokens": 0.14,
    "output_per_1m_tokens": 0.28,
    "currency": "USD"
  }
}
```

计算:

```text
input_cost = prompt_tokens / 1_000_000 * input_per_1m_tokens
output_cost = completion_tokens / 1_000_000 * output_per_1m_tokens
total_cost = input_cost + output_cost
```

前端展示统一使用:

```text
Estimated cost
预估费用
```

---

## 13. LLMService 流程

```python
async def generate(self, request: LLMRequest) -> LLMResponse:
    resolved_model = self.model_router.resolve(
        task_type=request.task_type,
        model_policy=request.model_policy,
        model_id=request.model_id,
        provider=request.provider,
    )

    api_key = self.credential_resolver.resolve(
        provider=resolved_model.provider,
        tenant_id=request.context.tenant_id if request.context else None,
        user_id=request.context.user_id if request.context else None,
    )

    adapter = self.provider_registry.get(resolved_model.provider)

    start = time.monotonic()
    status = "success"
    error_message = None
    response = None

    try:
        response = await adapter.generate(
            request=request,
            api_key=api_key,
            model=resolved_model.model,
        )
        return response

    except Exception as exc:
        status = "failed"
        error_message = str(exc)
        raise

    finally:
        latency_ms = int((time.monotonic() - start) * 1000)
        usage = response.usage if response else TokenUsage()

        cost = self.pricing_calculator.calculate(
            provider=resolved_model.provider,
            model=resolved_model.model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
        )

        await self.usage_tracker.record(
            context=request.context,
            provider=resolved_model.provider,
            model=resolved_model.model,
            model_policy=request.model_policy,
            usage=usage,
            cost=cost,
            latency_ms=latency_ms,
            status=status,
            error_message=error_message,
        )
```

重点:

- 成功调用要记录 usage。
- 失败调用也要记录 status / latency / error。
- usage 记录发生在 LLMService 层。
- Agent 不参与 usage 记录。

---

## 14. 后端 API

当前系统同时存在 `session_id` 和 `job_id`。本期 API 需要支持两类入口。

### 14.1 Session 总消耗

```http
GET /sessions/{session_id}/usage
```

用于 Creator / workflow run 顶部面板。

返回:

```json
{
  "session_id": "session_123",
  "total_calls": 3,
  "prompt_tokens": 10800,
  "completion_tokens": 4100,
  "total_tokens": 14900,
  "total_cost": 0.046,
  "currency": "USD",
  "latency_ms": 8900
}
```

### 14.2 Job 总消耗

```http
GET /jobs/{job_id}/usage
```

用于 Job Detail 顶部卡片。

返回:

```json
{
  "job_id": "job_123",
  "total_calls": 3,
  "prompt_tokens": 10800,
  "completion_tokens": 4100,
  "total_tokens": 14900,
  "total_cost": 0.046,
  "currency": "USD",
  "latency_ms": 8900
}
```

### 14.3 Step 消耗拆分

```http
GET /jobs/{job_id}/usage/steps
GET /sessions/{session_id}/usage/steps
```

返回:

```json
{
  "job_id": "job_123",
  "steps": [
    {
      "step_id": "step_1",
      "step_name": "竞品分析",
      "agent_name": "CompetitorAnalysisAgent",
      "total_calls": 1,
      "total_tokens": 4200,
      "total_cost": 0.012,
      "currency": "USD"
    }
  ]
}
```

### 14.4 LLM 调用明细

```http
GET /jobs/{job_id}/usage/events
GET /sessions/{session_id}/usage/events
```

返回:

```json
{
  "events": [
    {
      "agent_name": "TopicHypothesisAgent",
      "provider": "openai",
      "model": "gpt-4o-mini",
      "model_policy": "balanced",
      "prompt_tokens": 2100,
      "completion_tokens": 700,
      "total_tokens": 2800,
      "total_cost": 0.008,
      "currency": "USD",
      "latency_ms": 1200,
      "status": "success",
      "created_at": "2026-06-06T09:20:00Z"
    }
  ]
}
```

---

## 15. 前端展示

所有计费相关展示面板本期统一做成半透明效果。

### 15.1 视觉规则

浅色半透明面板:

```text
bg-white/70
backdrop-blur
border border-white/60
shadow-sm
```

高风险状态:

```text
预算接近上限: amber
严重超限或刷新失败: rose
```

不要默认用强警告色,避免用户把普通成本展示理解成错误状态。

### 15.2 Usage Summary Card(P0)

位置:

```text
Creator / Job Detail 顶部
```

展示:

```text
本次任务消耗

14.9K tokens
3 次模型调用 · 预估 $0.046
```

英文:

```text
Run Usage

14.9K tokens
3 LLM calls · Estimated $0.046
```

### 15.3 Step Usage Badge(P1)

Workflow Step 列表:

```text
竞品分析      completed      4.2K tokens · $0.012
选题生成      completed      2.8K tokens · $0.008
内容生成      running        -
```

用于定位:

```text
哪个 step 最贵
哪个 Agent token 异常
是否某一步 RAG 输入过长
是否生成步骤调用次数过多
```

### 15.4 LLM Calls 折叠明细(P1)

半透明折叠面板:

```text
LLM 调用明细
────────────────────────

TopicHypothesisAgent
openai / gpt-4o-mini
2.8K tokens · $0.008 · 1.2s · success

StrategyAgent
openai / gpt-4o-mini
4.1K tokens · $0.013 · 2.0s · success
```

该面板默认折叠,避免占用主流程空间。

---

## 16. 边界情况

### 16.1 Provider 不返回 usage

P0 处理:

```text
token 记 0
cost 记 0
status 仍记录 success / failed
```

P2 再考虑 tokenizer 估算。

### 16.2 Streaming

P0 可不迁移 streaming 链路。

若后续支持 stream:

```text
stream 完成后统一写 usage event
中途断开时 status=interrupted 或 failed
usage 已知则记录,未知则为 0
```

### 16.3 Retry 费用

retry 产生真实模型调用费用,因此每次 retry 都应记录一条 usage event。

P0/P1 暂不要求 `attempt_no` / `retry_of_event_id`,但 Step 汇总应包含所有 attempts。

### 16.4 价格变化

P0 使用 pricing config。

后续可演进:

```text
后台可配置
价格按时间版本化
provider 自动同步价格
```

---

## 17. Issue 拆分计划

本功能拆成 7 个 issue 实施。P0 4 个,P1 3 个。

### Issue 1(P0):LLM 抽象层基础骨架

状态:Done

目标:先把统一调用入口搭起来,不迁移业务逻辑。

范围:

- 新增 `app/services/llm/` 目录。
- 定义 `LLMRequest` / `LLMResponse` / `TokenUsage` / `LLMCallContext`。
- 新增 `LLMService.generate()` 入口。
- 新增 `ModelRouter`。
- 新增 `CredentialResolver`。
- 增加基础单测。

验收:

- `LLMService.generate()` 可通过 mock adapter 完成一次标准调用。
- Router 能根据 `model_policy` 解析 provider / model。
- CredentialResolver 能按 provider 从 env 读取 key。
- 不要求真实 Agent 迁移。

### Issue 2(P0):OpenAI-compatible Provider Adapter

状态:Done

目标:接入第一类真实 provider adapter。

范围:

- 实现 `OpenAICompatibleAdapter`。
- 支持 OpenAI / DeepSeek / Kimi / Qwen OpenAI-compatible endpoint。
- 标准化 `content` / `usage` / `provider` / `model` / `latency_ms`。
- 处理 provider 不返回 usage 的情况。

验收:

- adapter 返回统一 `LLMResponse`。
- usage 存在时能解析 prompt / completion / total tokens。
- usage 缺失时 token 记 0,不影响调用成功。
- provider 异常能向上抛出,供 LLMService 记录失败事件。

### Issue 3(P0):Usage Tracking 落库与 Pricing

状态:Done

目标:让每次 LLM 调用都能形成 usage event。

范围:

- 新增 `llm_usage_events` 表和 migration。
- 新增 `pricing.py`。
- 新增 `usage_tracker.py`。
- 成功调用写 success event。
- 失败调用写 failed event。
- cost 使用 pricing config 计算。

验收:

- 成功调用会写入 provider、model、tokens、cost、latency、status、created_at。
- 失败调用会写入 status、latency、error_message。
- cost 计算有单测覆盖。
- usage tracker 聚合基础逻辑有单测覆盖。

### Issue 4(P0):迁移主链路 + 总消耗 API + Summary 面板

状态:Done

目标:形成第一个用户可见里程碑。

范围:

- 迁移策略生成主链路到 `LLMService`。
- 迁移内容生成主链路到 `LLMService`。
- 新增 `GET /sessions/{session_id}/usage`。
- 新增 `GET /jobs/{job_id}/usage`。
- 前端新增半透明 Usage Summary Card。

验收:

- 跑完一个 Creator / workflow 任务后,数据库存在 `llm_usage_events`。
- Usage Summary Card 显示 total tokens、LLM calls、estimated cost。
- 面板使用半透明视觉样式。
- 这是 P0 端到端验收 issue。

### Issue 5(P1):补齐 Step / Agent Context

状态:Done

目标:让 usage event 能归属到具体步骤和 Agent。

范围:

- 在 workflow step 调用 LLM 时传入 `step_id`。
- 传入 `step_name`。
- 传入 `agent_name`。
- 确保同时带上 `session_id` / `job_id`。

验收:

- 主链路 usage event 能看到 step_name / agent_name。
- 不同 step 的调用不会混在同一归属里。
- 缺少 step context 时不阻断 LLM 调用,但 event 仍能记录。

### Issue 6(P1):Step 聚合 API

状态:Done

目标:让系统能回答“钱花在哪一步”。

范围:

- 新增 `GET /sessions/{session_id}/usage/steps`。
- 新增 `GET /jobs/{job_id}/usage/steps`。
- 按 step 聚合 calls、tokens、cost、currency。
- 单测覆盖多 step、多 agent、多失败调用。

验收:

- API 返回每步 total_calls、total_tokens、total_cost。
- 失败调用计入 calls 和 latency,status 可用于明细排查。
- Step 聚合结果与 event 明细求和一致。

### Issue 7(P1):LLM Calls 明细面板

状态:Done

目标:前端展示每次模型调用明细和 step badge。

范围:

- 新增 `GET /sessions/{session_id}/usage/events`。
- 新增 `GET /jobs/{job_id}/usage/events`。
- Workflow Step 列表增加 usage badge。
- 新增半透明 LLM Calls 折叠明细面板。
- 明细展示 provider、model、tokens、cost、latency、status。

验收:

- Step Usage Badge 能显示每步 tokens / cost。
- LLM Calls 面板默认折叠。
- 展开后可看到 provider、model、model_policy、tokens、cost、latency、status、created_at。
- 失败调用有清晰 status 和 error 提示。

---

## 18. 验收标准

### P0 验收

- 策略生成和内容生成主链路走 `LLMService.generate()`。
- Agent 不再在主链路直接调用具体 provider SDK。
- 跑完一个 Creator / workflow 任务后,数据库存在 `llm_usage_events`。
- 每条 usage event 至少包含 provider、model、tokens、estimated cost、latency、status、created_at。
- `GET /sessions/{session_id}/usage` 可返回总消耗。
- `GET /jobs/{job_id}/usage` 可返回总消耗。
- 前端半透明 Usage Summary Card 可显示 total tokens、LLM calls、estimated cost。

### P1 验收

- 主链路 LLMCallContext 能带出 step_name / agent_name。
- Step usage 聚合 API 可返回每步 tokens / cost。
- LLM events API 可返回调用明细。
- 前端 Step Usage Badge 能显示每步消耗。
- 前端 LLM Calls 折叠面板能看到 provider、model、tokens、cost、latency、status。

---

## 19. README 文案

```md
### Agent Usage & Cost Tracking

系统在统一 LLM Service 层记录每次模型调用的 token、estimated cost、latency 和调用状态,并按 Job / Session / Step / Agent 维度聚合展示。

用户可以在任务详情页查看本次任务的总消耗、各阶段消耗拆分和模型调用明细,从而定位高成本环节,辅助优化 prompt、RAG 输入和模型路由策略。
```
