# Task 5 Mission Ledger

| Field | Current value |
|---|---|
| Mission | A Creator user sees only server-executable Scope and recovery actions, and stale reads cannot replace the selected workflow's truth. |
| Current slice | 5A — truthful mutation projection; review findings implemented, awaiting independent re-review. |
| Contract IDs | STATE-5-1, STATE-5-5; AUTH-5-2..5; INV-5-1, INV-5-4; FAIL-5-1, FAIL-5-2, FAIL-5-5; ACC-5-1, ACC-5-4, ACC-5-5. |
| Acceptance RED | Watched fail: raw confirmation of an older non-projected Draft created a Contract; GET Scope exposed nested `execution_authorization_id`; failed specialist tasks still projected and executed Repair. |
| Last green proof | 67 focused Router/SQLite, replay, and Scope-store tests plus 4 narrow acceptance tests passed on 2026-08-22; focused Ruff and `git diff --check` passed. One excluded pre-existing Task 4 integrity test still fails because its isolated DB lacks `workflow_runs`. |
| Finding route | `IMPLEMENTATION_DEFECT` fixed: latest Draft is checked inside the confirmation write transaction; Repair projection/guard/replay share complete durable preflight; public POST/GET projections omit legacy authorization fields. Concurrent mutation arbitration remains deferred `DEBT-5-1` under the confirmed local single-user boundary. |
| Return point | Independent Slice 5A re-review; Task 4 publication-integrity semantics unchanged. |
| Next action | Commit the scoped Slice 5A fixes, run independent review, then return to the Contract Pack checkpoint before Slice 5B. |
| Open risk | Deferred `DEBT-5-1`: multi-actor workflow mutation needs a durable claim/lease before any future multi-user or externally callable deployment. Task 4 report-retry crash integrity remains a separate release dependency. |
