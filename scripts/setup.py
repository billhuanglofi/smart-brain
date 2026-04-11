"""
setup.py – one-time setup: download the embedding model into the repository.

Usage
-----
    python scripts/setup.py

What it does
------------
Downloads the 'all-MiniLM-L6-v2' sentence-transformer model (~90 MB) and
saves it to  models/all-MiniLM-L6-v2/  inside the repository root.

After running this script once on every machine, all upload and search
operations work fully offline.  The models/ directory is gitignored — each
developer runs this script on their own laptop after cloning.
"""

from __future__ import annotations

import sys
from pathlib import Path

_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

from _embedding import LOCAL_MODEL_PATH, MODEL_NAME  # noqa: E402


def download_model() -> None:
    """Download the embedding model to the local models/ directory."""
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except ImportError:
        print(
            "[ERROR] sentence-transformers is not installed.\n"
            "Run:  pip install -r requirements.txt",
            file=sys.stderr,
        )
        sys.exit(1)

    from _embedding import _is_onnx_error  # noqa: E402

    if LOCAL_MODEL_PATH.exists():
        print(f"✓ Model already present at {LOCAL_MODEL_PATH}  (nothing to do)")
        return

    LOCAL_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"  Downloading '{MODEL_NAME}' (~90 MB) from Hugging Face …")

    try:
        model = SentenceTransformer(MODEL_NAME)
    except Exception as exc:
        if not _is_onnx_error(exc):
            raise
        print(
            f"[WARN] ONNX Runtime could not be loaded ({exc}).\n"
            "       Retrying with PyTorch backend …",
            file=sys.stderr,
        )
        model = SentenceTransformer(MODEL_NAME, backend="torch")

    model.save(str(LOCAL_MODEL_PATH))
    print(f"✓ Model saved to {LOCAL_MODEL_PATH}")
    print("  You can now run upload.py and search.py fully offline.")


if __name__ == "__main__":
    download_model()
