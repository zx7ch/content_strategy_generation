# Execution Decision Identity Design

## Purpose

Define one complete, durable identity for a user coverage decision. It removes the current ambiguity in which the live resolver and historical migrations independently construct fingerprints. This is a correction to the existing authority design, not a new Scope revision or a change to the meaning of `ScopeContract.version`.

## Ubiquitous language

- **Decision**: the immutable user choice made against one Coverage Snapshot.
- **Execution Unit**: the stable, user-visible server identity for one Decision and all of its retries.
- **Attempt**: an internal worker try within one Execution Unit. It is not part of Decision identity.
- **Source Scope**: the Scope Contract that produced the Coverage Snapshot the user is resolving.
- **Resulting Scope**: the Scope Contract that the Decision authorizes. It equals Source Scope except for a user semantic relaxation.
- **Target constraint**: the required unmet constraint selected by an expand or relax decision.

## The one deep identity module

Create one private module/function, for example `build_execution_decision_identity(...)`. It is the only implementation allowed to serialize or hash a Decision. SQLite resolver code and migrations import/call it; neither may hand-build JSON or call `sha256` for a decision.

```python
@dataclass(frozen=True)
class ExecutionDecisionIdentity:
    schema: Literal["execution_decision_identity_v1"]
    coverage_snapshot_id: str
    source_scope_contract_id: str
    resulting_scope_contract_id: str
    resolution: Literal[
        "generate_limited_report",
        "expand_required_constraint",
        "relax_constraint",
    ]
    operation: Literal["limited_report", "supplementary_collection"]
    target_constraint_id: str | None
    supplementary_queries: tuple[str, ...]
```

`execution_unit_id` is `"seu_" + sha256(canonical_json(identity))[:24]`; the full digest is stored as `decision_fingerprint`. Canonical JSON uses UTF-8, `ensure_ascii=False`, sorted keys, `(',', ':')` separators, and no optional field omission.

### Field rules

| Field | Source | Why it is identity-bearing | Validation |
|---|---|---|---|
| `schema` | constant | permits a future *identity algorithm* migration without pretending it is a Scope change | exactly `execution_decision_identity_v1` |
| `coverage_snapshot_id` | request/decision target | a decision applies to exactly one observed coverage fact | snapshot exists and is awaiting a decision |
| `source_scope_contract_id` | snapshot | binds the decision to the semantics under which insufficiency was observed | equals snapshot Scope |
| `resulting_scope_contract_id` | decision result | distinguishes a semantic relaxation from same-Scope work | equals source except relaxation |
| `resolution` | normalized request | distinguishes limited report, expansion, relaxation | one enumerated value |
| `target_constraint_id` | resolved required unmet constraint | distinguishes two valid decisions with identical query text | required for expand/relax; null for limited |
| `supplementary_queries` | normalized request | distinguishes user-supplied expansion work | normalized, ordered tuple for expand; empty tuple otherwise |

`workflow_run_id`, `operation`, `execution_revision`, authorization ID, continuation ID, attempt number, lease token, timestamps, audit event ID, UI request ID, and provider correlation IDs are intentionally excluded from the fingerprint. `workflow_run_id` remains a persisted query/audit field; `operation` is derived and validated from `resolution`. The remaining fields are consequences or execution mechanics, not the user decision.

### One-decision rule

One Coverage Snapshot accepts exactly one persisted Decision. A byte-for-byte-equivalent normalized request returns that Decision's Execution Unit. Any request with a different canonical identity for the same `coverage_snapshot_id` is rejected with `coverage_decision_already_resolved`; it never creates a second Execution Unit. This makes different target constraints with identical queries distinguishable without allowing competing user decisions to race on one observed coverage fact.

## Normalization before construction

1. Trim/collapse whitespace in each supplementary query using the existing query cleaner.
2. Preserve query order: query order is a user instruction and may influence provider behavior. Reject duplicates after normalized comparison.
3. Resolve `target_constraint_id` against the Source Scope and require it be an unmet required constraint before identity construction.
4. Derive `operation` from `resolution`; do not accept it in HTTP payloads or migrate it from untrusted text without validation.
5. For a semantic relaxation, construct/resolve Resulting Scope first, then pass both explicit Scope IDs to the identity module. The source remains the snapshot Scope; the resulting Scope is the relaxed contract.

