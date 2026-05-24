# 请求生命周期全链路流程图

日期：2026-05-18

基于 `20260517_restructure0.1.0.md` 和 `2026-05-16-frontend-scope-v1-v2-alignment.md` 的架构设计。

---

## 1. 主流程：用户请求 → 结果展示

```mermaid
sequenceDiagram
    autonumber
    participant U  as 用户浏览器
    participant CO as Conversation Orchestrator
    participant M  as WorkflowRunManager
    participant DB as SQLite<br/>(workflow_runs / steps / events / artifacts)
    participant Q  as Job Queue<br/>(jobs 表)
    participant W  as Job Worker
    participant CB as Context Builder
    participant EX as Step Executor<br/>(Spider / RAG / LLM)
    participant AS as Artifact Store
    participant EV as Event Log (SSE)

    %% ── 1. 用户发消息 ──────────────────────────────────
    U->>CO: POST /threads/{id}/messages<br/>{ "text": "帮我生成防晒衣笔记" }
    CO->>CO: 保存 message 到 creator_messages
    CO->>CO: Intent Router → start_workflow

    %% ── 2. 创建 Run / Step / Job ────────────────────────
    CO->>M: start_run(thread_id, message_id, request)
    M->>DB: BEGIN IMMEDIATE<br/>INSERT workflow_runs (status=created)<br/>INSERT workflow_steps (pending × N)<br/>INSERT workflow_events (run_created)<br/>COMMIT
    M->>DB: UPDATE workflow_runs (status=running)
    M->>DB: UPDATE creator_threads (active_run_id)
    M->>Q: INSERT jobs (status=queued, run_id, step_id)
    M-->>CO: run_id, job_id
    CO-->>U: { message, assistant_reply, active_run_snapshot }

    %% ── 3. Worker lease job ──────────────────────────────
    W->>Q: SELECT … WHERE status=queued FOR UPDATE (lease)
    Q-->>W: job row
    W->>DB: UPDATE jobs (status=running, leased_at, lease_expires_at)
    W->>M: start_step(run_id, step_name, job_id)
    M->>DB: BEGIN IMMEDIATE<br/>UPDATE workflow_steps (status=running)<br/>INSERT workflow_events (step_started)<br/>COMMIT

    %% ── 4. SSE 推进度（step_started）────────────────────
    EV-->>U: SSE: step_started { step: "discovery.spider_search" }

    %% ── 5. 执行 Spider / RAG / LLM ──────────────────────
    W->>CB: build_context(run_id, step_name)
    CB->>DB: load run + constraints + relevant messages + prior artifacts
    CB-->>W: StepContext

    W->>EX: execute(StepContext)
    Note over EX: ① Spider 搜索（分页，每页触发 task_progress 事件）<br/>② RAG 索引与召回<br/>③ LLM 策略合成 / 并行笔记生成

    %% ── 6. commit guard ─────────────────────────────────
    EX-->>W: result
    W->>DB: SELECT run.status  ← commit guard
    alt run.status = cancelling / cancelled
        W->>M: cancel_step(run_id, step_name)
        M->>DB: UPDATE step/run → cancelled + event
    else run.status = running
        W->>AS: save artifact (strategy / notes / rag_result …)
        AS->>DB: INSERT workflow_artifacts
        W->>M: complete_step(run_id, step_name, artifact_refs)
        M->>DB: BEGIN IMMEDIATE<br/>UPDATE workflow_steps (status=succeeded)<br/>UPDATE workflow_runs (current_step, artifact_version)<br/>INSERT workflow_events (step_completed)<br/>COMMIT
    end

    %% ── 7. SSE 推结果 ────────────────────────────────────
    EV-->>U: SSE: step_completed / workflow_task_completed
    U->>U: 读取 artifact_refs → 渲染策略卡片 / 笔记列表

    %% ── 8. Run 完成 ──────────────────────────────────────
    M->>DB: UPDATE workflow_runs (status=succeeded, completed_at)<br/>INSERT workflow_events (run_succeeded)
    EV-->>U: SSE: run_succeeded
```

---

## 2. 页面刷新 / 断线恢复

