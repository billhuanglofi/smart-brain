"""
tests for scripts/ingest.py, scripts/search.py, and scripts/upload.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Allow importing from scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from ingest import _parse_csv, _parse_markdown, _split_text, ingest  # noqa: E402
from search import _keyword_search, _load_chunks  # noqa: E402
from upload import (  # noqa: E402
    _extract_adf_text,
    _parse_confluence_url,
    _parse_jira_url,
    parse_file,
    upsert_chunks,
)


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
        (kb / "data.bin").write_bytes(b"\x00\x01\x02")

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


# ---------------------------------------------------------------------------
# _parse_csv
# ---------------------------------------------------------------------------

class TestParseCsv:
    def test_parses_simple_csv(self, tmp_path):
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("name,age\nAlice,30\nBob,25", encoding="utf-8")
        chunks = list(_parse_csv(csv_file))
        assert len(chunks) >= 1
        combined = " ".join(c["text"] for c in chunks)
        assert "Alice" in combined
        assert "Bob" in combined

    def test_chunk_has_required_fields(self, tmp_path):
        csv_file = tmp_path / "report.csv"
        csv_file.write_text("col\nvalue", encoding="utf-8")
        chunks = list(_parse_csv(csv_file))
        assert chunks
        for chunk in chunks:
            assert "id" in chunk
            assert "source" in chunk
            assert "title" in chunk
            assert "text" in chunk

    def test_title_is_file_stem(self, tmp_path):
        csv_file = tmp_path / "my_report.csv"
        csv_file.write_text("x\n1", encoding="utf-8")
        chunks = list(_parse_csv(csv_file))
        assert all(c["title"] == "my_report" for c in chunks)

    def test_ingest_picks_up_csv_files(self, tmp_path):
        kb = tmp_path / "knowledge" / "general"
        kb.mkdir(parents=True)
        (kb / "data.csv").write_text("col1,col2\nfoo,bar\nbaz,qux", encoding="utf-8")
        out = tmp_path / "index"
        total = ingest(tmp_path / "knowledge", out)
        assert total > 0


# ---------------------------------------------------------------------------
# upload helpers
# ---------------------------------------------------------------------------

class TestParseJiraUrl:
    def test_bare_key(self):
        base, key = _parse_jira_url("PROJ-123")
        assert key == "PROJ-123"
        assert base is None

    def test_full_url(self):
        base, key = _parse_jira_url("https://acme.atlassian.net/browse/PROJ-456")
        assert key == "PROJ-456"
        assert base == "https://acme.atlassian.net"

    def test_invalid_returns_none(self):
        base, key = _parse_jira_url("https://example.com/no-issue-here")
        assert key is None


class TestParseConfluenceUrl:
    def test_extracts_page_id_and_base(self):
        base, pid = _parse_confluence_url(
            "https://acme.atlassian.net/wiki/spaces/ENG/pages/123456789/My+Page"
        )
        assert pid == "123456789"
        assert base == "https://acme.atlassian.net"

    def test_url_without_page_id_returns_none(self):
        _, pid = _parse_confluence_url("https://acme.atlassian.net/wiki/spaces/ENG/overview")
        assert pid is None


class TestExtractAdfText:
    def test_plain_text_node(self):
        node = {"type": "text", "text": "hello world"}
        assert "hello world" in _extract_adf_text(node)

    def test_nested_content(self):
        node = {
            "type": "paragraph",
            "content": [
                {"type": "text", "text": "foo"},
                {"type": "text", "text": "bar"},
            ],
        }
        result = _extract_adf_text(node)
        assert "foo" in result
        assert "bar" in result

    def test_none_returns_empty(self):
        assert _extract_adf_text(None) == ""

    def test_list_of_nodes(self):
        nodes = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
        result = _extract_adf_text(nodes)
        assert "a" in result and "b" in result


class TestParseFile:
    def test_parse_markdown_file(self, tmp_path):
        md = tmp_path / "doc.md"
        md.write_text("# Hello\nWorld", encoding="utf-8")
        chunks = parse_file(md, "general")
        assert chunks
        assert all(c["category"] == "general" for c in chunks)

    def test_parse_csv_file(self, tmp_path):
        csv = tmp_path / "data.csv"
        csv.write_text("a,b\n1,2", encoding="utf-8")
        chunks = parse_file(csv, "jira")
        assert chunks
        assert all(c["category"] == "jira" for c in chunks)

    def test_unsupported_extension_returns_empty(self, tmp_path):
        txt = tmp_path / "notes.txt"
        txt.write_text("some notes", encoding="utf-8")
        chunks = parse_file(txt, "general")
        assert chunks == []


class TestUpsertChunks:
    """Tests that upsert_chunks writes to ChromaDB using a temporary local store
    with a dummy embedding function (avoids network calls in CI / sandboxes)."""

    def _dummy_ef(self):
        """Return a minimal embedding function that produces fixed-length vectors."""
        from chromadb.api.types import EmbeddingFunction  # type: ignore

        class _DummyEF(EmbeddingFunction):
            def __init__(self):
                pass

            @staticmethod
            def name() -> str:
                return "dummy"

            def __call__(self, input):  # noqa: A002
                return [[0.1, 0.2, 0.3] for _ in input]

            @classmethod
            def build_from_config(cls, config):
                return cls()

            def get_config(self):
                return {}

        return _DummyEF()

    def _make_chunks(self, n: int = 3) -> list[dict]:
        return [
            {
                "id": f"chunk-{i}",
                "source": "test.md",
                "category": "general",
                "title": "Test Doc",
                "text": f"This is chunk number {i}.",
                "metadata": {},
            }
            for i in range(n)
        ]

    def test_upsert_returns_correct_count(self, tmp_path):
        db_path = str(tmp_path / "test_chroma")
        chunks = self._make_chunks(3)
        count = upsert_chunks(chunks, db_path=db_path, collection_name="test_col", embedding_function=self._dummy_ef())
        assert count == 3

    def test_upsert_is_idempotent(self, tmp_path):
        db_path = str(tmp_path / "test_chroma")
        ef = self._dummy_ef()
        chunks = self._make_chunks(2)
        upsert_chunks(chunks, db_path=db_path, collection_name="test_col", embedding_function=ef)
        count2 = upsert_chunks(chunks, db_path=db_path, collection_name="test_col", embedding_function=ef)
        assert count2 == 2  # same IDs, upsert overwrites silently

    def test_upsert_empty_is_noop(self, tmp_path):
        db_path = str(tmp_path / "test_chroma")
        count = upsert_chunks([], db_path=db_path, collection_name="test_col")
        assert count == 0

    def test_chunks_queryable_after_upsert(self, tmp_path):
        import chromadb  # type: ignore

        db_path = str(tmp_path / "test_chroma")
        ef = self._dummy_ef()
        chunks = self._make_chunks(5)
        upsert_chunks(chunks, db_path=db_path, collection_name="test_col", embedding_function=ef)

        client = chromadb.PersistentClient(path=db_path)
        col = client.get_collection("test_col")
        assert col.count() == 5

