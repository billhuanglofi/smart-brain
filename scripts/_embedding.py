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

ONNX Runtime note
-----------------
On some Windows machines the ONNX Runtime DLLs cannot be loaded (missing
Visual C++ Redistributable, architecture mismatch, etc.).  When that
happens the helper automatically falls back to the PyTorch backend so
that the model still works without requiring users to debug DLL issues.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List

MODEL_NAME = "all-MiniLM-L6-v2"

# Canonical path for the model bundled inside the repository
_REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_MODEL_PATH = _REPO_ROOT / "models" / MODEL_NAME


def _is_onnx_error(exc: BaseException) -> bool:
    """Return *True* if *exc* looks like an ONNX Runtime loading failure."""
    msg = str(exc).lower()
    return "onnx" in msg or "onnxruntime" in msg


class _PyTorchEmbeddingFunction:
    """ChromaDB-compatible embedding function backed by PyTorch (no ONNX)."""

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer  # type: ignore

        self.model_name = model_name
        self._model = SentenceTransformer(model_name, backend="torch")

    def __call__(self, input: List[str]) -> List[List[float]]:  # noqa: A002
        embeddings = self._model.encode(list(input), convert_to_numpy=True)
        return embeddings.tolist()


def _resolve_model_path() -> str:
    """Return the model identifier: local path when available, else HF name."""
    if LOCAL_MODEL_PATH.exists():
        return str(LOCAL_MODEL_PATH)

    print(
        f"[INFO] Local model not found at '{LOCAL_MODEL_PATH}'.\n"
        "       Run  python scripts/setup.py  once to bundle it in the repo.\n"
        "       Falling back to Hugging Face download …",
        file=sys.stderr,
    )
    return MODEL_NAME


def get_embedding_function():
    """
    Return a ChromaDB-compatible embedding function.

    Tries ChromaDB's built-in ``SentenceTransformerEmbeddingFunction`` first.
    If ONNX Runtime fails (common on Windows when the required DLLs are
    missing), it falls back to a pure-PyTorch embedding function
    automatically.
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

    model_path = _resolve_model_path()

    # --- First attempt: default backend (may use ONNX Runtime) -----------
    try:
        return SentenceTransformerEmbeddingFunction(model_name=model_path)
    except Exception as exc:
        if not _is_onnx_error(exc):
            raise
        print(
            f"[WARN] ONNX Runtime could not be loaded ({exc}).\n"
            "       Falling back to PyTorch backend …",
            file=sys.stderr,
        )

    # --- Fallback: explicit PyTorch backend ------------------------------
    return _PyTorchEmbeddingFunction(model_path)