```mermaid
flowchart TD
    A["用户刷新页面 / 重新打开对话"] --> B["GET /threads/{thread_id}"]
    B --> C["读取 active_run_id"]
    C --> D{"active_run_id 存在?"}

    D -->|否| E["显示空对话 / 欢迎语"]
    D -->|是| F["GET /workflow-runs/{run_id}/snapshot"]

    F --> G["恢复 run.status / phase / current_step<br/>constraint_version / artifact_version"]
    F --> H["读取 workflow_steps → 恢复进度条"]
    F --> I["读取 workflow_artifacts → 渲染已完成产物"]

    G & H & I --> J["GET /threads/{id}/events?after_event_id=last_seen"]
    J --> K["SSE replay 补发 last_event_id 之后的事件"]
    K --> L["前端按事件更新 UI，不从 job/event 反推状态"]

    style F fill:#dbeafe,stroke:#3b82f6
    style G fill:#dcfce7,stroke:#16a34a
    style H fill:#dcfce7,stroke:#16a34a
    style I fill:#dcfce7,stroke:#16a34a
```

---

## 3. 状态权责分层

```mermaid
flowchart TD
    subgraph 用户层
        U["用户消息<br/>creator_messages"]
        T["Thread<br/>active_run_id 指针"]
    end

    subgraph 业务状态层["业务状态层（唯一真相）"]
        WR["WorkflowRun<br/>status / phase / current_step"]
        WS["WorkflowStep<br/>status / attempt_count / checkpoint"]
        CT["WorkflowChildTask<br/>slot 并行生成状态"]
    end

    subgraph 技术执行层
        J["Job<br/>队列 / lease / retry"]
    end

    subgraph 可观察层
        EV["WorkflowEvent<br/>SSE / replay / 审计"]
        AR["WorkflowArtifact<br/>strategy / notes / rag_result"]
        WC["WorkflowConstraint<br/>用户补充约束"]
    end

    T --> WR
    WR --> WS
    WS --> CT
    WS --> J
    WR --> AR
    WR --> EV
    WR --> WC
    U --> WC

    J -.->|"回写 step 状态（不作业务真相）"| WS
    EV -.->|"SSE 推送，不反推状态"| U

    style WR fill:#fef9c3,stroke:#ca8a04,color:#000
    style WS fill:#fef9c3,stroke:#ca8a04,color:#000
```

---

## 4. 中断 / 取消竞态保护

```mermaid
sequenceDiagram
    participant U  as 用户
    participant API as API Route
    participant M   as WorkflowRunManager
    participant W   as Worker / Step Executor

    U->>API: POST /jobs/{id}/cancel
    API->>M: cancel_run(run_id)
    M->>M: BEGIN IMMEDIATE<br/>run.status = cancelling<br/>queued/retrying jobs → cancelled<br/>COMMIT + event: cancel_requested

    Note over W: Worker 此时正执行 LLM 调用（无法立即中断）

    W-->>W: LLM 返回结果
    W->>M: commit guard: SELECT run.status
    M-->>W: status = cancelling

    W->>M: cancel_step(run_id, step_name)
    M->>M: BEGIN IMMEDIATE<br/>step.status = cancelled<br/>run.status = cancelled<br/>COMMIT + event: cancelled

    Note over W: 不写 artifact，不推进下一步
    M-->>U: SSE: cancelled
```

---

## 5. 数据表权责一览

| 表 | 职责 | 权威性 |
|---|---|---|
| `creator_threads` | 对话容器，持有 `active_run_id` 指针 | 对话路由入口 |
| `creator_messages` | 用户可见对话时间线 | 对话历史 |
| `workflow_runs` | **整体业务状态唯一真相** | ★ 最高 |
| `workflow_steps` | **细粒度执行节点唯一真相** | ★ 最高 |
| `workflow_child_tasks` | 并行生成 slot 状态 | ★ 并行唯一真相 |
| `jobs` | 队列调度 / lease / retry（技术层） | 技术执行 |
| `workflow_events` | SSE 推送 / 断线 replay / 审计 | 可观察，不反推状态 |
| `workflow_artifacts` | 结构化产物引用（strategy / notes / rag）| 产物存储 |
| `workflow_constraints` | 用户运行中补充约束（归一化） | 约束版本管理 |
