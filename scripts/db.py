"""
db.py – export and import the knowledge vector database.

Use these commands to migrate the knowledge base between machines, back it up,
or move to a different vector database in the future.

Usage
-----
    # Export all knowledge to a portable JSONL snapshot
    python scripts/db.py export
    python scripts/db.py export --output my_snapshot.jsonl

    # Import (rebuild ChromaDB) from a snapshot on a new machine
    python scripts/db.py import knowledge_snapshot.jsonl

    # Custom DB location
    python scripts/db.py export --db-path /path/to/chroma_db
    python scripts/db.py import snapshot.jsonl --db-path /path/to/chroma_db

Migration workflow
------------------
  Old machine:
    python scripts/db.py export              # → knowledge_snapshot.jsonl

  Copy knowledge_snapshot.jsonl to the new machine (git commit, USB, etc.)

  New machine:
    git clone …
    pip install -r requirements.txt
    python scripts/setup.py                  # download embedding model
    python scripts/db.py import knowledge_snapshot.jsonl

Why JSONL?
----------
The snapshot is plain text — one JSON object per chunk — so it is:
  • Human-readable and diffable in git
  • Independent of ChromaDB's internal SQLite/HNSW format
  • Easy to re-import into any future vector DB (Qdrant, Weaviate, pgvector …)
  • Rebuildable: embeddings are recomputed from raw text on import
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

from _embedding import get_embedding_function  # noqa: E402
from upload import upsert_chunks  # noqa: E402

DEFAULT_SNAPSHOT = "knowledge_snapshot.jsonl"
DEFAULT_DB_PATH = "./chroma_db"
DEFAULT_COLLECTION = "knowledge"


def export_db(db_path: str, collection_name: str, output: Path) -> int:
    """Dump all documents from ChromaDB to a portable JSONL file."""
    try:
        import chromadb  # type: ignore
    except ImportError:
        print("[ERROR] chromadb is not installed.  Run:  pip install chromadb", file=sys.stderr)
        sys.exit(1)

    client = chromadb.PersistentClient(path=db_path)
    try:
        collection = client.get_collection(name=collection_name)
    except Exception:
        print(
            f"[ERROR] Collection '{collection_name}' not found in '{db_path}'.\n"
            "Run  python scripts/upload.py  first to populate the database.",
            file=sys.stderr,
        )
        sys.exit(1)

    total = collection.count()
    if total == 0:
        print("Collection is empty – nothing to export.")
        return 0

    # Retrieve in batches to handle large collections without exhausting memory
    BATCH = 500
    written = 0
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("w", encoding="utf-8") as fout:
        offset = 0
        while offset < total:
            result = collection.get(
                limit=BATCH,
                offset=offset,
                include=["documents", "metadatas"],
            )
            ids = result.get("ids") or []
            docs = result.get("documents") or []
            metas = result.get("metadatas") or []

            for chunk_id, doc, meta in zip(ids, docs, metas):
                record = {
                    "id": chunk_id,
                    "source": meta.get("source", ""),
                    "category": meta.get("category", "general"),
                    "title": meta.get("title", ""),
                    "text": doc,
                    "metadata": {
                        k: v for k, v in meta.items()
                        if k not in ("source", "category", "title")
                    },
                }
                fout.write(json.dumps(record, ensure_ascii=False) + "\n")
                written += 1

            offset += BATCH

    print(f"\n✓ {written} chunks exported to {output}")
    return written


def import_db(snapshot: Path, db_path: str, collection_name: str) -> int:
    """Re-populate ChromaDB from a JSONL snapshot (embeddings are recomputed)."""
    if not snapshot.exists():
        print(f"[ERROR] Snapshot file not found: {snapshot}", file=sys.stderr)
        sys.exit(1)

    chunks: list[dict] = []
    with snapshot.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))

    if not chunks:
        print("Snapshot is empty – nothing to import.")
        return 0

    print(f"  Importing {len(chunks)} chunks from {snapshot} …")
    ef = get_embedding_function()
    count = upsert_chunks(chunks, db_path=db_path, collection_name=collection_name, embedding_function=ef)
    print(f"\n✓ {count} chunks imported into '{collection_name}' at {db_path}")
    return count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export or import the smart-brain ChromaDB knowledge database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Export to the default snapshot file (knowledge_snapshot.jsonl)
  python scripts/db.py export

  # Export to a custom file
  python scripts/db.py export --output team_snapshot.jsonl

  # Import on a new machine (rebuilds ChromaDB from the snapshot)
  python scripts/db.py import knowledge_snapshot.jsonl

  # Import from a custom path
  python scripts/db.py import /shared/drive/team_snapshot.jsonl --db-path ~/my-brain
        """,
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # --- export ---
    exp = sub.add_parser("export", help="Dump ChromaDB contents to a portable JSONL file.")
    exp.add_argument(
        "--output",
        default=DEFAULT_SNAPSHOT,
        help=f"Output JSONL file path (default: {DEFAULT_SNAPSHOT}).",
    )
    exp.add_argument(
        "--db-path",
        default=DEFAULT_DB_PATH,
        help=f"ChromaDB directory (default: {DEFAULT_DB_PATH}).",
    )
    exp.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION,
        help=f"Collection name (default: {DEFAULT_COLLECTION}).",
    )

    # --- import ---
    imp = sub.add_parser("import", help="Rebuild ChromaDB from a JSONL snapshot.")
    imp.add_argument("snapshot", help="Path to the JSONL snapshot file.")
    imp.add_argument(
        "--db-path",
        default=DEFAULT_DB_PATH,
        help=f"ChromaDB directory (default: {DEFAULT_DB_PATH}).",
    )
    imp.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION,
        help=f"Collection name (default: {DEFAULT_COLLECTION}).",
    )

    args = parser.parse_args()

    if args.command == "export":
        export_db(args.db_path, args.collection, Path(args.output))
    elif args.command == "import":
        import_db(Path(args.snapshot), args.db_path, args.collection)


if __name__ == "__main__":
    main()
