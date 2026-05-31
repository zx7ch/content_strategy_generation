"""
XHS Spider Service - Wrapper for third-party Spider_XHS
"""

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import threading
from typing import Callable, List, Optional, Tuple, Dict, Any
from pydantic import BaseModel

from app.config import settings
from app.logging_config import get_logger

_logger = get_logger(__name__, component="spider")


class XHSPost(BaseModel):
    """标准化的小红书帖子数据"""
    note_id: str
    title: str
    title_is_explicit: bool = False
    content: str
    author: str
    tags: List[str]
    liked_count: int
    collected_count: int
    comment_count: int
    share_count: int
    note_url: str
    images: List[str]


class SpiderError(Exception):
    """Spider 错误基类"""
    pass


class SpiderTransientError(SpiderError):
    """临时错误（可重试）"""
    pass


class SpiderPermanentError(SpiderError):
    """永久错误（不可重试）"""
    pass


@dataclass(frozen=True, slots=True)
class SpiderSearchSortOption:
    key: str
    label: str
    value: int


SEARCH_SORT_OPTIONS: tuple[SpiderSearchSortOption, ...] = (
    SpiderSearchSortOption(key="general", label="综合", value=0),
    SpiderSearchSortOption(key="latest", label="最新", value=1),
    SpiderSearchSortOption(key="likes", label="最多点赞", value=2),
    SpiderSearchSortOption(key="comments", label="最多评论", value=3),
    SpiderSearchSortOption(key="collections", label="最多收藏", value=4),
)


