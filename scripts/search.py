"""
search.py – search the knowledge-base index.

Usage
-----
    # Search ChromaDB (recommended – built after running upload.py)
    python scripts/search.py "your query" --db-path ./chroma_db

    # Keyword / semantic search over legacy JSONL index (built by ingest.py)
    python scripts/search.py "your query" [--top-k 5] [--index-dir index] [--category jira]

The script supports three search modes:

  1. **ChromaDB (default when --db-path is given)** – uses the embeddings stored
     in the local vector database populated by scripts/upload.py.

  2. **Keyword (default when --index-dir is used)** – simple BM25-style TF-IDF
     ranking using only the standard library.  No extra dependencies required.

  3. **Semantic** – cosine-similarity over sentence embeddings.  Requires
     ``sentence-transformers`` and ``numpy`` to be installed
     (``pip install sentence-transformers numpy``).  Activated automatically
     when those packages are present and ``--semantic`` flag is passed.

Output format
-------------
Results are printed as JSON lines, each containing the chunk record plus a
"score" field.  This makes it easy to pipe results into another tool:

    python scripts/search.py "authentication flow" --db-path ./chroma_db --top-k 3 | python your_tool.py
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
# ChromaDB search
# ---------------------------------------------------------------------------

def _chromadb_search(
    query: str,
    db_path: str,
    collection_name: str,
    top_k: int,
    category: str | None,
) -> list[dict[str, Any]]:
    """Query the local ChromaDB vector database and return top-k results."""
    try:
        import chromadb  # type: ignore
    except ImportError:
        print(
            "[ERROR] chromadb is not installed.\n"
            "Install it with:  pip install chromadb",
            file=sys.stderr,
        )
        sys.exit(1)

    client = chromadb.PersistentClient(path=db_path)
    try:
        collection = client.get_collection(name=collection_name)
    except Exception:
        print(
            f"[ERROR] Collection '{collection_name}' not found in '{db_path}'.\n"
            "Run  python scripts/upload.py  first.",
            file=sys.stderr,
        )
        sys.exit(1)

    where: dict | None = {"category": category} if category else None
    kwargs: dict[str, Any] = {"query_texts": [query], "n_results": top_k}
    if where:
        kwargs["where"] = where

    results = collection.query(**kwargs)

    output = []
    ids = (results.get("ids") or [[]])[0]
    docs = (results.get("documents") or [[]])[0]
    metas = (results.get("metadatas") or [[]])[0]
    distances = (results.get("distances") or [[]])[0]

    for chunk_id, doc, meta, dist in zip(ids, docs, metas, distances):
        # ChromaDB returns L2 or cosine *distance*; convert to a similarity score
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
    parser.add_argument("--db-path", default=None, help="Path to ChromaDB directory (created by upload.py). When provided, ChromaDB search is used.")
    parser.add_argument("--collection", default="knowledge", help="ChromaDB collection name (default: knowledge).")
    parser.add_argument("--index-dir", default="index", help="Directory containing chunks.jsonl (legacy JSONL mode).")
    parser.add_argument("--category", default=None, help="Filter by category (e.g. jira, confluence, general).")
    parser.add_argument("--semantic", action="store_true", help="Use sentence-embedding based search (JSONL mode only).")
    args = parser.parse_args()

    if args.db_path:
        results = _chromadb_search(
            query=args.query,
            db_path=args.db_path,
            collection_name=args.collection,
            top_k=args.top_k,
            category=args.category,
        )
    else:
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
