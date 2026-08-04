# F003 Lite End-to-End Gap Closure Design

## Goal

Close the three remaining P0 gaps between the implemented Lite modules and a
repeatable Creator workflow that does not require an engineer to edit a run:

1. trust a structured subject only after one model proposal and deterministic
   backend validation;
2. let a user repair an eligible historical run from persisted packets without
   another Spider operation;
3. prove the complete new-run path with one real external end-to-end acceptance.

This design keeps the existing `SubjectStructure` fields and
`query_relevance_v2`. It does not add a fixed business vocabulary, a second
model repair pass, or a new `required_qualifiers` field.

## P0-1: One-Shot Subject Understanding

### Existing structure

Lite continues to use:

```text
canonical_subject
subject_type
core_entities
research_intents
context_modifiers
synonym_groups
ambiguities
resolution_state
```

`research_intents` retains its existing meaning. Lite executes only the first
non-empty intent. For `夏季凉感T恤`, the expected structure is:

```json
{
  "canonical_subject": "夏季凉感T恤",
  "core_entities": [
    {"canonical_name": "T恤", "raw_mentions": ["T恤"]}
  ],
  "research_intents": ["凉感"],
  "context_modifiers": ["夏季"],
  "ambiguities": [],
  "resolution_state": "resolved"
}
```

The deterministic compiler keeps its Lite rule:

```text
Q1 = core_entities[0] + research_intents[0]
Q2 = core_entities[0] + existing explicit user focus or direction facet
Q3 = the already frozen optional coverage fallback
```

Additional intents are preserved for audit but do not create Lite queries or
coverage requirements.

### Exactly one semantic proposal

The configured Pre-research model may propose a subject structure once. A
transport or configuration failure that produced no model response may resume
the same operation after configuration recovery. Once a response is received,
malformed or semantically untrusted structure is not sent to the model for a
repair or rewrite.

Backend code, not the model, decides whether the proposal is executable. The
existing schema, grounding, entity-count, synonym, and ambiguity checks remain.
The trust gate additionally returns `needs_confirmation` when:

- the only core raw mention normalizes to the complete user input while an
  intent or context modifier is also present;
- a core raw mention normalizes to an intent or context modifier;
- a core raw mention contains the complete normalized intent or context term,
  so the proposed decomposition did not separate the roles;
- the first executable research intent is empty;
- normalization leaves any executable term empty or duplicates incompatible
  roles.

These are structural checks, not Chinese category rules. No fixed category,
attribute, scene, brand, or SKU vocabulary is introduced.

### Structured user correction

When the proposal is untrusted, Creator does not route the user through the
normal free-text composer and does not call the model again. It renders one
structured correction card:

```text
核心对象 *   [T恤]
研究意图 *   [凉感]
使用场景     [夏季]
```

The bracketed words are grey input placeholders with no `示例：` prefix.
Trusted partial values may be prefilled; placeholders appear only for empty
fields. Core object and research intent accept one value. Context accepts
comma-, Chinese-comma-, or ideographic-comma-separated values.

Submitting the card calls a `confirm_subject_structure` workflow action. The
payload contains the current structure/input fingerprint, one core object, one
research intent, and normalized context values. User-entered values are
authoritative and therefore need not be substrings of the original sentence.
The backend still enforces non-empty required fields, length/count limits,
normalization, duplicate removal, one Lite core object, and stale-fingerprint
rejection. It constructs and freezes the confirmed `SubjectStructure` without
an LLM call.

The existing `clarify_subject` free-text/model-rewrite path is no longer used by
the Lite Creator UI. It may remain temporarily for compatibility, but it is not
an executable Lite recovery path.

## P0-2: User-Operated Historical Packet Replay

### Eligibility and entry point

Creator shows `使用已有笔记重新处理` only when all conditions hold:

- the latest publication is evidence-only because of
  `query_subject_not_supported`;
