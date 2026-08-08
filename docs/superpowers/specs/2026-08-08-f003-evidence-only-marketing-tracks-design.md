# F003 Evidence-Only Marketing Track Projection Design

## Goal

Keep the three governed product-marketing tracks visible in a Lite report when
the publication state is `evidence_only_report`. A terminal
`insufficient_evidence` decision is an explicit report result, not an omitted
section.

## Scope

This change affects only the read projection and Creator presentation of an
already published report.

- The Lite read model must return `sections.marketing_conclusions` and the
  safe `sections.priority_action` for an `evidence_only_report`.
- Creator must render all three tracks whenever the Lite projection supplies
  them, including when the publication state is `evidence_only_report`.
- An `insufficient_evidence` card shows its reason and verification direction.
  It has no statement, citation control, product-effect claim, or investment
  recommendation.
- Existing suppression of unverified main findings and weak signals for
  `evidence_only_report` remains unchanged.

## Data Flow

The immutable report draft already contains `marketing_need`,
`marketing_value`, and `marketing_message` sections with terminal decision
states. The Lite reader projects those decisions into a stable three-track
object, then Creator renders the cards from that object. Neither component
re-evaluates admission or changes the frozen report artifact.

## Compatibility

No migration, replay, Spider call, LLM call, or report republishing is
required. Existing evidence-only reports become more complete on their next
Lite API read. Clients without the new display behavior remain safe because
the track objects contain terminal state and verification guidance rather than
unsupported conclusions.

## Tests

1. A Lite read-model test proves that an evidence-only product-marketing
   report returns all three terminal tracks and a safe priority action.
2. A Creator browser test proves that those three cards are visible, show
   `暂无可验证结论` and their verification direction, and do not show a
   conclusion statement or citation control.
3. Existing evidence-only tests continue to prove that main findings and weak
   signals remain hidden.
