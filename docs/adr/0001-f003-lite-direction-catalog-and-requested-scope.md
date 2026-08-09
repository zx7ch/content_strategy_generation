# F003 Lite separates the direction catalog from a run's requested scope

Gate 4A will make `product_marketing`, `competitor_discovery`, and
`content_performance` a stable catalog shown in every Brief, while each Lite
run freezes a user-selected non-empty `requested_direction_ids` subset. A
direction omitted by the user is `not_requested`; `unavailable` is reserved
for a requested direction whose frozen capability conditions prevent
collection. This replaces the earlier all-three-per-run interpretation so the
final user-selectable product does not need a second scope-contract migration.

## Consequences

The shared schema, read model, count rules, recovery presentation, fixtures,
and browser acceptance must distinguish `not_requested` from
`insufficient_evidence` and `unavailable`. Gate 4A implements that contract
once; Gate 3 turns catalog directions into real collection capabilities. The
Brief shows the complete catalog, while the report renders only requested
directions and explicitly states the frozen requested scope.

Gate 4A validates all seven non-empty selection combinations plus empty
selection rejection at the API boundary; browser acceptance covers single,
double, and all-direction requests. Its one real collection proof is the
validated `product_marketing` path, while controlled backend states cover the
remaining report, recovery, and citation-navigation presentations.

## Consequences

`/lite-report` is the only retained report API contract after Gate 4A.
`/report`, `/results`, and their consumers and tests are removed; Creator has
no report fallback. A later formal delivery may expand the shared contract,
but does not preserve these endpoints.

The citation drawer retrieves its on-demand evidence-detail projection through
`/lite-report` with requested citation-group identifiers. The initial response
contains only report cards and citation summaries; no separate evidence-bundle
report endpoint remains.

The obsolete EvidenceBundle route, frontend helper, services, stores, tests,
and persistence tables are removed in Gate 4A. Before the destructive
migration, Gate 2's retained run, checkpoint, canonical-source, citation,
trace, and acceptance evidence must be proved independent of that model.
