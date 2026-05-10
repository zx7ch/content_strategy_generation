"""Normative Postgres DDL contract for V2 Phase 1 foundation and ingestion tables."""

from __future__ import annotations

FOUNDATION_TABLES: tuple[str, ...] = (
    "users",
    "workspaces",
    "workspace_members",
    "brands",
    "brand_channels",
    "brand_state_snapshots",
    "brand_policy_configs",
    "topic_pool_items",
    "decision_batches",
    "decision_events",
    "decision_batch_items",
    "candidate_set_snapshots",
    "publish_records",
    "performance_snapshots",
    "feedback_events",
    "scorer_configs",
    "evaluation_runs",
    "evaluation_run_slices",
)

P1_2_EVIDENCE_TABLES: tuple[str, ...] = (
    "ingestion_runs",
    "extension_capture_sessions",
    "data_import_previews",
    "authors",
    "topics",
    "content_items",
    "content_metrics_snapshots",
    "comments",
)


def build_p1_1_schema_sql() -> str:
    return """
CREATE TABLE users (
    id UUID PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE workspaces (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE workspace_members (
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    user_id UUID NOT NULL REFERENCES users(id),
    role TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (workspace_id, user_id)
);

CREATE TABLE brands (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    name TEXT NOT NULL,
    category TEXT,
    stage TEXT NOT NULL,
    target_audience JSONB NOT NULL DEFAULT '{}'::jsonb,
    brand_voice JSONB NOT NULL DEFAULT '{}'::jsonb,
    goals JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_demo BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE brand_channels (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    brand_id UUID NOT NULL REFERENCES brands(id),
    platform TEXT NOT NULL,
    external_account_id TEXT,
    account_name TEXT,
    profile_url TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE brand_state_snapshots (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    brand_id UUID NOT NULL REFERENCES brands(id),
    state_version TEXT NOT NULL,
    stage TEXT NOT NULL,
    state_features JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_type TEXT NOT NULL DEFAULT 'rule_engine',
    source_version TEXT NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL,
    valid_from TIMESTAMPTZ NOT NULL,
    valid_to TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE brand_policy_configs (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    brand_id UUID NOT NULL REFERENCES brands(id),
    policy_name TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    hard_filter_rules JSONB NOT NULL DEFAULT '{}'::jsonb,
    brand_fit_rules JSONB NOT NULL DEFAULT '{}'::jsonb,
    exploration_preset_override JSONB NOT NULL DEFAULT '{}'::jsonb,
    topic_type_targets JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE topic_pool_items (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    brand_id UUID NOT NULL REFERENCES brands(id),
    topic_id UUID,
    title TEXT NOT NULL,
    angle TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    evidence_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_agent TEXT NOT NULL,
    source_run_id UUID,
    status TEXT NOT NULL DEFAULT 'candidate',
    novelty_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    fit_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    trend_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    historical_reward_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    policy_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    final_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    last_scored_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE decision_batches (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    brand_id UUID NOT NULL REFERENCES brands(id),
    brand_state_snapshot_id UUID NOT NULL REFERENCES brand_state_snapshots(id),
    brand_policy_config_id UUID NOT NULL REFERENCES brand_policy_configs(id),
    objective TEXT NOT NULL,
    exploration_mode TEXT NOT NULL,
    context_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    policy_name TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    candidate_count INTEGER NOT NULL,
    chosen_count INTEGER NOT NULL,
    requested_slot_count INTEGER NOT NULL,
    batch_status TEXT NOT NULL DEFAULT 'completed',
    created_by_type TEXT NOT NULL,
    created_by_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE decision_events (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    brand_id UUID NOT NULL REFERENCES brands(id),
    decision_batch_id UUID NOT NULL REFERENCES decision_batches(id),
    brand_state_snapshot_id UUID NOT NULL REFERENCES brand_state_snapshots(id),
    brand_policy_config_id UUID NOT NULL REFERENCES brand_policy_configs(id),
    slot_index INTEGER NOT NULL,
    serving_policy_name TEXT NOT NULL,
    serving_policy_version TEXT NOT NULL,
    logging_policy_name TEXT NOT NULL,
    logging_policy_version TEXT NOT NULL,
    decision_mode TEXT NOT NULL,
    exploration_mode TEXT NOT NULL,
    objective TEXT NOT NULL,
    context_features JSONB NOT NULL,
    candidate_set JSONB NOT NULL,
    ranked_list JSONB NOT NULL,
    chosen_action_id UUID NOT NULL,
    propensities JSONB NOT NULL,
    reward_version TEXT NOT NULL,
    normalization_window_spec JSONB NOT NULL DEFAULT '{}'::jsonb,
    sampling_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE decision_batch_items (
    batch_id UUID NOT NULL REFERENCES decision_batches(id),
    topic_pool_item_id UUID NOT NULL REFERENCES topic_pool_items(id),
    selected_slot_index INTEGER NOT NULL,
    final_rank_position INTEGER NOT NULL,
    source_decision_event_id UUID REFERENCES decision_events(id),
    review_status TEXT NOT NULL DEFAULT 'pending',
    reviewed_at TIMESTAMPTZ,
    reviewed_by_type TEXT,
    reviewed_by_id UUID,
    edited_title TEXT,
    edited_angle TEXT,
    edited_hypothesis TEXT,
    review_notes TEXT,
    score DOUBLE PRECISION NOT NULL,
    reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (batch_id, topic_pool_item_id)
);

CREATE TABLE candidate_set_snapshots (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    brand_id UUID NOT NULL REFERENCES brands(id),
    decision_batch_id UUID NOT NULL REFERENCES decision_batches(id),
    decision_event_id UUID REFERENCES decision_events(id),
    snapshot_scope TEXT NOT NULL,
    slot_index INTEGER,
    candidate_count INTEGER NOT NULL,
    candidate_set JSONB NOT NULL,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE publish_records (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    brand_id UUID NOT NULL REFERENCES brands(id),
    channel_id UUID NOT NULL REFERENCES brand_channels(id),
    topic_pool_item_id UUID REFERENCES topic_pool_items(id),
    decision_event_id UUID REFERENCES decision_events(id),
    decision_batch_id UUID REFERENCES decision_batches(id),
    publish_status TEXT NOT NULL,
    published_at TIMESTAMPTZ,
    content_item_id UUID,
    creative_variant TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CHECK (
        decision_event_id IS NULL OR decision_batch_id IS NOT NULL
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE performance_snapshots (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    brand_id UUID NOT NULL REFERENCES brands(id),
    publish_record_id UUID NOT NULL REFERENCES publish_records(id),
    observation_window_hours INTEGER NOT NULL,
    snapshot_at TIMESTAMPTZ NOT NULL,
    reward_version TEXT NOT NULL,
    raw_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    normalized_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    short_term_reward DOUBLE PRECISION NOT NULL DEFAULT 0,
    long_term_reward DOUBLE PRECISION NOT NULL DEFAULT 0,
    composite_reward DOUBLE PRECISION NOT NULL DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE feedback_events (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    brand_id UUID NOT NULL REFERENCES brands(id),
    publish_record_id UUID NOT NULL REFERENCES publish_records(id),
    decision_event_id UUID,
    event_type TEXT NOT NULL,
    observation_window_hours INTEGER,
    reward_version TEXT NOT NULL,
    reward_window_start_at TIMESTAMPTZ,
    reward_window_end_at TIMESTAMPTZ,
    reward_payload JSONB NOT NULL,
    CHECK (
        decision_event_id IS NULL OR (
            observation_window_hours IS NOT NULL
            AND reward_window_start_at IS NOT NULL
            AND reward_window_end_at IS NOT NULL
        )
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE scorer_configs (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    brand_id UUID NOT NULL REFERENCES brands(id),
    scorer_name TEXT NOT NULL,
    scorer_version TEXT NOT NULL,
    topic_type TEXT,
    confidence_threshold INTEGER NOT NULL,
    max_age_seconds INTEGER NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE evaluation_runs (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    brand_id UUID NOT NULL REFERENCES brands(id),
    evaluation_type TEXT NOT NULL,
    policy_name TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    baseline_policy_name TEXT,
    baseline_policy_version TEXT,
    dataset_start_at TIMESTAMPTZ,
    dataset_end_at TIMESTAMPTZ,
    sample_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by_type TEXT NOT NULL,
    created_by_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);

CREATE TABLE evaluation_run_slices (
    id UUID PRIMARY KEY,
    evaluation_run_id UUID NOT NULL REFERENCES evaluation_runs(id),
    slice_key TEXT NOT NULL,
    slice_value TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_brands_workspace_id ON brands(workspace_id);
CREATE INDEX idx_brand_channels_brand_id ON brand_channels(brand_id);
CREATE INDEX idx_brand_state_snapshots_brand_id ON brand_state_snapshots(brand_id, valid_from DESC);
CREATE INDEX idx_brand_policy_configs_brand_id ON brand_policy_configs(brand_id, is_active);
CREATE INDEX idx_topic_pool_items_brand_id ON topic_pool_items(brand_id);
CREATE INDEX idx_topic_pool_items_status ON topic_pool_items(status);
CREATE INDEX idx_topic_pool_items_final_score ON topic_pool_items(final_score DESC);
CREATE INDEX idx_decision_batches_brand_id ON decision_batches(brand_id);
CREATE INDEX idx_decision_events_brand_id ON decision_events(brand_id);
CREATE INDEX idx_decision_batch_items_slot ON decision_batch_items(batch_id, selected_slot_index);
CREATE INDEX idx_candidate_set_snapshots_batch_id ON candidate_set_snapshots(decision_batch_id);
CREATE INDEX idx_feedback_events_publish_record_id ON feedback_events(publish_record_id);
CREATE INDEX idx_scorer_configs_brand_id ON scorer_configs(brand_id, is_active);
CREATE INDEX idx_evaluation_runs_brand_id ON evaluation_runs(brand_id, created_at DESC);
CREATE INDEX idx_evaluation_run_slices_run_id ON evaluation_run_slices(evaluation_run_id);
""".strip()


