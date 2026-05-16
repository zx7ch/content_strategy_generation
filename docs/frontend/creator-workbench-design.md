# Creator Workbench Design

## Goal

`创作台` is the user-facing bridge between V1 long-running content generation and V2 brand growth workflows.

It should make one product promise clear:

> A long-running generation task can execute in the background while the user keeps talking in the same conversation window.

This matters because V1 currently implements a `workflow session` runtime, while the product experience needs a `conversation thread` runtime above it.

MVP scope:

- The first Creator Workbench implementation uses V1 `EDITING MODE`: strategy generation and content generation.
- V1 `EXPLORATION MODE` is deferred. It should later become an explicit topic exploration sub-mode rather than being implied by the current chat UI.

## Current Frontend Shape

Route:

- `/creator`

Top-level layout:

- Left side: conversation history, new thread button, model/API key settings placeholder.
- Main area: active conversation, background task status strip, chat messages, input box.

Important interaction decision:

- The chat input remains usable while a background task is running.
- The task status strip owns `停止 / 恢复 / 完成` controls.
- The send button always means "send this chat message", not "stop the task".

This avoids mixing two different concepts:

- chat message submission
- workflow job control

## Conceptual Runtime Model

```text
ConversationThread
  id
  title
  messages[]
  active_workflow_session_id?
  active_job_id?

WorkflowSession
  session_id
  stage: init | strategy | generation | completed | failed
  lifecycle_state: alive | frozen | purged

WorkflowJob
  job_id
  job_type: strategy | generate
  status: queued | paused | running | retrying | succeeded | failed | cancelled
```

The frontend should treat these as separate but linked records:

- One conversation thread may reference one active workflow session.
- A workflow session may have multiple jobs over time.
- A chat message may be linked to a workflow job as a constraint, status query, pause/resume command, or normal chat turn.

## Interaction Cases

### 1. User Starts a Content Task

```text
User message
-> intent router identifies generation/strategy intent
-> create or reuse workflow session
-> enqueue strategy/generate job
-> attach job_id to conversation thread
-> render task status strip
```

### 2. User Continues Chatting While Task Runs

```text
User message during running job
-> keep chat input enabled
-> classify intent
   -> add_constraint
   -> ask_status
   -> pause_job
   -> cancel_job
   -> free_chat
```

Current frontend implementation mocks this behavior. The backend still needs a real conversation/message store and intent router.

### 3. User Pauses or Resumes

Pause is not the same as cancel.

- `pause`: queued/retrying jobs can become paused; running jobs should stop only at safe stage boundaries.
- `resume`: paused jobs return to queued and can be leased by the worker again.
- `cancel`: unfinished jobs become cancelled and should not later write succeeded results.

Current backend already has partial queue primitives for `paused`, `resume`, and `cancelled`, but lacks a public, complete conversation-level control API.

## Backend APIs Needed Next

Minimum APIs to make the frontend real:

- `POST /threads`
- `GET /threads`
- `GET /threads/{thread_id}`
- `POST /threads/{thread_id}/messages`
- `POST /threads/{thread_id}/workflow`
- `POST /jobs/{job_id}/pause`
- `POST /jobs/{job_id}/resume`
- `POST /jobs/{job_id}/cancel`
- `GET /threads/{thread_id}/events`

SSE should eventually be thread-scoped, while preserving job/session event replay:

```text
thread event stream
  chat_message_delta
  workflow_stage_changed
  workflow_task_progress
  workflow_task_failed
  workflow_task_completed
  workflow_paused
  workflow_resumed
```

## Product Wording

Current truthful statement:

> V1 supports async workflow execution, state persistence, SSE progress, and resume for paused/frozen sessions. It does not yet fully support ChatGPT-style free conversation in the same window while a workflow task runs.

Target product statement:

> I am separating conversation thread from workflow session. The same chat window can stay interactive while a background job runs; user messages are routed by intent into status query, pause/resume/cancel, constraint update, or normal LLM chat.
