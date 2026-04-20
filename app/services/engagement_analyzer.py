"""Engagement analyzer for post scoring and platform preference extraction.

Scoring contract (docs/api_schemas.md §1.2):
- engagement_rate = lambda_weight * norm_likes + (1 - lambda_weight) * norm_collects
- score_posts returns sorted scores and keeps both score(0-1) and raw_score(0-100).
- Normalization uses Min-Max in [0, 1].
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import statistics
from typing import Dict, Iterable, List, Tuple

from app.models.session import PlatformPreference, Proposal
from app.services.xhs_spider import XHSPost


@dataclass(slots=True)
class EngagementScore:
    """Scored post with raw and normalized score."""

    post: XHSPost
    score: float  # normalized score in [0, 1]
    raw_score: int


class EngagementAnalyzer:
    """Analyze engagement signals and rank content proposals.

    Algorithm notes:
    1. Raw engagement formula: lambda_weight * norm_likes + (1 - lambda_weight) * norm_collects.
    2. Min-Max normalization on likes/collects for cross-query comparability.
    3. Proposal ranking weights:
       - title length match: 0.5
       - popular tag overlap: 0.4
       - pattern alignment: 0.1
    """

    def score_posts(self, posts: List[XHSPost], lambda_weight: float = 0.5) -> List[EngagementScore]:
        """Score and rank posts by weighted normalized likes/collects."""
        if not posts:
            return []

        weight = min(1.0, max(0.0, float(lambda_weight)))
        likes = [int(post.liked_count) for post in posts]
        collects = [int(post.collected_count) for post in posts]
        min_likes, max_likes = min(likes), max(likes)
        min_collects, max_collects = min(collects), max(collects)

        scored: List[EngagementScore] = []
        for post in posts:
            if max_likes == min_likes:
                norm_likes = 0.0
            else:
                norm_likes = (post.liked_count - min_likes) / (max_likes - min_likes)

            if max_collects == min_collects:
                norm_collects = 0.0
            else:
                norm_collects = (post.collected_count - min_collects) / (max_collects - min_collects)

            engagement_rate = weight * norm_likes + (1.0 - weight) * norm_collects
            scored.append(
                EngagementScore(
                    post=post,
                    score=float(engagement_rate),
                    raw_score=int(round(engagement_rate * 100)),
                )
            )

        scored.sort(key=lambda item: item.score, reverse=True)
        return scored

    def analyze_platform_preferences(self, posts: List[XHSPost]) -> PlatformPreference:
        """Extract platform preferences from posts.

        Returns:
        - avg_title_length
        - popular_tags (top 10 by frequency)
        - optimal_posting_times (fallback buckets; XHSPost has no create-time field)
        - content_patterns
        """
        if not posts:
            return PlatformPreference(
                avg_title_length=15,
                popular_tags=["教程", "清单", "测评"],
                optimal_posting_times=["12:00", "19:00", "20:00"],
                content_patterns=["图文笔记"],
            )

        title_lengths = [len(post.title) for post in posts if post.title]
        avg_title_length = int(statistics.mean(title_lengths)) if title_lengths else 15

        tags: List[str] = []
        for post in posts:
            tags.extend(post.tags)
        tag_counter = Counter(tags)
        popular_tags = [tag for tag, _ in tag_counter.most_common(10)]

        # XHSPost currently does not expose note_create_time;
        # keep fixed recommendation buckets from spec baseline behavior.
        optimal_posting_times = ["12:00", "19:00", "20:00", "21:00"]

        content_patterns = self._analyze_content_patterns(posts)

        return PlatformPreference(
            avg_title_length=avg_title_length,
            popular_tags=popular_tags,
            optimal_posting_times=optimal_posting_times,
            content_patterns=content_patterns,
        )

    def _analyze_content_patterns(self, posts: List[XHSPost]) -> List[str]:
        """Detect coarse content patterns from top posts."""
        if not posts:
            return ["图文笔记"]

        patterns: List[str] = []
        top_posts = posts[:10]

        has_images = any(bool(post.images) for post in top_posts)
        has_video_like = any(not post.images for post in top_posts)
        if has_images:
            patterns.append("图文笔记")
        if has_video_like:
            patterns.append("视频笔记")

        question_titles = sum(1 for post in top_posts if "?" in post.title or "？" in post.title)
        if question_titles > len(top_posts) * 0.3:
            patterns.append("疑问式标题")

        avg_content_len = statistics.mean(len(post.content) for post in top_posts)
        if avg_content_len > 200:
            patterns.append("长文案")
        elif avg_content_len < 100:
            patterns.append("短文案")
        else:
            patterns.append("中等长度文案")

        return patterns

    def score_proposals(self, proposals: List[Proposal], preferences: PlatformPreference) -> List[Proposal]:
        """Rank proposals by platform preference fit and return descending order."""
        if not proposals:
            return []

        avg_title_length = max(1, preferences.avg_title_length)
        popular_tags = set(preferences.popular_tags)
        pattern_set = set(preferences.content_patterns)

        scored: List[Proposal] = []
        for proposal in proposals:
            hook_len = len(proposal.hook)
            title_delta = abs(hook_len - avg_title_length)
            title_score = max(0.0, 1.0 - (title_delta / max(avg_title_length, 10)))

            if proposal.suggested_tags and popular_tags:
                overlap = len(set(proposal.suggested_tags) & popular_tags)
                tag_score = overlap / max(1, len(set(proposal.suggested_tags)))
            else:
                tag_score = 0.0

            pattern_score = 0.0
            if "疑问式标题" in pattern_set and ("?" in proposal.hook or "？" in proposal.hook):
                pattern_score += 1.0
            if "长文案" in pattern_set and len(proposal.outline) >= 120:
                pattern_score += 1.0
            if "短文案" in pattern_set and len(proposal.outline) <= 80:
                pattern_score += 1.0
            pattern_score = min(1.0, pattern_score)

            total_score = 0.5 * title_score + 0.4 * tag_score + 0.1 * pattern_score
            proposal.score = round(float(total_score), 6)
            scored.append(proposal)

        scored.sort(key=lambda item: item.score, reverse=True)
        return scored

    def get_top_performing_authors(self, posts: List[XHSPost], top_n: int = 5) -> List[Tuple[str, float]]:
        """Return top authors by average engagement raw score."""
        author_scores: Dict[str, List[float]] = defaultdict(list)

        for scored in self.score_posts(posts):
            author_scores[scored.post.author].append(float(scored.raw_score))

        author_avg = [
            (author, statistics.mean(scores))
            for author, scores in author_scores.items()
            if len(scores) >= 2
        ]
        author_avg.sort(key=lambda item: item[1], reverse=True)
        return author_avg[:top_n]

    def analyze_content_gaps(self, posts: Iterable[XHSPost]) -> List[str]:
        """Simple gap detection from missing common categories in tags."""
        tag_counter: Counter[str] = Counter()
        for post in posts:
            tag_counter.update(post.tags)

        common_categories = ["教程", "测评", "对比", "清单", "避坑", "省钱"]
        existing_categories = set(tag_counter.keys())
        gaps = [category for category in common_categories if category not in existing_categories]
        return gaps[:3]