def build_p1_5_schema_sql() -> str:
    return """
CREATE TABLE IF NOT EXISTS evaluation_runs (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    brand_id UUID NOT NULL REFERENCES brands(id),
    evaluation_type TEXT NOT NULL,
    policy_name TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    baseline_policy_name TEXT,
    baseline_policy_version TEXT,
    dataset_start_at TIMESTAMPTZ,
    dataset_end_at TIMESTAMPTZ,
    sample_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by_type TEXT NOT NULL,
    created_by_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS evaluation_run_slices (
    id UUID PRIMARY KEY,
    evaluation_run_id UUID NOT NULL REFERENCES evaluation_runs(id),
    slice_key TEXT NOT NULL,
    slice_value TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_evaluation_runs_brand_id ON evaluation_runs(brand_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_evaluation_run_slices_run_id ON evaluation_run_slices(evaluation_run_id);
""".strip()


def build_p1_2_schema_sql() -> str:
    return """
CREATE TABLE ingestion_runs (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    brand_id UUID NOT NULL REFERENCES brands(id),
    entry_type TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_adapter TEXT,
    dedupe_key TEXT,
    source_config JSONB NOT NULL DEFAULT '{}'::jsonb,
    stats JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE extension_capture_sessions (
    capture_session_id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    brand_id UUID NOT NULL REFERENCES brands(id),
    channel_id UUID REFERENCES brand_channels(id),
    capture_token TEXT NOT NULL,
    status TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    captured_at TIMESTAMPTZ,
    preview_payload JSONB,
    ingestion_receipt JSONB,
    error_summary JSONB
);

CREATE TABLE data_import_previews (
    preview_id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    brand_id UUID NOT NULL REFERENCES brands(id),
    file_name TEXT NOT NULL,
    status TEXT NOT NULL,
    uploaded_at TIMESTAMPTZ NOT NULL,
    parsed_row_count INTEGER NOT NULL DEFAULT 0,
    preview_payload JSONB,
    ingestion_receipt JSONB,
    field_errors JSONB NOT NULL DEFAULT '[]'::jsonb,
    error_summary JSONB
);

CREATE TABLE authors (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    platform TEXT NOT NULL,
    platform_author_id TEXT NOT NULL,
    display_name TEXT,
    profile_url TEXT,
    follower_count BIGINT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, platform, platform_author_id)
);

CREATE TABLE topics (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    brand_id UUID NOT NULL REFERENCES brands(id),
    normalized_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    topic_type TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE content_items (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    brand_id UUID REFERENCES brands(id),
    channel_id UUID REFERENCES brand_channels(id),
    author_id UUID REFERENCES authors(id),
    platform TEXT NOT NULL,
    platform_content_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_url TEXT,
    title TEXT,
    body_text TEXT,
    published_at TIMESTAMPTZ,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    content_hash TEXT,
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    topic_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (workspace_id, platform, platform_content_id)
);

CREATE TABLE content_metrics_snapshots (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    content_item_id UUID NOT NULL REFERENCES content_items(id),
    snapshot_at TIMESTAMPTZ NOT NULL,
    likes BIGINT NOT NULL DEFAULT 0,
    comments BIGINT NOT NULL DEFAULT 0,
    collects BIGINT NOT NULL DEFAULT 0,
    shares BIGINT NOT NULL DEFAULT 0,
    views BIGINT,
    follows_gained BIGINT,
    reward_components JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE comments (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    content_item_id UUID NOT NULL REFERENCES content_items(id),
    platform_comment_id TEXT NOT NULL,
    author_name TEXT,
    body_text TEXT NOT NULL,
    commented_at TIMESTAMPTZ,
    sentiment_label TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (workspace_id, content_item_id, platform_comment_id)
);

CREATE INDEX idx_ingestion_runs_brand_id ON ingestion_runs(brand_id, created_at DESC);
CREATE INDEX idx_extension_capture_sessions_brand_id ON extension_capture_sessions(brand_id, created_at DESC);
CREATE INDEX idx_data_import_previews_brand_id ON data_import_previews(brand_id, uploaded_at DESC);
CREATE INDEX idx_topics_brand_id ON topics(brand_id);
CREATE UNIQUE INDEX uq_topics_brand_name ON topics(brand_id, normalized_name);
CREATE INDEX idx_content_items_brand_id ON content_items(brand_id);
CREATE INDEX idx_content_items_author_id ON content_items(author_id);
CREATE INDEX idx_content_items_published_at ON content_items(published_at);
CREATE INDEX idx_content_metrics_content_item_id ON content_metrics_snapshots(content_item_id);
CREATE INDEX idx_content_metrics_snapshot_at ON content_metrics_snapshots(snapshot_at);
""".strip()


