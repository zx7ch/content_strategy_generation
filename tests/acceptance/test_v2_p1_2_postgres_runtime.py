from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path

import pytest

from app.config import Settings
from app.v2.decision.bootstrap import build_decision_runtime
from app.v2.feedback.bootstrap import build_feedback_runtime
from app.v2.foundation.bootstrap import build_master_data_runtime
from app.v2.ingestion.bootstrap import build_ingestion_runtime
from app.v2.topic_pool.bootstrap import build_topic_pool_runtime
from app.v2.topic_pool.scorer import ScorerService

@pytest.fixture(scope="session")
def postgres_runtime_ready(acceptance_enabled: None) -> None:
    if importlib.util.find_spec("py_pglite") is None:
        pytest.skip("py-pglite is required for real Postgres runtime acceptance tests")
    if importlib.util.find_spec("psycopg") is None:
        pytest.skip("psycopg is required for real Postgres runtime acceptance tests")

    work_dir = Path(__file__).resolve().parents[2] / ".tmp" / "py_pglite_runtime"
    if not (work_dir / "node_modules").exists():
        pytest.skip("prepare .tmp/py_pglite_runtime/node_modules before running real Postgres tests")


@pytest.fixture
def pglite_dsn(postgres_runtime_ready: None):
    from py_pglite import PGliteConfig, PGliteManager

    work_dir = Path(__file__).resolve().parents[2] / ".tmp" / "py_pglite_runtime"
    config = PGliteConfig(
        timeout=30,
        cleanup_on_exit=True,
        auto_install_deps=False,
        work_dir=work_dir,
        use_tcp=True,
        tcp_port=55432,
    )
    manager = PGliteManager(config)
    manager.start()
    try:
        dsn = manager.get_connection_string().replace("postgresql+psycopg://", "postgresql://")
        yield dsn
    finally:
        manager.stop()


@pytest.mark.acceptance
@pytest.mark.real_dependency
def test_real_postgres_historical_import_persists_across_runtime_instances(
    acceptance_enabled: None,
    pglite_dsn: str,
) -> None:
    settings = Settings(_env_file=None, POSTGRES_DSN=pglite_dsn)

    _, master_service = build_master_data_runtime(settings)
    _, ingestion_service = build_ingestion_runtime(settings)

    workspace = master_service.create_workspace(name="Acme", slug="acme-real")
    brand = master_service.create_brand(
        workspace_id=workspace.id,
        name="Acme Outdoor",
        category="outdoor",
        stage="cold_start",
    )

    result = ingestion_service.create_data_import(
        workspace_id=workspace.id,
        brand_id=brand.id,
        import_type="historical_note_import_v1",
        platform="xiaohongshu",
        rows=[
            {
                "published_at": "2025-09-10T12:00:00+08:00",
                "title": "换季敏感肌稳定住了",
                "body_text": "正文内容",
                "likes": 320,
                "collects": 96,
                "comments": 28,
                "shares": 31,
                "source_url": "https://www.xiaohongshu.com/explore/abc",
            }
        ],
    )

    assert result.status == "accepted"
    assert result.imported_item_count == 1

    _, second_master_service = build_master_data_runtime(settings)
    _, second_ingestion_service = build_ingestion_runtime(settings)

    brands = second_master_service.list_brands(workspace_id=workspace.id)
    runs = second_ingestion_service.list_ingestion_runs(brand_id=brand.id)
    items = second_ingestion_service.list_content_items(brand_id=brand.id)

    assert [item.name for item in brands] == ["Acme Outdoor"]
    assert len(runs) == 1
    assert runs[0].entry_type == "data_import"
    assert runs[0].stats["accepted_row_count"] == 1
    assert len(items) == 1
    assert items[0].title == "换季敏感肌稳定住了"
    assert items[0].platform == "xiaohongshu"


