# V2 Development Tasks

## 1. Purpose

This document translates [docs/v2/dev_spec.md](/Users/czx/Documents/agentic/content_strategy_generation/docs/v2/dev_spec.md) into phased implementation work.

Rules:

- `docs/v2/dev_spec.md` is the product and system source of truth
- this document decides delivery order
- phase-specific shortcuts, exclusions, and rollout constraints belong here, not in the spec
- the frontend console reference in `docs/v2/dev_spec.md` section `9.7 Frontend Console Direction` is mandatory input for delivery planning
- the static HTML example, module explanations, and frontend implementation direction in the spec must be preserved in full even when the implementation moves from static prototype to `Next.js`
- frontend route shells may be introduced earlier than their business logic phase, but live API integration must not cross the owning phase boundary

Frontend phase-boundary rule:

- `P1-1` may deliver the route shell for `/topic-pool`, `/decisions`, `/publish`, `/performance`, and `/evaluation`
- but only `P1-1`-owned master-data surfaces may claim live integration during `P1-1`
- routes owned by later phases may render shell, loading, empty, or clearly unavailable states before their backend contract is delivered, but they must not pretend to be live with mock business data
- once a phase is delivered, its runtime page behavior must bind to the real backend contract, use route-driven interaction, and surface real errors rather than falling back to fabricated data

## 2. Phase 1

Phase 1 positioning:

- `Phase 1: 可上线闭环`
- goal: let the system realistically support one brand from collection to decision to feedback write-back
- success means the loop is end-to-end usable, even if reward quality is still only directionally correct

### 2.0 Phase 1 Consolidated Rules

The following Phase 1 shortcuts, exclusions, and runtime boundaries are intentionally centralized here rather than repeated across `docs/v2/dev_spec.md`.

- Platform scope:
  - runtime platform scope is `xhs`
  - the browser-extension capture path is the formal `source_sync` adapter for market / competitor capture in this phase
- Ingestion contract:
  - manual historical import uses the fixed `historical_note_import_v1` contract
  - spreadsheet parsing uses a fixed template plus limited alias map
  - operator-driven free-form column mapping is out of scope
- Audience signal scope:
  - `Pattern Insight Agent` may use only `brand_profile`, `comment_signal`, and `behavior_inference`
  - no dependency on platform-exported audience data
- Topic-pool evidence and scoring:
  - when no stronger weighting signal is available, `evidence_summary.sources[].weight` may default to equal weights
  - scorer refresh is triggered through `ScorerService.ensureFresh(...)`
  - `historical_reward_score` depends only on canonical `performance_snapshots`
  - missing `feedback_events` must not block scorer execution
  - present `feedback_events` must not change scorer outputs
  - scorer aggregation is by `topic_type` only; angle-level reward history is out of scope
- Conversion proxy scope:
  - only rate-like proxy values in `[0, 1]` are supported
  - count-based or currency-based proxy values require a later reward version
- Frontend capability boundary:
  - current shipped IA splits the operator flow across:
    - `/brands/[id]` for brand configuration
    - `/data-sources` for search observation and data entry
    - `/data-processing` for preview, validation, and processing history
  - `浏览器采集` and `历史数据上传` use the dual-lane workflow defined in the spec
  - publish workflow is record registration and attribution, not platform auto-posting

### 2.0.1 Spec Reconciliation Decisions

Recorded on `2026-04-18` after review:

- `existing_topic_pool_summary.inventory_item_count` is the canonical field name replacing `active_item_count`
- `inventory_item_count` counts topic-pool rows with `status ∈ {candidate, approved, scheduled}`
- `9.7.3` TypeScript examples must align one-to-one with the canonical schema and API contracts, replacing legacy demo enums and field names
- `9.7 Frontend Console Direction` is the canonical home for frontend runtime behavior, routes, and error-display rules
- `9.0 Brand Config Ingestion Workflow` owns brand-detail ingestion workflow and API interaction only; duplicated UI behavior should not be restated elsewhere
- Phase 1-only rules stay in this delivery-planning document unless a long-lived structural constraint requires inclusion in the spec

