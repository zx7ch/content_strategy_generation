# Task 3 report — Bind continuation entrypoints to execution authority

## Delivered

- Added one service-level execution-authority guard for formal dispatch,
  direct worker execution, persisted continuations, and report publication.
- Legacy `start_formal_research`, `retry_formal_research`, and
  `resume_formal_research` now reject an `awaiting_scope_decision` run unless
  the persisted continuation authorization owns the scope and revision.
- Preserved the initial path: a confirmed Scope with no coverage snapshot can
  dispatch its first collection.
- Validated continuation operation against its authorization state and require
  the exact persisted authorization record.
- Hardened Lite report projection so a limited-report authorization must own
  the coverage continuation revision.
- Documented direct `source-collections` as diagnostic-only; it records trace
  observations but does not write formal evidence, coverage, or reports.

## Tests

Red/green regressions added in `tests/e2e/test_content_research_scope_api.py`:

- initial confirmed Scope dispatches first collection;
- awaiting coverage rejects legacy start/retry/resume and direct worker-service
  start;
- the public Lite report remains unavailable while the scope decision is
  pending.

Focused verification passed:

```text
pytest tests/e2e/test_content_research_scope_api.py tests/e2e/test_content_research_report_publication_timeline_api.py tests/unit/test_content_research_dispatch_worker.py -q
37 passed in 3.59s
```

## Notes

The existing source-collection API coverage already verifies that direct
collection leaves the runtime running and formal subagent task queued; Task 3
keeps that route deliberately outside the formal evidence chain.
