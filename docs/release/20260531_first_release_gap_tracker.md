# XHS Creator First Release Gap Tracker

Date: 2026-05-31
Scope: first internal release last check.

This tracker records the release gaps found against the three goals in the first-release checklist. Each item must be implemented and verified with current-state evidence before release.

## Goal 1 - Topic Pool Reads Accepted Publish Candidates

- [x] `publish_candidate` artifacts are scoped by `workspace_id` and `brand_id`.
- [x] `/publish-candidates` filters by workspace, brand, and optional `thread_id` / `run_id`.
- [x] Creator thread creation carries the selected brand/workspace context into the workflow.
- [x] Accepted notes materialized into `publish_candidate` carry title, topic type, core hypothesis, predicted score, score type, and source.
- [x] Topic type and core hypothesis are derived from strategy/proposal/note payloads where available, not only defaulted.
- [x] Creator completion links to `/topic-pool` filtered to the just-completed run.

Verification:

- `pytest -q tests/e2e/test_creator_complete_workflow_v2.py ...` includes `test_publish_candidates_are_workspace_brand_and_run_scoped`.
- `npm run build` verifies the filtered Topic Pool page and Creator link compile.

## Goal 2 - Local-First Runtime Connection Layer

- [x] `/health` exposes `service`, `version`, `api_contract`, and `features`.
- [x] Frontend detector checks `MIN_BACKEND_VERSION` and `REQUIRED_API_CONTRACT`.
- [x] Runtime offline, version mismatch, contract mismatch, and API errors are distinguishable in user-facing states.
- [x] Creator and topic-pool client fetches include workspace/user headers instead of unauthenticated browser fetches.
- [x] CORS has an explicit allowlist and avoids wildcard methods/headers.
- [x] Runtime prewarm endpoint starts local embedding warmup after detector success.

Verification:

- `pytest -q tests/e2e/test_runtime_connection_layer.py`
- `npm run build`

## Goal 3 - Creator Workbench Experience

- [x] Completion copy points to Topic Pool, not Publish Records.
- [x] Completion ack includes a direct Topic Pool link filtered to the just-completed thread/run.
- [x] First embedding initialization is visible in workflow progress.
- [x] Embedding prewarm starts on app open / detector success, before the first user request when possible.
- [x] `add_constraint` ack includes scope, impact, and whether it can affect the current run.
- [x] If a constraint arrives after the generation phase, ack honestly says it will not affect the current output and offers rerun/regenerate.
- [x] Low-confidence constraint classification asks a clarification question instead of silently doing nothing.
- [x] Pause/resume release posture is decided and verified; retained for v1 with existing backend/UI command paths covered.

Verification:

- `pytest -q tests/unit/test_conversation_orchestrator.py`
- `pytest -q tests/integration/test_workflow_pause_cancel_jobs.py`
- `npm run build`
