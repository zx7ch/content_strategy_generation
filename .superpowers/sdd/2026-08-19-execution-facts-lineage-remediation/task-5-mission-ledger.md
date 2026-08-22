# Task 5 Mission Ledger

| Field | Current value |
|---|---|
| Mission | A Creator user sees only server-executable Scope and recovery actions, and stale reads cannot replace the selected workflow's truth. |
| Current slice | 5A — truthful mutation projection; typed replay-input remediation implemented, awaiting independent re-review. |
| Contract IDs | STATE-5-1, STATE-5-5; AUTH-5-2..5; INV-5-1, INV-5-4; FAIL-5-1, FAIL-5-2, FAIL-5-5; ACC-5-1, ACC-5-4, ACC-5-5. |
| Acceptance RED | Watched fail on 2026-08-22: malformed `DirectionSelection`, extra/mismatched relevance copies, and cross-workflow/direction packet references all returned replay-eligible; the complete owned fixture remained eligible. |
| Last green proof | 2026-08-22: 101 focused Router/SQLite/lite-read/packet-replay tests passed; focused Ruff, compileall and `git diff --check` passed. RED history: 5 false-eligible cases failed before implementation; real admission then exposed the no-Scope ownership crash and passed after ownership inheritance. |
| Finding route | `IMPLEMENTATION_DEFECT` fixed: one immutable typed replay-input builder now owns publication eligibility, frozen direction/relevance/sample policy, typed selection, checkpoints, packet ownership and execution ownership. Projection carries Ready internally, guard returns it, and replay consumes it. Dead relevance-revision decoding was removed. Existing stale-Draft/public-field fixes remain green; concurrent mutation stays deferred `DEBT-5-1`. |
| Return point | Independent Slice 5A re-review after the typed replay-input acceptance gate; Task 4 publication-integrity semantics unchanged. |
| Next action | Commit the scoped remediation and run independent Slice 5A re-review before returning to Slice 5B. |
| Open risk | Deferred `DEBT-5-1`: multi-actor workflow mutation needs a durable claim/lease before any future multi-user or externally callable deployment. Task 4 report-retry crash integrity remains a separate release dependency. |