## Compatibility and migration contract

The identity module accepts an explicit `LegacyDecisionInput`, not raw table rows. A single adapter reconstructs that input from legacy authorization + continuation + coverage rows.

| Situation | Source Scope | Resulting Scope | Target constraint | Queries |
|---|---|---|---|---|
| Limited report | Coverage Snapshot Scope | same | null | `[]` |
| Expansion | Coverage Snapshot Scope | same | persisted audit event target; if absent, **unrecoverable** | continuation queries |
| Relaxation | Coverage Snapshot Scope | authorization Scope | persisted audit event target; if absent, **unrecoverable** | `[]` |

Legacy rows that lack target-constraint data must not be silently assigned `""`. Migration records `identity_state="legacy_identity_incomplete"` and exposes a non-replayable/manual-recovery state. This is safer than collapsing different decisions. New persistence therefore adds these columns to `content_research_scope_execution_units`:

```text
identity_schema                 TEXT NOT NULL
identity_json                   TEXT NOT NULL
identity_state                  TEXT NOT NULL  -- canonical | legacy_identity_incomplete
legacy_authorization_id         TEXT NULL UNIQUE
```

`decision_fingerprint` remains a unique index only among `identity_state='canonical'`. Incomplete historical units retain a stable surrogate ID but may never be used to answer a new exact replay automatically.

The forward migration reads the original Scope audit event for `constraint_id`; it must not infer a target from query text. It writes the full canonical `identity_json`, validates that its fingerprint agrees with any live resolver replay, and is one transaction with migration ledger update. A migration failure rolls back schema/data/ledger together.

## API and observability contract

- Action results expose `execution_unit: { id, state, recovery_state }`; authorization/continuation IDs are diagnostic compatibility fields only and are not accepted to identify a replay.
- Exact replay supplies `coverage_snapshot_id` and the same normalized decision fields. The service first resolves the canonical identity, then returns the matching canonical unit; it never chooses the latest Coverage Snapshot implicitly.
- Every execution fact and provider correlation includes `execution_unit_id`; attempts add `attempt_no`. The trace response includes the stored `identity_json` (safe projection) and fact sequence, so an operator can verify why a unit exists.

## Required compatibility matrix

| Case | Expected result |
|---|---|
| Same snapshot + identical limited request twice | same canonical unit |
| Same snapshot + same expansion target/queries twice | same canonical unit |
| Same snapshot + different valid target, same query | explicit `coverage_decision_already_resolved`; never silent collapse |
| Same snapshot + same target, different normalized query | explicit `coverage_decision_already_resolved` |
| Same snapshot + relaxation target twice | same canonical unit and same resulting Scope |
| Runtime-created decision then legacy alias reader | same unit |
| Legacy migrated canonical record then runtime exact replay | same unit |
| Historical record with missing target | explicit non-replayable recovery state, no guessed identity |
| `0025/0026` failure midway | no tables/rows/ledger partial state; retry produces canonical data |

## Acceptance tests before implementation resumes

1. Parameterized unit tests invoke the identity module for every row in the matrix and compare canonical JSON and digest, not just unit IDs.
2. Store concurrency test resolves the same canonical identity from two SQLite connections and creates one row/fact only.
3. End-to-end API test persists a decision, changes later Coverage state, and replays the original payload using its explicit snapshot ID; it returns the original unit without recollection.
4. Migration fixture seeds each legacy row shape, including relaxation with target audit data and missing target data; it checks canonical replay or explicit manual recovery.
5. Failure-injection migration test proves rollback/retry of both data and migration ledger with seeded legacy aliases.

## Non-goals

- This document does not implement worker lease fencing, provider outcome handling, Coverage ownership, report lineage, or Creator UI. Those remain the following tasks once this identity contract passes review.
- It does not convert the system to event sourcing or add public IDs for every internal step.
