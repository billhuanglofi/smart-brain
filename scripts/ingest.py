"""
ingest.py – process knowledge files (Excel, Markdown, CSV) into a JSONL chunk index.

Usage
-----
    python scripts/ingest.py [--knowledge-dir knowledge] [--output-dir index]

The script walks every sub-directory of *knowledge_dir*, detects .md, .xlsx, .xls
and .csv files, splits them into overlapping text chunks, and writes each chunk as a
single JSON line to *output_dir*/chunks.jsonl.

Each chunk record contains:
  {
    "id":        "<sha256 of content>",
    "source":    "<relative file path>",
    "category":  "<top-level sub-folder, e.g. jira / confluence / general>",
    "title":     "<file stem or sheet name>",
    "text":      "<chunk text>",
    "metadata":  { ... any extra key-value pairs from the file ... }
  }

The index can then be used by scripts/search.py or by any external AI tool that
reads the JSONL file directly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CHUNK_SIZE = 512       # target characters per chunk
CHUNK_OVERLAP = 64     # overlap between consecutive chunks


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _split_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split *text* into overlapping chunks of approximately *chunk_size* chars."""
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end].strip())
        start += chunk_size - overlap
    return [c for c in chunks if c]


# ---------------------------------------------------------------------------
# Markdown parser
# ---------------------------------------------------------------------------

def _parse_markdown(path: Path) -> Iterator[dict]:
    """Yield chunk dicts from a Markdown file."""
    try:
        import markdown  # type: ignore
        from bs4 import BeautifulSoup  # type: ignore
        raw = path.read_text(encoding="utf-8", errors="replace")
        html = markdown.markdown(raw)
        plain = BeautifulSoup(html, "html.parser").get_text(separator="\n")
    except ImportError:
        # Fallback: strip Markdown syntax with a simple regex
        raw = path.read_text(encoding="utf-8", errors="replace")
        plain = re.sub(r"[#*`_~>\[\]!]", "", raw)

    for chunk in _split_text(plain):
        yield {
            "id": _sha256(chunk),
            "source": str(path),
            "title": path.stem,
            "text": chunk,
            "metadata": {},
        }


# ---------------------------------------------------------------------------
# Excel parser
# ---------------------------------------------------------------------------

def _parse_excel(path: Path) -> Iterator[dict]:
    """Yield chunk dicts from an Excel workbook (all sheets)."""
    try:
        import pandas as pd  # type: ignore
    except ImportError:
        print(f"[WARN] pandas not installed – skipping {path}", file=sys.stderr)
        return

    engine = "openpyxl" if path.suffix.lower() == ".xlsx" else "xlrd"
    try:
        sheets: dict = pd.read_excel(path, sheet_name=None, engine=engine, dtype=str)
    except Exception as exc:
        print(f"[WARN] Could not read {path}: {exc}", file=sys.stderr)
        return

    for sheet_name, df in sheets.items():
        df = df.fillna("")
        # Convert each row to a plain-text key=value representation
        rows_text = []
        for _, row in df.iterrows():
            parts = [f"{col}: {val}" for col, val in row.items() if str(val).strip()]
            if parts:
                rows_text.append("  |  ".join(parts))

        full_text = "\n".join(rows_text)
        for chunk in _split_text(full_text):
            yield {
                "id": _sha256(chunk),
                "source": str(path),
                "title": f"{path.stem} – {sheet_name}",
                "text": chunk,
                "metadata": {"sheet": sheet_name},
            }


# ---------------------------------------------------------------------------
# CSV parser
# ---------------------------------------------------------------------------

def _parse_csv(path: Path) -> Iterator[dict]:
    """Yield chunk dicts from a CSV file."""
    try:
        import pandas as pd  # type: ignore
    except ImportError:
        print(f"[WARN] pandas not installed – skipping {path}", file=sys.stderr)
        return

    try:
        df = pd.read_csv(path, dtype=str).fillna("")
    except Exception as exc:
        print(f"[WARN] Could not read {path}: {exc}", file=sys.stderr)
        return

    rows_text = []
    for _, row in df.iterrows():
        parts = [f"{col}: {val}" for col, val in row.items() if str(val).strip()]
        if parts:
            rows_text.append("  |  ".join(parts))

    full_text = "\n".join(rows_text)
    for chunk in _split_text(full_text):
        yield {
            "id": _sha256(chunk),
            "source": str(path),
            "title": path.stem,
            "text": chunk,
            "metadata": {},
        }


# ---------------------------------------------------------------------------
# Main ingestion loop
# ---------------------------------------------------------------------------

PARSERS = {
    ".md": _parse_markdown,
    ".xlsx": _parse_excel,
    ".xls": _parse_excel,
    ".csv": _parse_csv,
}


def ingest(knowledge_dir: Path, output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "chunks.jsonl"

    total = 0
    seen_ids: set[str] = set()

    with out_path.open("w", encoding="utf-8") as fout:
        for root, _dirs, files in os.walk(knowledge_dir):
            root_path = Path(root)
            # Determine category from the first sub-directory level
            try:
                rel = root_path.relative_to(knowledge_dir)
                category = rel.parts[0] if rel.parts else "general"
            except ValueError:
                category = "general"

            for fname in sorted(files):
                file_path = root_path / fname
                ext = file_path.suffix.lower()
                parser = PARSERS.get(ext)
                if parser is None:
                    continue

                print(f"  Ingesting {file_path} …")
                for chunk in parser(file_path):
                    chunk["category"] = category
                    # Deduplicate identical text across files
                    if chunk["id"] in seen_ids:
                        continue
                    seen_ids.add(chunk["id"])
                    fout.write(json.dumps(chunk, ensure_ascii=False) + "\n")
                    total += 1

    print(f"\n✓ {total} chunks written to {out_path}")
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest knowledge files into a JSONL index.")
    parser.add_argument("--knowledge-dir", default="knowledge", help="Root directory of knowledge files.")
    parser.add_argument("--output-dir", default="index", help="Directory where chunks.jsonl is written.")
    args = parser.parse_args()

    knowledge_dir = Path(args.knowledge_dir)
    output_dir = Path(args.output_dir)

    if not knowledge_dir.is_dir():
        print(f"[ERROR] knowledge-dir '{knowledge_dir}' does not exist.", file=sys.stderr)
        sys.exit(1)

    ingest(knowledge_dir, output_dir)


if __name__ == "__main__":
    main()
