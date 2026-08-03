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
5. normalized QueryGroup and canonical-note deduplication inside each direction,
   while retaining all QueryGroup hit lineage;
6. deterministic core-object and explicit-user-focus coverage checks;
7. checkpoint replay that consumes persisted candidates and packets before any
   new provider call;
8. focused tests for valid, ambiguous, duplicate-query, fallback, and replay
   paths.

Lite does not ship automatic multi-core-entity decomposition, a visual subject
editor, embeddings, an adaptive global budget, multilingual query expansion,
general negation planning, repeated LLM query rewriting, or cross-direction
single-flight collection. The formal schema may represent future capabilities,
but the Lite executor must not activate them. Cross-direction physical reuse is
a Gate 4B prerequisite before multiple directions are enabled for real parallel
collection, not part of Task 5I's single-direction release path.

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

### Conversational clarification

`needs_confirmation` is a product state, not an LLM failure. Creator keeps the
existing bottom composer and does not add an input field to the Pre-research
card. While a run is in `subject_needs_confirmation`, the composer placeholder
asks the user to clarify the research object and the submitted message is routed
to the same run as:

```json
{
  "action": "clarify_subject",
  "payload": {"clarification_text": "苹果品牌，关注年轻人的内容偏好"}
}
```

The message remains in the normal conversation timeline. The backend appends
the clarification to the Pre-research input, generates a new structure with a
new input fingerprint, and supersedes the prior unconfirmed structure for
execution without deleting its audit record. Clarification does not consume an
LLM failure-recovery attempt and cannot call Spider. A valid structure updates
the existing card in place with the compact confirmation line. A subject change
after formal collection starts requires a new run.

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

The existing per-direction `detail_fetch_cap=30` is clarified as the
**direction detail evaluation cap**: the number of distinct note details a
direction may evaluate, whether a detail was fetched for that direction or
reused from the same run. Trace separately counts physical detail calls. A note
shared by two directions therefore consumes one physical call and one logical
evaluation slot in each direction.

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

## Gate 4B Cross-Direction Physical Deduplication

The following contract is retained for the formal multi-direction release but
is explicitly deferred from Task 5I. Task 5I continues to deduplicate normalized
Q1/Q2 queries and canonical note IDs within one direction using the existing
directional pipeline.

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

### Single-flight ownership and reusable artifacts

Deduplication is transactional rather than a check-then-call cache. A run-level
collection ledger owns each physical operation through
`reserved -> running -> completed | failed | outcome_unknown`. The first
consumer atomically reserves the operation; concurrent consumers bind to it and
wait for or reuse its terminal result. Ownership belongs to the run, not to the
specialist that first requested it.

Provider operation checkpoints store only lifecycle and a safe summary. The
reusable data is stored separately as a run-scoped collection artifact:

- search artifacts reference the persisted candidate manifest/pages;
- detail artifacts reference the canonical source and normalized detail packet;
- `collection_binding` records which direction and QueryGroup consume an
  artifact and whether it used a direction evaluation slot.

Shared failures are one physical fact. `auth_required` pauses the run once;
`outcome_unknown` blocks every consumer from a blind retry; provider automatic
retry runs once for the physical operation; `note_unavailable` marks the shared
candidate unavailable while each direction independently selects a replacement.
Cancellation of the whole run owns cancellation of physical work; completion or
failure of one child does not delete a shared artifact required by siblings.

## Observability and Checkpoint Contract

The existing newest-first workflow timeline remains unchanged. The new design
adds durable logical checkpoints without turning them into extra workflow
stages:

| Stage | Purpose | Safe Trace projection |
| --- | --- | --- |
| `subject_structure` | generation identity and deterministic validation | resolution state, type, reason codes, short structure hash |
| `query_plan` | frozen Q1/Q2/Q3 roles and normalized deduplication | primary/fallback counts, merged count, short plan hash |
| `coverage_decision` | staged eligibility and focus coverage | discovered/deduplicated/relevant/detail-eligible/admitted counts |
| `fallback_decision` | durable Q3 activation | activated/exhausted and stable reason codes |
| `relevance_revision` | historical packet-only v2 repair | revision state and zero-provider-call replay marker |

Task 5I leaves existing specialist-scoped physical `operation` checkpoints and
provider counters unchanged. It adds safe structure, plan, coverage, fallback,
and historical-revision detail only. `collection_binding`, run-owned physical
operation identities, consumer/reuse counts, and cross-direction double-entry
accounting are delivered with the Gate 4B single-flight ledger.

Q3 activation and collection reuse are normal control flow, not errors or
retries. `fallback_exhausted`, an actual provider failure, or an invalid subject
may produce a visible reason code. Refresh and resume must preserve structure,
plan, fallback, physical operation, artifact, and binding identities.

The contract versions `subject_structure_schema_version`,
`query_compiler_version`, `coverage_policy_version`,
`collection_ledger_schema_version`, and `query_relevance_version`. Old Trace
records remain readable and are not rewritten to pretend these checkpoints
existed. New runs use the new versions; eligible history only appends
`relevance_revision` and downstream replay records.

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
15. subject clarification uses the existing composer, stays in the same
    Pre-research run, and does not consume model-recovery budget;
16. Trace exposes safe plan, coverage, fallback, and direction-evaluation
    summaries without query, note ID, prompt, or secret
    leakage;
17. old runs remain readable without fabricated new checkpoints, while an
    eligible historical replay adds `relevance_revision` and zero operations.

Gate 4B adds separate concurrency acceptance for atomic ownership, collection
artifacts/bindings, shared failures, physical/logical double counters, and
cross-direction no-duplicate Spider calls.

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
