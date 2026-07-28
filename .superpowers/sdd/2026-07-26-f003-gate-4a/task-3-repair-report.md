# Task 3 Repair Report

Base reviewed: `e2b11db`.

## Root cause and red/green evidence

- **Payload contract regression:** removal of EvidenceBundle persistence also
  removed `evidence_bundle_id` and `evidence_bundle_ids` from the recursive
  report-payload validator.  RED: the new six-case unit matrix (Draft,
  FaithfulnessDecision, Publication × the two legacy keys) failed because no
  `ValueError` was raised.  GREEN: all six now reject nested legacy references.
- **Migration proof gap:** migration `0013` already dropped only the two
  aggregate tables and old snapshot column; its failure was missing a genuine
  persisted-artifact regression.  The replacement integration test creates a
  pre-0013 database state (ledger stops at `0012`, old tables/column present),
  then writes a complete Gate 2 artifact before invoking the real migration.
  It passed on the existing migration implementation, proving no production
  migration change was needed for preservation.
- **Duplicated test obfuscation:** the three test files each constructed the
  removed-name fragment independently.  They now import the one explicit
  `LEGACY_EVIDENCE_BUNDLE_FRAGMENT` test constant.

## Exact legacy preservation proof

Before migration, the test persists a workflow run with a trace, accepted
`EvidenceRecord`, `CanonicalSourceRecord`, completed `StageCheckpointRecord`,
governed snapshot citation group, report draft/decision/publication, and
materialized Creator artifact.  The old snapshot column is populated and the
old aggregate tables contain a row.  After real `0013` runs, the test proves:

- LiteReportReader returns `complete_verified_report` and citation `citation_7`.
- Canonical source URL, evidence-to-trace relation, trace workflow id, and
  checkpoint id remain readable.
- Both aggregate tables and the old snapshot column are absent.

## Verification

- RED: `pytest -q tests/unit/test_content_research_persistence_models.py` →
  `6 failed` before the validator restoration.
- GREEN: targeted unit/schema/migration/materializer/read-model suite →
  `32 passed`.
- `ruff check` on every changed Python file → passed (only the repository’s
  existing deprecated-settings warning).
- `git diff --check` → passed.
- Scan for the prior duplicated join expression in `tests/` → no matches.
- Live-code scan finds legacy bundle terms only in the payload rejection guard
  and migration `0013`; no EvidenceBundle model/store/service remains.

Patch commit hash is reported with the task handoff after this report is
committed.
