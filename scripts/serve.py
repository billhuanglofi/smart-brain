"""
serve.py – lightweight HTTP API server that exposes the smart-brain knowledge
base over HTTP so that any external tool (including a code-review AI) can
query it without needing access to the local file system.

Usage
-----
    # Recommended: serve ChromaDB (populated by scripts/upload.py)
    python scripts/serve.py --db-path ./chroma_db

    # Legacy: serve the flat JSONL index (built by scripts/ingest.py)
    python scripts/serve.py --index-dir index

    # Custom host / port
    python scripts/serve.py --db-path ./chroma_db --host 0.0.0.0 --port 5001

Endpoints
---------

GET /health
    Returns {"status": "ok"}.

GET /search?q=<query>[&top_k=5][&category=jira]
    Returns a JSON array of matching chunk objects (each with a "score" field).
    Works with both ChromaDB and JSONL backends.

GET /categories
    Returns a JSON array of distinct category names in the index.

POST /search
    Accepts JSON body:
      { "query": "...", "top_k": 5, "category": "..." }
    Returns same format as GET /search.

Querying from another project
------------------------------
Start the server on the smart-brain machine:

    python scripts/serve.py --db-path ./chroma_db --port 5001

Then from any other Python project:

    import requests

    BRAIN_URL = "http://localhost:5001"   # or the server's IP / hostname

    def get_knowledge_context(query: str, top_k: int = 5) -> str:
        resp = requests.get(
            f"{BRAIN_URL}/search",
            params={"q": query, "top_k": top_k},
            timeout=5,
        )
        resp.raise_for_status()
        chunks = resp.json()
        return "\n".join(f"[{c['category']}] {c['title']}: {c['text'][:300]}" for c in chunks)

Or with a POST request:

    resp = requests.post(
        f"{BRAIN_URL}/search",
        json={"query": query, "top_k": 5, "category": "jira"},
    )
    chunks = resp.json()
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

from search import _keyword_search, _load_chunks, _semantic_search  # noqa: E402


# ---------------------------------------------------------------------------
# ChromaDB search helper (mirrors search.py _chromadb_search but reuses
# a single persistent client for the lifetime of the server process)
# ---------------------------------------------------------------------------

def _build_chromadb_searcher(db_path: str, collection_name: str):
    """Return a callable ``search(query, top_k, category) -> list[dict]``."""
    try:
        import chromadb  # type: ignore
    except ImportError:
        print("[ERROR] chromadb is not installed.  Run:  pip install chromadb", file=sys.stderr)
        sys.exit(1)

    from _embedding import get_embedding_function  # noqa: E402

    ef = get_embedding_function()
    client = chromadb.PersistentClient(path=db_path)
    try:
        collection = client.get_collection(name=collection_name, embedding_function=ef)
    except Exception:
        print(
            f"[ERROR] Collection '{collection_name}' not found in '{db_path}'.\n"
            "Run  python scripts/upload.py  first to populate the database.",
            file=sys.stderr,
        )
        sys.exit(1)

    def _search(query: str, top_k: int, category: str | None) -> list[dict]:
        where = {"category": category} if category else None
        kwargs: dict = {"query_texts": [query], "n_results": top_k}
        if where:
            kwargs["where"] = where
        results = collection.query(**kwargs)

        ids = (results.get("ids") or [[]])[0]
        docs = (results.get("documents") or [[]])[0]
        metas = (results.get("metadatas") or [[]])[0]
        distances = (results.get("distances") or [[]])[0]

        output = []
        for chunk_id, doc, meta, dist in zip(ids, docs, metas, distances):
            score = round(1.0 - dist, 6)
            output.append({
                "id": chunk_id,
                "source": meta.get("source", ""),
                "category": meta.get("category", ""),
                "title": meta.get("title", ""),
                "text": doc,
                "metadata": {k: v for k, v in meta.items() if k not in ("source", "category", "title")},
                "score": score,
            })
        return output

    def _categories() -> list[str]:
        result = collection.get(include=["metadatas"])
        metas = result.get("metadatas") or []
        return sorted({m.get("category", "") for m in metas if m.get("category")})

    return _search, _categories


# ---------------------------------------------------------------------------
# Flask app factory
# ---------------------------------------------------------------------------

def _build_app(
    db_path: str | None,
    collection_name: str,
    index_dir: Path,
):
    try:
        from flask import Flask, jsonify, request  # type: ignore
    except ImportError:
        print("[ERROR] Flask is not installed. Run:  pip install flask", file=sys.stderr)
        sys.exit(1)

    app = Flask(__name__)

    if db_path:
        _chroma_search, _chroma_categories = _build_chromadb_searcher(db_path, collection_name)

        def _run_search(query: str, top_k: int, category: str | None, **_) -> list[dict]:
            return _chroma_search(query, top_k, category)

        def _get_categories() -> list[str]:
            return _chroma_categories()

    else:
        def _run_search(query: str, top_k: int, category: str | None, semantic: bool = False) -> list[dict]:  # type: ignore[misc]
            chunks = _load_chunks(index_dir, category=category)
            if not chunks:
                return []
            if semantic:
                return _semantic_search(query, chunks, top_k)
            return _keyword_search(query, chunks, top_k)

        def _get_categories() -> list[str]:  # type: ignore[misc]
            all_chunks = _load_chunks(index_dir)
            return sorted({c.get("category", "") for c in all_chunks if c.get("category")})

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get("/categories")
    def categories():
        return jsonify(_get_categories())

    @app.get("/search")
    def search_get():
        query = request.args.get("q", "").strip()
        if not query:
            return jsonify({"error": "Missing query parameter 'q'"}), 400
        top_k = int(request.args.get("top_k", 5))
        category = request.args.get("category") or None
        semantic = request.args.get("semantic", "false").lower() == "true"
        return jsonify(_run_search(query, top_k, category, semantic=semantic))

    @app.post("/search")
    def search_post():
        body = request.get_json(force=True, silent=True) or {}
        query = str(body.get("query", "")).strip()
        if not query:
            return jsonify({"error": "Missing 'query' in request body"}), 400
        top_k = int(body.get("top_k", 5))
        category = body.get("category") or None
        semantic = bool(body.get("semantic", False))
        return jsonify(_run_search(query, top_k, category, semantic=semantic))

    return app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serve the smart-brain knowledge base over HTTP.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # ChromaDB backend (recommended – populated by upload.py)
  python scripts/serve.py --db-path ./chroma_db

  # Legacy JSONL backend (built by ingest.py)
  python scripts/serve.py --index-dir index

  # Custom host and port
  python scripts/serve.py --db-path ./chroma_db --host 0.0.0.0 --port 5001
        """,
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--db-path",
        default=None,
        help="Path to ChromaDB directory (created by upload.py).  Recommended.",
    )
    source.add_argument(
        "--index-dir",
        default="index",
        help="Directory containing chunks.jsonl (legacy JSONL mode).",
    )
    parser.add_argument(
        "--collection",
        default="knowledge",
        help="ChromaDB collection name (default: knowledge).",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0).")
    parser.add_argument("--port", type=int, default=5001, help="Bind port (default: 5001).")
    args = parser.parse_args()

    app = _build_app(
        db_path=args.db_path,
        collection_name=args.collection,
        index_dir=Path(args.index_dir),
    )
    backend = f"ChromaDB at {args.db_path}" if args.db_path else f"JSONL index at {args.index_dir}"
    print(f"Smart-Brain API running on http://{args.host}:{args.port}  [{backend}]")
    app.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()

