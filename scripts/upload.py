"""
upload.py – upload knowledge to a local ChromaDB vector database.

Accepts:
  • A local file path (.md, .xlsx, .xls, .csv)
  • A local directory path (all supported files inside are processed recursively)
  • A Jira issue URL or issue key (e.g. PROJ-123)
  • A Confluence page URL

Usage
-----
    # Single file
    python scripts/upload.py /path/to/doc.md

    # All files in a directory
    python scripts/upload.py /path/to/docs/

    # Specific file type with an explicit category label
    python scripts/upload.py /path/to/report.xlsx --category jira

    # Jira issue (reads credentials from .env or environment variables)
    python scripts/upload.py --jira PROJ-123
    python scripts/upload.py --jira https://company.atlassian.net/browse/PROJ-123

    # Confluence page
    python scripts/upload.py --confluence https://company.atlassian.net/wiki/spaces/ENG/pages/123456789

    # Custom DB location / collection
    python scripts/upload.py /path/to/doc.md --db-path ./chroma_db --collection knowledge

Environment variables (can be placed in a .env file in the repo root)
---------------------------------------------------------------------
    JIRA_BASE_URL      https://company.atlassian.net
    JIRA_EMAIL         you@company.com
    JIRA_API_TOKEN     <your Jira API token>

    CONFLUENCE_BASE_URL   https://company.atlassian.net   (often same as JIRA_BASE_URL)
    CONFLUENCE_EMAIL      you@company.com
    CONFLUENCE_API_TOKEN  <your Confluence API token>

Generate a Jira/Confluence API token at:
    https://id.atlassian.com/manage-profile/security/api-tokens
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterator

# ---------------------------------------------------------------------------
# Load .env if present (optional dependency)
# ---------------------------------------------------------------------------

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed; rely on real env vars

# ---------------------------------------------------------------------------
# Reuse parsers from ingest.py (same directory)
# ---------------------------------------------------------------------------

_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

from ingest import PARSERS, _sha256, _split_text  # noqa: E402


# ---------------------------------------------------------------------------
# ChromaDB helpers
# ---------------------------------------------------------------------------

def _get_collection(db_path: str, collection_name: str, embedding_function=None):
    """Return (or create) a ChromaDB collection stored at *db_path*."""
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
    kwargs: dict = {"name": collection_name, "metadata": {"hnsw:space": "cosine"}}
    if embedding_function is not None:
        kwargs["embedding_function"] = embedding_function
    collection = client.get_or_create_collection(**kwargs)
    return collection


def upsert_chunks(
    chunks: list[dict],
    db_path: str,
    collection_name: str,
    embedding_function=None,
) -> int:
    """Upsert *chunks* into ChromaDB and return the number of chunks written.

    Parameters
    ----------
    embedding_function:
        Optional ChromaDB-compatible embedding function.  When *None* (default),
        ChromaDB uses its built-in model (downloads once, then cached locally).
        Pass a custom function in tests to avoid network calls.
    """
    if not chunks:
        return 0

    collection = _get_collection(db_path, collection_name, embedding_function)

    ids = [c["id"] for c in chunks]
    documents = [c["text"] for c in chunks]
    metadatas = [
        {
            "source": c.get("source", ""),
            "category": c.get("category", "general"),
            "title": c.get("title", ""),
            **{k: str(v) for k, v in (c.get("metadata") or {}).items()},
        }
        for c in chunks
    ]

    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
    return len(chunks)


# ---------------------------------------------------------------------------
# File parsing dispatcher
# ---------------------------------------------------------------------------

def parse_file(path: Path, category: str) -> list[dict]:
    """Parse a single supported file and return a list of chunk dicts."""
    ext = path.suffix.lower()
    parser = PARSERS.get(ext)
    if parser is None:
        print(f"[SKIP] Unsupported file type: {path.suffix}  ({path})", file=sys.stderr)
        return []

    chunks = []
    for chunk in parser(path):
        chunk.setdefault("category", category)
        chunks.append(chunk)
    return chunks


def parse_path(target: Path, category: str) -> list[dict]:
    """
    Parse a file or directory.

    If *target* is a directory every supported file inside is processed
    recursively.  The category is inferred from the first sub-directory level
    relative to *target* when not explicitly overridden.
    """
    if target.is_file():
        return parse_file(target, category)

    if target.is_dir():
        all_chunks: list[dict] = []
        for root, _dirs, files in os.walk(target):
            root_path = Path(root)
            # Derive category from the first sub-directory level
            try:
                rel = root_path.relative_to(target)
                derived_cat = rel.parts[0] if rel.parts else category
            except ValueError:
                derived_cat = category

            for fname in sorted(files):
                file_path = root_path / fname
                all_chunks.extend(parse_file(file_path, derived_cat))
        return all_chunks

    print(f"[ERROR] Path does not exist: {target}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Atlassian Document Format (ADF) text extractor  (Jira Cloud)
# ---------------------------------------------------------------------------

def _extract_adf_text(node: Any) -> str:
    """Recursively pull plain text out of an Atlassian Document Format node."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return " ".join(_extract_adf_text(item) for item in node)
    if isinstance(node, dict):
        text = node.get("text", "")
        content = node.get("content", [])
        return (text + " " + _extract_adf_text(content)).strip()
    return ""