class XHSSpiderClient:
    """
    XHS Spider 客户端封装
    
    Usage:
        client = XHSSpiderClient()
        posts = await client.search_with_retry("巴黎时装周穿搭")
    """
    
    _cwd_lock = threading.Lock()

    def __init__(self, cookies: Optional[str] = None):
        self.cookies = cookies or settings.XHS_SPIDER_COOKIES
        self.max_retries = self._resolve_retry_budget()
        self.backoff_base = self._safe_int(settings.XHS_SPIDER_BACKOFF_BASE, default=2)
        self._api = None
        self._submodule_path = Path(__file__).parent.parent / "ingest" / "xhs_spider"

    @classmethod
    def get_search_sort_options(cls) -> tuple[SpiderSearchSortOption, ...]:
        return SEARCH_SORT_OPTIONS

    @classmethod
    def get_hotspot_sort_options(cls) -> tuple[SpiderSearchSortOption, ...]:
        return tuple(option for option in SEARCH_SORT_OPTIONS if option.key in {"likes", "comments", "collections"})

    def _configure_node_path(self) -> None:
        node_modules = self._submodule_path / "node_modules"
        if not node_modules.exists():
            return
        current = os.environ.get("NODE_PATH", "")
        target = str(node_modules)
        if target in current.split(os.pathsep):
            return
        os.environ["NODE_PATH"] = target if not current else f"{target}{os.pathsep}{current}"

    def _run_in_submodule_cwd(self, fn, *args, **kwargs):
        """Run call in spider submodule cwd for execjs relative imports."""
        original_dir = os.getcwd()
        with self._cwd_lock:
            os.chdir(str(self._submodule_path))
            try:
                return fn(*args, **kwargs)
            finally:
                os.chdir(original_dir)

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        if isinstance(value, bool):
            return default
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                return default
        return default

    def _resolve_retry_budget(self) -> int:
        # Spec: 3 auto retries + 2 user retries.
        auto = self._safe_int(getattr(settings, "XHS_SPIDER_MAX_AUTO_RETRIES", None), default=-1)
        user = self._safe_int(getattr(settings, "XHS_SPIDER_MAX_USER_RETRIES", None), default=-1)
        if auto >= 0 and user >= 0:
            return auto + user
        # Backward-compatible fallback
        return self._safe_int(getattr(settings, "XHS_SPIDER_MAX_RETRIES", 5), default=5)
    
    def _get_api(self):
        """Lazy load XHS_Apis from submodule"""
        if self._api is None:
            try:
                # Import from git submodule: app/ingest/xhs_spider
                # Need to set correct working directory for relative paths in submodule
                import sys
                import os
                from pathlib import Path
                
                if str(self._submodule_path) not in sys.path:
                    sys.path.insert(0, str(self._submodule_path))
                self._configure_node_path()

                # Change to submodule directory for relative path resolution
                original_dir = os.getcwd()
                os.chdir(str(self._submodule_path))
                try:
                    from apis.xhs_pc_apis import XHS_Apis
                    self._api = XHS_Apis()
                finally:
                    os.chdir(original_dir)
                    
            except ImportError as e:
                _logger.error("spider submodule import failed", error=str(e), submodule_path=str(self._submodule_path))
                raise SpiderPermanentError(
                    f"XHS Spider submodule not available. "
                    f"Please run: git submodule update --init\n"
                    f"Error: {e}"
                )
        return self._api
    
    def _classify_error(self, error_msg: str) -> SpiderError:
        """分类错误类型"""
        error_lower = error_msg.lower()
        
        transient_keywords = [
            "timeout", "connection", "network", "temporarily", 
            "retry", "rate limit", "too many requests"
        ]
        auth_keywords = ["cookie", "auth", "unauthorized", "forbidden", "login"]
        
        if any(kw in error_lower for kw in transient_keywords):
            return SpiderTransientError(error_msg)
        elif any(kw in error_lower for kw in auth_keywords):
            return SpiderPermanentError(f"Auth error: {error_msg}")
        else:
            return SpiderPermanentError(error_msg)
    
    async def search(
        self,
        query: str,
        num: int = 50,
        sort: int = 2,
        on_page: Optional[Callable[[List["XHSPost"]], None]] = None,
    ) -> Tuple[bool, str, List["XHSPost"]]:
        """
        搜索小红书笔记

        Args:
            query: 搜索关键词
            num: 需要获取的笔记数量
            sort: 排序方式 (2=最多点赞, 1=最新)
            on_page: 每页结果到达时调用的同步回调（在线程池里执行）

        Returns:
            (success, message, posts)
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._sync_search, query, num, sort, on_page
        )

    def _sync_search(
        self,
        query: str,
        num: int,
        sort: int,
        on_page: Optional[Callable[[List["XHSPost"]], None]] = None,
    ) -> Tuple[bool, str, List["XHSPost"]]:
        """同步分页搜索，每页完成后触发 on_page 回调（在线程池中运行）。"""
        api = self._get_api()
        all_posts: List[XHSPost] = []
        page = 1

        try:
            while True:
                success, msg, res_json = self._run_in_submodule_cwd(
                    api.search_note,
                    query,
                    self.cookies,
                    page,
                    sort,
                )
                if not success:
                    if not all_posts:
                        return False, msg, []
                    break

                items = (res_json.get("data") or {}).get("items") or []
                has_more = bool((res_json.get("data") or {}).get("has_more", False))

                batch: List[XHSPost] = []
                for raw in items:
                    try:
                        batch.append(self._normalize_post(raw))
                    except Exception:
                        continue

                all_posts.extend(batch)

                if batch and on_page is not None:
                    try:
                        on_page(batch)
                    except Exception:
                        pass

                page += 1
                if len(all_posts) >= num or not has_more:
                    break

            return True, "", all_posts[:num]

        except Exception as exc:
            _logger.exception("spider sync_search raised", query=query, error=str(exc))
            if all_posts:
                return True, "", all_posts[:num]
            raise self._classify_error(str(exc))
    
    async def search_with_retry(
        self,
        query: str,
        num: int = 50,
        sort: int = 2,
        on_page: Optional[Callable[[List["XHSPost"]], None]] = None,
    ) -> List[XHSPost]:
        """
        带重试的搜索
        
        Args:
            query: 搜索关键词
            num: 需要获取的笔记数量
        
        Returns:
            List[XHSPost]
        
        Raises:
            SpiderPermanentError: 达到最大重试次数或遇到永久错误
        """
        last_error = None
        
        # max_retries means number of retries after the initial attempt.
        for retry_count in range(self.max_retries + 1):
            try:
                success, msg, posts = await self.search(query, num, sort, on_page=on_page)
                
                if success:
                    return posts
                else:
                    # API returned failure - treat as transient
                    last_error = SpiderTransientError(msg)
                    
            except SpiderTransientError as e:
                _logger.warning("spider transient error, will retry", attempt=retry_count, error=str(e))
                last_error = e
                # Will retry
                
            except SpiderPermanentError as e:
                _logger.error("spider permanent error, aborting retries", attempt=retry_count, error=str(e))
                raise
            
            # Calculate backoff: 2^attempt seconds (2, 4, 8, 16, 32)
            if retry_count < self.max_retries:
                wait_time = self.backoff_base ** (retry_count + 1)
                await asyncio.sleep(wait_time)
        
        # Max retries exceeded
        raise SpiderPermanentError(
            f"Spider failed after {self.max_retries} retries. Last error: {last_error}"
        )

    @staticmethod
    def _safe_str(value: Any, default: str = "") -> str:
        if value is None:
            return default
        return str(value)

    @staticmethod
    def _extract_tags(raw_post: Dict[str, Any], note_card: Dict[str, Any]) -> List[str]:
        tag_candidates = raw_post.get("note_tags") or raw_post.get("tags") or note_card.get("tag_list") or []
        tags: List[str] = []
        for tag in tag_candidates:
            if isinstance(tag, str) and tag.strip():
                tags.append(tag.strip())
                continue
            if isinstance(tag, dict):
                name = tag.get("name")
                if isinstance(name, str) and name.strip():
                    tags.append(name.strip())
        return tags

    @staticmethod
    def _extract_images(raw_post: Dict[str, Any], note_card: Dict[str, Any]) -> List[str]:
        image_candidates = raw_post.get("note_image_list") or raw_post.get("image_list") or note_card.get("image_list") or []
        images: List[str] = []
        for image in image_candidates:
            if isinstance(image, str) and image.strip():
                images.append(image.strip())
                continue
            if isinstance(image, dict):
                info_list = image.get("info_list") or []
                for info in reversed(info_list):
                    if isinstance(info, dict) and info.get("url"):
                        images.append(str(info["url"]))
                        break
        return images

    @staticmethod
    def _build_note_url(note_id: str, raw_post: Dict[str, Any]) -> str:
        if not note_id:
            return ""
        xsec_token = raw_post.get("xsec_token")
        xsec_source = raw_post.get("xsec_source")
        if xsec_token and xsec_source:
            return (
                f"https://www.xiaohongshu.com/explore/{note_id}"
                f"?xsec_token={xsec_token}&xsec_source={xsec_source}"
            )
        if xsec_token:
            return f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={xsec_token}"
        return f"https://www.xiaohongshu.com/explore/{note_id}"

    def _normalize_post(self, raw_post: Dict[str, Any]) -> XHSPost:
        """
        将原始 Spider 返回转换为标准化 XHSPost
        """
        def parse_int(val, default=0):
            if val is None:
                return default
            try:
                return int(val)
            except (ValueError, TypeError):
                return default

        note_card = raw_post.get("note_card") or {}
        user = note_card.get("user") or {}
        interact_info = note_card.get("interact_info") or {}
        content = self._safe_str(
            raw_post.get("note_desc")
            or raw_post.get("desc")
            or note_card.get("desc")
        )

        author = self._safe_str(
            raw_post.get("author_nick_name")
            or raw_post.get("nickname")
            or user.get("nickname")
        )
        note_id = self._safe_str(raw_post.get("note_id") or raw_post.get("id"))
        title, title_is_explicit = self._derive_title(
            raw_post.get("note_display_title")
            or raw_post.get("title")
            or raw_post.get("display_title")
            or note_card.get("title"),
            note_card.get("display_title"),
            content,
            fallback=self._build_title_fallback(author, note_card.get("type")),
        )
        note_url = self._safe_str(raw_post.get("note_url") or raw_post.get("url")) or self._build_note_url(
            note_id,
            raw_post,
        )

        return XHSPost(
            note_id=note_id,
            title=title,
            title_is_explicit=title_is_explicit,
            content=content,
            author=author,
            tags=self._extract_tags(raw_post, note_card),
            liked_count=parse_int(raw_post.get("note_liked_count", interact_info.get("liked_count"))),
            collected_count=parse_int(raw_post.get("collected_count", interact_info.get("collected_count"))),
            comment_count=parse_int(raw_post.get("comment_count", interact_info.get("comment_count"))),
            share_count=parse_int(raw_post.get("share_count", interact_info.get("share_count"))),
            note_url=note_url,
            images=self._extract_images(raw_post, note_card),
        )

    @classmethod
    def _derive_title(cls, *candidates: Any, fallback: str = "无标题") -> tuple[str, bool]:
        for candidate in candidates[:-1]:
            title = cls._safe_str(candidate).strip()
            if title:
                return title, True

        content = cls._safe_str(candidates[-1]).strip() if candidates else ""
        excerpt = cls._safe_str(content).strip()
        if excerpt:
            first_line = excerpt.splitlines()[0].strip()
            if len(first_line) > 36:
                return first_line[:33].rstrip() + "...", False
            return first_line, False
        return fallback, False

    @classmethod
    def _build_title_fallback(cls, author: str, note_type: Any) -> str:
        normalized_author = cls._safe_str(author).strip()
        normalized_type = cls._safe_str(note_type).strip().lower()
        if not normalized_author and not normalized_type:
            return "无标题"
        normalized_author = normalized_author or "作者"
        if normalized_type == "video":
            return f"{normalized_author}的视频笔记"
        return f"{normalized_author}的笔记"


# Convenience function for direct use
async def search_xhs(query: str, num: int = 50) -> List[XHSPost]:
    """
    便捷函数：搜索小红书笔记
    
    Args:
        query: 搜索关键词
        num: 需要获取的笔记数量
    
    Returns:
        List[XHSPost]
    """
    client = XHSSpiderClient()
    return await client.search_with_retry(query, num)
