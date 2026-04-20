# Development Guide: P5-4 - 文档交付

> Generated: 2026-03-22
> Architect: implementation skill
> Status: Draft -> Ready for development
> Source: dev_spec.md §9.3, docs/testing_strategy.md §12.x

## 1. Task Context

### Scope Boundary
- **Task ID**: `P5-4`
- **Task Name**: 文档交付
- **Phase**: Phase 5 - 测试交付
- **Dependencies**:
  - `P5-3` 已完成，E2E 主链路与错误契约已有自动化证据
  - `§9.4` 当前无 `OPEN` residual，但文档需明确这一状态和关闭证据来源
- **Task Goal**:
  - 让 README、CHANGELOG、API/故障排查文档与当前代码、配置、测试结果一致，可支撑新成员搭建、运行、调用和排障

### In Scope
- 更新 `README.md`，补齐安装、配置、启动、测试、典型 API 流程、SSE 使用方式、当前能力边界
- 更新 `docs/CHANGELOG.md`，反映 2026-03-21 完成的测试交付、API/SSE/lifecycle/budget/error contract 收口结果
- 新增或补齐 `docs/` 下 API 使用/故障排查文档，使高频问题与真实错误契约一致
- 将 residual backlog 当前状态写清楚：目前 `§9.4` 已无 `OPEN` 项，但需要说明证据来源位于哪些测试与 spec 记录
- 同步 `dev_spec.md` 中 `P5-4` 的完成记录和本轮文档交付验证结果

### Out Of Scope
- 不新增或重构生产功能
- 不引入 acceptance 级真实外部依赖验证
- 不重写已有架构设计文档的非必要部分，如数据模型长篇设计说明

### Required Deliverables
- Production: 无
- Tests: 文档演练与针对性命令验证，必要时补最小文档相关回归命令
- Spec/Docs:
  - `README.md`
  - `docs/CHANGELOG.md`
  - 至少一个面向使用者的 `docs/` 文档补充 API 使用或故障排查
  - `dev_spec.md` 中 `P5-4` 状态、进度记录、必要的 residual 审计同步

### Acceptance Criteria (from dev_spec.md §9.3 + current phase obligations)
- [ ] AC1 `README.md` 的安装、配置、运行、测试命令与当前仓库实现一致，可被新成员直接照做
- [ ] AC2 API 示例、SSE 用法、错误/生命周期说明与 `router.py`、`app/main.py`、当前 E2E 证据一致
- [ ] AC3 故障排查覆盖高频问题：配置缺失、Spider cooldown、session frozen/purged、非法 `Last-Event-ID`、SSE/uvicorn 验证限制
- [ ] AC4 文档明确记录当前 `§9.4` 无 `OPEN residual`，并标注关闭证据来源，不允许文档与 spec 进度脱节
- [ ] AC5 `dev_spec.md` 中 `P5-4` 行和 Progress Record 与本轮实际交付、验证结果一致

### Residual Obligations (from dev_spec.md §9.4)
- **Relevant OPEN Residuals**:
  - 当前无 `OPEN` residual；本任务必须审计并在文档中明确这一状态
- **Current-Phase Carry-Forward Items To Re-check**:
  - `P5-1` / `P5-2` / `P5-3` 已关闭项的证据是否在用户可见文档中能被理解和追溯
  - 先前发现的 warning 类事项是否需要作为“已知限制”写入文档，例如受限沙箱下 `test_sse_uvicorn.py` 可能 skip、第三方依赖弃用 warning 不影响主功能
- **Resolved By This Task**:
  - `P5-4` 自身的文档缺口
- **Deferred / Blocked**:
  - 若发现第三方依赖 warning 需要代码层升级，而非文档澄清，本任务仅记录为已知限制，不扩展为代码改造

### Contract Inventory
- Upstream contracts:
  - `app/config.py` 中的 `Settings` 和 `.env.example`
  - `app/api/routes/router.py` 中的 REST/SSE 接口、错误契约、预算字段、`Last-Event-ID`
  - `app/main.py` 中 worker lifespan 与启动方式
- Downstream contracts:
  - 新成员 onboarding
  - 手工 API 演练
  - 后续维护者根据 CHANGELOG / 故障排查快速定位问题
- Files/interfaces with compatibility risk:
  - `README.md` 与 `.env.example`/真实命令不同步
  - `docs/CHANGELOG.md` 未覆盖 2026-03-21 的测试交付与文档收口
  - API 文档未体现当前真实响应/错误码/SSE 语义

### Test Requirements (from docs/testing_strategy.md §12.x)
- **Test File**: README / API 文档演练
- **Test Scenarios**:
  1. README 的安装、配置、运行步骤可复现
  2. API 示例与真实接口契约一致
  3. 故障排除指南覆盖高频失败路径
  4. 已关闭 residual 的证据和当前无 `OPEN` residual 的事实表达清晰
- **Test Target**: 确认交付文档能支撑部署、调用、常见问题排查，并准确反映 residual backlog 状态

---

## 2. Architecture Context

### System Position
```
README / docs
  -> explain FastAPI API layer
  -> explain SQLite queue + worker lifecycle
  -> explain SessionManager lifecycle semantics
  -> explain deterministic test strategy and known limitations
```

### Tech Stack
- Language/runtime: Python 3.10+, FastAPI, Uvicorn
- Primary libraries/services: `fastapi`, `httpx`, `aiosqlite`, `langgraph`, `pydantic-settings`
- Execution pattern: API 入队，后台 worker 执行，SSE 推送进度和生命周期事件
- Key behavioral constraints:
  - 单机部署，任务持久化到 SQLite
  - 外部 Spider / LLM / RAG 在测试中默认 fake
  - Session 有 `alive/frozen/purged` 生命周期
  - SSE 使用 `Last-Event-ID` 做 replay