def build_p1_2_ingestion_workspace_alignment_sql() -> str:
    return """
ALTER TABLE brand_channels
ADD COLUMN IF NOT EXISTS profile_url TEXT;

CREATE TABLE IF NOT EXISTS extension_capture_sessions (
    capture_session_id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    brand_id UUID NOT NULL REFERENCES brands(id),
    channel_id UUID REFERENCES brand_channels(id),
    capture_token TEXT NOT NULL,
    status TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    captured_at TIMESTAMPTZ,
    preview_payload JSONB,
    ingestion_receipt JSONB,
    error_summary JSONB
);

CREATE TABLE IF NOT EXISTS data_import_previews (
    preview_id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    brand_id UUID NOT NULL REFERENCES brands(id),
    file_name TEXT NOT NULL,
    status TEXT NOT NULL,
    uploaded_at TIMESTAMPTZ NOT NULL,
    parsed_row_count INTEGER NOT NULL DEFAULT 0,
    preview_payload JSONB,
    ingestion_receipt JSONB,
    field_errors JSONB NOT NULL DEFAULT '[]'::jsonb,
    error_summary JSONB
);

CREATE INDEX IF NOT EXISTS idx_extension_capture_sessions_brand_id
ON extension_capture_sessions(brand_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_data_import_previews_brand_id
ON data_import_previews(brand_id, uploaded_at DESC);
""".strip()


def build_p1_6_remove_account_handle_sql() -> str:
    return """
ALTER TABLE brand_channels
ADD COLUMN IF NOT EXISTS profile_url TEXT;

ALTER TABLE brand_channels
DROP COLUMN IF EXISTS account_handle;
""".strip()


def build_p1_s2_6_demo_column_sql() -> str:
    return """
ALTER TABLE brands
ADD COLUMN IF NOT EXISTS is_demo BOOLEAN NOT NULL DEFAULT FALSE;
""".strip()
