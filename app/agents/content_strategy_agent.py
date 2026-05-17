"""Strategy agent implementation for editing-mode strategy generation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
import asyncio
from typing import Any, Awaitable, Callable, Optional

ProgressCallback = Callable[[str], Awaitable[None]]


async def _safe_notify(callback: ProgressCallback, message: str) -> None:
    try:
        await callback(message)
    except Exception:
        pass

from app.config import settings
from app.llm.client import LLMClient
from app.memory.session_state import SessionManager
from app.models.session import (
    ContentStrategy,
    PlatformPreference,
    SessionError,
    SessionLifecycleState,
    SessionStage,
    SpiderNote,
)
from app.prompts.strategy import (
    CONTENT_STRATEGY_DATA_DRIVEN,
    CONTENT_STRATEGY_GENERIC,
    QUERY_EXPANSION_PROMPT,
)
from app.services.engagement_analyzer import EngagementAnalyzer
from app.services.rag_service import QualityScore, RAGService
from app.services.web_search import SearchOrchestrator, SearchIntent, build_default_search_orchestrator
from app.services.xhs_spider import XHSPost, XHSSpiderClient


@dataclass(slots=True)
class StrategyResult:
    success: bool
    message: str
    quality_score: float = 0.0
    content_strategy: Optional[ContentStrategy] = None
    platform_preference: Optional[PlatformPreference] = None
    used_fallback: bool = False
    error_code: Optional[str] = None


class StrategyExecutionError(RuntimeError):
    """Structured internal error for strategy workflow."""

    def __init__(self, message: str, *, error_code: str):
        super().__init__(message)
        self.error_code = error_code


def compute_engagement_score(
    liked_count: int,
    collected_count: int,
    all_likes: list[int],
    all_collects: list[int],
    lambda_weight: float = 0.5,
) -> float:
    """Compatibility helper for weighted normalized engagement.

    Defined in the legacy unit tests and aligned to docs/api_schemas.md:
    engagement_rate = lambda_weight * norm_likes + (1 - lambda_weight) * norm_collects
    """
    weight = min(1.0, max(0.0, float(lambda_weight)))

    min_likes, max_likes = min(all_likes), max(all_likes)
    min_collects, max_collects = min(all_collects), max(all_collects)

    if max_likes == min_likes:
        norm_likes = 0.0
    else:
        norm_likes = (liked_count - min_likes) / (max_likes - min_likes)

    if max_collects == min_collects:
        norm_collects = 0.0
    else:
        norm_collects = (collected_count - min_collects) / (max_collects - min_collects)

    return max(0.0, min(1.0, weight * norm_likes + (1.0 - weight) * norm_collects))


class ContentStrategyAgent:
    """Strategy workflow: retrieve -> index -> expand(optional) -> generate strategy."""

    def __init__(
        self,
        session_manager: Optional[SessionManager] = None,
        spider_client: Optional[XHSSpiderClient] = None,
        search_orchestrator: Optional[SearchOrchestrator] = None,
        rag_service: Optional[RAGService] = None,
        engagement_analyzer: Optional[EngagementAnalyzer] = None,
        llm_client: Optional[LLMClient] = None,
    ):
        self.session_manager = session_manager or SessionManager()
        db_path = getattr(self.session_manager, "db_path", None)
        self.search_orchestrator = search_orchestrator or build_default_search_orchestrator(
            spider_client=spider_client,
            db_path=db_path,
        )
        self.rag = rag_service or RAGService()
        self.analyzer = engagement_analyzer or EngagementAnalyzer()
        self.llm = llm_client or LLMClient()

    async def execute(self, session_id: str, *, progress_callback: Optional[ProgressCallback] = None) -> StrategyResult:
        async def _notify(message: str) -> None:
            if progress_callback is not None:
                try:
                    await progress_callback(message)
                except Exception:
                    pass

        async with self.session_manager as manager:
            session = await manager.get_session(session_id)
            if session is None:
                return StrategyResult(
                    success=False,
                    message="Session not found",
                    error_code="SESSION_NOT_FOUND",
                )

            await manager.update_session(session_id, stage=SessionStage.STRATEGY)

            try:
                posts = await self._retrieve_data(session_id=session_id, user_query=session.user_query, progress_callback=progress_callback)
                await _notify("搜索完成，开始建立内容索引...")
                quality = await self._chunk_and_index(
                    session_id=session_id,
                    posts=posts,
                    query=session.user_query,
                )

                expanded_queries: list[str] = []
                if self._should_expand(quality.score, quality.total_notes):
                    await _notify("内容数量不足，正在补充搜索...")
                    posts, quality, expanded_queries = await self._expand_and_retry(
                        session_id=session_id,
                        user_query=session.user_query,
                        seed_posts=posts,
                        seed_quality=quality,
                    )
                    await _notify(f"补充搜索完成，共 {len(posts)} 篇内容")

                platform_preference = self.analyzer.analyze_platform_preferences(posts)
                used_fallback = quality.score < settings.QUALITY_SCORE_THRESHOLD

                if used_fallback:
                    await _notify("数据不足，使用通用策略...")
                    strategy = await self._generate_generic_strategy(session.user_query)
                else:
                    await _notify("正在基于真实数据生成内容策略...")
                    strategy = await self._generate_data_driven_strategy(
                        user_query=session.user_query,
                        posts=posts,
                        platform_pref=platform_preference,
                    )
                strategy.data_source_quality = quality.score

                spider_notes = [
                    SpiderNote(
                        note_id=post.note_id,
                        title=post.title,
                        content=post.content,
                        tags=post.tags,
                    )
                    for post in posts
                ]
                await manager.update_session(
                    session_id,
                    spider_notes=spider_notes,
                    quality_score=quality.score,
                    expanded_queries=expanded_queries,
                    used_fallback=used_fallback,
                    platform_preference=platform_preference,
                    content_strategy=strategy,
                    stage=SessionStage.STRATEGY,
                )

                return StrategyResult(
                    success=True,
                    message="Strategy generated successfully",
                    quality_score=quality.score,
                    content_strategy=strategy,
                    platform_preference=platform_preference,
                    used_fallback=used_fallback,
                )
            except StrategyExecutionError as exc:
                extra_fields: dict[str, Any] = {}
                if exc.error_code == "SPIDER_SERVICE_UNAVAILABLE":
                    now = datetime.utcnow()
                    extra_fields.update(
                        {
                            "spider_cooldown_until": (now + timedelta(minutes=30)).isoformat(),
                        }
                    )
                await manager.update_session(
                    session_id,
                    error=SessionError(
                        code=exc.error_code,
                        message=str(exc),
                        stage=SessionStage.STRATEGY,
                    ),
                    stage=SessionStage.FAILED,
                    **extra_fields,
                )
                return StrategyResult(
                    success=False,
                    message=str(exc),
                    error_code=exc.error_code,
                )

    async def _retrieve_data(self, *, session_id: str, user_query: str, limit: int = 50, progress_callback: Optional[ProgressCallback] = None) -> list[XHSPost]:
        on_page = None
        if progress_callback is not None:
            loop = asyncio.get_event_loop()
            collected: list[int] = [0]

            def on_page(batch: list) -> None:
                collected[0] += len(batch)
                count = collected[0]
                asyncio.run_coroutine_threadsafe(
                    _safe_notify(progress_callback, f"搜索到 {count} 篇相关内容..."),
                    loop,
                )

        batch = await self.search_orchestrator.discover(
            SearchIntent(
                query=user_query,
                platform="xiaohongshu",
                goal="strategy_discover",
                session_id=session_id,
                workflow_stage=SessionStage.STRATEGY.value,
            ),
            limit=limit,
            on_page=on_page,
        )
        if not batch.items and batch.failure_reason in {"auth_required", "rate_limited", "permanent_error", "transient_error"}:
            raise StrategyExecutionError(
                "Spider failed after retry budget",
                error_code="SPIDER_SERVICE_UNAVAILABLE",
            )

        posts: list[XHSPost] = []
        for item in batch.items:
            post = self._evidence_to_post(item)
            if post is not None:
                posts.append(post)
        if not posts:
            raise StrategyExecutionError(
                "No data found for query",
                error_code="INSUFFICIENT_DATA",
            )
        return posts

    async def _chunk_and_index(self, session_id: str, posts: list[XHSPost], query: str) -> QualityScore:
        """Convert XHS posts to RAG docs and index into single collection."""
        # Explicit chunking step keeps StrategyAgent contract aligned with dev_spec P2-2.
        self.rag.chunk_posts(posts)
        return await self.rag.index_documents(session_id, posts, query)

    @staticmethod
    def _should_expand(quality_score: float, doc_count: int) -> bool:
        return quality_score < settings.QUALITY_SCORE_THRESHOLD and doc_count < settings.EXPANSION_DOC_COUNT_MAX

    async def _expand_and_retry(
        self,
        *,
        session_id: str,
        user_query: str,
        seed_posts: list[XHSPost],
        seed_quality: QualityScore,
    ) -> tuple[list[XHSPost], QualityScore, list[str]]:
        """Run query expansion with stop conditions from dev_spec §6.2.2."""
        expanded_queries = await self._generate_expanded_queries(
            original_query=user_query,
            doc_count=seed_quality.total_notes,
            quality_score=seed_quality.score,
            expansion_count=0,
            existing_queries=[user_query]
        )
        if not expanded_queries:
            return seed_posts, seed_quality, []

        all_posts_by_id: dict[str, XHSPost] = {post.note_id: post for post in seed_posts}
        current_quality = seed_quality
        executed_queries: list[str] = []

        for query in expanded_queries:
            try:
                candidate_posts = await self._retrieve_data(
                    session_id=session_id,
                    user_query=query,
                    limit=30,
                )
            except StrategyExecutionError:
                continue

            new_unique_posts = [post for post in candidate_posts if post.note_id not in all_posts_by_id]
            new_unique_docs = len(new_unique_posts)
            if new_unique_docs < settings.EXPANSION_MIN_NEW_UNIQUE_DOCS:
                break

            for post in new_unique_posts:
                all_posts_by_id[post.note_id] = post

            executed_queries.append(query)
            merged_posts = list(all_posts_by_id.values())
            new_quality = await self._chunk_and_index(
                session_id=session_id,
                posts=merged_posts,
                query=user_query,
            )

            quality_gain = new_quality.score - current_quality.score
            current_quality = new_quality

            if current_quality.score >= settings.QUALITY_SCORE_THRESHOLD:
                return merged_posts, current_quality, executed_queries
            if quality_gain < settings.EXPANSION_MIN_QUALITY_GAIN:
                break
            if current_quality.total_notes >= settings.EXPANSION_DOC_COUNT_MAX:
                break

        return list(all_posts_by_id.values()), current_quality, executed_queries

    async def _generate_expanded_queries(
        self, 
        original_query: str, 
        doc_count: int = 0, 
        quality_score: float = 0.0, 
        expansion_count: int = 0,
        existing_queries: list[str] | None = None
    ) -> list[str]:
        prompt = QUERY_EXPANSION_PROMPT.format(
            query=original_query,
            doc_count=doc_count,
            quality_score=quality_score,
            expansion_count=expansion_count,
            existing_queries="; ".join(existing_queries) if existing_queries else "无"
        )
        try:
            response = await self.llm.chat(
                system="你是查询扩展助手。",
                user=prompt,
                max_tokens=200,
                temperature=0.7,
            )
            queries = [
                line.strip().lstrip("-").strip()
                for line in response.split("\n")
                if line.strip() and not line.strip().startswith("#")
            ]
            deduped: list[str] = []
            seen: set[str] = set()
            for query in queries:
                if query not in seen and query != original_query:
                    seen.add(query)
                    deduped.append(query)
            return deduped[:5]
        except Exception:
            return []

    async def _generate_data_driven_strategy(
        self,
        *,
        user_query: str,
        posts: list[XHSPost],
        platform_pref: PlatformPreference,
    ) -> ContentStrategy:
        scored_posts = self.analyzer.score_posts(posts[:10])
        top_posts_context = "\n\n".join(
            [
                f"标题: {entry.post.title}\n标签: {', '.join(entry.post.tags)}\n互动分: {entry.raw_score}"
                for entry in scored_posts[:5]
            ]
        )

        prompt = CONTENT_STRATEGY_DATA_DRIVEN.format(
            user_query=user_query,
            platform_summary=(
                f"平均标题长度: {platform_pref.avg_title_length}, "
                f"热门标签: {', '.join(platform_pref.popular_tags[:5])}"
            ),
            top_posts=top_posts_context,
            top_k=len(scored_posts[:5]),
        )
        return await self._llm_generate_strategy(prompt, user_query=user_query, default_quality=0.0)

    async def _generate_generic_strategy(self, user_query: str) -> ContentStrategy:
        prompt = CONTENT_STRATEGY_GENERIC.format(query=user_query)
        return await self._llm_generate_strategy(prompt, user_query=user_query, default_quality=0.0)

    async def _llm_generate_strategy(self, prompt: str, *, user_query: str, default_quality: float) -> ContentStrategy:
        try:
            response = await self.llm.chat(
                system="你是小红书内容策略专家。输出严格 JSON。",
                user=prompt,
                max_tokens=1500,
                temperature=0.7,
            )
            payload = self._parse_json_payload(response)
            return ContentStrategy(
                positioning=str(payload.get("positioning", "生活方式分享者")),
                target_audience=str(payload.get("target_audience", "25-35岁用户")),
                content_pillars=list(payload.get("content_pillars", ["生活方式"])),
                key_messaging=str(payload.get("key_messaging", "")),
                content_types=list(payload.get("content_types", ["图文笔记"])),
                posting_strategy=str(payload.get("posting_strategy", "每周2-3更")),
                data_source_quality=default_quality,
            )
        except Exception:
            return ContentStrategy(
                positioning="生活方式分享者",
                target_audience="25-35岁对该主题感兴趣的用户",
                content_pillars=[user_query, "实用经验"],
                key_messaging="给出可执行、可复用的真实建议",
                content_types=["图文笔记"],
                posting_strategy="每周2-3篇，晚间时段优先",
                data_source_quality=default_quality,
            )

    @staticmethod
    def _parse_json_payload(response: str) -> dict[str, Any]:
        text = response.strip()
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            candidate = text[start : end + 1]
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed

        raise ValueError("LLM response is not valid JSON object")

    @staticmethod
    def _evidence_to_post(evidence: Any) -> Optional[XHSPost]:
        canonical_id = getattr(evidence, "canonical_id", None)
        source_url = getattr(evidence, "source_url", "") or ""
        title = getattr(evidence, "title", "") or ""
        content_text = getattr(evidence, "content_text", "") or ""
        if not canonical_id and not source_url and not title and not content_text:
            return None

        metrics = getattr(evidence, "metrics", {}) or {}
        return XHSPost(
            note_id=str(canonical_id or source_url or title[:24] or "imported"),
            title=title or content_text[:80] or "无标题",
            content=content_text,
            author=str(getattr(evidence, "author", "") or ""),
            tags=[str(tag) for tag in (getattr(evidence, "tags", []) or [])],
            liked_count=int(metrics.get("liked_count") or 0),
            collected_count=int(metrics.get("collected_count") or 0),
            comment_count=int(metrics.get("comment_count") or 0),
            share_count=int(metrics.get("share_count") or 0),
            note_url=source_url,
            images=[str(media) for media in (getattr(evidence, "media", []) or []) if str(media).strip()],
        )
