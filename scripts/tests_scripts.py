"""
tests for scripts/ingest.py and scripts/search.py
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

# Allow importing from scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from ingest import _parse_markdown, _split_text, ingest  # noqa: E402
from search import _keyword_search, _load_chunks  # noqa: E402


# ---------------------------------------------------------------------------
# _split_text
# ---------------------------------------------------------------------------

class TestSplitText:
    def test_short_text_returns_single_chunk(self):
        chunks = _split_text("hello world", chunk_size=512, overlap=64)
        assert chunks == ["hello world"]

    def test_long_text_creates_multiple_chunks(self):
        text = "a " * 300  # 600 chars
        chunks = _split_text(text, chunk_size=100, overlap=10)
        assert len(chunks) > 1

    def test_chunks_overlap(self):
        text = "abcdefghij" * 20  # 200 chars
        chunks = _split_text(text, chunk_size=50, overlap=10)
        # Verify that consecutive chunks share a suffix/prefix
        assert chunks[0][-10:] in chunks[1]

    def test_empty_text_returns_empty_list(self):
        assert _split_text("") == []

    def test_whitespace_only_returns_empty_list(self):
        assert _split_text("   \n\t  ") == []


# ---------------------------------------------------------------------------
# _parse_markdown
# ---------------------------------------------------------------------------

class TestParseMarkdown:
    def test_parses_simple_markdown(self, tmp_path):
        md = tmp_path / "doc.md"
        md.write_text("# Title\n\nSome content here.", encoding="utf-8")
        chunks = list(_parse_markdown(md))
        assert len(chunks) >= 1
        combined = " ".join(c["text"] for c in chunks)
        assert "Title" in combined or "content" in combined

    def test_chunk_has_required_fields(self, tmp_path):
        md = tmp_path / "doc.md"
        md.write_text("Hello world", encoding="utf-8")
        chunks = list(_parse_markdown(md))
        assert chunks
        for chunk in chunks:
            assert "id" in chunk
            assert "source" in chunk
            assert "title" in chunk
            assert "text" in chunk
            assert "metadata" in chunk

    def test_title_is_file_stem(self, tmp_path):
        md = tmp_path / "my_document.md"
        md.write_text("content", encoding="utf-8")
        chunks = list(_parse_markdown(md))
        assert all(c["title"] == "my_document" for c in chunks)


# ---------------------------------------------------------------------------
# ingest() end-to-end
# ---------------------------------------------------------------------------

class TestIngest:
    def test_ingest_creates_chunks_jsonl(self, tmp_path):
        kb = tmp_path / "knowledge" / "general"
        kb.mkdir(parents=True)
        (kb / "guide.md").write_text("# Guide\n\nThis is a test guide.", encoding="utf-8")

        out = tmp_path / "index"
        total = ingest(tmp_path / "knowledge", out)

        assert total > 0
        chunks_file = out / "chunks.jsonl"
        assert chunks_file.exists()

        lines = [json.loads(l) for l in chunks_file.read_text().splitlines() if l.strip()]
        assert len(lines) == total

    def test_ingest_sets_category_from_subdirectory(self, tmp_path):
        kb = tmp_path / "knowledge" / "jira"
        kb.mkdir(parents=True)
        (kb / "ticket.md").write_text("Ticket details here.", encoding="utf-8")

        out = tmp_path / "index"
        ingest(tmp_path / "knowledge", out)

        lines = [json.loads(l) for l in (out / "chunks.jsonl").read_text().splitlines() if l.strip()]
        assert all(c["category"] == "jira" for c in lines)

    def test_ingest_deduplicates_identical_chunks(self, tmp_path):
        kb = tmp_path / "knowledge" / "general"
        kb.mkdir(parents=True)
        content = "Duplicate content text."
        (kb / "a.md").write_text(content, encoding="utf-8")
        (kb / "b.md").write_text(content, encoding="utf-8")

        out = tmp_path / "index"
        total = ingest(tmp_path / "knowledge", out)
        # Only one unique chunk should be written despite two identical files
        assert total == 1

    def test_ingest_ignores_unsupported_file_types(self, tmp_path):
        kb = tmp_path / "knowledge" / "general"
        kb.mkdir(parents=True)
        (kb / "data.csv").write_text("col1,col2\n1,2", encoding="utf-8")

        out = tmp_path / "index"
        total = ingest(tmp_path / "knowledge", out)
        assert total == 0


# ---------------------------------------------------------------------------
# _keyword_search
# ---------------------------------------------------------------------------

class TestKeywordSearch:
    def _make_chunks(self, texts: list[str]) -> list[dict]:
        return [
            {"id": str(i), "source": "test", "category": "general", "title": "t", "text": t, "metadata": {}}
            for i, t in enumerate(texts)
        ]

    def test_returns_top_k_results(self):
        chunks = self._make_chunks(["apple banana", "cherry", "apple cherry", "banana cherry apple"])
        results = _keyword_search("apple", chunks, top_k=2)
        assert len(results) <= 2

    def test_relevant_chunk_ranks_first(self):
        chunks = self._make_chunks(["unrelated text", "authentication flow token refresh"])
        results = _keyword_search("authentication", chunks, top_k=5)
        assert results
        assert results[0]["text"] == "authentication flow token refresh"

    def test_no_match_returns_empty(self):
        chunks = self._make_chunks(["hello world", "foo bar"])
        results = _keyword_search("zzz_no_match_zzz", chunks, top_k=5)
        assert results == []

    def test_results_have_score_field(self):
        chunks = self._make_chunks(["the quick brown fox"])
        results = _keyword_search("quick fox", chunks, top_k=5)
        assert results
        assert "score" in results[0]

    def test_empty_query_returns_empty(self):
        chunks = self._make_chunks(["some text"])
        results = _keyword_search("", chunks, top_k=5)
        assert results == []


# ---------------------------------------------------------------------------
# _load_chunks
# ---------------------------------------------------------------------------

class TestLoadChunks:
    def test_loads_all_chunks(self, tmp_path):
        chunks_file = tmp_path / "chunks.jsonl"
        records = [
            {"id": "1", "source": "a.md", "category": "jira", "title": "A", "text": "hello", "metadata": {}},
            {"id": "2", "source": "b.md", "category": "confluence", "title": "B", "text": "world", "metadata": {}},
        ]
        chunks_file.write_text("\n".join(json.dumps(r) for r in records))
        loaded = _load_chunks(tmp_path)
        assert len(loaded) == 2

    def test_filters_by_category(self, tmp_path):
        chunks_file = tmp_path / "chunks.jsonl"
        records = [
            {"id": "1", "category": "jira", "text": "jira text"},
            {"id": "2", "category": "confluence", "text": "conf text"},
        ]
        chunks_file.write_text("\n".join(json.dumps(r) for r in records))
        loaded = _load_chunks(tmp_path, category="jira")
        assert len(loaded) == 1
        assert loaded[0]["category"] == "jira"

    def test_exits_when_file_missing(self, tmp_path):
        with pytest.raises(SystemExit):
            _load_chunks(tmp_path)
