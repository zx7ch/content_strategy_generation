# Product-marketing conclusion quality research

**Status:** Research basis; approved product and lifecycle decisions are recorded in the
[Task 3.1 design](../superpowers/specs/2026-08-25-task-3-1-marketing-conclusion-trace-design.md)

**Date:** 2026-08-25
**Question:** How should this project turn social-content evidence into useful, auditable product-marketing conclusions without inventing claims or overstating confidence?

## Executive finding

The missing capability is not a better one-shot prompt. It is an explicit evidence-analysis pipeline between collected notes and the three marketing tracks:

1. preserve query provenance and remove duplicate notes;
2. extract **atomic, verbatim evidence units** from note sentences;
3. group compatible units by aspect, meaning, qualifiers, and polarity;
4. expose supporting, contradictory, and insufficient evidence separately;
5. generate one atomic conclusion from one coherent evidence cluster;
6. independently verify every conclusion against its cited units;
7. let deterministic backend policy, rather than the LLM, decide whether the conclusion may be published.

This is the smallest design that can improve conclusion quality while preserving auditability. For the current sample size of roughly 30–40 notes, it does **not** require BERTopic, HDBSCAN, a vector database, or a numerical “confidence score.” The first delivery should use sentence-level extraction, one Runtime-loaded Research embedding adapter for near-paraphrase grouping, strict structured outputs, deterministic evidence checks, and an explicit counterevidence path. It does not refactor the existing RAG/Chroma path.

## Current implementation gap

The repository already has useful governance foundations:

- [`marketing_conclusions.py`](../../app/content_research/marketing_conclusions.py) validates evidence identity, run and direction ownership, exact quote fields, and independent note/author counts.
- [`marketing_conclusion_analysis.py`](../../app/content_research/marketing_conclusion_analysis.py) asks an LLM to propose a track, a statement, and supporting claim IDs.
- [`contracts.py`](../../app/content_research/contracts.py) defines the three tracks and publication thresholds.

The main quality gap is earlier in the chain. [`product_marketing.py`](../../app/content_research/admission/product_marketing.py) currently promotes coarse fields—for example, an entire first body line or title—into marketing evidence. It does not yet perform sentence-level atomic extraction, aspect/qualifier identification, contradiction pairing, or semantic grouping. Consequently, the conclusion model can receive forty admitted records but still lack sufficiently precise evidence from which to form a defensible conclusion.

The implementation therefore needs an **evidence-analysis layer**, not a relaxation of the existing evidence threshold.

## Research basis

### 1. Retrieval should preserve breadth before synthesis

The current `A`, `A + B`, and `A + C` query portfolio is suitable for collecting distinct views of the same hard subject constraint. Query provenance should remain attached to every note.

