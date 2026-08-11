# Lite Release Preparation

**Status:** release ready
**Scope:** Lite Content Research release candidate

## Release rule

Every P0 gate must pass before release. If a gate encounters an exception,
failed assertion, abnormal process exit, or an external-call count that exceeds
its declared budget, stop all later gates and analyse the failure before any
retry or code change.

## P0 gates

| Gate | User-visible success criterion | Result |
| --- | --- | --- |
| P0-1 New research happy path | A user can create research and receive a readable final report. | Passed |
| P0-2 LLM configuration, failure, and recovery | Model configuration is understandable and the original run can continue safely. | Passed |
| P0-3 Restart and historical replay | A historical run opens after restart without an external call. | Passed |
| P0-4 Three report states | Complete, directional, and evidence-only reports show evidence, gaps, and limits. | Passed |
| P0-5 Citation availability | Available and unavailable citations explain their navigation state. | Passed |

## Packaging boundaries

- The runtime keeps user data, configuration, databases, and model cache outside
  the executable bundle, so upgrades retain existing history.
- The release archive excludes local databases, the developer-machine `data/`
  directory, and local `.env` files. The bundled `config.env` is only a
  first-launch template.
- A packaged-runtime two-launch check confirmed that user configuration and
  historical database content survive a replacement launch.

## Acceptance evidence

- Controlled Creator browser acceptance passed all 29 cases.
- Runtime, package metadata, model configuration, presearch, formal workflow,
  report-publication, and Trace regressions passed in their focused suites.
- The single authorised live Spider and LLM acceptance run completed with a
  readable evidence-only report. The report safely disclosed insufficient
  evidence instead of fabricating a marketing conclusion.
- A backend restart replayed the persisted report with zero additional provider
  operations.
- The release archive passed its ZIP integrity check and repository diff passed
  whitespace validation.
