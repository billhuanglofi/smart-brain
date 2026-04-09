# smart-brain 🧠

A **company knowledge repository** that acts as the long-term memory for your
AI-powered code-review tool.  Drop Excel spreadsheets or Markdown documents
from Jira / Confluence into the `knowledge/` folder, and this repo will
automatically index them into a searchable JSONL store that your Python AI tool
can query.

---

## Table of contents

1. [Repository layout](#repository-layout)
2. [Quick start](#quick-start)
3. [Adding knowledge](#adding-knowledge)
4. [Searching the index](#searching-the-index)
5. [HTTP API server](#http-api-server)
6. [Integration with your code-review AI tool](#integration-with-your-code-review-ai-tool)
7. [GitHub Actions auto-indexing](#github-actions-auto-indexing)

---

## Repository layout

```
smart-brain/
├── knowledge/
│   ├── jira/          ← Jira ticket exports (.md or .xlsx)
│   ├── confluence/    ← Confluence page exports (.md or .xlsx)
│   └── general/       ← Any other internal docs
├── scripts/
│   ├── ingest.py      ← Parse files → chunks.jsonl
│   ├── search.py      ← CLI / library search over the index
│   ├── serve.py       ← HTTP API server (Flask)
│   └── tests_scripts.py ← Unit tests
├── index/             ← Generated (gitignored locally, committed by CI)
│   └── chunks.jsonl
├── requirements.txt
├── .gitignore
└── .github/
    └── workflows/
        └── index.yml  ← Auto-rebuild index on push to main
```

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/billhuanglofi/smart-brain.git
cd smart-brain

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Build the index from all knowledge files
python scripts/ingest.py

# 4. Search
python scripts/search.py "authentication flow"
```

---

## Adding knowledge

Drop **Markdown** (`.md`) or **Excel** (`.xlsx` / `.xls`) files into the
appropriate sub-folder of `knowledge/`:

| Source         | Target folder            |
|----------------|--------------------------|
| Jira export    | `knowledge/jira/`        |
| Confluence page| `knowledge/confluence/`  |
| Other docs     | `knowledge/general/`     |

Then commit and push.  The GitHub Actions workflow will re-index automatically.

To re-index locally without pushing:

```bash
python scripts/ingest.py --knowledge-dir knowledge --output-dir index
```

---

## Searching the index

### CLI

```bash
# Keyword search (no extra dependencies)
python scripts/search.py "JWT token refresh" --top-k 5

# Filter to a specific category
python scripts/search.py "sprint velocity" --category jira --top-k 3

# Semantic / embedding search (requires: pip install sentence-transformers numpy)
python scripts/search.py "database migration strategy" --semantic --top-k 5
```

Each result is printed as a JSON line:

```json
{
  "id": "a1b2c3d4",
  "source": "knowledge/confluence/architecture_overview.md",
  "category": "confluence",
  "title": "Architecture Overview",
  "text": "...",
  "metadata": {},
  "score": 0.042
}
```

### Python library

```python
import sys
sys.path.insert(0, "scripts")

from search import _keyword_search, _load_chunks

chunks  = _load_chunks("index")
results = _keyword_search("CORS policy", chunks, top_k=5)
for r in results:
    print(r["title"], "–", r["text"][:120])
```

---

## HTTP API server

The optional HTTP server lets any external tool query the knowledge base over
the network — no shared file system required.

```bash
pip install flask
python scripts/serve.py --port 5001
```

### Endpoints

| Method | Path          | Description                                   |
|--------|---------------|-----------------------------------------------|
| GET    | `/health`     | Health check → `{"status": "ok"}`             |
| GET    | `/categories` | List all knowledge categories                 |
| GET    | `/search?q=…` | Keyword search (add `&semantic=true` for embeddings) |
| POST   | `/search`     | Same as GET but with a JSON body              |

**GET /search example:**

```bash
curl "http://localhost:5001/search?q=authentication&top_k=3"
```

**POST /search example:**

```bash
curl -X POST http://localhost:5001/search \
     -H "Content-Type: application/json" \
     -d '{"query": "database migration", "top_k": 5, "category": "confluence"}'
```

---

## Integration with your code-review AI tool

Below is the recommended pattern for enriching a PR-diff review with context
from this knowledge base.  Add this helper to your existing code-review Python
project:

```python
"""knowledge_context.py – drop this into your code-review project."""

import requests

BRAIN_URL = "http://localhost:5001"   # or the URL where serve.py is running


def get_knowledge_context(pr_diff: str, top_k: int = 5) -> str:
    """
    Fetch the most relevant knowledge chunks for the given PR diff and
    return them as a formatted string ready to be injected into an LLM prompt.
    """
    # Use the first 500 chars of the diff as a search query
    query = pr_diff[:500]
    try:
        resp = requests.get(
            f"{BRAIN_URL}/search",
            params={"q": query, "top_k": top_k},
            timeout=5,
        )
        resp.raise_for_status()
        chunks = resp.json()
    except requests.RequestException:
        return ""   # gracefully degrade when the brain is unavailable

    if not chunks:
        return ""

    lines = ["### Relevant internal knowledge\n"]
    for c in chunks:
        lines.append(f"**[{c['category']}] {c['title']}**")
        lines.append(c["text"][:400])
        lines.append("")
    return "\n".join(lines)
```

Then inject the returned string into your existing review prompt:

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

### Alternative: file-based integration (no server)

If both repos run on the same machine you can skip the HTTP server and read
`index/chunks.jsonl` directly:

```python
import json, sys
sys.path.insert(0, "/path/to/smart-brain/scripts")

from search import _keyword_search, _load_chunks

chunks  = _load_chunks("/path/to/smart-brain/index")
results = _keyword_search(pr_diff[:500], chunks, top_k=5)
context = "\n".join(r["text"] for r in results)
```

---

## GitHub Actions auto-indexing

Every push to `main` that modifies a file under `knowledge/` triggers
`.github/workflows/index.yml`.  The workflow:

1. Installs Python dependencies.
2. Runs `scripts/ingest.py` to rebuild `index/chunks.jsonl`.
3. Commits the updated index back to `main` with the message
   `chore: rebuild knowledge index [skip ci]`.

This means your code-review AI tool can simply `git pull` this repo to get the
latest knowledge without any manual steps.

---

## Running tests

```bash
pip install pytest
pytest scripts/tests_scripts.py -v
```
