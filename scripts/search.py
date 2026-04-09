"""
search.py – search the knowledge-base index built by scripts/ingest.py.

Usage
-----
    python scripts/search.py "your query" [--top-k 5] [--index-dir index] [--category jira]

The script supports two search modes:

  1. **Keyword (default)** – simple BM25-style TF-IDF ranking using only the
     standard library.  No extra dependencies required.

  2. **Semantic** – cosine-similarity over sentence embeddings.  Requires
     ``sentence-transformers`` and ``numpy`` to be installed
     (``pip install sentence-transformers numpy``).  Activated automatically
     when those packages are present and ``--semantic`` flag is passed.

Output format
-------------
Results are printed as JSON lines, each containing the chunk record plus a
"score" field.  This makes it easy to pipe results into another tool:

    python scripts/search.py "authentication flow" --top-k 3 | python your_tool.py
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    return re.findall(r"\b\w+\b", text.lower())


def _load_chunks(index_dir: Path, category: str | None = None) -> list[dict]:
    chunks_path = index_dir / "chunks.jsonl"
    if not chunks_path.exists():
        print(
            f"[ERROR] Index not found at '{chunks_path}'.\n"
            "Run  python scripts/ingest.py  first.",
            file=sys.stderr,
        )
        sys.exit(1)

    chunks = []
    with chunks_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            chunk = json.loads(line)
            if category and chunk.get("category") != category:
                continue
            chunks.append(chunk)
    return chunks


# ---------------------------------------------------------------------------
# Keyword search (TF-IDF / BM25-lite)
# ---------------------------------------------------------------------------

def _keyword_search(query: str, chunks: list[dict], top_k: int) -> list[dict[str, Any]]:
    """Return top-k chunks ranked by a simple TF-IDF overlap score."""
    query_tokens = set(_tokenize(query))
    if not query_tokens:
        return []

    # Build IDF: document frequency for each token
    df: dict[str, int] = defaultdict(int)
    for chunk in chunks:
        tokens = set(_tokenize(chunk["text"]))
        for t in tokens:
            df[t] += 1
    N = len(chunks)
    idf = {t: math.log((N + 1) / (freq + 1)) + 1 for t, freq in df.items()}

    scored = []
    for chunk in chunks:
        tokens = _tokenize(chunk["text"])
        tf = Counter(tokens)
        doc_len = len(tokens) or 1
        score = sum(
            (tf[t] / doc_len) * idf.get(t, 0)
            for t in query_tokens
        )
        if score > 0:
            scored.append({**chunk, "score": round(score, 6)})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


# ---------------------------------------------------------------------------
# Semantic search
# ---------------------------------------------------------------------------

def _semantic_search(query: str, chunks: list[dict], top_k: int) -> list[dict[str, Any]]:
    """Return top-k chunks ranked by cosine similarity of sentence embeddings."""
    try:
        import numpy as np  # type: ignore
        from sentence_transformers import SentenceTransformer  # type: ignore
    except ImportError:
        print(
            "[ERROR] Semantic search requires 'sentence-transformers' and 'numpy'.\n"
            "Install them with:  pip install sentence-transformers numpy",
            file=sys.stderr,
        )
        sys.exit(1)

    model = SentenceTransformer("all-MiniLM-L6-v2")
    texts = [c["text"] for c in chunks]
    corpus_embeddings = model.encode(texts, show_progress_bar=True, convert_to_numpy=True)
    query_embedding = model.encode([query], convert_to_numpy=True)[0]

    # Cosine similarity
    norms = np.linalg.norm(corpus_embeddings, axis=1, keepdims=True) + 1e-10
    normalised = corpus_embeddings / norms
    q_norm = query_embedding / (np.linalg.norm(query_embedding) + 1e-10)
    scores = normalised @ q_norm

    top_indices = scores.argsort()[::-1][:top_k]
    results = []
    for idx in top_indices:
        results.append({**chunks[idx], "score": float(scores[idx])})
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Search the smart-brain knowledge index.")
    parser.add_argument("query", help="Search query string.")
    parser.add_argument("--top-k", type=int, default=5, help="Number of results to return (default: 5).")
    parser.add_argument("--index-dir", default="index", help="Directory containing chunks.jsonl.")
    parser.add_argument("--category", default=None, help="Filter by category (e.g. jira, confluence, general).")
    parser.add_argument("--semantic", action="store_true", help="Use sentence-embedding based search.")
    args = parser.parse_args()

    chunks = _load_chunks(Path(args.index_dir), category=args.category)
    if not chunks:
        print("No chunks found (empty index or category filter matched nothing).", file=sys.stderr)
        sys.exit(0)

    if args.semantic:
        results = _semantic_search(args.query, chunks, args.top_k)
    else:
        results = _keyword_search(args.query, chunks, args.top_k)

    for result in results:
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
