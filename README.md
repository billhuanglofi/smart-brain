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
7. [Querying from another project via HTTP](#querying-from-another-project-via-http)
8. [Running tests](#running-tests)

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

## Querying from another project via HTTP

The **recommended way** for another project to query smart-brain is over HTTP.
Start the server on the smart-brain machine and call it from anywhere.

### 1 — Start the server

```bash
# On the machine where smart-brain lives:
python scripts/serve.py --db-path ./chroma_db
# Smart-Brain API running on http://0.0.0.0:5001  [ChromaDB at ./chroma_db]
```

Custom port or bind address:

```bash
python scripts/serve.py --db-path ./chroma_db --host 0.0.0.0 --port 5001
```

### 2 — Available endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Returns `{"status": "ok"}` — use for liveness checks |
| GET | `/categories` | JSON array of distinct category names in the database |
| GET | `/search?q=…` | Semantic search; optional `&top_k=5` and `&category=jira` |
| POST | `/search` | Same search with a JSON body |

### 3 — Call from any language

**Python (copy-paste into your other project):**

```python
import requests

BRAIN_URL = "http://localhost:5001"   # change to hostname/IP if on another machine


def query_knowledge(query: str, top_k: int = 5, category: str | None = None) -> list[dict]:
    """Return a list of relevant knowledge chunks from smart-brain."""
    params = {"q": query, "top_k": top_k}
    if category:
        params["category"] = category
    resp = requests.get(f"{BRAIN_URL}/search", params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


# Example
chunks = query_knowledge("JWT token refresh", top_k=3)
for c in chunks:
    print(f"[{c['category']}] {c['title']}  (score={c['score']:.2f})")
    print(c["text"][:200])
    print()
```

**POST (richer filtering):**

```python
resp = requests.post(
    f"{BRAIN_URL}/search",
    json={"query": "authentication flow", "top_k": 5, "category": "confluence"},
)
chunks = resp.json()
```

**curl:**

```bash
curl "http://localhost:5001/search?q=authentication+flow&top_k=3"
curl -X POST http://localhost:5001/search \
     -H "Content-Type: application/json" \
     -d '{"query": "sprint velocity", "top_k": 5, "category": "jira"}'
```

### 4 — Response format

Each item in the returned array:

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

Higher `score` (max 1.0) means more semantically similar to your query.

### 5 — Integration example: inject into an LLM prompt

```python
def build_review_prompt(pr_diff: str) -> str:
    chunks = query_knowledge(pr_diff[:500], top_k=5)
    context = "\n".join(
        f"[{c['category']}] {c['title']}\n{c['text'][:400]}"
        for c in chunks
    )
    return f"""You are a senior software engineer performing a code review.

### Relevant internal knowledge
{context}

## Pull request diff
{pr_diff}

Review the diff. Focus on correctness, security, and adherence to internal standards.
"""
```

### File-based alternative (same machine, no HTTP server)

If your other project runs on the same machine you can import ChromaDB directly:

```python
import sys
sys.path.insert(0, "/path/to/smart-brain/scripts")

import chromadb
from _embedding import get_embedding_function

client = chromadb.PersistentClient(path="/path/to/smart-brain/chroma_db")
collection = client.get_collection("knowledge", embedding_function=get_embedding_function())
results = collection.query(query_texts=["your query"], n_results=5)
context = "\n".join(results["documents"][0])
```

---

## Running tests

```bash
pip install pytest
pytest scripts/tests_scripts.py -v
```
