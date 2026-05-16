# Frontend Design Notes

This folder records product and interaction design decisions for the frontend.

The current frontend has two primary surfaces:

- `工作台`: the V2 growth console for brand, ingestion, topic pool, decision, publish, performance, and evaluation workflows.
- `创作台`: the conversation-first creation surface that bridges V1 workflow sessions with a chat-like user experience.

Static demo material:

- `brand-growth-console-demo.html`: standalone HTML prototype provided before the Next.js implementation pass. Open it directly in a browser for quick visual reference.

Related specs:

- [Deployment Spec](../deployment/deployment_spec.md): deployment-state architecture for the cloud frontend, local Agent Runtime, and cloud LLM inference model.

Key principle:

- `conversation thread` and `workflow session/job` must be modeled separately.
- A user should be able to continue chatting in the same thread while a long-running workflow job is still executing.
- Pause/resume/cancel controls are workflow-job controls, not replacements for the chat input.
