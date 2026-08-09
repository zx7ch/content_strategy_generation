# Lite Release Preparation

**Status:** in progress  
**Scope:** Lite Content Research release candidate  
**Decision date:** 2026-08-09

## Release rule

Every P0 gate must pass before release.  If a gate encounters an exception,
failed assertion, abnormal process exit, or an external-call count that exceeds
its declared budget, stop all later gates immediately.  Preserve the failing
run and logs, then analyse the failure before any retry or code change.

A successful demonstration is not a substitute for a failed P0 gate.

## P0 gates

| Gate | User-visible success criterion | Validation boundary |
| --- | --- | --- |
| P0-1 New research happy path | A user can create research without developer intervention and receives a readable final report. | The only gate allowed to make a real Spider and LLM run. |
| P0-2 LLM configuration, failure, and recovery | The model can be configured and validated; failures are understandable; the user can continue the original run. | Validate configuration and recovery without repeating completed Spider work. |
| P0-3 Restart and historical replay | A historical run opens directly after restart and makes no external call. | External-operation delta must be zero. |
| P0-4 Three report states | Complete conclusions, directional conclusions, and evidence-only reports each clearly show evidence, gaps, and limits. | Deterministic fixtures and UI/API regression only. |
| P0-5 Citation availability | "View original note" and "Evidence details" work for the demo and formal-acceptance samples; an unavailable source states why. | No external call; test both available and unavailable source states. |

## Packaging boundaries

1. An upgrade preserves the user's existing data directory and its historical
   records.  The installer and runtime must not clear that directory.
2. No developer-machine `data/` directory may be manually copied into a
   release directory or into a user data directory.  Test records must never
   ship.  If a demo is shipped, it must be explicit, isolated, and labelled as
   demo data.

## Execution order

1. Run P0-4, P0-5, and the packaging audit using deterministic data.
2. Run P0-2 and verify recovery semantics.
3. Perform the single real P0-1 happy-path acceptance run.
4. Restart the local runtime and run P0-3 against the resulting historical run.
5. Record command output, run IDs, external-operation deltas, and release
   decision for every gate in this document or its linked acceptance record.

## Explicit non-goals

- A report-only demo run is not required to synthesize an execution Trace.
- This preparation does not alter or delete existing user history.
- This preparation does not use a demonstration result to claim that a real
  external dependency is healthy.
