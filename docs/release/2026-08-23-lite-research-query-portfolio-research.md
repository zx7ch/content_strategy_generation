# Lite Content Research Query Portfolio Research — 2026-08-23

## Status

Research note. This is not an approved product spec and does not claim knowledge
of Xiaohongshu's proprietary query interpretation or ranking algorithm.

## Question

For a product-marketing topic with:

- `A`: the core product/object;
- `B`: a product or experience aspect;
- `C`: a usage context, audience, or occasion;

is the three-query portfolio `A`, `A B`, `A C` a sound way to improve the
variety of collected content, and how should the UI describe these inputs?

## Finding

Yes. `A`, `A B`, `A C` is a sound, deterministic Lite baseline when `B` and `C`
are distinct, concrete phrases that people plausibly use in search. It is best
described as **explicit query-aspect reformulation** or a **faceted query
portfolio**, not as classical synonym-based query expansion.

More specifically, query-reformulation research calls the pattern that retains
all original terms and adds a qualifier a **specializing reformulation**. This
gives the product a precise description for `A B` and `A C`: two single-facet
specializations anchored by `A`. See the original Microsoft Research
[specializing-query-reformulation paper](https://www.microsoft.com/en-us/research/wp-content/uploads/2023/04/sigir2023-gender-specializing-query-reformulation.pdf).

The two breadth effects must be kept separate:

- Within one request, adding `B` or `C` usually makes the request more
  aspect-specific and may narrow or shift its ranking.
- Across all three requests, pooling the lists can increase aggregate coverage
  because the portfolio deliberately probes the broad topic and two different
  aspects.

This distinction matches TREC's treatment of a faceted query: one broad topic
can contain several aspects or partial information needs, and a search session
may use multiple queries to cover different subsets. See the official
[TREC 2009 Web Track overview](https://trec.nist.gov/pubs/trec18/papers/WEB09.OVERVIEW.pdf)
and [TREC 2011 Session Track overview](https://trec.nist.gov/pubs/trec20/papers/SESSION.OVERVIEW.2011.pdf).

Do not display the formula as literal Boolean `A & B` unless the downstream
engine documents AND semantics. A consumer search box may tokenize and rank
terms softly, so `A B` is not guaranteed to return a mathematical subset of
the results for `A`. For comparison, Lucene explicitly distinguishes required
(`MUST`) from optional (`SHOULD`) clauses in its
[official query semantics](https://lucene.apache.org/core/7_7_3/core/org/apache/lucene/search/package-summary.html).

## Why “重点了解什么” is the wrong search-field model

“重点了解什么” asks for a research goal, not a search expression. A user may
reason in a sentence such as “我想知道它夏天穿会不会闷、会不会显胖”, while
the useful search vocabulary is closer to `透气`, `凉感`, `显瘦`, `上身效果`,
or `通勤穿搭`. Sending the abstract label `上身感受` directly to a search box
mixes two different domain objects:

1. **Research goal** — a natural-language question that determines what the
   final analysis should answer.
2. **Query aspect term** — a short, observable phrase chosen to retrieve one
   slice of source content.

The system should translate a research goal into editable query-aspect terms
and preview the resulting queries. It should not ask the user to understand an
internal `research_intent` field and then silently treat its value as a search
term.

For the current product-marketing case, clearer editable labels are:

- `研究对象` (required): `长袖衬衫`
- `产品／体验检索词` (optional): `凉感` or `显瘦`
- `场景／人群检索词` (optional): `夏季通勤`

This product/attribute structure also has precedent in product-search research:
Google's work on latent structured intent separates product queries into
concrete attributes such as category, brand, product line, and feature/style,
rather than one generic “intent” slot. See the original
[structured shopping-query paper](https://research.google/pubs/predicting-latent-structured-intents-from-shopping-queries/).

The UI should immediately preview the execution portfolio:

```text
长袖衬衫
长袖衬衫 凉感
长袖衬衫 夏季通勤
```

The natural-language research goal can remain elsewhere in the Brief, but it
should not be presented as though every word will be executed verbatim.

## Relationship to known IR methods

### Query reformulation and faceted subqueries

The xQuAD authors model aspects of an underspecified query as subqueries. They
note that aspect subqueries can be obtained from query reformulations, an
external corpus, or salient phrases in the initially retrieved collection.
This is the closest established model for treating `A B` and `A C` as sibling
probes rooted in `A`. See the original
[xQuAD paper](https://terrierteam.dcs.gla.ac.uk/publications/ecir2010_rodrygo_div.pdf)
and the authors' later
[web-search diversification paper](https://doi.org/10.1145/1772690.1772780).

### Classical query expansion / relevance feedback

Classical query expansion primarily addresses vocabulary mismatch by adding or
reweighting related terms. Relevance models can estimate useful terms from the
query and collection, while relevance or pseudo-relevance feedback learns from
initial results. These methods can help propose better values for `B` and `C`,
but they are not equivalent to the fixed three-query portfolio. See Lavrenko
and Croft's original
[Relevance-Based Language Models paper](https://ciir.cs.umass.edu/pubfiles/ir-225.pdf)
and Salton and Buckley's original
[relevance-feedback study](https://doi.org/10.1002/%28SICI%291097-4571%28199006%2941%3A4%3C288%3A%3AAID-ASI8%3E3.0.CO%3B2-H).

For Lite, collection-driven expansion should be a later, explicitly authorized
step: first run `A`, inspect candidate phrases, then propose a revised scope.
Automatic expansion before user confirmation would conflict with the current
scope-contract boundary.

### MMR and xQuAD-style result diversification

MMR and xQuAD operate after candidates exist:

- MMR trades relevance to the query against dissimilarity from already selected
  results, reducing duplicate or near-duplicate content. See Carbonell and
  Goldstein's original [MMR paper](https://www.cs.cmu.edu/~jgc/publication/The_Use_MMR_Diversity_Based_LTMIR_1998.pdf).
- xQuAD balances relevance with novelty and coverage of aspects that are still
  underrepresented. This is more aspect-aware than MMR when the system retains
  `A`, `B`, and `C` provenance.

Neither algorithm generates the three queries by itself. They improve the
selection or ordering of the pooled results.

## Implementation availability

xQuAD is an algorithmic framework, not a search API. The original formulation
greedily builds a result list. At each step it scores every remaining candidate
using both its relevance to the main query and its coverage of each aspect,
discounting an aspect as previously selected documents already cover it. A
usable implementation therefore needs, at minimum:

- a candidate set and a main-query relevance score `P(d|q)` for each item;
- explicit aspect weights `P(a|q)` for `B` and `C`;
- an aspect-relevance score `P(d|a)` for every candidate/aspect pair;
- a relevance/diversity trade-off `lambda`, an output size `k`, score
  normalization, and deterministic tie-breaking;
- a greedy selection loop implementing the novelty product over already
  selected items.

These inputs are part of the algorithm, not supplied automatically by a
library. In this product, Xiaohongshu rank positions could be transformed into
main-query scores and query-group provenance could provide a simple binary or
rank-based aspect score, but those are product-specific modeling choices that
must be specified and tested. See the University of Glasgow record and the
authors' [original xQuAD paper](https://terrierteam.dcs.gla.ac.uk/publications/ecir2010_rodrygo_div.pdf).

No xQuAD implementation is present in this repository's declared Python or
frontend dependencies. The closest installable third-party implementation
found is [FairDiverse 1.0.0 on PyPI](https://pypi.org/project/fairdiverse/),
whose repository was still active in 2026. It is not a suitable drop-in here:
its [`xQuAD` source](https://github.com/XuChen0427/FairDiverse/blob/master/fairdiverse/search/postprocessing_model/xQuAD.py)
expects a ClueWeb-style `bm25_scores.pkl`, query IDs, toolkit configuration,
filesystem output, and the toolkit's offline evaluation flow. The package's
official [`setup.py`](https://github.com/XuChen0427/FairDiverse/blob/master/setup.py)
also installs a research stack including Torch, SciPy, CVXPY, MIP, and Gurobi.
Adopting that whole package for one short greedy reranker would add substantial
unrelated surface area.

Accordingly, there is no maintained Python or JavaScript package identified
that can be directly adopted with the current candidate model. If xQuAD is
later justified, a small local implementation of the formula is the lower-risk
option, but only after defining the score inputs above. For Lite, per-query
quotas, stable-note-ID deduplication, and provenance-based coverage accounting
remain simpler and do not require xQuAD.

## Recommended Lite retrieval pipeline

1. Freeze one mandatory core object `A` and up to two concrete aspect terms
   `B`, `C` in the confirmed Scope.
2. Execute `A`, `A B`, and `A C` separately with an explicit per-query budget.
3. Preserve the query-group provenance on every candidate.
4. Pool results and deduplicate by stable note ID before counting coverage.
5. Ensure that each query group contributes candidates; merely issuing three
   requests does not guarantee diversity if their results heavily overlap.
6. For a small Lite implementation, use per-group quotas plus deduplication.
   If candidate volume justifies ranking infrastructure, apply xQuAD-style
   aspect coverage or MMR-style anti-redundancy after pooling.
7. Evaluate the portfolio with per-group unique yield, pairwise overlap,
   independent-author count, and coverage of the confirmed aspects—not only
   total result count.

If three ranked lists later need a generic fusion baseline, Reciprocal Rank
Fusion is a simple established option; its original study combines rankings
without requiring comparable raw scores. See the original
[RRF paper](https://cormack.uwaterloo.ca/cormack/cormacksigir09-rrf.pdf).

## Product recommendation

Accept `A`, `A B`, `A C` as the default Lite query compiler. Keep the core
object in every query. Replace the user-facing “核心对象 / 首要研究意图 / 使用
场景” decomposition with editable **search vocabulary** and a direct
three-query preview. Do not show a separate research-goal field in the Brief:
every user-visible value in this confirmation must map to a frozen executable
query, so the user can predict the exact search requests.

This change is valuable even without MMR, xQuAD, or learned query expansion.
Those algorithms are optional improvements after the basic query portfolio,
provenance, deduplication, and coverage accounting are correct.
