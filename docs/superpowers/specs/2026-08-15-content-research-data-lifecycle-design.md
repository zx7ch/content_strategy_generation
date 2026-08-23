# Content Research Data Lifecycle Design

## Goal

Make every completed Content Research Lite run auditable and recoverable across
Runtime restarts, while keeping credentials and provider-session data private.

The companion visual release record is
[`docs/release/2026-08-15-content-research-data-lifecycle-fix.html`](../../release/2026-08-15-content-research-data-lifecycle-fix.html).

## Confirmed product decisions

- A user changing research directions or returning to the checklist archives the
  prior run. It never deletes collected evidence or the report.
- Historical research is retained indefinitely. Permanent deletion is a later,
  explicit user action with confirmation; it is not part of this P0 patch.
- Candidate and selection information is a user-facing research feature, not
  merely a developer trace. It must show only safe note metadata and reasons.
- Runtime diagnostics are developer-facing observation. They must expose the
  build identity and resolved storage location but never secrets.
- Runtime configuration is minimal. Content Research LLM and Xiaohongshu
  credentials are configured through Creator and stored in local SQLite.

## Data contract

The existing SQLite DB remains the sole source of truth. A run stores:

1. discovery pages in `content_research_stage_checkpoints` (`collect_page`);
2. detail candidates and revisions in checkpoints (`detail` and
   `selection_revision`);
3. safe note projections in directional packets and source projections;
4. results in snapshots, decisions, drafts, and publications;
5. operation-level trace facts in observation events.

The user-facing evidence endpoint reads the persisted directional packet model
and checkpoint candidate/selection data. It must not return cookies, provider
tokens, raw session payloads, or LLM credentials.

## P0 changes

### Archive instead of delete

`end_content_research` is an archive transition. It clears only the active run
pointer for the thread and appends an `content_research_archived` event. It
does not call `WorkflowStore.delete_run` or `SQLiteContentResearchStore.delete_workflow`.
The Creator copy must say the research was archived and may be reviewed later.

### Runtime storage observability

The packaged Runtime computes a stable data home under macOS Application
Support. Startup logs and `/health` expose a `runtime_diagnostics` object with:

- runtime build/version identity;
- resolved absolute SQLite path;
- database existence, byte size, and last-modified timestamp.

The diagnostics response is safe by construction and does not serialize any
configuration value, credential, cookie, request payload, or user content.

### Candidate audit read model

The existing direction evidence route returns safe candidates, selections,
exclusions, and packets. The frontend adds a typed API helper and a dialog for
the requested direction. It presents title, author, link, retrieval query,
detail status, selected/excluded state, and reasons. Evidence-only reports
surface this entry point prominently. JSON export is local: the browser
downloads the already-safe read model without a new raw-data endpoint.

### Minimal Runtime configuration

The runtime config template is reduced to comments and optional `LOG_LEVEL`.
On upgrade, legacy `config.env` is copied to a timestamped backup and rewritten
to the minimal template. API keys, cookies, model/provider fields, storage
paths, web frontend fields, V2, RAG, alerting, and generation settings are no
longer active Runtime configuration. Runtime-owned data paths continue to be
forced after config loading.

## Non-goals

- Changing evidence relevance/admission policy; the observed `related 0 /
  admitted 0` result is a valid product judgement to inspect, not a persistence
  fix.
- Uploading research or credentials to a remote server.
- Implementing the separate permanent-delete UI in this patch.

## Acceptance

1. Legacy config values cannot alter Runtime-owned storage paths.
2. A frozen Runtime exposes its resolved DB path and build identity without
   secrets.
3. A persisted fixture run survives Runtime restart and returns its candidate,
   trace, and report data.
4. Returning to the checklist archives the run and preserves every research
   table/API read path.
5. Evidence-only UI opens a safe candidate inspection dialog and exports the
   same safe data as JSON.
