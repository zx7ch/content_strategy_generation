from app.content_research.sources.canonical_registry import CanonicalSourceRegistry
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore


def test_registry_merges_note_evidence_kinds_but_keeps_comment_lineage(tmp_path):
    registry = CanonicalSourceRegistry(SQLiteContentResearchStore(str(tmp_path / "research.db")))

    search_note = registry.resolve_note(provider="xiaohongshu", note_id="note_1", canonical_url="https://xhs/note_1")
    detail_note = registry.resolve_note(provider="xiaohongshu", note_id="note_1")
    comment = registry.resolve_comment(provider="xiaohongshu", comment_id="comment_1", parent_note_canonical_source_id=search_note.id)

    assert search_note.id == detail_note.id
    assert comment.id != search_note.id
    assert comment.payload["parent_note_canonical_source_id"] == search_note.id
