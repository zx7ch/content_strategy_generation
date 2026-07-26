"""Xiaohongshu source adapter constants."""

SOURCE_PROVIDER = "xiaohongshu"
SOURCE_KIND_SEARCH_RESULT_MINIMAL = "search_result_minimal"
SOURCE_KIND_SEARCH_RESULT = "search_result"
SOURCE_KIND_NOTE_DETAIL = "note_detail"
SOURCE_KIND_COMMENT = "comment"
SOURCE_KIND_TOPIC_OR_KEYWORD_PAGE = "topic_or_keyword_page"
SUPPORTED_SOURCE_KINDS = frozenset(
    {
        SOURCE_KIND_SEARCH_RESULT_MINIMAL,
        SOURCE_KIND_SEARCH_RESULT,
        SOURCE_KIND_NOTE_DETAIL,
        SOURCE_KIND_COMMENT,
        SOURCE_KIND_TOPIC_OR_KEYWORD_PAGE,
    }
)
# Payload schemas may exist ahead of acquisition support. Runtime capability
# checks must use this set rather than the broader schema vocabulary above.
IMPLEMENTED_SOURCE_KINDS = frozenset({
    SOURCE_KIND_SEARCH_RESULT_MINIMAL,
    SOURCE_KIND_SEARCH_RESULT,
})

OPERATION_DISCOVER_CANDIDATES = "discover_candidates"
OPERATION_COLLECT_NOTE_DETAIL = "collect_note_detail"
OPERATION_COLLECT_COMMENTS = "collect_comments"

STATUS_COMPLETED = "completed"
STATUS_EMPTY = "empty"
STATUS_FAILED = "failed"
STATUS_PARTIAL_COMPLETED = "partial_completed"

FAILURE_UNSUPPORTED_SOURCE_KIND = "unsupported_source_kind"
FAILURE_EMPTY_RESULT = "empty_result"
FAILURE_AUTH_REQUIRED = "auth_required"
FAILURE_RATE_LIMITED = "rate_limited"
FAILURE_TIMEOUT = "timeout"
FAILURE_TRANSIENT_ERROR = "transient_error"
FAILURE_PROVIDER_ACCESS_REJECTED = "provider_access_rejected"
FAILURE_PARSER_ERROR = "parser_error"
FAILURE_UNAVAILABLE = "unavailable"

COOKIE_STATUS_VALID = "valid"
COOKIE_STATUS_INVALID = "invalid"
COOKIE_STATUS_UNKNOWN = "unknown"