### Constraints
- 文档必须以当前实现为准，不复制过时 spec 规划字段
- 文档验证以实际命令、测试和代码为证据，而不是口头描述
- 本任务不应把 warning 误写成功能缺陷，也不能掩盖真实限制

---

## 3. Technical Design

### 3.1 Module Structure

**Files to Create/Modify:**
```text
README.md
docs/CHANGELOG.md
docs/troubleshooting.md
dev_spec.md
```

**Per-file Change Intent**:
| Path | NEW/MODIFY | Required Change | Linked AC / Residual |
|------|------------|-----------------|----------------------|
| `README.md` | `MODIFY` | 重写为可执行的入门与使用文档，覆盖 setup/config/run/test/API flow/SSE/known limits | `AC1`, `AC2`, `AC4` |
| `docs/CHANGELOG.md` | `MODIFY` | 增补 2026-03-21 文档/测试交付记录，反映当前功能与 contract 收口 | `AC2`, `AC4` |
| `docs/troubleshooting.md` | `NEW` | 汇总高频问题、错误码、SSE 验证限制、warning 说明与排查建议 | `AC3`, `AC4` |
| `dev_spec.md` | `MODIFY` | 更新 `P5-4` 行与 Progress Record，记录文档交付验证结果 | `AC5` |

### 3.2 Class & Interface Design

**Primary Entry Point**: 文档本身，无新增生产接口。

**Documentation Contract Checklist**:
- README 必须列出：
  - 环境要求
  - 初始化命令
  - `.env.example` 到 `.env` 的复制与必填项
  - 启动 API 命令
  - 最小会话 -> strategy -> generate -> SSE 演练
  - 分层测试命令
- Troubleshooting 必须列出：
  - `SESSION_FROZEN`
  - `SESSION_PURGED`
  - `SPIDER_COOLDOWN_ACTIVE`
  - `INVALID_LAST_EVENT_ID`
  - `BUDGET_EXCEEDED`
  - `JOB_MAX_RETRIES_EXCEEDED`
  - `test_sse_uvicorn.py` 在受限环境可能 skip
  - 当前已知第三方依赖 warning 的性质和影响边界

### 3.3 Algorithm & Logic Flow

**Core Flow**:
```
Read current implementation truth
  -> compare README / CHANGELOG / docs with code and test evidence
  -> update user-facing docs with exact commands and contracts
  -> run doc-aligned validation commands
  -> update dev_spec progress row + P5-4 progress record
```

### 3.4 Implementation Checklist
- [ ] 盘点 README 当前缺口，并按真实实现补齐快速开始和 API 流程
- [ ] 更新 CHANGELOG，记录当前版本相对 2.0.0 的收口内容
- [ ] 新建或补齐故障排查文档
- [ ] 核对 `dev_spec.md` 的 `P5-4` 状态与交付记录
- [ ] 运行最小验证命令并记录结果

**Error Classification Rules**:
- 文档信息缺失或命令错误 -> 本任务直接修复
- 第三方依赖 warning 或环境限制 -> 记录为 known limitation，不扩大为代码开发

### 3.5 Error Handling Strategy

**Failure Mapping**:
```
Doc mismatch
├── Command mismatch -> fix README / troubleshooting
├── API contract mismatch -> fix docs using router/spec/test evidence
└── Residual status mismatch -> fix dev_spec + docs together
```

---

## 4. Test Strategy

### Required Validation Commands
- `pytest tests/unit/test_project_setup_acceptance.py -q`
- `pytest tests/e2e/test_session_flow.py tests/e2e/test_strategy_api.py tests/e2e/test_generation_api.py tests/e2e/test_resume_and_lifecycle.py tests/e2e/test_sse_api.py tests/e2e/test_error_contracts.py tests/e2e/test_idempotency_and_concurrency.py -q`
- 如环境允许，再运行 `pytest tests/e2e/test_sse_uvicorn.py -q`；若受限 skip，文档需说明原因

### Validation Focus
- 文档中的命令在仓库中真实存在且与当前路径匹配
- README API 示例能对齐当前路由和阶段约束
- Troubleshooting 对错误码/状态码/建议动作的描述与 `router.py` 一致
- `dev_spec.md` 的 `P5-4` 进度记录与实际验证结果一致

### Acceptance Mapping
- `AC1`: README 命令与 `setup_env.sh` / `uvicorn app.main:create_app --factory` / pytest 命令一致
- `AC2`: README + CHANGELOG + docs/troubleshooting 对齐 `router.py` / `app.main.py` / E2E 证据
- `AC3`: `docs/troubleshooting.md` 覆盖高频问题与限制
- `AC4`: README / troubleshooting / CHANGELOG 明确 residual 状态与测试证据来源
- `AC5`: `dev_spec.md` 的 `P5-4` 行与 Progress Record 更新完成

---

## 5. Notes For Coder

- 这是文档任务，不要顺手改业务逻辑，除非发现文档无法如实描述当前行为且必须以极小改动修复明显错字或接口元数据
- 若发现 API 文档与实现冲突，优先以实现 + 自动化测试为准修正文档
- 若 `test_sse_uvicorn.py` 在当前沙箱 skip，不把它记成失败；在文档中写清是环境约束
- 进度同步时，若本轮未发现新的 `OPEN residual`，`§9.4` 不必新增条目，但 `P5-4` 的 Progress Record 需要写明“无新增 OPEN residual”
