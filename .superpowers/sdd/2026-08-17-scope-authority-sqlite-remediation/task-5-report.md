# Task 5 report: frozen Scope matching and coverage

## Delivered

- Added immutable per-candidate Scope matching projections with contract version,
  query-group provenance, per-constraint status/evidence/evidence fields,
  eligibility, and stable exclusion reasons.
- Matching evaluates the frozen core object plus allowed aliases and every Scope
  constraint across title, body, tags, and safe source metadata fields.
- Product-marketing collection now executes the confirmed Scope Contract's final
  queries and IDs. The old automatic fallback is disabled for Scope-governed
  collection, so inadequate coverage cannot silently broaden retrieval.
- Product-marketing admission keeps the existing query-provenance, canonical
  source, and quote-field validation, then consumes the persisted Scope match
  instead of applying the separate `core_entity + first_intent` literal gate.
- Added deterministic Scope coverage persistence by required constraint, query
  group, eligible sample, and independent author. Unmet coverage persists an
  `awaiting_scope_decision` `CoverageSnapshot` with exact stable reason codes.
- Added `query_group_collected`, `candidate_scope_evaluated`, and
  `coverage_evaluated` Scope audit events. Event IDs are retry-stable and event
  payloads correspond to the persisted collection page, candidate projection,
  and coverage snapshot facts.
- Formal workflow completion now stops before governance and report publication
  when the persisted Scope coverage state is `awaiting_scope_decision`.
- Trace presentation was not changed. Coverage resolution and frontend behavior
  were not implemented.

## TDD evidence

### Candidate matching RED

Command:

```text
pytest tests/unit/test_content_research_scope_matching.py -v
```

Observed before production changes:

```text
ImportError: cannot import name 'evaluate_scope_match'
```

The focused tests pin an eligible `夏季通勤衬衫` source using the approved `衬衫`
core alias and an autumn-only source excluded with
`required_constraint_unmatched:season`.

### Coverage persistence RED

Command:

```text
pytest tests/integration/test_content_research_scope_coverage.py -v
```

Observed before coverage production changes:

```text
ImportError: cannot import name 'persist_scope_coverage_evaluation'
```

The integration test pins exact persisted constraint/query-group/author counts,
snapshot state, unmet constraint IDs, reason codes, and audit-to-fact
correspondence.

### Eligible-sample reason RED

Command:

```text
pytest tests/integration/test_content_research_scope_coverage.py -q
```

Observed after adding the expectation but before the production branch:

```text
FAILED ... differing _summary.reason_codes
```

The subsequent minimal change added `minimum_eligible_scope_samples_unmet` when
the count of candidates satisfying all required constraints is below policy.

## Verification

Focused and adjacent regression command:

```text
pytest tests/unit/test_content_research_scope_matching.py \
  tests/integration/test_content_research_scope_coverage.py \
  tests/unit/test_content_research_product_marketing_admission.py \
  tests/integration/test_content_research_direction_pipeline_store.py \
  tests/e2e/test_content_research_scope_api.py \
  tests/e2e/test_content_research_brief_confirm_api.py -q
```

Result: `113 passed in 11.93s`.

Static verification:

```text
ruff check app/content_research/contracts.py \
  app/content_research/admission/relevance.py \
  app/content_research/workflow/directional_pipeline.py \
  app/content_research/service.py \
  tests/unit/test_content_research_scope_matching.py \
  tests/integration/test_content_research_scope_coverage.py
git diff --check
```

Result: Ruff reported `All checks passed!` (plus the repository's existing
top-level-lint configuration deprecation warning); `git diff --check` exited 0.

An additional legacy formal-workflow E2E run produced `39 passed, 9 failed`.
Every failure called `start_formal_research` directly after brief confirmation
and received the now-required `scope_confirmation_required` response introduced
by completed remediation Task 2. Those tests do not prepare/confirm a Scope and
are outside this task's allowed test-file scope.

## Remaining concerns

- The repository's older formal-workflow E2E fixtures still need migration to
  prepare and confirm Scope before starting collection.
- Scope coverage resolution and report limitation projection remain explicitly
  deferred to the next Scope Contract task; this task only persists the pending
  decision and prevents a normal report.