When ranked lists need to be combined, Reciprocal Rank Fusion (RRF) is a simple rank-only fusion method that performed robustly across retrieval systems in its [original paper](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf). When too many near-duplicates compete for a limited analysis budget, Maximal Marginal Relevance (MMR) explicitly balances query relevance with novelty in the [original formulation](https://www.cs.cmu.edu/~jgc/publication/The_Use_MMR_Diversity_Based_LTMIR_1998.pdf). xQuAD goes further by explicitly modeling query aspects and rewarding uncovered aspects in its [original paper](https://terrierteam.dcs.gla.ac.uk/publications/ecir2010_rodrygo_div.pdf).

For this project now:

- retain per-query quotas, stable note-ID deduplication, and query provenance;
- do not make RRF mandatory unless Spider exposes meaningful, comparable ranks;
- use MMR only when the evidence set exceeds the LLM/context budget;
- defer xQuAD until the product has explicit, stable aspect definitions beyond the current optional `B/C` expansion terms.

Retrieval diversification should prevent repetitive evidence; it does not itself validate a marketing conclusion.

### 2. Evidence extraction must precede theme naming

Braun and Clarke's original thematic-analysis method describes a recursive process of familiarization, initial coding, searching for themes, reviewing themes against extracts and the whole dataset, defining/naming themes, and reporting. It also calls for retaining contradictions, tensions, and deviant cases instead of forcing all extracts into a smooth account ([original paper](https://w.pauldowling.me/tmr/readings/Braun%20%26%20Clarke%202006.pdf)).

Mature text-analysis products expose a similar separation of operations without proving a universal algorithm:

- Sprinklr documents a pipeline whose base unit is a mention, followed by sentence splitting, product/entity and cohesive-phrase identification, category mapping, and sentiment assignment ([official Product Insights documentation](https://www.sprinklr.com/help/articles/advanced-concepts/how-does-the-product-insights-model-identify-insights/63e394a0a9d511790301667f)).
- Qualtrics recommends an iterative combination of top-down, bottom-up, and automatic topic construction, with topic-level sentiment and drill-down to original responses ([official Text iQ best practices](https://www.qualtrics.com/support/survey-platform/data-and-analysis-module/text-iq/text-iq-best-practices/)). Its automatic recommended-topic workflow requires at least 500 untagged comments ([official topics documentation](https://www.qualtrics.com/support/survey-platform/data-and-analysis-module/text-iq/topics-in-text-iq/)), which is a warning against assuming that unsupervised topic discovery will be stable on this project's 30–40-note runs.
- Brandwatch's official workflow tells users to validate AI summaries by inspecting mention snippets, filters, segmentation, and topic views; it may return no summary when evidence is insufficient ([official Iris documentation](https://social-media-management-help.brandwatch.com/en/articles/12767980-using-iris-conversation-insights-in-listen)).

These products support the design principle—sentence-level evidence, topic/aspect organization, sentiment or polarity, and source drill-down—but their documentation should not be treated as disclosure of a transferable algorithm.

### 3. Three tracks need different evidence semantics

The three product-marketing tracks should not be three labels applied to the same generic summary.

Gutman's original means–end chain model connects product attributes to consequences and personal values ([original paper](https://journals.sagepub.com/doi/pdf/10.1177/002224298204600207)); the later laddering method elicits those links through progressively deeper questions ([original paper](https://is.muni.cz/el/econ/jaro2013/MPH_MVPS/39278324/LadderingTheoy_original.pdf)). Strategyzer's official Value Proposition Canvas separately organizes customer jobs, pains, and gains alongside products/services, pain relievers, and gain creators ([official method](https://www.strategyzer.com/library/the-value-proposition-canvas)).

Adapted conservatively to observed UGC, the project should use:

| User-facing track | Admissible evidence | Output meaning | Must not imply |
|---|---|---|---|
| 需求 / 使用情境 | audience, scenario, job, friction, pain, desired outcome | what the customer is trying to do or avoid, and under what conditions | that this product solves it |
| 产品价值 | explicit attribute, experienced consequence/benefit, limitation, objection | what users observed or experienced about a product/property | an attribute→benefit causal link absent from the quotes |
| 内容表达 | repeated wording, question, comparison, metaphor, title framing | language and framing that can represent an evidenced concern or value | engagement, persuasion, or conversion efficacy |

The useful internal chain is `attribute → experienced consequence → customer value`, but each edge must be explicitly supported. A title phrase can support a message pattern; it cannot by itself support product performance.

### 4. A conclusion should be atomic and citation-complete

ALCE evaluates generated answers on fluency, correctness, and citation quality, and emphasizes that citations let users verify individual statements ([original paper](https://aclanthology.org/2023.emnlp-main.398.pdf)). FActScore decomposes generation into atomic facts and checks each against a source ([original paper](https://aclanthology.org/2023.emnlp-main.741.pdf)). FEVER uses `supported`, `refuted`, and `not enough information` decisions tied to evidence ([original paper](https://arxiv.org/abs/1803.05355)). SummaC found that whole-document versus whole-summary inference suffers from a granularity mismatch and instead compares smaller sentence units before aggregation ([original paper](https://aclanthology.org/2022.tacl-1.10/)).

The project should borrow atomicity and evidence-pair verification, not these benchmarks' truth claims or English-model scores. UGC is non-authoritative and frequently conflicting. A supported conclusion here means only: **the cited observed material entails the stated, properly qualified synthesis**.

### 5. Confidence should describe evidence strength, not statistical certainty

The IPCC's calibrated confidence framework combines evidence type, amount, quality, consistency, and degree of agreement, reserving probability language for cases with an adequate quantitative basis ([official AR6 guidance](https://www.ipcc.ch/report/ar6/wg1/chapter/chapter-1/)). This is a useful design precedent, but this project must not present its UGC assessment as scientific IPCC confidence.

AAPOR's official report warns that social-media data are generally nonprobability data: they can provide valuable qualitative insights, but cannot ordinarily support population estimates; the unit of analysis and transparent limitations are critical ([official report](https://aapor.org/wp-content/uploads/2022/11/AAPOR_Social_Media_Report_FNL.pdf)).

Therefore:

- do not display a probability or percentage “confidence” score;
- keep categorical decisions such as `selected`, `directional`, `insufficient`, and add `contested` and `analysis_failed` where needed;
- display the components behind the decision: unique notes, independent authors, query-group coverage, exact/body quote count, supporting versus contradictory units, and provenance completeness;
- treat the existing three-note/two-author rule as a product governance threshold, not a statistically validated confidence boundary;
- never call a one-run cluster a “trend”; trend claims require a time baseline and change/burst analysis.

## Recommended analysis contract

### Stage 1 — Preserve collected evidence

Each collected note retains:

- run ID, note ID, author ID, and source URL;
- exact Spider query and query-group provenance;
- title/body field identity and text;
- collection timestamp and rank when available.

The immutable Evidence Snapshot stores the exact title/body field copies and hashes used by the
analysis, rather than only retaining note IDs. Retries and historical verification read this snapshot
even if the external note later changes or becomes unavailable; navigation availability is projected
separately from the frozen evidence fact.

Stable note-ID deduplication happens before analysis. Duplicate presence across queries is retained as provenance, not counted as independent support.

### Stage 2 — Extract atomic evidence units

Split each note into sentence or short-clause candidates. Ask the LLM, using a strict JSON schema, to return zero or more units:

```json
{
  "note_id": "...",
  "field": "title|body",
  "quote": "exact source substring",
  "entity": "confirmed core subject",
  "aspect": "...",
  "evidence_type": "use_context|pain|desired_outcome|attribute|experienced_benefit|objection|language_pattern",
  "polarity": "positive|negative|mixed|neutral",
  "qualifiers": { "audience": [], "scenario": [] },
  "proposed_tracks": ["need|value|message"]
}
```

The backend must reject a unit if the quote is not an exact substring of the named field, refers to the wrong run/note/entity, or contains a causal/outcome statement not present in the quote. The LLM proposes semantics; it does not establish source identity.

OpenAI's current API documentation recommends `json_schema` Structured Outputs over the older `json_object` mode because the former enforces the supplied schema ([official API reference](https://developers.openai.com/api/reference/java/resources/beta/subresources/responses)). Schema conformance still does not prove semantic correctness, so deterministic validation remains necessary.

### Stage 3 — Group compatible evidence, without hiding disagreement

For the current dataset size, use a lightweight process. A dedicated Research adapter loads,
warms, and health-checks the embedding model once during Runtime startup; a research Run only calls
that adapter and never lazily loads, silently switches models, or replaces failure with zero vectors.
The adapter fixes input formatting and exposes a model/revision/dimension/normalization fingerprint;
the existing RAG/Chroma service remains unchanged:

1. partition by compatible track, evidence type/aspect, qualifiers, and polarity;
2. embed the atomic text with the repository's existing multilingual sentence-transformer;
3. use a documented cosine threshold and deterministic graph components or agglomerative grouping for near-paraphrases;
4. let the LLM propose a short cluster label, while retaining every member and source;
5. pair same-aspect, compatible-qualifier clusters with opposing polarity as support/counterevidence.

BERTopic's original pipeline combines transformer embeddings, clustering, and class-based TF–IDF ([original paper](https://arxiv.org/abs/2203.05794)). It becomes relevant when runs have enough material for stable emergent topics, but should not be a dependency of the immediate fix. At current scale it adds operational complexity without resolving evidence granularity or auditability.

### Stage 4 — Generate one conclusion per coherent cluster

The synthesizer receives only admitted atomic units and returns strict structured output:

```json
{
  "track": "need|value|message",
  "statement": "one atomic, qualified conclusion",
  "supporting_evidence_ids": ["..."],
  "contradicting_evidence_ids": ["..."],
  "qualifiers": { "audience": [], "scenario": [] },
  "limitation": "..."
}
```

It must not combine unrelated clusters to satisfy a quota. A weak coherent cluster may become
`directional`; an incoherent or unsupported proposal becomes `no_publishable_conclusion`.
All three planned tracks are evaluated, but only tracks with supported output are visible.

### Stage 5 — Verify conclusion/evidence entailment independently

Run a separate verifier for each atomic conclusion against the cited evidence units and classify it as:

- `supported`;
- `refuted`;
- `not_enough_information`.

The verifier must see both supporting and contradictory units. Initially this can be a separate, tightly scoped LLM call rather than an English-only off-the-shelf NLI model; the choice must be validated against Chinese UGC. The backend recomputes note, author, query, field, and contradiction counts and applies the publication policy.

No run may be presented as “verified complete” if conclusion analysis or verification failed. A completed collection with failed analysis is `analysis_failed`, not a successful zero-conclusion report.

## Contradictions and weak signals

Contradictions are useful results, not cleanup noise. The system should:

- compare only units on the same aspect with compatible audience/scenario qualifiers;
- preserve positive and negative evidence separately;
- publish a qualified `contested` conclusion when both sides clear the governance threshold;
- show the dominant statement only when every valid counterevidence item and limitation remain visible,
  even when the counterevidence is below the `contested` threshold;
- treat one- or two-source patterns as directional hypotheses, never trends or general market facts.

This directly prevents a frequent synthesis failure: averaging “凉但贴身” and “不贴身但不够凉” into an unsupported generic statement about “comfortable cooling.”

## Evaluation and acceptance criteria

The evaluation set should be Chinese, human-labeled, and include the real failure cases found during manual E2E testing. OpenAI's official evaluation guidance recommends defining the task, testing representative inputs, using human-labeled ground truth, and iterating on measured failures ([official eval guide](https://developers.openai.com/api/docs/guides/evals), [evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)).

Measure each stage separately:

| Stage | Required evaluation |
|---|---|
| atomic extraction | exact-span precision/recall; invented-quote rate; entity precision |
| track mapping | macro-F1 across need/value/message; ambiguous multi-track review |
| grouping | pairwise cluster precision/recall; qualifier-mismatch rate |
| disagreement | contradiction precision/recall and missed-counterevidence rate |
| synthesis | atomicity; citation completeness; citation correctness; unsupported-causality rate |
| decision policy | exact match for selected/directional/no_publishable_conclusion/contested/analysis_failed |
| end to end | false “verified complete” rate; every displayed conclusion traceable to visible exact quotes |

The release E2E should prove that:

1. the previewed query equals the actual Spider query;
2. at least one track may produce a governed conclusion without forcing all three tracks to output;
   every published, evaluated, and omitted track retains an explicit, truthful coverage reason;
3. clicking a conclusion reveals its exact support and counterevidence quotes;
4. deliberately conflicting notes produce a visible contested/limited result;
5. any planned-track technical failure after collection produces a recoverable analysis failure and no
   publication; successfully verified track checkpoints are retained and a compatible retry only reruns
   failed tracks;
6. prompt, model, schema, and policy versions are recorded for reproduction.
7. directional-only output is labeled as a limited sample lead, never as a verified conclusion.

## Immediate implementation scope versus later work

### Implement in the main-flow fix

- exact sentence/clause evidence extraction with strict structured output;
- deterministic quote, identity, and entity validation;
- lightweight aspect/qualifier/polarity grouping using existing embeddings;
- distinct need/value/message evidence semantics;
- support and counterevidence IDs in every conclusion proposal;
- independent atomic groundedness verification;
- deterministic publication decisions and truthful analysis-failure state;
- browser E2E covering traceability, contradiction, and provider failure.

### Defer until evidence volume or product needs justify it

- RRF/MMR beyond the existing quota/deduplication policy;
- xQuAD aspect diversification;
- BERTopic/HDBSCAN topic discovery;
- time-series burst detection for actual trend claims;
- causal or commercial-effect claims, which require experiments or business outcome data rather than social-content synthesis.

## Decision summary

The project should not attempt to generate “more conclusions” from the current forty records. It should first create **better evidence objects**. Once atomic evidence, disagreement, qualifiers, and provenance are explicit, the existing governed three-track system can produce useful conclusions when the evidence supports them—and an honest, diagnosable insufficient result when it does not.
