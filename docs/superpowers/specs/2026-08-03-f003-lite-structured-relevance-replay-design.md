# F003 Lite Structured Relevance and Packet-Only Recovery Design

## Goal

Fix the false `insufficient_admitted_evidence` result produced when Spider
successfully collects relevant notes but admission requires the complete
confirmed-subject phrase to appear in one direct quote. Recover the affected
Lite run from its persisted packets without another provider call.

## Confirmed Failure

Run `run_2eb08077d2334b0a84b0252c974868aa` collected 30 note-detail packets and
created 60 product-marketing claim candidates. All 32 Xiaohongshu operations
completed and none failed. Admission nevertheless rejected every candidate:

- the frozen subject anchor was `夏季防晒穿搭`;
- the frozen category-anchor and synonym sets were empty;
- 26 candidate statements contained `防晒`, but none contained the complete
  phrase `夏季防晒穿搭`;
- relevance-qualified, eligible, and independent-author counts consequently
  collapsed to zero;
- `sample_threshold_unmet` was a downstream consequence of zero relevance,
  not a lack of collected samples.

The provider-real author fallback is active and is not part of this failure.

## Scope

This repair covers the shared formal admission path used by Lite:

1. deterministic structured relevance contracts for new runs;
2. an append-only relevance-contract revision for an affected existing run;
3. packet-only replay of admission through report publication;
4. tests and acceptance evidence proving no Spider operation was repeated.

It does not add an LLM relevance classifier, loosen query provenance, change
Spider selection, or modify Creator-side admission logic.

## Structured Relevance Contract

The relevance contract advances to `query_relevance_v2`. It keeps the complete
normalized subject for audit, but treats it as a strong exact-match alternative
rather than the only useful anchor.

The deterministic category vocabulary gains a sunscreen category group:

- canonical category anchor: `防晒`;
- allowed product synonyms: `防晒衣`, `防晒服`, `防晒衫`, `防晒外套`;
- contextual terms such as `穿搭`, `搭配`, and `夏季` are not independently
  sufficient to pass admission.

For `夏季防晒穿搭`, the frozen contract therefore contains the complete
subject plus the `防晒` category group. A candidate is relevance-qualified
only when all existing provenance and quote-field checks pass and its direct
quote contains either:

- the complete subject anchor; or
- the canonical category anchor or one of its frozen synonyms.

Generic summer clothing, generic outfit, metrics-only, URL-only, and search
result evidence remain rejected. Directions without a recognized structured
category remain fail-closed on their complete subject anchor.

## Existing-Run Revision

The original `RunPolicySnapshot` and `DirectionContract` remain unchanged. The
repair appends a completed `StageCheckpointRecord` with stage name
`relevance_revision`. Its payload records:

- workflow run and direction identity;
- base policy snapshot ID and hash;
- original relevance-contract hash and algorithm version;
- replacement relevance contract and its hash;
- revision reason `structured_subject_anchor_repair`;
- revision algorithm version `query_relevance_v2`;
- creation time.

The replay entry validates that the revision targets the run's current frozen
snapshot and query-group IDs. Admission uses the revision only when those
identities match. The revision hash and algorithm version participate in the
admission checkpoint and decision fingerprints, so old decisions cannot be
mistaken for current decisions.

New runs freeze `query_relevance_v2` directly and do not need a revision.

## Packet-Only Replay

Recovery uses the existing
`ContentResearchService.replay_downstream_from_persisted_packets` boundary.
The replay path:

1. reads the completed selection and packet checkpoints;
2. loads the 30 existing directional evidence packets;
3. resolves and validates the append-only relevance revision;
4. recomputes relevance, eligibility, author independence, candidates, and
   admission decisions;
5. regenerates direction result, governance, snapshot, audit, publication, and
   the run-scoped Creator artifact projection.

The replay boundary has no provider callback. Canonical sources, packets,
selection, provider-operation checkpoints, and collection checkpoints remain
unchanged. A newer immutable report publication is appended while Creator
retains one run-scoped `artifact_result` timeline message.

## Error Handling

Replay fails closed without publishing a replacement report when:

- the base snapshot or query-group identity differs from the revision;
- the replacement contract is malformed or has an unsupported algorithm;
- completed selection or packet checkpoints are missing;
- no persisted packet is available;
- governed audit still finds insufficient admitted evidence.

These failures do not trigger collection or provider retries.

## Tests

TDD coverage must prove:

1. `夏季防晒穿搭` freezes `防晒` plus the allowed sunscreen synonyms;
2. a provenance-valid `防晒衣` title or content quote qualifies;
3. a quote containing only `夏季`, `穿搭`, or unrelated high metrics fails;
4. an existing v1 run can append a v2 revision and replay from packets;
5. mismatched snapshot/query-group revisions fail closed;
6. provider-operation IDs/count and packet IDs are identical before and after
   replay;
7. the affected run produces admitted claims, non-zero citations, and a
   readable latest report without a second Spider call.

## Acceptance

The repair is accepted when the affected run is replayed from its existing 30
packets and:

- at least three relevant eligible sources from at least two independent
  provider-real authors satisfy the frozen sample policy;
- the latest report is no longer `evidence_only_report` solely because of
  `query_subject_not_supported`;
- citations point to the persisted Xiaohongshu notes;
- provider-operation and packet identity sets are unchanged;
- targeted backend, report, trace, and frontend regression suites pass.