@pytest.mark.acceptance
@pytest.mark.real_dependency
def test_real_postgres_source_sync_dedupes_and_persists_latest_metrics(
    acceptance_enabled: None,
    pglite_dsn: str,
) -> None:
    settings = Settings(_env_file=None, POSTGRES_DSN=pglite_dsn)

    _, master_service = build_master_data_runtime(settings)
    _, ingestion_service = build_ingestion_runtime(settings)

    workspace = master_service.create_workspace(name="Beta", slug="beta-real")
    brand = master_service.create_brand(
        workspace_id=workspace.id,
        name="Beta Beauty",
        category="beauty",
        stage="growth",
    )
    channel = master_service.create_brand_channel(
        workspace_id=workspace.id,
        brand_id=brand.id,
        platform="xhs",
        account_name="Beta Beauty",
        profile_url="https://www.xiaohongshu.com/user/profile/beta-beauty",
    )

    result = ingestion_service.create_source_sync(
        workspace_id=workspace.id,
        brand_id=brand.id,
        source_type="xhs_extension_capture",
        source_adapter="extension_source_sync_adapter_v1",
        channel_id=channel.id,
        capture_payload={
            "page_type": "search_result",
            "captured_at": "2026-04-11T10:00:00+08:00",
            "items": [
                {
                    "note_id": "abc123",
                    "source_url": "https://www.xiaohongshu.com/explore/abc123?xsec_token=1",
                    "title": "轻量徒步装备",
                    "visible_text_excerpt": "正文摘要一",
                    "author_handle": "competitor-a",
                    "author_name": "竞品A",
                    "likes": 100,
                    "comments": 10,
                    "collects": 20,
                    "shares": 3,
                },
                {
                    "note_id": "abc123",
                    "source_url": "https://www.xiaohongshu.com/explore/abc123?xsec_token=2",
                    "title": "轻量徒步装备更新版",
                    "visible_text_excerpt": "正文摘要二",
                    "author_handle": "competitor-a",
                    "author_name": "竞品A",
                    "likes": 188,
                    "comments": 22,
                    "collects": 41,
                    "shares": 7,
                },
            ],
        },
    )

    assert result.imported_item_count == 1
    assert result.deduped_item_count == 1

    _, second_ingestion_service = build_ingestion_runtime(settings)
    items = second_ingestion_service.list_content_items(brand_id=brand.id)
    runs = second_ingestion_service.list_ingestion_runs(brand_id=brand.id)

    assert len(items) == 1
    assert items[0].channel_id == channel.id
    assert items[0].title == "轻量徒步装备更新版"
    assert items[0].metadata["normalized_source_url"] == "https://www.xiaohongshu.com/explore/abc123"
    assert len(runs) == 1
    assert runs[0].entry_type == "source_sync"
    assert runs[0].stats["deduped_item_count"] == 1


