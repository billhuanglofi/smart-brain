# smart-brain 🧠

A **local-first company knowledge base** that feeds an AI-powered code-review tool.
Upload files from your laptop directly to a local [ChromaDB](https://www.trychroma.com/) vector
database, or pull pages straight from Jira and Confluence.  Everything runs on
your machine — no cloud services, no GitHub Actions required.

---

## Table of contents

1. [Repository layout](#repository-layout)
2. [First-time setup](#first-time-setup)
3. [Quick start](#quick-start)
4. [Uploading knowledge](#uploading-knowledge)
   - [Upload a local file](#upload-a-local-file)
   - [Upload an entire directory](#upload-an-entire-directory)
   - [Fetch a Jira issue](#fetch-a-jira-issue)
   - [Fetch a Confluence page](#fetch-a-confluence-page)
5. [Searching the database](#searching-the-database)
6. [Migrating to a new machine](#migrating-to-a-new-machine)
7. [HTTP API server](#http-api-server)
8. [Integration with the code-review AI tool](#integration-with-the-code-review-ai-tool)
9. [Running tests](#running-tests)

---

## Repository layout

```
smart-brain/
├── knowledge/
│   ├── jira/          ← drop Jira exports (.md, .xlsx, .csv) here
│   ├── confluence/    ← drop Confluence exports here
│   └── general/       ← any other internal docs
├── models/            ← gitignored; populated once by scripts/setup.py
│   └── all-MiniLM-L6-v2/   ← the embedding model (~90 MB)
├── scripts/
│   ├── setup.py       ← STEP 1 – download the embedding model into models/
│   ├── upload.py      ← STEP 2 – upload files / Jira / Confluence → ChromaDB
│   ├── search.py      ← search ChromaDB or the legacy JSONL index
│   ├── db.py          ← export / import for migration between machines
│   ├── _embedding.py  ← shared embedding-function helper (used internally)
│   ├── ingest.py      ← batch-build a flat JSONL index (optional)
│   ├── serve.py       ← lightweight HTTP API over ChromaDB (optional)
│   └── tests_scripts.py
├── .env.example       ← copy to .env and fill in your credentials
├── requirements.txt
└── .gitignore
```

---

## First-time setup

Run these steps **once** on every laptop where you use smart-brain.

```bash
# 1. Clone
git clone https://github.com/billhuanglofi/smart-brain.git
cd smart-brain

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Download the embedding model into models/
#    (~90 MB, downloaded once, then fully offline)
python scripts/setup.py

# 4. Copy and fill in Jira / Confluence credentials
cp .env.example .env
# edit .env with your JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN …
```

After step 3, the model lives in `models/all-MiniLM-L6-v2/` inside the repo.
All subsequent uploads and searches are **fully offline**.

---

## Quick start

```bash
# Upload a document
python scripts/upload.py /path/to/your/doc.md

# Search
python scripts/search.py "authentication flow" --db-path ./chroma_db
```

---

## Uploading knowledge

All uploads go through **`scripts/upload.py`**.

### Upload a local file

Supported formats: `.md` (Markdown), `.xlsx`, `.xls` (Excel), `.csv`.

```bash
python scripts/upload.py /path/to/document.md
python scripts/upload.py /path/to/report.xlsx --category jira
python scripts/upload.py /path/to/export.csv  --category confluence
```

### Upload an entire directory

All supported files inside the directory are processed recursively.
The category is inferred from the first sub-directory level unless you override it.

```bash
python scripts/upload.py /path/to/docs/
python scripts/upload.py /path/to/docs/ --category general
```

### Fetch a Jira issue

Provide either a bare issue key or the full browser URL.
Credentials are read from `.env` (see `.env.example`).

```bash
python scripts/upload.py --jira PROJ-123
python scripts/upload.py --jira https://your-company.atlassian.net/browse/PROJ-123
```

Fetches: summary, full description (Jira Cloud ADF format is handled), and all
comments.

### Fetch a Confluence page

Provide the full browser URL of the page.

```bash
python scripts/upload.py --confluence https://your-company.atlassian.net/wiki/spaces/ENG/pages/123456789
```

### Common options

| Flag | Default | Description |
|---|---|---|
| `--category` | auto-detected | Label used to tag and filter chunks |
| `--db-path` | `./chroma_db` | Where ChromaDB stores its data |
| `--collection` | `knowledge` | ChromaDB collection name |

---

## Searching the database

```bash
# Semantic search via ChromaDB (recommended)
python scripts/search.py "JWT token refresh" --db-path ./chroma_db

# Filter to a specific category
python scripts/search.py "sprint velocity" --db-path ./chroma_db --category jira

# Limit results
python scripts/search.py "database migration" --db-path ./chroma_db --top-k 3

# Legacy JSONL keyword search (after running scripts/ingest.py)
python scripts/search.py "your query" --index-dir index
```

Results are printed as JSON lines:

```json
{
  "id": "a1b2c3d4",
  "source": "jira:PROJ-123",
  "category": "jira",
  "title": "Fix authentication timeout",
  "text": "...",
  "metadata": {"issue_key": "PROJ-123", "url": "https://..."},
  "score": 0.94
}
```

---

## Migrating to a new machine

The `db.py` script exports all knowledge to a plain JSONL file that can be
moved between machines, committed to git, or imported into a different vector
database in the future.

### Export from the old machine

```bash
python scripts/db.py export
# → writes knowledge_snapshot.jsonl (one JSON record per chunk)
```

```bash
# Custom output file or DB location
python scripts/db.py export --output team_snapshot.jsonl
python scripts/db.py export --output backup.jsonl --db-path /path/to/chroma_db
```

### Copy the snapshot

The generated `knowledge_snapshot.jsonl` is plain text — copy it however you like:

```bash
# Option A: commit to git (if the file is small enough)
git add knowledge_snapshot.jsonl && git commit -m "chore: update knowledge snapshot"

# Option B: copy manually
cp knowledge_snapshot.jsonl /shared/drive/
scp knowledge_snapshot.jsonl user@new-machine:~/smart-brain/
```

### Import on the new machine

```bash
# After cloning and running first-time setup on the new machine:
python scripts/db.py import knowledge_snapshot.jsonl
```

The import rebuilds all vector embeddings from the raw text in the snapshot,
so it works even after upgrading the embedding model.

### Why JSONL and not a ChromaDB directory copy?

| | ChromaDB folder copy | JSONL snapshot |
|---|---|---|
| Portable between OS/arch | ❌ (SQLite binary) | ✅ |
| Human-readable / diffable | ❌ | ✅ |
| Can move to Qdrant/pgvector | ❌ | ✅ |
| Includes raw text for re-embedding | ❌ | ✅ |

---

## HTTP API server

The optional Flask server exposes the knowledge base over HTTP so your
code-review tool can query it remotely (or from a different process).

```bash
pip install flask
python scripts/serve.py --db-path ./chroma_db --port 5001
```

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check |
| GET | `/categories` | List categories in the index |
| GET | `/search?q=…&top_k=5&category=jira` | Search |
| POST | `/search` | Search with JSON body |

---

## Integration with the code-review AI tool

Add this helper to your existing code-review Python project:

```python
"""knowledge_context.py – paste into your code-review project."""

import requests

BRAIN_URL = "http://localhost:5001"   # URL where scripts/serve.py is running


def get_knowledge_context(pr_diff: str, top_k: int = 5) -> str:
    """
    Fetch the most relevant internal knowledge for a PR diff and return it
    as a formatted string ready to inject into an LLM prompt.
    """
    try:
        resp = requests.get(
            f"{BRAIN_URL}/search",
            params={"q": pr_diff[:500], "top_k": top_k},
            timeout=5,
        )
        resp.raise_for_status()
        chunks = resp.json()
    except requests.RequestException:
        return ""   # degrade gracefully when the brain is unavailable

    if not chunks:
        return ""

    lines = ["### Relevant internal knowledge\n"]
    for c in chunks:
        lines.append(f"**[{c['category']}] {c['title']}**")
        lines.append(c["text"][:400])
        lines.append("")
    return "\n".join(lines)
```

Inject the returned string into your review prompt:

```python
from knowledge_context import get_knowledge_context

def build_review_prompt(pr_diff: str) -> str:
    context = get_knowledge_context(pr_diff)
    return f"""You are a senior software engineer performing a code review.

{context}

## Pull request diff
{pr_diff}

Review the diff above. Focus on correctness, security, and adherence to the
internal standards described in the knowledge context.
"""
```

### File-based alternative (no HTTP server)

If the code-review tool runs on the same machine you can query ChromaDB directly:

```python
import sys
sys.path.insert(0, "/path/to/smart-brain/scripts")

import chromadb
from _embedding import get_embedding_function

client = chromadb.PersistentClient(path="/path/to/smart-brain/chroma_db")
collection = client.get_collection("knowledge", embedding_function=get_embedding_function())
results = collection.query(query_texts=[pr_diff[:500]], n_results=5)
context = "\n".join(results["documents"][0])
```

---

## Running tests

```bash
pip install pytest
pytest scripts/tests_scripts.py -v
```