# ---------------------------------------------------------------------------
# Jira connector
# ---------------------------------------------------------------------------

_JIRA_KEY_RE = re.compile(r"([A-Z][A-Z0-9]+-\d+)")


def _parse_jira_url(url_or_key: str) -> tuple[str | None, str | None]:
    """
    Return (base_url, issue_key) extracted from a Jira issue URL or bare key.

    Examples
    --------
    "PROJ-123"                                    → (None, "PROJ-123")
    "https://acme.atlassian.net/browse/PROJ-123"  → ("https://acme.atlassian.net", "PROJ-123")
    """
    m = _JIRA_KEY_RE.search(url_or_key)
    if not m:
        return None, None
    issue_key = m.group(1)

    # Try to extract base URL from the provided string
    base_url: str | None = None
    if url_or_key.startswith("http"):
        from urllib.parse import urlparse
        parsed = urlparse(url_or_key)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

    return base_url, issue_key


def fetch_jira(url_or_key: str, category: str = "jira") -> list[dict]:
    """
    Fetch a Jira issue and return a list of chunk dicts.

    Credentials are read from environment variables (or .env):
        JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN
    """
    try:
        import requests  # type: ignore
    except ImportError:
        print("[ERROR] requests is not installed.  Run:  pip install requests", file=sys.stderr)
        sys.exit(1)

    detected_base, issue_key = _parse_jira_url(url_or_key)
    if not issue_key:
        print(f"[ERROR] Could not extract a Jira issue key from: {url_or_key}", file=sys.stderr)
        sys.exit(1)

    base_url = os.environ.get("JIRA_BASE_URL", detected_base or "")
    email = os.environ.get("JIRA_EMAIL", "")
    token = os.environ.get("JIRA_API_TOKEN", "")

    if not base_url:
        print(
            "[ERROR] JIRA_BASE_URL is not set.\n"
            "Add it to your .env file or set it as an environment variable.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not email or not token:
        print(
            "[ERROR] JIRA_EMAIL and JIRA_API_TOKEN must be set.\n"
            "Add them to your .env file or set them as environment variables.",
            file=sys.stderr,
        )
        sys.exit(1)

    api_url = f"{base_url.rstrip('/')}/rest/api/3/issue/{issue_key}"
    print(f"  Fetching Jira issue {issue_key} from {base_url} …")

    resp = requests.get(api_url, auth=(email, token), timeout=30)
    if resp.status_code == 401:
        print("[ERROR] Jira authentication failed. Check JIRA_EMAIL and JIRA_API_TOKEN.", file=sys.stderr)
        sys.exit(1)
    if resp.status_code == 404:
        print(f"[ERROR] Jira issue {issue_key} not found.", file=sys.stderr)
        sys.exit(1)
    resp.raise_for_status()

    data = resp.json()
    fields = data.get("fields", {})
    summary = fields.get("summary", issue_key)
    description_raw = fields.get("description") or {}

    # Jira Cloud returns ADF; Jira Server returns a plain string
    if isinstance(description_raw, dict):
        description = _extract_adf_text(description_raw)
    else:
        description = str(description_raw)

    # Also include comments
    comments_raw = (fields.get("comment") or {}).get("comments", [])
    comment_texts = []
    for c in comments_raw:
        body = c.get("body") or {}
        if isinstance(body, dict):
            comment_texts.append(_extract_adf_text(body))
        else:
            comment_texts.append(str(body))

    full_text = f"{summary}\n\n{description}"
    if comment_texts:
        full_text += "\n\n--- Comments ---\n" + "\n\n".join(comment_texts)

    chunks = []
    for chunk_text in _split_text(full_text):
        chunks.append({
            "id": _sha256(chunk_text),
            "source": f"jira:{issue_key}",
            "category": category,
            "title": summary,
            "text": chunk_text,
            "metadata": {"issue_key": issue_key, "url": f"{base_url}/browse/{issue_key}"},
        })
    return chunks


# ---------------------------------------------------------------------------
# Confluence connector
# ---------------------------------------------------------------------------

_CONFLUENCE_PAGE_ID_RE = re.compile(r"/pages/(\d+)")


def _parse_confluence_url(url: str) -> tuple[str | None, str | None]:
    """
    Return (base_url, page_id) from a Confluence page URL.

    Example
    -------
    "https://acme.atlassian.net/wiki/spaces/ENG/pages/123456789/Title"
    → ("https://acme.atlassian.net", "123456789")
    """
    from urllib.parse import urlparse
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    m = _CONFLUENCE_PAGE_ID_RE.search(url)
    page_id = m.group(1) if m else None
    return base_url, page_id


def fetch_confluence(url: str, category: str = "confluence") -> list[dict]:
    """
    Fetch a Confluence page and return a list of chunk dicts.

    Credentials are read from environment variables (or .env):
        CONFLUENCE_BASE_URL, CONFLUENCE_EMAIL, CONFLUENCE_API_TOKEN
    """
    try:
        import requests  # type: ignore
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        print(
            "[ERROR] Missing dependencies.  Run:  pip install requests beautifulsoup4",
            file=sys.stderr,
        )
        sys.exit(1)

    detected_base, page_id = _parse_confluence_url(url)
    if not page_id:
        print(
            f"[ERROR] Could not extract a Confluence page ID from: {url}\n"
            "Expected URL format: .../wiki/spaces/SPACE/pages/<pageId>/...",
            file=sys.stderr,
        )
        sys.exit(1)

    base_url = os.environ.get("CONFLUENCE_BASE_URL", detected_base or "")
    email = os.environ.get("CONFLUENCE_EMAIL", "")
    token = os.environ.get("CONFLUENCE_API_TOKEN", "")

    if not base_url:
        print(
            "[ERROR] CONFLUENCE_BASE_URL is not set.\n"
            "Add it to your .env file or set it as an environment variable.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not email or not token:
        print(
            "[ERROR] CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN must be set.\n"
            "Add them to your .env file or set them as environment variables.",
            file=sys.stderr,
        )
        sys.exit(1)

    api_url = (
        f"{base_url.rstrip('/')}/wiki/rest/api/content/{page_id}"
        "?expand=body.storage,title,version"
    )
    print(f"  Fetching Confluence page {page_id} from {base_url} …")

    resp = requests.get(api_url, auth=(email, token), timeout=30)
    if resp.status_code == 401:
        print("[ERROR] Confluence authentication failed. Check CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN.", file=sys.stderr)
        sys.exit(1)
    if resp.status_code == 404:
        print(f"[ERROR] Confluence page {page_id} not found.", file=sys.stderr)
        sys.exit(1)
    resp.raise_for_status()

    data = resp.json()
    title = data.get("title", page_id)
    body_html = data.get("body", {}).get("storage", {}).get("value", "")
    plain = BeautifulSoup(body_html, "html.parser").get_text(separator="\n")

    chunks = []
    for chunk_text in _split_text(plain):
        chunks.append({
            "id": _sha256(chunk_text),
            "source": f"confluence:{page_id}",
            "category": category,
            "title": title,
            "text": chunk_text,
            "metadata": {"page_id": page_id, "url": url},
        })
    return chunks


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload knowledge files or Jira/Confluence content to the local ChromaDB vector database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/upload.py /path/to/doc.md
  python scripts/upload.py /path/to/docs/ --category confluence
  python scripts/upload.py /path/to/data.csv --category jira
  python scripts/upload.py --jira PROJ-123
  python scripts/upload.py --jira https://company.atlassian.net/browse/PROJ-123
  python scripts/upload.py --confluence https://company.atlassian.net/wiki/spaces/ENG/pages/123456
        """,
    )

    # Positional: optional file/directory path
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Path to a local file (.md, .xlsx, .xls, .csv) or directory.",
    )

    # Remote sources
    remote = parser.add_mutually_exclusive_group()
    remote.add_argument(
        "--jira",
        metavar="URL_OR_KEY",
        help="Jira issue URL or bare issue key (e.g. PROJ-123).",
    )
    remote.add_argument(
        "--confluence",
        metavar="URL",
        help="Confluence page URL.",
    )

    # Options
    parser.add_argument(
        "--category",
        default=None,
        help=(
            "Category label for all uploaded chunks "
            "(default: inferred from sub-directory name, 'general' otherwise)."
        ),
    )
    parser.add_argument(
        "--db-path",
        default="./chroma_db",
        help="Directory where ChromaDB stores its data (default: ./chroma_db).",
    )
    parser.add_argument(
        "--collection",
        default="knowledge",
        help="ChromaDB collection name (default: knowledge).",
    )

    args = parser.parse_args()

    if not args.path and not args.jira and not args.confluence:
        parser.print_help()
        sys.exit(0)

    chunks: list[dict] = []

    if args.path:
        target = Path(args.path)
        category = args.category or "general"
        chunks = parse_path(target, category)

    elif args.jira:
        chunks = fetch_jira(args.jira, category=args.category or "jira")

    elif args.confluence:
        chunks = fetch_confluence(args.confluence, category=args.category or "confluence")

    if not chunks:
        print("No chunks to upload.")
        sys.exit(0)

    print(f"  Upserting {len(chunks)} chunks into ChromaDB collection '{args.collection}' at {args.db_path} …")
    count = upsert_chunks(chunks, db_path=args.db_path, collection_name=args.collection)
    print(f"\n✓ {count} chunks uploaded.")


if __name__ == "__main__":
    main()