@pytest.mark.acceptance
@pytest.mark.real_dependency
def test_real_postgres_phase1_loop_persists_across_runtime_rebuilds(
    acceptance_enabled: None,
    pglite_dsn: str,
) -> None:
    settings = Settings(_env_file=None, APP_ENV="production", POSTGRES_DSN=pglite_dsn)

    _, master_service = build_master_data_runtime(settings)
    ingestion_store, ingestion_service = build_ingestion_runtime(settings)
    topic_pool_store, topic_pool_service = build_topic_pool_runtime(
        settings,
        master_data_service=master_service,
        ingestion_store=ingestion_store,
    )
    decision_store, decision_service = build_decision_runtime(
        settings,
        master_data_service=master_service,
        topic_pool_store=topic_pool_store,
    )
    feedback_store, feedback_service = build_feedback_runtime(
        settings,
        master_data_service=master_service,
        topic_pool_store=topic_pool_store,
        decision_store=decision_store,
    )
    scorer_service = ScorerService(
        master_data_service=master_service,
        topic_pool_store=topic_pool_store,
        feedback_store=feedback_store,
    )
    topic_pool_service.attach_scorer_service(scorer_service)
    decision_service.attach_scorer_service(scorer_service)

    workspace = master_service.create_workspace(name="Gamma", slug="gamma-real")
    brand = master_service.create_brand(
        workspace_id=workspace.id,
        name="Gamma Outdoor",
        category="outdoor",
        stage="growth",
        target_audience={"age_ranges": ["25-34"], "gender_skew": "female"},
    )
    channel = master_service.create_brand_channel(
        workspace_id=workspace.id,
        brand_id=brand.id,
        platform="xiaohongshu",
        account_name="Gamma 小红书",
        profile_url="https://www.xiaohongshu.com/user/profile/gamma-outdoor",
    )
    policy = master_service.replace_active_brand_policy_config(
        workspace_id=workspace.id,
        brand_id=brand.id,
        policy_name="baseline_rule_v1",
        policy_version="v1",
        topic_type_targets={
            "targets": [
                {"topic_type": "scenario", "min_ratio": 0.34, "max_ratio": 1.0, "priority_boost": 0.12},
                {"topic_type": "problem", "min_ratio": 0.0, "max_ratio": 0.5, "priority_boost": 0.03},
            ]
        },
    )
    snapshot = master_service.create_brand_state_snapshot(
        workspace_id=workspace.id,
        brand_id=brand.id,
        state_version="state_v1",
        stage="growth",
        state_features={"audience_focus": "urban commuting"},
        source_version="v1",
    )

    ingestion_result = ingestion_service.create_source_sync(
        workspace_id=workspace.id,
        brand_id=brand.id,
        source_type="xhs_extension_capture",
        source_adapter="extension_source_sync_adapter_v1",
        channel_id=channel.id,
        capture_payload={
            "page_type": "search_result",
            "captured_at": "2026-04-11T10:00:00+08:00",
            "items": [
                {
                    "note_id": "note-1",
                    "source_url": "https://www.xiaohongshu.com/explore/note-1",
                    "title": "通勤徒步鞋怎么选",
                    "visible_text_excerpt": "解决上下班和周末轻徒步切换问题",
                    "author_handle": "competitor-a",
                    "likes": 128,
                    "comments": 22,
                    "collects": 63,
                    "shares": 11,
                    "tags": ["通勤", "徒步"],
                },
                {
                    "note_id": "note-2",
                    "source_url": "https://www.xiaohongshu.com/explore/note-2",
                    "title": "尺码痛点怎么避坑",
                    "visible_text_excerpt": "买鞋最怕前掌挤脚和后跟磨脚",
                    "author_handle": "competitor-b",
                    "likes": 116,
                    "comments": 25,
                    "collects": 48,
                    "shares": 6,
                    "tags": ["避坑", "尺码"],
                },
                {
                    "note_id": "note-3",
                    "source_url": "https://www.xiaohongshu.com/explore/note-3",
                    "title": "周末轻徒步穿搭清单",
                    "visible_text_excerpt": "从鞋包到外套的一套轻量方案",
                    "author_handle": "competitor-c",
                    "likes": 104,
                    "comments": 14,
                    "collects": 41,
                    "shares": 5,
                    "tags": ["穿搭", "徒步"],
                },
            ],
        },
    )
    assert ingestion_result.imported_item_count == 3

    topic_refresh = topic_pool_service.refresh_topic_pool(
        workspace_id=workspace.id,
        brand_id=brand.id,
        archive_threshold_days=60,
    )
    assert topic_refresh.generated_item_count >= 1

    topic_pool = topic_pool_service.list_topic_pool(workspace_id=workspace.id, brand_id=brand.id)
    assert topic_pool.items

    decision = decision_service.run_decision_batch(
        workspace_id=workspace.id,
        brand_id=brand.id,
        requested_slot_count=3,
        objective="topic_recommendation",
        exploration_mode="balanced",
    )
    assert decision.chosen_count == 3

    first_item = decision.items[0]
    publish = feedback_service.create_publish_record(
        workspace_id=workspace.id,
        brand_id=brand.id,
        channel_id=channel.id,
        topic_pool_item_id=first_item.topic_pool_item_id,
        decision_event_id=first_item.decision_event_id,
        decision_batch_id=decision.batch_id,
        publish_status="published",
        creative_variant="v1",
    )
    performance = feedback_service.import_performance_snapshot(
        workspace_id=workspace.id,
        publish_record_id=publish.id,
        observation_window_hours=168,
        snapshot_at=datetime.fromisoformat("2026-04-17T09:30:00+08:00"),
        reward_version="reward_v1",
        metrics={
            "impressions": 12000,
            "clicks": 850,
            "likes": 320,
            "comments": 28,
            "collects": 96,
            "shares": 31,
            "follows_gained": 12,
            "conversion_proxy": {
                "value": 0.08,
                "type": "store_click_rate",
                "source": "manual_import",
            },
        },
    )
    assert performance.composite_reward > 0

    evaluation = feedback_service.create_evaluation_run(
        workspace_id=workspace.id,
        brand_id=brand.id,
        evaluation_type="replay",
    )
    assert evaluation.sample_count >= 1

    _, second_master_service = build_master_data_runtime(settings)
    second_ingestion_store, second_ingestion_service = build_ingestion_runtime(settings)
    second_topic_pool_store, second_topic_pool_service = build_topic_pool_runtime(
        settings,
        master_data_service=second_master_service,
        ingestion_store=second_ingestion_store,
    )
    second_decision_store, _second_decision_service = build_decision_runtime(
        settings,
        master_data_service=second_master_service,
        topic_pool_store=second_topic_pool_store,
    )
    second_feedback_store, second_feedback_service = build_feedback_runtime(
        settings,
        master_data_service=second_master_service,
        topic_pool_store=second_topic_pool_store,
        decision_store=second_decision_store,
    )
    second_scorer = ScorerService(
        master_data_service=second_master_service,
        topic_pool_store=second_topic_pool_store,
        feedback_store=second_feedback_store,
    )
    second_topic_pool_service.attach_scorer_service(second_scorer)

    brands = second_master_service.list_brands(workspace_id=workspace.id)
    topics = second_topic_pool_service.list_topic_pool(workspace_id=workspace.id, brand_id=brand.id)
    runs = second_ingestion_service.list_ingestion_runs(brand_id=brand.id)
    publishes = second_feedback_service.list_publish_records(workspace_id=workspace.id, brand_id=brand.id)
    snapshots = second_feedback_service.list_performance_snapshots(workspace_id=workspace.id, brand_id=brand.id)
    latest_eval = second_feedback_service.get_latest_evaluation_run(workspace_id=workspace.id, brand_id=brand.id)

    assert [item.name for item in brands] == ["Gamma Outdoor"]
    assert policy.brand_id == brand.id
    assert snapshot.brand_id == brand.id
    assert len(runs) == 1
    assert runs[0].entry_type == "source_sync"
    assert topics.total_candidate_count >= 1
    assert len(publishes) == 1
    assert publishes[0].channel_id == channel.id
    assert len(snapshots) == 1
    assert snapshots[0].composite_reward > 0
    assert latest_eval is not None
    assert latest_eval.sample_count >= 1