Recorded on `2026-04-19` after `P1-S2-5` reconciliation:

- shipped frontend IA is canonical:
  - `/brands/[id]` = 品牌配置
  - `/data-sources` = 搜索观察台 + 数据入口工作区
  - `/data-processing` = 预览 / 校验结果 / 处理历史
- `account_handle` is removed from canonical channel contracts; operator-facing homepage linkage uses `account_name + profile_url`
- older guides that described `/brands/[id]` as the canonical runtime ingestion workspace must be treated as superseded by the split IA above

### 2.1 P1-1 基础与主数据

Delivery:

- SQLite-backed MVP schema aligned with the V2 domain model
- Postgres-compatible schema notes only where they support the future Collaborative Cloud Runtime migration path in [Deployment Spec §10](../deployment/deployment_spec.md#10-project-positioning-statement)
- `workspaces`
- `brands`
- `brand_channels`
- `brand_state_snapshots`
- `brand_policy_configs`
- `topic_pool_items`
- all `decision_*` tables
- `publish_records`
- `performance_snapshots`
- `feedback_events`
- `scorer_configs`
- local-first workspace resolution; basic authentication is deferred until the Collaborative Cloud Runtime or explicit auth milestone
- `workspace`-scoped isolation
- brand-level config read/write
- frontend app scaffold with `Next.js 14+`, App Router, `TypeScript`, and `Tailwind CSS`
- root `layout.tsx`, landing redirect, `TopNav`, and `Sidebar`
- route skeletons for:
  - `/brands`
  - `/brands/[id]`
  - `/data-sources`
  - `/data-processing`
  - `/topic-pool`
  - `/decisions`
  - `/publish`
  - `/performance`
  - `/evaluation`
- shared UI primitives for the console:
  - `Button`
  - `Card`
  - `Table`
  - `StatCard`
- initial frontend type contracts and API contract adapters aligned to:
  - `Brand`
  - `BrandProfileData`
  - `Topic`
  - `DecisionItem`
  - `DataSourcesPageData`
  - `DataProcessingPageData`
  - `DiscoveryWorkspaceData`

Completion standard:

- all business data is isolated by `workspace_id + brand_id`
- `brand_policy_config_id` and `brand_state_snapshot_id` are traceable end to end
- the frontend shell preserves the information architecture from the approved HTML reference in the spec
- sidebar active state follows route state rather than local DOM toggling
- the workspace-level shell is ready for real API integration without changing route structure
- operators move between pages through buttons, links, and route transitions rather than manual API editing or URL hacking

### 2.2 P1-2 数据入口与证据层

Delivery:

- `POST /brands/{id}/source-syncs`
- `POST /brands/{id}/data-imports`
- Chrome extension as the formal Phase 1 `source_sync` adapter
- `historical_note_import_v1`
- `authors`
- `content_items`
- `content_metrics_snapshots`
- `comments`
- `topics`
- frontend entry surfaces for:
  - source sync trigger
  - historical data import trigger
  - ingest status cards and receipts backed by the formal APIs, with real loading and error states

Completion standard:

- market and competitor data can enter the formal data model through `source-syncs`
- manually prepared historical notes can be imported, deduplicated, and persisted reliably
- the console can surface ingestion initiation and status in a way that maps directly to the formal ingestion contracts
- the delivered lane workflow does not rely on mock payloads or hidden fallback branches

### 2.3 P1-3 Agent + Topic Pool

Delivery:

- `Competitor Scan Agent`
- `Pattern Insight Agent`
- `Topic Hypothesis Agent`
- `Topic Pool Normalizer / Enricher`
- normative `evidence_summary` persistence
- `archive_candidates` execution logic
- `/brands` page implementation for brand list, core stats, and brand configuration entry point
- `/topic-pool` page implementation for topic list, score display, source display, and refresh action
- shared table and stat-card composition matching the static console reference

Completion standard:

- agents output only evidence, insight, and proposal layers
- `topic_pool_items` is produced by a deterministic normalization pipeline, not by directly storing raw agent output
- the console can render brand state, target audience, hypothesis, score, and source fields with loading, empty, and error states
- the `Topic Pool` page remains structurally aligned with spec sections `3.5` and `5.3`
- the `Topic Pool` page reads real backend data for delivered functionality and reports backend failures explicitly

### 2.4 P1-4 决策与评分

Delivery:

- `Brand Fit Evaluator`
- `Topic Pool Scorer`
- `ScorerService.ensureFresh(...)`
- `Decision Engine`
- `POST /brands/{id}/decisions/run`
- `PATCH /decision-batches/{id}/items/{slot_index}`
- `/decisions` page implementation for decision stats, selected topic list, and slot-level actions
- route-driven navigation from topic pool to decision execution
- frontend action flows for `accept`, `reject`, and `edit_and_accept`

Completion standard:

- `requested_slot_count = 3` produces 3 non-repeating slot-level `decision_events`
- `topic_type_targets`, `min_ratio = 0`, and `priority_boost` rules are reproducible
- Phase 1 `historical_reward_score` depends only on `performance_snapshots`
- decision results shown in the console are traceable to one persisted `decision_batch`
- slot review actions in the console map cleanly to `PATCH /decision-batches/{id}/items/{slot_index}`
- decision-mode distinctions such as `Exploitation` and `Exploration` are visible in the operator workflow
- route-driven navigation from topic-pool actions to decision review does not depend on manual API manipulation or mock fallback

### 2.5 P1-5 发布反馈与评估

Delivery:

- `POST /publish-records`
- `POST /performance/import`
- `POST /evaluation-runs`
- offline `replay`
- offline `SNIPS`
- ESS diagnostics
- candidate quality metrics
- guardrail monitoring
- `/publish` page implementation for publish record management
- `/performance` page implementation for reward metrics and feedback review
- `/evaluation` page implementation for replay/SNIPS summaries, ESS diagnostics, and failure-case display
- button flows for manual publish, performance import, and evaluation run trigger

Completion standard:

- every non-manual publish can be traced to exactly one `decision_event_id`
- reward is replayable
- evaluation fails closed when candidate set, propensity, or lineage is missing
- the console can display publish lineage, reward snapshots, and evaluation outputs without breaking the end-to-end user workflow
- Phase 1 frontend surfaces cover the full loop from brand setup to evaluation review
- delivered publish, performance, and evaluation pages surface real API errors instead of substituting demo data

### 2.6 Phase 1 Recommended Order

Recommended implementation sequence:

1. schema + migrations
2. brand/profile/policy APIs + frontend shell
3. ingestion + source-sync/data-import entry UI
4. agents + `/brands` and `/topic-pool` pages
5. topic pool normalizer + scorer + topic-pool data integration
6. decision engine + `/decisions` review flow
7. publish + performance + `/publish` and `/performance` pages
8. evaluation + `/evaluation` page
9. end-to-end acceptance across API and console

### 2.7 Phase 1 Launch Gate

Phase 1 is launch-ready only when it can:

- collect market and historical brand content
- generate and maintain a topic pool
- run one candidate decision batch
- let users accept / reject / edit topics
- import post-performance and update scores
- run offline replay and basic guardrail checks
- let a workspace user complete the loop through the frontend console routes defined in the spec

### 2.8 Explicit Phase 1 Exclusions

- No agent-based brand state recognition
- No Thompson Sampling in serving path
- No LinUCB in serving path
- No formal online A/B orchestration
- No automatic rollout expansion
- No automatic exploration tuning
- No doubly robust OPE
- No slate or combinatorial OPE
- No expert-labeled candidate gold sets
- No candidate recall benchmarking
- No dependency on platform-exported audience data

### 2.9 Phase 1 Acceptance

- A brand team inside one `workspace` must be able to complete one full loop:
  - initialize brand profile and policy
  - ingest owned content and market evidence
  - generate topic pool
  - request a recommendation batch
  - review and accept/reject/edit topics
  - record a publish result
  - import performance
  - receive a later recommendation batch that reflects the recorded feedback
- Phase 1 must be operationally `60% usable`:
  - reward may be directionally correct rather than highly accurate
  - scoring may be simple
  - but no closed-loop step may be missing
- A single `decision_batch` with `requested_slot_count = 3` must persist exactly 3 slot-level `decision_events`
- Slot-level decisions must not repeat the same topic within the batch
- Every non-manual `publish_record` must map to exactly one `decision_event_id`
- `feedback_events` must be sufficient to reconstruct chosen action, propensity distribution, reward version, and reward window
- Offline evaluation must fail closed on missing candidate set, invalid propensities, missing reward version, or missing state snapshot lineage
- Candidate quality metrics must be reproducible from stored `candidate_set_snapshots`
- Every `decision_batch` and `decision_event` must resolve to one persisted `brand_state_snapshot` and one active `brand_policy_config`
- `Topic Pool Normalizer / Enricher` must convert candidate proposals into persisted `topic_pool_items` with valid `evidence_summary`
- `Topic Pool Scorer` must be able to refresh stale topic scores without embedding refresh logic in `Decision Engine`
- A second recommendation batch after performance import must show downstream state updates in at least one of:
  - candidate eligibility
  - topic scores
  - ranking order
  - archive state
- The frontend console must expose the same loop through:
  - `/brands`
  - `/topic-pool`
  - `/decisions`
  - `/publish`
  - `/performance`
  - `/evaluation`
- The approved HTML reference in the spec must still be recognizable in navigation structure, page grouping, and primary operator actions after implementation migration

### 2.10 Post-Phase-1 Alignment Backlog

These items come from newer clarifications in `docs/v2/dev_spec.md` and should be tracked as follow-up hardening work rather than inserted back into the nearly finished Phase 1 plan.

#### UI-ALIGN-1: Data Intake Workspace Contract Hardening

Source:

- `docs/v2/dev_spec.md` `9.0 Brand Config Ingestion Workflow`
- `docs/v2/dev_spec.md` `9.7.2 Functional Modules`

Status update:

- `2026-04-15`: implemented initial formal lane workflow
- evidence:
  - backend capture-session and data-import-preview routes added
  - early implementation placed `Data Intake Workspace` lanes on `/brands/[id]`
  - verified by `tests/unit/test_v2_ingestion_service.py`, `tests/unit/test_v2_ingestion_api.py`, and frontend build
- `2026-04-19`: reconciled with shipped IA
  - current runtime route ownership is:
    - `/brands/[id]` = 品牌配置
    - `/data-sources` = 搜索观察台 + 浏览器采集 + 历史数据上传
    - `/data-processing` = 预览 / 校验结果 / 处理历史
  - the original brand-page ingestion framing should now be read as superseded by the split IA above
- remaining nuance:
  - current lane actions still rely on built-in payload scaffolds rather than a real browser-extension return path or real uploaded spreadsheet file parsing
  - keep this nuance visible until the extension bridge and file-upload parser are fully productized

Required follow-up:

- ensure the shipped runtime data-entry surface remains the canonical operator path rather than a developer-only side tool
- ensure both `浏览器采集` and `历史数据上传` lanes implement:
  - entry action area
  - read-only canonical JSON preview
  - status card
  - latest receipt area
- keep `automatic fill + automatic sync` as the default formal workflow
- keep `retry sync` but scope it to the current preview payload rather than forcing the operator to re-enter data
- ensure failure responses expose structured validation or ingestion errors directly in the page state

Done when:

- the combined `/data-sources` + `/data-processing` flow reflects the exact lane behavior defined in spec section `9.0`
- operators do not need to hand-edit JSON, API parameters, or URLs to complete ingestion

#### UI-ALIGN-2: Topic Pool Explainability Surface

Source:

- `docs/v2/dev_spec.md` `3.5`
- `docs/v2/dev_spec.md` `5.3`
- `docs/v2/dev_spec.md` `9.7.2 Functional Modules`

Status update:

- `2026-04-15`: implemented
- evidence:
  - backend explainability payload added to `/brands/{id}/topic-pool`
  - `/topic-pool` now renders score breakdown and evidence provenance
  - verified by `tests/unit/test_v2_topic_pool_service.py` and `tests/unit/test_v2_topic_pool_api.py`

Required follow-up:

- add expandable evidence provenance views for topic-pool items
- expose provenance rows with at least:
  - source link
  - original title
  - interaction metrics
  - signal type
  - contribution weight or relative contribution
- when `final_score` is shown, expose the scorer-owned component breakdown instead of only the aggregate score

Done when:

- operators can inspect why a candidate exists and why it scored as shown without leaving the console

#### UI-ALIGN-3: Runtime Error Honesty and No-Fallback Cleanup

Source:

- `docs/v2/dev_spec.md` `9.7.2 Functional Modules`
- `docs/v2/dev_spec.md` `9.7.3 Implementation Approach`
- `docs/v2/dev_spec.md` `11. Non-Negotiable V2 Rules`

Required follow-up:

- audit shipped frontend routes for any remaining runtime mock fallback, silent demo mode, or fabricated success state
- replace leftover placeholder success flows with real `loading`, `empty`, `error`, and `retry` handling
- preserve real backend error summaries in operator-visible UI where requests fail

Status update:

- `2026-04-17`: implemented runtime no-fallback cleanup for shipped client routes
- evidence:
  - removed mock/demo fallback returns from runtime client loaders in `frontend/src/lib/api.ts`
  - `WorkspaceProvider` now blocks on bootstrap failure and offers retry instead of installing fake workspace identity
  - `/topic-pool`, `/decisions`, `/publish`, `/performance`, and `/evaluation` now surface retryable live read errors rather than demo payloads
  - documented `404` no-data behavior remains preserved for latest decision batch and latest evaluation run
  - verified by `node --test frontend/src/lib/server-api.test.ts frontend/src/lib/api.test.ts` and frontend build

Done when:

- delivered runtime pages no longer mask backend failures with sample data
- mock data remains limited to tests, fixtures, and isolated component verification

### 2.11 Phase 1 Stage 2 Completion Tasks

These tasks convert the remaining Phase 1 gaps and alignment follow-ups into a concrete second-stage completion plan. They are still Phase 1 work: the goal is to fully close the shipped closed-loop contract rather than expand into Phase 2 capabilities.

#### 2.11.1 P1-S2-1 Data Intake Workspace Productization

Goal:

- finish the operator-facing ingestion workflow so the shipped `品牌配置 -> 数据源 -> 数据处理` path fully covers the formal data-intake surface defined by spec section `9.0`

Delivery:

- complete the capture-session workflow:
  - `POST /brands/{id}/extension-capture-sessions`
  - `GET /brands/{id}/extension-capture-sessions/{session_id}`
  - `POST /extension-captures`
- complete the historical-import preview workflow:
  - `POST /brands/{id}/data-import-previews`
  - `GET /brands/{id}/data-import-previews/{preview_id}`
- replace built-in payload scaffolds with:
  - real extension return-path handling
  - real uploaded spreadsheet parsing
- make the shipped `数据源 / 数据处理` flow the canonical ingestion workspace with:
  - entry action area
  - read-only canonical preview
  - status card
  - latest receipt
  - retry sync against the current preview payload
  - structured failure rendering

Completion standard:

- operators can complete both ingestion lanes from the shipped `数据源 / 数据处理` flow without hand-editing JSON, API parameters, or URLs
- lane state survives refresh through persisted capture-session / preview records
- automatic fill + automatic sync remains the default formal workflow
- runtime page state reflects the exact workflow defined in spec section `9.0`

#### 2.11.2 P1-S2-2 Topic Pool Explainability And Scorer Completion

Goal:

- finish the Phase 1 topic-pool execution contract so candidates are not only generated, but also formally evaluated, rescored, and explainable to operators

Delivery:

- implement:
  - `Brand Fit Evaluator`
  - `Topic Pool Scorer`
  - `ScorerService.ensureFresh(...)`
- replace placeholder topic-pool scoring with the real Phase 1 scorer contract
- keep `historical_reward_score` driven only by canonical `performance_snapshots`
- ensure scorer refresh remains outside `Decision Engine`
- expand `/topic-pool` operator explainability with:
  - expandable evidence provenance views
  - source rows containing link, original title, interaction metrics, signal type, and contribution weight
  - scorer-owned component breakdown whenever `final_score` is shown

Completion standard:

- topic-pool candidates expose real scorer component values rather than placeholder scores
- stale topic scores can be refreshed through the scorer service boundary
- operators can inspect both why a candidate exists and why it scored as shown without leaving the console

#### 2.11.3 P1-S2-3 Feedback-Driven Second-Batch Closure

Goal:

- prove the full Phase 1 loop includes downstream state update after feedback, not just a single pass from setup to evaluation

Delivery:

- extend the API and acceptance path so a second recommendation batch is run after:
  - publish record creation
  - performance import
- assert at least one downstream update in:
  - candidate eligibility
  - topic scores
  - ranking order
  - archive state
- update release-gate and acceptance evidence so the second-batch effect is part of Phase 1 proof, not an implicit assumption

Completion standard:

- acceptance artifacts show a first recommendation batch, a feedback write-back step, and a second batch with observable downstream change
- the release gate proves that the system is not merely logging feedback, but incorporating it into later decisions

#### 2.11.4 P1-S2-4 Deployment Runtime Convergence

Goal:

- remove the remaining gap between Phase 1 contract proof and the active deployment target defined in [Deployment Spec §1.4 Deployment Model](../deployment/deployment_spec.md#14-deployment-model)
- keep Postgres work scoped to the future Collaborative Cloud Runtime migration path described in [Deployment Spec §10 Project Positioning Statement](../deployment/deployment_spec.md#10-project-positioning-statement)

Delivery:

- complete local-first runtime support across:
  - foundation
  - ingestion
  - topic pool
  - decision
  - feedback / evaluation
- keep SQLite-backed persistence as the default shipped MVP runtime contract
- keep in-memory stores available only for tests and isolated local acceptance where appropriate
- keep Postgres schema/migration work as future migration preparation, not a Phase 1 launch blocker
- run schema, migration, and task-scoped validation against the local-first path

Completion standard:

- SQLite is the default local-first runtime source of truth for shipped Phase 1 behavior
- migrations, runtime DDL, and service/store contracts remain in parity
- full-loop validation can run without depending on in-memory-only behavior

#### 2.11.5 P1-S2-5 Frontend Contract And Guide Reconciliation

Goal:

- reconcile the remaining Phase 1 implementation guides and frontend contracts with the latest canonical spec terminology and route behavior

Delivery:

- update Phase 1 guides so they align with the canonical contract names introduced in:
  - `docs/v2/dev_spec.md` `9.7.3`
  - `docs/v2/development_tasks.md` `2.0.1`
- replace remaining mock-era or stale frontend contract terminology such as:
  - outdated `DecisionItem` shape
  - outdated publish / performance / evaluation read-model names
  - stale topic-pool inventory naming
- ensure delivered route behavior in guides matches the canonical ownership split:
  - `9.0` owns ingestion workflow
  - `9.7` owns frontend runtime behavior

Completion standard:

- Phase 1 guides can be used as implementation inputs without having to reinterpret outdated type names or duplicated rules
- frontend type contracts, API helper naming, route ownership, and guide terminology are consistent with the current spec

### 2.12 Phase 1 Final Closure Gate

Phase 1 should be considered fully closed only when:

- the shipped runtime uses the formal ingestion workspace across `品牌配置 -> 数据源 -> 数据处理`
- topic pool scoring and explainability use the real Phase 1 scorer contract
- a second recommendation batch after feedback shows downstream state change
- local-first SQLite-backed runtime behavior is the default shipped path, with Postgres kept as future Collaborative Cloud Runtime migration work
- Phase 1 guides, frontend contracts, and the canonical spec no longer drift in terminology or ownership

Recommended completion order:

1. `P1-S2-1 Data Intake Workspace Productization`
2. `P1-S2-2 Topic Pool Explainability And Scorer Completion`
3. `P1-S2-3 Feedback-Driven Second-Batch Closure`
4. `P1-S2-4 Deployment Runtime Convergence`
5. `P1-S2-5 Frontend Contract And Guide Reconciliation`

## 3. Phase 2

Phase 2 positioning:

- `Phase 2: 学习增强与实验化验证`
- goal: improve policy quality and evaluation rigor without changing the closed-loop core already proven in Phase 1
- success means the system learns better and experiments more safely, while keeping the same brand workflow stable

### 3.1 P2-1 学习策略升级

Delivery:

- `thompson_sampling_v1`
- posterior updates in `learning_states`
- offline comparison of baseline vs Thompson before any serving rollout
- scorer and policy versioning needed for parallel policy evaluation
- decision and evaluation UI upgrades for baseline vs Thompson comparison
- frontend state-management upgrade path from `React Context` to `Zustand` if cross-route policy state becomes complex
- `SWR` polling and cache strategy for dashboard-like freshness on decision, performance, and evaluation pages

Completion standard:

- Thompson policy can be replayed against historical decision logs
- posterior state updates are persisted and reproducible
- no learning policy enters serving before outperforming baseline offline on supported slices
- policy comparison results are visible in the console rather than only in backend logs or raw tables

### 3.2 P2-2 实验与评估增强

Delivery:

- formal online experiment orchestration:
  - `experiments`
  - `experiment_arms`
  - `experiment_assignments`
- small-traffic A/B support
- candidate quality expansion:
  - expert-labeled gold candidate sets
  - `candidate_recall@k`
  - ranking quality benchmarks
  - generator-to-policy attribution analysis
- reward validity expansion:
  - reward-target correlation
  - reward component stability
  - lagged reward consistency
  - reward-hacking signal detection
- state-recognition validation loops for `brand_stage`
- evaluation page expansion for:
  - experiment results
  - candidate quality benchmarking
  - reward validity drill-down
  - slice-based diagnostics

Completion standard:

- one policy experiment can be configured, assigned, and analyzed through formal experiment tables
- reward validity can be reported beyond simple proxy correlation
- candidate generation quality can be measured separately from ranking quality
- operators can inspect why a policy improved or degraded through the console without leaving the product surface

### 3.3 P2-3 控制与安全

Delivery:

- serving policy and logging policy divergence under explicit configuration
- configuration-driven ESS review workflow
- weighted topic coverage if business quotas require it
- stronger rollout review process tied to offline and online diagnostics
- rollout control and ESS review surfaces in the frontend console
- explicit UI affordances for low-ESS warnings, guardrail failures, and review-required states

Completion standard:

- logging policy can remain evaluation-safe while serving policy evolves
- low-ESS situations surface a review workflow instead of silent rollout
- business coverage constraints can be expressed without breaking replayability
- unsafe rollout states are visible and reviewable in the console before operators expand traffic

### 3.4 Phase 2 Recommended Order

Recommended implementation sequence:

1. Thompson offline replay support
2. posterior persistence in `learning_states`
3. decision and evaluation UI support for policy comparison
4. experiment tables and assignment flow
5. small-traffic A/B support
6. candidate quality and reward-validity expansion
7. ESS review and rollout control in backend and console

### 3.5 Phase 2 Launch Gate

Phase 2 is considered complete only when it can:

- compare baseline and Thompson on the same logged history
- run a formal low-risk online experiment
- explain whether gains come from better candidates, better ranking, or reward artifacts
- stop unsafe rollout when ESS or guardrails fail
- preserve the same end-to-end brand workflow proven in Phase 1

## 4. Phase 3

Phase 3 positioning:

- `Phase 3: 强适应策略与高级评估`
- goal: move from safe bandit serving to stronger adaptive decision systems and richer off-policy evaluation
- success means the system can operate more autonomously without sacrificing traceability or safety

### 4.1 P3-1 策略能力升级

Delivery:

- `linucb_v1`
- richer context features
- online model updates
- policy comparison and rollout automation based on evaluation gates
- richer frontend diagnostics for contextual features, policy state visibility, and rollout-gate inspection

Completion standard:

- contextual policies can consume richer state and candidate features
- online updates are versioned and recoverable
- policy promotion is gated by explicit evaluation outcomes rather than manual judgment alone
- operators can inspect the context features and promotion evidence that drove a rollout decision

### 4.2 P3-2 高级评估升级

Delivery:

- doubly robust evaluation when support coverage is sufficient
- slate-aware or combinatorial evaluation if multi-slot dependence becomes material
- position-aware or dependency-aware models if batch ordering affects outcomes
- advanced evaluation visualizations for multi-slot, position-aware, and support-coverage analysis

Completion standard:

- OPE can move beyond replay/SNIPS when data support is adequate
- multi-slot recommendation quality can be evaluated without pretending slots are independent
- ordering effects are measurable when they begin to affect real outcomes
- advanced evaluation outputs remain understandable through productized console views rather than only analyst-side queries

### 4.3 P3-3 自动化运营

Delivery:

- semi-automatic or automatic exploration tuning driven by ESS and guardrails
- controlled rollout automation
- policy promotion / rollback workflow
- operator console support for automated exploration tuning visibility, promotion state, and rollback execution review

Completion standard:

- exploration settings can adapt based on evidence instead of static presets alone
- rollout expansion and rollback are executable operational workflows
- policy lifecycle management no longer depends on manual ad hoc intervention
- automation remains auditable from the frontend control surface

### 4.4 Phase 3 Recommended Order

Recommended implementation sequence:

1. richer context feature pipeline
2. `linucb_v1`
3. frontend diagnostics for contextual policy inspection
4. advanced OPE methods + advanced evaluation views
5. exploration automation
6. rollout automation and policy lifecycle tooling with console review surfaces

### 4.5 Phase 3 Launch Gate

Phase 3 is considered complete only when it can:

- serve contextual policies with recoverable online updates
- evaluate those policies with stronger OPE than replay/SNIPS alone
- account for multi-slot dependencies when needed
- automate rollout, promotion, and rollback without losing auditability

## 5. Notes

- Features listed in later phases may be explored earlier, but must not block Phase 1 launch
- Any feature that changes learning semantics, reward semantics, or evaluation support must update both:
  - [docs/v2/dev_spec.md](/Users/czx/Documents/agentic/content_strategy_generation/docs/v2/dev_spec.md)
  - this phased task document

## 6. Future TODOs

### AUTH-1: 用户登录与多租户身份管理

**背景**：当前实现采用 [Deployment Spec §1.2 Core Positioning](../deployment/deployment_spec.md#12-core-positioning) 定义的 local-first MVP 部署模型。SSR 页面不得读取用户本地 Agent Runtime；任何仍通过 SSR 或 `server-api.ts` 调用 `localhost` 的页面必须按 [Deployment Spec §7.3](../deployment/deployment_spec.md#73-server-components-accessing-local-api) 改为浏览器侧 runtime API。现有本地开发/验收流程中包含默认 workspace seed 与示例数据补齐，但这属于联调与验收便利，不代表正式产品的多租户与品牌初始化规范。

**触发条件**：当有多个品牌团队需要使用同一套部署，或需要区分用户操作权限时实施。

**需要实现**：
- `users` 表注册/登录接口（邮箱 + 密码或 OAuth）
- JWT/Session 签发与校验，替换当前 header-based workspace 声明
- `workspace_members` 表写入与查询，用于 workspace 归属校验
- 前端登录页、token 存储、自动刷新
- `WorkspaceProvider` 与浏览器侧 workspace resolver 改为从 auth token 解析 workspace_id/user_id，替换当前 `/workspaces/default` 拉取方式；SSR resolver 只可用于不依赖本地 Agent Runtime 的云端/静态内容

**改动范围**：
- `app/v2/auth.py`：`resolve_workspace_principal` 改为从 JWT payload 解析
- `app/v2/foundation/bootstrap.py`：移除默认 workspace / demo data seed；`GET /workspaces/default` 替换为标准 auth 端点
- `frontend/src/components/providers/WorkspaceProvider.tsx`：改为 auth context，从 token 获取 workspace/user
- `frontend/src/lib/api.ts`：default workspace resolver 替换为从 auth context 读取
- `frontend/src/lib/server-api.ts`：不得调用用户本地 Agent Runtime；仅保留静态或云端自有数据读取职责
