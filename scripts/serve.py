"""
serve.py – lightweight HTTP API server that exposes the smart-brain knowledge
index over HTTP so that any external tool (including the code-review AI) can
query it without needing access to the local file system.

Usage
-----
    pip install flask
    python scripts/serve.py [--index-dir index] [--host 0.0.0.0] [--port 5001]

Endpoints
---------

GET /health
    Returns {"status": "ok"}.

GET /search?q=<query>[&top_k=5][&category=jira][&semantic=false]
    Returns a JSON array of matching chunk objects (each with a "score" field).

GET /categories
    Returns a JSON array of distinct category names in the index.

POST /search
    Accepts JSON body:
      { "query": "...", "top_k": 5, "category": "...", "semantic": false }
    Returns same format as GET /search.

Integration with the code-review AI tool
-----------------------------------------
Add the following step inside your code-review prompt-building logic:

    import requests
    BRAIN_URL = "http://localhost:5001"   # or wherever serve.py is running

    def get_knowledge_context(pr_diff: str, top_k: int = 5) -> str:
        resp = requests.get(BRAIN_URL + "/search", params={"q": pr_diff[:500], "top_k": top_k})
        resp.raise_for_status()
        chunks = resp.json()
        if not chunks:
            return ""
        lines = ["Relevant knowledge context:"]
        for c in chunks:
            lines.append(f"- [{c['category']}] {c['title']}: {c['text'][:300]}")
        return "\n".join(lines)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Reuse search helpers from search.py
# ---------------------------------------------------------------------------
# Allow running from repo root (python scripts/serve.py) or from scripts/
_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

from search import _keyword_search, _load_chunks, _semantic_search  # noqa: E402


def _build_app(index_dir: Path):
    try:
        from flask import Flask, jsonify, request  # type: ignore
    except ImportError:
        print("[ERROR] Flask is not installed. Run:  pip install flask", file=sys.stderr)
        sys.exit(1)

    app = Flask(__name__)

    def _chunks(category: str | None = None):
        """Load chunks from disk on every request (supports hot-reload of index)."""
        return _load_chunks(index_dir, category=category)

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get("/categories")
    def categories():
        all_chunks = _chunks()
        cats = sorted({c.get("category", "") for c in all_chunks if c.get("category")})
        return jsonify(cats)

    def _run_search(query: str, top_k: int, category: str | None, semantic: bool) -> list[dict]:
        chunks = _chunks(category=category)
        if not chunks:
            return []
        if semantic:
            return _semantic_search(query, chunks, top_k)
        return _keyword_search(query, chunks, top_k)

    @app.get("/search")
    def search_get():
        query = request.args.get("q", "").strip()
        if not query:
            return jsonify({"error": "Missing query parameter 'q'"}), 400
        top_k = int(request.args.get("top_k", 5))
        category = request.args.get("category") or None
        semantic = request.args.get("semantic", "false").lower() == "true"
        return jsonify(_run_search(query, top_k, category, semantic))

    @app.post("/search")
    def search_post():
        body = request.get_json(force=True, silent=True) or {}
        query = str(body.get("query", "")).strip()
        if not query:
            return jsonify({"error": "Missing 'query' in request body"}), 400
        top_k = int(body.get("top_k", 5))
        category = body.get("category") or None
        semantic = bool(body.get("semantic", False))
        return jsonify(_run_search(query, top_k, category, semantic))

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the smart-brain knowledge index over HTTP.")
    parser.add_argument("--index-dir", default="index", help="Directory containing chunks.jsonl.")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0).")
    parser.add_argument("--port", type=int, default=5001, help="Bind port (default: 5001).")
    args = parser.parse_args()

    index_dir = Path(args.index_dir)
    app = _build_app(index_dir)
    print(f"smart-brain API running on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
