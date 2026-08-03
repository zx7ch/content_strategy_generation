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

## General Subject Structure

This design is the formal F003 subject contract, not a Lite-only exception.
Pre-research advances from one free-text confirmation to a schema-constrained
`subject_structure` that works for unpredictable brand, category, SKU, scene,
trend, and mixed-language input:

```text
canonical_subject
subject_type
core_entities[]
research_intents[]
context_modifiers[]
synonym_groups{}
```

The Pre-research LLM produces this structure. F003 does not maintain a fixed
business category vocabulary. The backend validates types, normalization,
length/count limits, non-empty core entities, synonym ownership, and duplicate
or overlapping groups. It records Provider, model, Prompt version, schema
version, and input fingerprint beside the result.

Creator renders a compact confirmation line such as “核心对象：防晒服饰｜意图：
穿搭｜场景：夏季”. A user edit invalidates the previous structure and runs
Pre-research structure generation again. Formal collection cannot start until
the structure is valid and confirmed.

Model failure, invalid structured output, or an empty/ambiguous core entity
converges to the existing model-recovery or Brief-confirmation boundary. The
system must not substitute a hard-coded category list or silently fall back to
matching the complete user sentence.

## Structured Relevance Contract

The confirmed structure is frozen into the Research Brief, RunPolicySnapshot,
and each requested direction's DirectionContract. The relevance contract
advances to `query_relevance_v2` and contains:

- normalized core-entity anchor groups and their frozen synonyms;
- intents and context modifiers as non-authoritative scope metadata;
- complete subject text for display and audit, not as the only gate;
- QueryGroup identities, allowed quote fields, matching mode, and structure
  generation identity.

Admission remains deterministic and performs no LLM call. A candidate is
relevance-qualified only when the existing query provenance and quote-field
checks pass and its direct quote matches a frozen core entity or one of that
entity's frozen synonyms. Intent and context terms may constrain query
generation and disclosure, but cannot independently admit evidence.

For the regression example `夏季防晒穿搭`, a valid structure may freeze the
core entity `防晒服饰`, synonyms such as `防晒衣` and `防晒服`, the intent
`穿搭`, and the context modifier `夏季`. The example does not create a
hard-coded sunscreen rule. Generic summer or outfit content, metrics-only,
URL-only, and search-result-only evidence remain rejected.

## Existing-Run Revision

The original `RunPolicySnapshot` and `DirectionContract` remain unchanged. For
an affected legacy run, the configured Pre-research model generates a
schema-valid structure from the already confirmed subject and locked query
plan. This is a model-only recovery operation and has no Spider capability.
After validation, the repair appends a completed `StageCheckpointRecord` with
stage name `relevance_revision`. Its payload records:

- workflow run and direction identity;
- base policy snapshot ID and hash;
- original relevance-contract hash and algorithm version;
- replacement subject structure, generation identity, relevance contract, and
  their hashes;
- revision reason `structured_subject_anchor_repair`;
- revision algorithm version `query_relevance_v2`;
- creation time.

The replay entry validates that the revision targets the run's current frozen
subject, snapshot, and query-group IDs. Admission uses the revision only when
those identities match. Structure/revision hashes and algorithm version
participate in admission checkpoint and decision fingerprints, so old
decisions cannot be mistaken for current decisions.

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

- Pre-research structure generation or schema validation fails;
- the base snapshot or query-group identity differs from the revision;
- the replacement contract is malformed or has an unsupported algorithm;
- completed selection or packet checkpoints are missing;
- no persisted packet is available;
- governed audit still finds insufficient admitted evidence.

These failures do not trigger collection or provider retries.

## Tests

TDD coverage must prove:

1. schema-valid structures are accepted for previously unseen Chinese,
   English, brand, SKU, scene, and mixed-language subjects without a category
   vocabulary lookup;
2. empty entities, orphan synonyms, duplicate groups, and malformed model
   output fail before formal collection;
3. Brief renders the compact core-entity/intent/context confirmation and a
   subject edit invalidates the previous structure;
4. a provenance-valid direct quote matching a frozen core entity or synonym
   qualifies;
5. a quote containing only intent/context terms or unrelated high metrics
   fails;
6. an existing v1 run can append a v2 revision and replay from packets;
7. mismatched subject/snapshot/query-group revisions fail closed;
8. provider-operation IDs/count and packet IDs are identical before and after
   replay;
9. the affected run produces admitted claims, non-zero citations, and a
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
