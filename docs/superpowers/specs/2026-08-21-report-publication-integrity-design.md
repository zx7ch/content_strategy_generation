# Report Publication Integrity Design

## Confirmed product decision

If a published Content Research report is later found to depend on an execution attempt with an integrity failure, the system preserves the existing report and its frozen evidence. It must not silently delete, overwrite, or replace it.

The report becomes **integrity-flagged**: users can still read the historical artifact and its original Scope/Coverage/trace, but the UI and API state that it must not be used as a current research conclusion and requires manual handling.

## State rules

| Event | Publication state | User-visible effect |
|---|---|---|
| Normal materialization | `published` | Report is current and usable. |
| Failed materialization/retry | `retry_pending` | No new publication is created; retry is bound to the exact `publication_id`. |
| Integrity failure after publication | `integrity_flagged` | Historical report remains readable with a blocking integrity notice and recovery guidance. |
| Manual resolution | `superseded` or `published` successor | The original remains historical; a separately identified successor may become current. |

## Invariants

- `publication_id + retry intent + runtime finalizing state` are persisted atomically; recovery never selects a workflow-global latest report.
- A publication's frozen lineage is immutable. An integrity failure creates a separate append-only integrity event; it does not mutate historical Scope, Coverage, attempt identity, report content, or trace facts.
- Materialization refuses a publication already marked `integrity_flagged`; it cannot silently publish/re-publish it as healthy.
- API/read-model responses include `integrity_state`, `integrity_reason`, and safe recovery guidance.
- A later success is a separately identified successor publication, never an in-place replacement of the flagged report.

## Required failure-injection acceptance cases

1. Crash before and after each write in failure → retry scheduling; restart recovers only the same `publication_id`.
2. Two Scope publications exist; an older failed publication retries without touching the newer publication.
3. A published report's frozen attempt is later integrity-failed; the old report remains readable and is `integrity_flagged`, while materialization is rejected.
4. A manual successor is published; readers can distinguish the flagged original from the new current report.
