# smart-brain 🧠

A **local-first company knowledge base** that feeds an AI-powered code-review tool.
Upload files from your laptop directly to a local [ChromaDB](https://www.trychroma.com/) vector
database, or pull pages straight from Jira and Confluence.  Everything runs on
your machine — no cloud services, no GitHub Actions required.

---

## Table of contents

1. [Repository layout](#repository-layout)
2. [Quick start](#quick-start)
3. [Uploading knowledge](#uploading-knowledge)
   - [Upload a local file](#upload-a-local-file)
   - [Upload an entire directory](#upload-an-entire-directory)
   - [Fetch a Jira issue](#fetch-a-jira-issue)
   - [Fetch a Confluence page](#fetch-a-confluence-page)
4. [Searching the database](#searching-the-database)
5. [HTTP API server](#http-api-server)
6. [Integration with the code-review AI tool](#integration-with-the-code-review-ai-tool)
7. [Running tests](#running-tests)

---

## Repository layout

```
smart-brain/
├── knowledge/
│   ├── jira/          ← drop Jira exports (.md, .xlsx, .csv) here
│   ├── confluence/    ← drop Confluence exports here
│   └── general/       ← any other internal docs
├── scripts/
│   ├── upload.py      ← PRIMARY TOOL – upload files / Jira / Confluence → ChromaDB
│   ├── search.py      ← search ChromaDB or the legacy JSONL index
│   ├── ingest.py      ← batch-build a flat JSONL index (optional)
│   ├── serve.py       ← lightweight HTTP API over ChromaDB (optional)
│   └── tests_scripts.py
├── .env.example       ← copy to .env and fill in your credentials
├── requirements.txt
└── .gitignore
```

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/billhuanglofi/smart-brain.git
cd smart-brain

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Copy and fill in Jira / Confluence credentials (only needed for remote fetch)
cp .env.example .env
# edit .env with your JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN …

# 4. Upload your first document
python scripts/upload.py /path/to/your/doc.md

# 5. Search
python scripts/search.py "authentication flow" --db-path ./chroma_db
```

> **First run note**: ChromaDB will download a small embedding model (~90 MB) and
> cache it in `~/.cache/chroma/`.  This only happens once.

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

client = chromadb.PersistentClient(path="/path/to/smart-brain/chroma_db")
collection = client.get_collection("knowledge")
results = collection.query(query_texts=[pr_diff[:500]], n_results=5)
context = "\n".join(results["documents"][0])
```

---

## Running tests

```bash
pip install pytest
pytest scripts/tests_scripts.py -v
```

