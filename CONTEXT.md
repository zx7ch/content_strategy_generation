# Content Strategy Generation

The shared language for the Content Research Lite workflow and its Creator
Workbench presentation.

## Content Research Lite

**Lite run**:
A content-research workflow run whose requested directions, policy, sampling,
and capability conditions are frozen at creation.
_Avoid_: Lite task, research job

**Direction catalog**:
The stable, release-level set of available Lite directions: `product_marketing`,
`competitor_discovery`, and `content_performance`. It is always shown in the
Brief and is not itself the run's requested scope.
_Avoid_: run direction set, selected scope

**Requested directions**:
The non-empty user-selected subset of the direction catalog frozen into a Lite
run as `requested_direction_ids`.
_Avoid_: direction catalog, enabled directions

**Unavailable direction**:
A requested direction that cannot collect evidence in a particular run because
its frozen capability conditions are not met.
_Avoid_: unselected direction, failed direction

**Not-requested direction**:
A catalog direction deliberately omitted from a Lite run by the user. It is
neither a failure nor unavailable. It remains in the read model for audit but
is not rendered in the report; the report names its requested directions.
_Avoid_: unavailable direction, skipped failure

**Gate 4A**:
The internal/pre-release integration gate that activates the sole Lite Creator
contract, including the direction catalog and requested-direction scope.
_Avoid_: Lite release

**Gate 4B**:
The delivery-acceptance gate after all three catalog directions pass real
execution and browser acceptance; it authorizes the formal Lite release.
_Avoid_: preview gate

**Controlled backend state**:
A durable test run or controlled adapter outcome served through the real Lite
report API for deterministic acceptance coverage.
_Avoid_: frontend mock

**Frontend-zero-mock acceptance**:
Browser acceptance in which Creator reads only the real Lite report API;
component fixtures cannot substitute for API or browser proof.
_Avoid_: mocked E2E

**Core search object (A)**:
The sole required product-marketing scope condition and the broad retrieval
anchor, such as `长袖衬衫`. Suggested queries retain it, while a user-edited
query may omit it and becomes exploratory.
_Avoid_: primary intent, mandatory query token

**Product/experience query aspect (B)**:
An optional concrete phrase people plausibly search with A to diversify the
product or experience results, such as `凉感`. It is not an evidence-admission
condition.
_Avoid_: research goal, primary intent

**Context/audience query aspect (C)**:
An optional concrete phrase people plausibly search with A to diversify a
scenario, audience, or occasion, such as `夏季通勤`. It is not an
evidence-admission condition.
_Avoid_: required context, analysis goal

**Suggested query portfolio**:
The non-authoritative product-marketing proposal `A`, `A B`, and `A C`, omitting
groups whose optional aspect is unavailable. The user's frozen Scope query
groups, not this proposal, authorize execution.
_Avoid_: Boolean query, mandatory three-query set

**Destructive UI-contract cutover**:
The Gate 4A deletion of legacy Creator UI/API contract implementations and
their tests, while preserving validated F003 workflow capabilities and Gate 2
evidence. It also physically removes the obsolete EvidenceBundle persistence
model after proving the retained Gate 2 evidence has no dependency on it.
_Avoid_: research-data purge, historical adapter

**`F003_LITE_PREVIEW_ENABLED`**:
The whole-feature, environment-level pre-release isolation switch. When off,
Creator has no F003 entry point and the backend rejects new Lite runs; it is
removed at Gate 4B and never controls individual directions.
_Avoid_: direction flag
