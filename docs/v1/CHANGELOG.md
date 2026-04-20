# Changelog

## [2.1.0] - 2026-03-22

### Added

- 文档交付完成：
  - `README.md` 现已覆盖环境初始化、配置、启动、API 主链路、SSE、测试命令与已知限制
  - 新增 `docs/troubleshooting.md`，汇总高频错误码、生命周期问题、SSE 重连说明与环境限制
- `dev_spec.md` 已同步 `P5-3` / `P5-4` 的真实完成状态与验证记录

### Changed

- 项目状态说明更新为当前真实交付结果：
  - unit: `205 passed`
  - integration: `31 passed`
  - e2e: `14 passed`
- README 中的运行与测试说明已对齐 `app/main.py`、`app/config.py`、`.env.example` 与当前路由契约

### Fixed

- 文档与实现进度不一致的问题：
  - `P5-3` 不再显示为未开始
  - residual backlog 当前无 `OPEN` 项的事实已在文档中明确说明
- README/API 说明不再遗漏以下当前行为：
  - `GET /sessions/{id}` 的预算字段
  - `Last-Event-ID` 非法值统一返回 `INVALID_LAST_EVENT_ID`
  - `session_frozen` / `session_purged` 生命周期事件可通过 SSE 观察

### Known Limitations

- `tests/e2e/test_sse_uvicorn.py` 在受限环境中可能因本地端口绑定限制而 skip
- E2E 仍使用 fake Spider / LLM / RAG，不替代 acceptance 级真实依赖验证
- 第三方依赖链仍可能产生 `websockets` 类弃用 warning，当前不影响核心功能

---

## [2.0.0] - 2026-02-27

### Major Changes

#### Content Strategy Model
- **Changed**: `ContentStrategy` 从简单的 `PlatformReport` 扩展为当前运行时策略模型
- **Added**: `positioning`, `target_audience`, `content_pillars`, `key_messaging`, `content_types`, `posting_strategy`, `data_source_quality`

#### Quality Threshold
- **Changed**: `quality_score` 触发 query expansion 的阈值从 `0.5` 调整为 `0.35`

#### Generation Phase - Similarity Check
- **Added**: 并行生成后的相似度检查机制
- **Added**: proposal 重选与 warning / rewrite 语义

### API Changes

- `POST /sessions/{id}/generate` 响应对齐当前 generation contract
- `GET /sessions/{id}` 纳入预算字段、reindex 状态与当前 job 信息
- `GET /sessions/{id}/events` 使用 `Last-Event-ID` 做 replay

### Error Handling

- **Added**: `GENERATION_PARTIAL_FAILURE`
- **Added**: `INSUFFICIENT_DATA`
- **Added**: 生命周期与 SSE 相关错误契约在后续交付中补齐

---

## [1.0.0] - 2026-02-26

### Initial Version

- 初始项目骨架
- Strategy / Generation / Session / Queue / API 路线图定义
