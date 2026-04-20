"""
Tests for XHS Spider Service

Coverage:
- XHSSpiderClient initialization
- search() method
- search_with_retry() - retry logic, backoff
- Error classification (transient vs permanent)
- Data normalization
- Edge cases
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, Mock

from app.services.xhs_spider import (
    SEARCH_SORT_OPTIONS,
    SpiderSearchSortOption,
    XHSSpiderClient,
    XHSPost,
    SpiderError,
    SpiderTransientError,
    SpiderPermanentError,
)


class TestXHSSpiderClientInitialization:
    """Test XHSSpiderClient initialization."""

    def test_init_with_default_cookies(self):
        """Test initialization uses settings cookies by default."""
        with patch("app.services.xhs_spider.settings") as mock_settings:
            mock_settings.XHS_SPIDER_COOKIES = "test_cookie"
            mock_settings.XHS_SPIDER_MAX_RETRIES = 5
            mock_settings.XHS_SPIDER_BACKOFF_BASE = 2
            
            client = XHSSpiderClient()
            assert client.cookies == "test_cookie"
            assert client.max_retries == 5
            assert client.backoff_base == 2

    def test_init_with_custom_cookies(self):
        """Test initialization with custom cookies."""
        with patch("app.services.xhs_spider.settings") as mock_settings:
            mock_settings.XHS_SPIDER_MAX_RETRIES = 5
            mock_settings.XHS_SPIDER_BACKOFF_BASE = 2
            
            client = XHSSpiderClient(cookies="custom_cookie")
            assert client.cookies == "custom_cookie"


class TestErrorClassification:
    """Test error classification logic."""

    @pytest.fixture
    def client(self):
        with patch("app.services.xhs_spider.settings") as mock_settings:
            mock_settings.XHS_SPIDER_MAX_RETRIES = 5
            mock_settings.XHS_SPIDER_BACKOFF_BASE = 2
            return XHSSpiderClient()

    def test_classify_transient_error_timeout(self, client):
        """Test timeout errors are classified as transient."""
        error = client._classify_error("Connection timeout")
        assert isinstance(error, SpiderTransientError)

    def test_classify_transient_error_network(self, client):
        """Test network errors are classified as transient."""
        error = client._classify_error("Network unreachable")
        assert isinstance(error, SpiderTransientError)

    def test_classify_transient_error_rate_limit(self, client):
        """Test rate limit errors are classified as transient."""
        error = client._classify_error("Rate limit exceeded, please retry")
        assert isinstance(error, SpiderTransientError)

    def test_classify_permanent_error_auth(self, client):
        """Test auth errors are classified as permanent."""
        error = client._classify_error("Invalid cookie or unauthorized")
        assert isinstance(error, SpiderPermanentError)
        assert "Auth error" in str(error)

    def test_classify_permanent_error_unknown(self, client):
        """Test unknown errors default to permanent."""
        error = client._classify_error("Some random error")
        assert isinstance(error, SpiderPermanentError)


class TestSearchWithRetry:
    """Test search_with_retry method with retry logic."""

    @pytest.fixture
    def client(self):
        with patch("app.services.xhs_spider.settings") as mock_settings:
            mock_settings.XHS_SPIDER_MAX_RETRIES = 3
            mock_settings.XHS_SPIDER_BACKOFF_BASE = 2
            return XHSSpiderClient()

    @pytest.mark.asyncio
    async def test_search_success_no_retry(self, client):
        """Test successful search doesn't trigger retry."""
        mock_post = MagicMock()
        mock_post.note_id = "note_1"
        
        with patch.object(client, 'search', return_value=(True, "", [mock_post])):
            result = await client.search_with_retry("query")
            assert len(result) == 1

    @pytest.mark.asyncio
    async def test_retry_on_transient_error(self, client):
        """Test retry happens on transient errors."""
        call_count = 0
        
        async def mock_search(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise SpiderTransientError("timeout")
            return True, "", [MagicMock()]
        
        with patch.object(client, 'search', side_effect=mock_search):
            with patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
                result = await client.search_with_retry("query")
                
                # Should retry 3 times total
                assert call_count == 3
                # Should sleep twice (not on last attempt)
                assert mock_sleep.call_count == 2
                # Verify backoff timing: 2^1=2s, 2^2=4s
                mock_sleep.assert_any_call(2)
                mock_sleep.assert_any_call(4)

    @pytest.mark.asyncio
    async def test_no_retry_on_permanent_error(self, client):
        """Test no retry on permanent errors."""
        with patch.object(client, 'search', side_effect=SpiderPermanentError("auth failed")):
            with pytest.raises(SpiderPermanentError) as exc_info:
                await client.search_with_retry("query")
            
            assert "auth failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self, client):
        """Test error raised when max retries exceeded."""
        with patch.object(client, 'search', side_effect=SpiderTransientError("always fails")):
            with patch('asyncio.sleep', new_callable=AsyncMock):
                with pytest.raises(SpiderPermanentError) as exc_info:
                    await client.search_with_retry("query")
                
                assert "failed after 3 retries" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_retry_backoff_sequence_matches_3_plus_2_spec(self):
        """Spec requires 3 auto + 2 user retries -> 5 retries with 2/4/8/16/32 backoff."""
        with patch("app.services.xhs_spider.settings") as mock_settings:
            mock_settings.XHS_SPIDER_COOKIES = "cookie"
            mock_settings.XHS_SPIDER_MAX_AUTO_RETRIES = 3
            mock_settings.XHS_SPIDER_MAX_USER_RETRIES = 2
            mock_settings.XHS_SPIDER_MAX_RETRIES = 5
            mock_settings.XHS_SPIDER_BACKOFF_BASE = 2
            client = XHSSpiderClient()

        with patch.object(client, "search", side_effect=SpiderTransientError("timeout")):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                with pytest.raises(SpiderPermanentError) as exc_info:
                    await client.search_with_retry("query")

                assert "after 5 retries" in str(exc_info.value)
                assert client.search.call_count == 6  # initial + 5 retries
                waits = [call.args[0] for call in mock_sleep.call_args_list]
                assert waits == [2, 4, 8, 16, 32]

    @pytest.mark.asyncio
    async def test_search_with_retry_passes_sort_parameter(self, client):
        """Hotspot ranking depends on passing explicit sort choices through retries."""
        with patch.object(client, "search", return_value=(True, "", [MagicMock()])) as mock_search:
            await client.search_with_retry("query", num=12, sort=4)

        mock_search.assert_awaited_once_with("query", 12, 4)


class TestDataNormalization:
    """Test data normalization from raw posts."""

    @pytest.fixture
    def client(self):
        with patch("app.services.xhs_spider.settings") as mock_settings:
            mock_settings.XHS_SPIDER_MAX_RETRIES = 5
            mock_settings.XHS_SPIDER_BACKOFF_BASE = 2
            return XHSSpiderClient()

    def test_normalize_complete_post(self, client):
        """Test normalization of complete post data."""
        raw = {
            "note_id": "abc123",
            "note_display_title": "Test Title",
            "note_desc": "Test content",
            "author_nick_name": "test_user",
            "note_tags": ["tag1", "tag2"],
            "note_liked_count": 100,
            "collected_count": 50,
            "comment_count": 20,
            "share_count": 10,
            "note_url": "https://xhs.com/abc123",
            "note_image_list": ["img1.jpg", "img2.jpg"],
        }
        
        post = client._normalize_post(raw)
        
        assert isinstance(post, XHSPost)
        assert post.note_id == "abc123"
        assert post.title == "Test Title"
        assert post.content == "Test content"
        assert post.author == "test_user"
        assert post.tags == ["tag1", "tag2"]
        assert post.liked_count == 100
        assert post.collected_count == 50
        assert post.comment_count == 20
        assert post.share_count == 10
        assert post.note_url == "https://xhs.com/abc123"
        assert post.images == ["img1.jpg", "img2.jpg"]

    def test_normalize_post_with_missing_fields(self, client):
        """Test normalization handles missing fields gracefully."""
        raw = {
            "note_id": "xyz789",
            "note_display_title": "Minimal Post",
            # Missing many fields
        }
        
        post = client._normalize_post(raw)
        
        assert post.note_id == "xyz789"
        assert post.title == "Minimal Post"
        assert post.content == ""
        assert post.author == ""
        assert post.tags == []
        assert post.liked_count == 0
        assert post.images == []

    def test_normalize_post_with_string_numbers(self, client):
        """Test normalization handles string number values."""
        raw = {
            "note_id": "test",
            "note_display_title": "Test",
            "note_liked_count": "500",  # String instead of int
            "collected_count": "abc",   # Invalid string
        }
        
        post = client._normalize_post(raw)
        
        assert post.liked_count == 500
        assert post.collected_count == 0  # Defaults on parse failure

    def test_normalize_search_result_item_with_nested_note_card(self, client):
        """Search API items use nested note_card fields and should still normalize correctly."""
        raw = {
            "id": "nested123",
            "xsec_token": "token",
            "xsec_source": "pc_search",
            "note_card": {
                "title": "",
                "desc": "来自搜索结果的正文",
                "user": {"nickname": "真实作者"},
                "interact_info": {
                    "liked_count": 88,
                    "collected_count": 13,
                    "comment_count": 5,
                    "share_count": 2,
                },
                "tag_list": [{"name": "护肤"}, {"name": "敏感肌"}],
                "image_list": [{"info_list": [{"url": "small.jpg"}, {"url": "large.jpg"}]}],
            },
        }

        post = client._normalize_post(raw)

        assert post.note_id == "nested123"
        assert post.title == "来自搜索结果的正文"
        assert post.title_is_explicit is False
        assert post.content == "来自搜索结果的正文"
        assert post.author == "真实作者"
        assert post.tags == ["护肤", "敏感肌"]
        assert post.liked_count == 88
        assert post.collected_count == 13
        assert post.comment_count == 5
        assert post.share_count == 2
        assert post.note_url.startswith("https://www.xiaohongshu.com/explore/nested123")
        assert post.images == ["large.jpg"]

    def test_normalize_post_uses_trimmed_excerpt_when_title_is_missing(self, client):
        raw = {
            "note_id": "nested456",
            "note_card": {
                "title": "",
                "desc": "这是一段比较长的正文内容，用来兜底展示热点榜里的标题，长度会被适当截断，避免过长。",
            },
        }

        post = client._normalize_post(raw)

        assert post.title.startswith("这是一段比较长的正文内容")
        assert post.title.endswith("...")
        assert post.title_is_explicit is False

    def test_normalize_post_uses_note_card_display_title_when_present(self, client):
        raw = {
            "note_id": "nested789",
            "note_card": {
                "display_title": "真实热点标题",
                "desc": "正文备用内容",
            },
        }

        post = client._normalize_post(raw)

        assert post.title == "真实热点标题"
        assert post.title_is_explicit is True

    def test_normalize_post_falls_back_to_author_label_when_no_title_or_desc(self, client):
        raw = {
            "note_id": "nested999",
            "note_card": {
                "type": "video",
                "user": {"nickname": "测试作者"},
            },
        }

        post = client._normalize_post(raw)

        assert post.title == "测试作者的视频笔记"
        assert post.title_is_explicit is False

    def test_sync_search_returns_standardized_xhspost_list(self, client):
        """search(query) acceptance: returns standardized XHSPost list."""
        raw = [{
            "note_id": "n1",
            "note_display_title": "标题",
            "note_desc": "内容",
            "author_nick_name": "作者",
            "note_tags": ["tag"],
            "note_liked_count": 12,
            "collected_count": 3,
            "comment_count": 1,
            "share_count": 0,
            "note_url": "https://xhs.test/n1",
            "note_image_list": [],
        }]
        with patch.object(client, "_get_api") as mock_get_api:
            mock_api = MagicMock()
            mock_api.search_some_note.return_value = (True, "", raw)
            mock_get_api.return_value = mock_api
            success, msg, posts = client._sync_search("q", 1, 2)

        assert success is True
        assert msg == ""
        assert len(posts) == 1
        assert isinstance(posts[0], XHSPost)
        assert posts[0].note_id == "n1"


class TestSortParameter:
    """Test sort parameter is correctly passed to underlying API."""

    @pytest.fixture
    def client(self):
        with patch("app.services.xhs_spider.settings") as mock_settings:
            mock_settings.XHS_SPIDER_MAX_RETRIES = 2
            mock_settings.XHS_SPIDER_BACKOFF_BASE = 2
            mock_settings.XHS_SPIDER_COOKIES = "test_cookie"
            return XHSSpiderClient()

    @pytest.mark.asyncio
    async def test_sort_parameter_passed_to_api(self, client):
        """Test that sort parameter is correctly passed to search_some_note."""
        with patch.object(client, '_get_api') as mock_get_api:
            mock_api = MagicMock()
            mock_api.search_some_note.return_value = (True, "", [])
            mock_get_api.return_value = mock_api
            
            # Test default sort (2=最多点赞)
            # Signature: search_some_note(query, require_num, cookies_str, sort_type_choice=0, ...)
            await client.search("query", num=10)
            mock_api.search_some_note.assert_called_once_with("query", 10, "test_cookie", 2)

    @pytest.mark.asyncio
    async def test_sort_parameter_custom_value(self, client):
        """Test custom sort value is passed correctly."""
        with patch.object(client, '_get_api') as mock_get_api:
            mock_api = MagicMock()
            mock_api.search_some_note.return_value = (True, "", [])
            mock_get_api.return_value = mock_api
            
            # Test sort=1 (最新)
            # Signature: search_some_note(query, require_num, cookies_str, sort_type_choice=0, ...)
            await client.search("query", num=20, sort=1)
            mock_api.search_some_note.assert_called_once_with("query", 20, "test_cookie", 1)

    def test_hotspot_sort_options_come_from_spider_sort_registry(self, client):
        options = client.get_hotspot_sort_options()

        assert options == tuple(option for option in SEARCH_SORT_OPTIONS if option.key in {"likes", "comments", "collections"})
        assert [option.value for option in options] == [2, 3, 4]


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    @pytest.fixture
    def client(self):
        with patch("app.services.xhs_spider.settings") as mock_settings:
            mock_settings.XHS_SPIDER_MAX_RETRIES = 2
            mock_settings.XHS_SPIDER_BACKOFF_BASE = 2
            return XHSSpiderClient()

    @pytest.mark.asyncio
    async def test_empty_query(self, client):
        """Test handling of empty query string."""
        with patch.object(client, '_get_api') as mock_get_api:
            mock_api = MagicMock()
            mock_api.search_some_note.return_value = (True, "", [])
            mock_get_api.return_value = mock_api
            
            success, msg, posts = await client.search("")
            assert success is True
            assert posts == []

    @pytest.mark.asyncio
    async def test_api_returns_failure(self, client):
        """Test handling when API returns failure status."""
        with patch.object(client, '_get_api') as mock_get_api:
            mock_api = MagicMock()
            mock_api.search_some_note.return_value = (False, "API Error", [])
            mock_get_api.return_value = mock_api
            
            success, msg, posts = await client.search("query")
            assert success is False
            assert msg == "API Error"
            assert posts == []

    def test_normalize_post_with_none_values(self, client):
        """Test normalization with None values."""
        raw = {
            "note_id": "test",
            "note_display_title": None,
            "note_desc": None,
            "note_liked_count": None,
            "note_tags": None,
        }
        
        post = client._normalize_post(raw)
        
        assert post.title == "无标题"
        assert post.liked_count == 0  # None defaults to 0
        assert post.tags == []  # None defaults to []
