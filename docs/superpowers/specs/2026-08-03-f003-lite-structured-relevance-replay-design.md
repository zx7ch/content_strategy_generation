# F003 Lite Structured Subject, Query Compilation, and Packet-Only Recovery Design

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
provider ranking, or modify Creator-side admission logic. For new Lite runs it
does define the minimum deterministic query compilation, fallback, and run-level
physical deduplication contract needed to make the structured subject usable by
the shared Spider pipeline.

## Lite Delivery Boundary

Lite ships only the following core path:

1. schema-constrained subject generation and backend trust validation;
2. compact Brief confirmation of core object, intent, and context;
3. at most two primary QueryGroups, normalized before identity and deduplicated;
4. at most one precompiled deterministic coverage fallback;
5. run-level physical deduplication of equivalent searches and note details,
   while retaining all direction and QueryGroup hit lineage;
6. deterministic core-object and explicit-user-focus coverage checks;
7. checkpoint replay that consumes persisted candidates and packets before any
   new provider call;
8. focused tests for valid, ambiguous, duplicate-query, fallback, and replay
   paths.

Lite does not ship automatic multi-core-entity decomposition, a visual subject
editor, embeddings, an adaptive global budget, multilingual query expansion,
general negation planning, or repeated LLM query rewriting. The formal schema
may represent future capabilities, but the Lite executor must not activate them.

## General Subject Structure

This design is the formal F003 subject contract, not a Lite-only exception.
Pre-research advances from one free-text confirmation to a schema-constrained
`subject_structure` that works for unpredictable brand, category, SKU, scene,
trend, and mixed-language input:

```text
canonical_subject
subject_type
core_entities[{canonical_name, raw_mentions[]}]
research_intents[]
context_modifiers[]
synonym_groups{}
ambiguities[]
resolution_state
```

The Pre-research LLM produces this structure. F003 does not maintain a fixed
business category vocabulary. The backend validates types, normalization,
length/count limits, non-empty core entities, synonym ownership, and duplicate
or overlapping groups. Each `raw_mentions` value must be grounded in the
normalized user input; `canonical_name` may normalize a grounded mention but
cannot replace that grounding. It records Provider, model, Prompt version,
schema version, and input fingerprint beside the result.

The LLM proposes structure but does not decide whether collection may start.
The backend returns `needs_confirmation` and does not compile QueryGroups when
any of these deterministic conditions holds:

- model output is malformed after at most one schema-repair attempt;
- `canonical_subject`, `core_entities`, or grounded `raw_mentions` is empty;
- an entity or synonym group is orphaned, duplicated, or contradictory;
- the LLM reports an unresolved ambiguity or multiple plausible meanings;
- Lite receives more than one primary core entity;
- the compiled core query would contain only an intent or context modifier.

Brand and SKU input is not intrinsically ambiguous. It requires confirmation
only when the structure cannot establish a grounded brand/product/model
relationship. Numeric LLM self-confidence is not a collection gate.

Creator renders a compact confirmation line such as “核心对象：防晒服饰｜意图：
穿搭｜场景：夏季”. A user edit invalidates the previous structure and runs
Pre-research structure generation again. Formal collection cannot start until
the structure is valid and confirmed.

Model failure, invalid structured output, or an empty/ambiguous core entity
converges to the existing model-recovery or Brief-confirmation boundary. The
system must not substitute a hard-coded category list or silently fall back to
matching the complete user sentence.

## Deterministic Query Compilation

After confirmation, a versioned compiler creates the complete query plan before
Spider starts. It does not construct a Cartesian product of entities, synonyms,
intents, and contexts.

- `Q1 core_intent`: the preferred grounded core entity plus the primary research
  intent for the direction.
- `Q2 user_focus`: the same core entity plus an explicit user focus or uncovered
  context. If there is no explicit focus, the compiler may use the direction's
  second frozen evidence facet. It must omit Q2 when that adds no new normalized
  meaning.
- `Q3 coverage_fallback`: the next frozen alias plus the uncovered intent or
  context. It is compiled and hashed up front but is inactive initially.

Query identity is computed after Unicode, case, whitespace, punctuation, and
confirmed-alias normalization and includes provider, sort, time window, and
candidate cap. Equivalent Q1/Q2 queries merge into one physical request while
retaining all logical roles and direction lineage. Synonyms are admission
equivalents and fallback material, not a default primary QueryGroup.

Lite freezes `primary_query_group_cap=2`,
`coverage_fallback_query_group_cap=1`, and the existing
per-QueryGroup `candidate_cap=20`. The normal discovery ceiling is therefore
40 candidates per direction and the fallback ceiling is 60, before canonical
source deduplication. Existing direction sample and detail limits remain the
single source of truth; specialist retries do not reset or multiply them.

## Coverage Fallback and Stopping

Q3 activates only after Q1/Q2 have exhausted their persisted candidate pools
and at least one frozen condition remains unsatisfied:

- minimum relevant eligible samples;
- minimum independent authors;
- direct core-entity support;
- coverage of an explicit user focus marked by the confirmed Brief;
- replacement capacity after invalid, unavailable, or blocking-field detail
  failures.

Raw discovery count never satisfies these conditions. The pipeline records
`discovered`, `deduplicated`, `relevant`, `detail_eligible`, and `admitted`
separately. Failure to cover an explicit focus after Q3 does not promote generic
evidence into that focus; the affected aspect remains structured insufficient
while independently supported findings may still publish under the existing
partial-report contract.

Q3 activation is a durable, idempotent checkpoint decision. Refresh and resume
cannot generate another fallback or rerun Q1/Q2.

## Run-Level Physical Deduplication

The logical direction plan and the physical provider-call ledger are separate.
Within one run:

- equivalent search identities execute once and fan their frozen hits out to
  every owning direction and QueryGroup;
- one canonical provider note ID is fetched once, even when selected by several
  groups or directions;
- every packet preserves the complete set of query/rank hits and direction
  consumers;
- each direction still enforces its frozen logical sample, author, and detail
  limits;
- Lite adds no adaptive run-wide budget in this delivery.

A retry resumes the same operation/checkpoint and never creates a fresh query
or detail budget. Existing persisted candidates and details are always consumed
before Q3 or another provider call is considered.

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
10. Q1 and Q2 that normalize to the same query produce one provider operation
    and retain both logical roles;
11. synonyms are not issued as a primary group, but a frozen alias may be used
    by Q3 after a recorded coverage gap;
12. the same query or note selected by multiple directions is physically
    collected once while all hit lineage remains replayable;
13. sample, author, core-object, and explicit-focus coverage independently drive
    deterministic fallback and partial-result behavior;
14. a refresh, user recovery, or specialist retry cannot reset candidate/detail
    budgets or create a different Q3.

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
