"""
_embedding.py – shared embedding-function helper for smart-brain.

All vector operations (upload, search, export/import) use the same
sentence-transformer model so that embeddings stay consistent.

Model
-----
all-MiniLM-L6-v2  (~90 MB, fast, good quality for English text)

Location preference
-------------------
1. Local copy at  <repo>/models/all-MiniLM-L6-v2/  (run setup.py once)
2. System / Hugging Face hub cache  (downloads on first use)

To avoid the download entirely, run the one-time setup:

    python scripts/setup.py
"""

from __future__ import annotations

import sys
from pathlib import Path

MODEL_NAME = "all-MiniLM-L6-v2"

# Canonical path for the model bundled inside the repository
_REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_MODEL_PATH = _REPO_ROOT / "models" / MODEL_NAME


def get_embedding_function():
    """
    Return a ChromaDB-compatible ``SentenceTransformerEmbeddingFunction``.

    Uses the local model copy when available (after running setup.py),
    otherwise falls back to downloading from Hugging Face.
    """
    try:
        from chromadb.utils.embedding_functions.sentence_transformer_embedding_function import (  # type: ignore
            SentenceTransformerEmbeddingFunction,
        )
    except ImportError:
        print(
            "[ERROR] sentence-transformers is not installed.\n"
            "Run:  pip install -r requirements.txt",
            file=sys.stderr,
        )
        sys.exit(1)

    if LOCAL_MODEL_PATH.exists():
        model_path = str(LOCAL_MODEL_PATH)
    else:
        print(
            f"[INFO] Local model not found at '{LOCAL_MODEL_PATH}'.\n"
            "       Run  python scripts/setup.py  once to bundle it in the repo.\n"
            "       Falling back to Hugging Face download …",
            file=sys.stderr,
        )
        model_path = MODEL_NAME

    return SentenceTransformerEmbeddingFunction(model_name=model_path)