- completed selection and persisted directional evidence packets exist;
- the specialist task is terminal-successful or partial-successful;
- no matching successful `query_relevance_v2` revision has already produced a
  newer complete or partial report.

The entry point is shown in the evidence-insufficient report card. Trace may
describe the same recovery state but does not provide a second independent
action.

### Recovery flow

Clicking the entry point calls `repair_from_persisted_packets` for the same
workflow run:

1. validate the frozen subject, policy snapshot, locked QueryGroup identities,
   completed selection, and packet set;
2. reuse an already confirmed compatible structure when one exists;
3. otherwise request exactly one model subject-structure proposal with no
   Spider capability;
4. if trusted, append the immutable `relevance_revision` and continue;
5. if untrusted, return `subject_needs_confirmation` and render the same
   structured correction card;
6. `confirm_subject_structure` freezes the user values, appends the revision,
   and invokes `replay_downstream_from_persisted_packets`;
7. admission, direction result, governance, composition, audit, publication,
   and Creator artifact projection run through their production interfaces.

The UI displays `复用 N 条已有笔记 · 新增采集 0 次` while recovery is pending
and after it completes.

### Safety and idempotency

The recovery interface has no source-provider adapter. Before and after replay,
it asserts identical provider-operation and packet ID sets. A run-scoped lock
and checkpoint fingerprint make repeated clicks idempotent. Refresh resumes the
existing pending correction or replay; it cannot create another model proposal,
revision for the same structure, report message, or provider budget.

Ineligible, stale, malformed, packetless, or audit-failing recovery attempts
fail closed. They never fall back to Spider.

## P0-3: Real New-Run Acceptance

The acceptance subject is `夏季凉感T恤`. It must be entered from Creator and
use the configured OpenAI and Xiaohongshu adapters.

Acceptance proves:

1. Pre-research returns or the user corrects `核心对象：T恤｜研究意图：凉感｜场景：夏季`;
2. the model is not called a second time after an untrusted semantic result;
3. Q1 contains the core object and first intent, Q2 follows the existing Lite
   focus/facet rule, and no more than one frozen Q3 exists;
4. Spider operation count respects the frozen query/detail budgets and Q3 is
   activated at most once;
5. the report publishes citations from real persisted Xiaohongshu notes;
6. Trace remains newest-first and exposes safe structure, query-plan, coverage,
   fallback, retry, and timing facts;
7. refresh restores the same run, report, Trace, model configuration, and any
   pending recovery action;
8. an eligible historical fixture completes through the new Creator recovery
   entry with unchanged operation and packet identity sets.

External unavailability is not disguised as success. Auth, model configuration,
and provider failures must end at their existing recoverable UI states and may
be resumed without creating a second logical attempt.

## Observability

The safe Trace projection adds no raw subject, full query, prompt, provider
payload, credentials, note IDs, or headers. It records:

- one-shot structure attempt state and stable reason codes;
- whether structure authority is `model_proposal` or `user_confirmed`;
- short structure/input fingerprints;
- historical recovery eligibility and packet count;
- relevance revision status and reason;
- replayed stage range;
- unchanged provider-operation and packet counts.

## Tests

Focused tests cover:

- valid `夏季凉感T恤` decomposition;
- complete-sentence core, overlapping roles, malformed output, missing first
  intent, and multiple entities routing to the correction card after one model
  response;
- structured correction submission with no model or Spider call;
- placeholder copy `T恤`, `凉感`, and `夏季`;
- Q1 using only `research_intents[0]`;
- historical eligibility, one-shot proposal, user correction, idempotent replay,
  stale fingerprint rejection, and unchanged operation/packet IDs;
- frontend refresh and browser interaction;
- one real new-run acceptance and one historical packet-only acceptance.

## Deferred Work

This P0 closure does not make every admitted quote directly support the first
intent, synthesize raw findings into higher-level conclusions, execute multiple
research intents, add a category vocabulary, add embeddings, or implement
cross-direction physical deduplication. Those remain separate report-quality or
Gate 4B work.
