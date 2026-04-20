"""Unit tests for EngagementAnalyzer (P2-1 acceptance coverage)."""

from __future__ import annotations

from app.models.session import PlatformPreference, Proposal
from app.services.engagement_analyzer import EngagementAnalyzer
from app.services.xhs_spider import XHSPost


def _make_post(
    note_id: str,
    title: str,
    content: str,
    tags: list[str],
    liked: int,
    collected: int,
    comments: int,
    shares: int,
) -> XHSPost:
    return XHSPost(
        note_id=note_id,
        title=title,
        content=content,
        author="author-a",
        tags=tags,
        liked_count=liked,
        collected_count=collected,
        comment_count=comments,
        share_count=shares,
        note_url=f"https://xhs.com/{note_id}",
        images=["img1"],
    )


def _make_proposal(pid: str, hook: str, outline: str, tags: list[str]) -> Proposal:
    return Proposal(
        proposal_id=pid,
        angle="angle",
        hook=hook,
        outline=outline,
        target_emotion="共鸣",
        content_pillars=["pillar"],
        suggested_tags=tags,
    )


def test_score_posts_uses_lambda_formula_and_sorted_descending():
    analyzer = EngagementAnalyzer()
    posts = [
        _make_post("n1", "A", "content", ["tag1"], liked=100, collected=10, comments=999, shares=999),
        _make_post("n2", "B", "content", ["tag2"], liked=50, collected=30, comments=0, shares=0),
    ]

    scored = analyzer.score_posts(posts, lambda_weight=0.7)

    # norm_likes: n1=1.0, n2=0.0
    # norm_collects: n1=0.0, n2=1.0
    # score = 0.7*norm_likes + 0.3*norm_collects
    # n1=0.7, n2=0.3; comments/shares must not affect score.
    assert scored[0].post.note_id == "n1"
    assert scored[0].raw_score == 70
    assert scored[1].raw_score == 30


def test_score_posts_normalized_values_are_in_zero_one_range():
    analyzer = EngagementAnalyzer()
    posts = [
        _make_post("n1", "A", "content", ["tag1"], liked=0, collected=0, comments=0, shares=0),
        _make_post("n2", "B", "content", ["tag2"], liked=10, collected=0, comments=0, shares=0),
        _make_post("n3", "C", "content", ["tag3"], liked=30, collected=0, comments=0, shares=0),
    ]

    scored = analyzer.score_posts(posts)
    for item in scored:
        assert 0.0 <= item.score <= 1.0


def test_score_posts_when_all_raw_scores_equal_normalized_to_zero():
    analyzer = EngagementAnalyzer()
    posts = [
        _make_post("n1", "A", "x", ["tag"], liked=1, collected=1, comments=1, shares=1),
        _make_post("n2", "B", "x", ["tag"], liked=1, collected=1, comments=1, shares=1),
    ]

    scored = analyzer.score_posts(posts)
    assert all(item.score == 0.0 for item in scored)


def test_analyze_platform_preferences_returns_expected_fields():
    analyzer = EngagementAnalyzer()
    posts = [
        _make_post("n1", "三分钟学会抹茶拿铁", "短内容", ["抹茶", "教程"], liked=1, collected=1, comments=1, shares=1),
        _make_post("n2", "抹茶拿铁避坑清单", "这是一个偏长一点的内容" * 20, ["抹茶", "避坑"], liked=2, collected=2, comments=2, shares=2),
    ]

    pref = analyzer.analyze_platform_preferences(posts)

    assert pref.avg_title_length > 0
    assert pref.popular_tags[0] == "抹茶"
    assert len(pref.optimal_posting_times) >= 3
    assert len(pref.content_patterns) >= 1


def test_score_proposals_ranks_by_preference_fit():
    analyzer = EngagementAnalyzer()
    pref = PlatformPreference(
        avg_title_length=12,
        popular_tags=["抹茶", "教程"],
        optimal_posting_times=["20:00"],
        content_patterns=["疑问式标题", "中等长度文案"],
    )

    high_fit = _make_proposal(
        "p1",
        "抹茶拿铁怎么做？",
        "步骤1步骤2步骤3" * 10,
        ["抹茶", "教程"],
    )
    low_fit = _make_proposal(
        "p2",
        "随便聊聊",
        "短",
        ["旅行"],
    )

    ranked = analyzer.score_proposals([low_fit, high_fit], pref)

    assert ranked[0].proposal_id == "p1"
    assert ranked[0].score > ranked[1].score


def test_scoring_algorithm_has_documentation():
    doc = EngagementAnalyzer.__doc__ or ""
    assert "Raw engagement formula" in doc
    assert "Min-Max normalization" in doc
