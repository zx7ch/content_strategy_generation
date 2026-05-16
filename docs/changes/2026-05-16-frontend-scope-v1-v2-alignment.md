# 2026-05-16 Frontend Scope Alignment for V1/V2

## Status

Accepted.

## Context

The product now has two primary frontend surfaces:

- `Creator Workbench` (`/creator`) for conversation-first content creation.
- `Workspace Console` (`/brands`, `/topic-pool`, `/decisions`, `/publish`, `/performance`, `/evaluation`, etc.) for the brand growth operating loop.

The existing V1 spec contains both `EDITING MODE` and `EXPLORATION MODE`. The current frontend Creator Workbench, however, is designed around chat + long-running workflow execution, not the exploration branch/roll/candidate workspace.

The existing V2 spec defines the full brand growth loop and a future collaborative runtime profile. The current deployment direction is local-first: cloud-hosted frontend, local Agent Runtime, and cloud LLM inference.

## Decision

V1 maps to the Creator Workbench. The Creator Workbench MVP will use V1 `EDITING MODE` first:

```text
user message
-> workflow session
-> strategy job
-> generation job
-> SSE progress
-> generated notes
```

V1 `EXPLORATION MODE` is deferred. It remains a designed capability, but it is not part of the initial Creator Workbench MVP. If introduced later, it must become an explicit Creator Workbench sub-mode with:

- topic exploration entrypoint
- candidate cards
- refine / refresh actions
- candidate confirmation
- handoff into strategy generation

V2 maps to the Workspace Console. The Workspace Console MVP focuses on the operator loop:

```text
brand setup
-> ingestion
-> topic pool
-> decision
-> publish record
-> performance feedback
-> evaluation
```

The local-first deployment profile is the first real deployment target. The collaborative cloud runtime profile remains a future migration path.

## Consequences

- `/creator` should first be wired to existing V1 strategy/generation/session/job/SSE APIs.
- `/creator` should not claim exploration branch support until the exploration UI and backend contracts are implemented.
- The chat input remains independent from workflow task controls; running jobs should not block follow-up messages in the same thread.
- The Workspace Console should prioritize real local runtime data access before advanced cloud collaboration features.
- Postgres remains important for the collaborative cloud runtime, but it is not required for the local-first MVP.

## Deferred Capabilities

V1 deferred:

- `ExplorationPlanner`
- `ExplorationStateStore`
- exploration branches / rolls / candidates
- exploration candidate cards
- refine / refresh / confirm handoff UI

V2 deferred:

- full multi-user workspace membership and RBAC
- Postgres as default system of record for the collaborative cloud runtime
- complete contextual bandit policy surface
- full offline replay diagnostics beyond the MVP evaluation view

## Next Implementation Priority

1. Make cloud-hosted frontend detect and connect to the local Agent Runtime.
2. Move local runtime API reads from Server Components to browser-side clients.
3. Wire `/creator` to V1 Editing Mode: sessions, strategy, generation, jobs, and SSE.
4. Surface generated outputs into the Workspace Console where appropriate.
5. Introduce V1 Exploration Mode only after the core creation workflow is reliable.
