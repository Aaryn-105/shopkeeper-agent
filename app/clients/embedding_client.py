"""Embedding client for bge-small-zh via sentence-transformers.

Loaded lazily on first call so the FastAPI lifespan does not block. If the model
path is missing we fall back to a deterministic hash-based fake vector so the
rest of the system (FAISS, recall, tests) can still operate offline.
"""
from __future__ import annotations
import hashlib
from typing import Optional

from app.core.config import cfg


def _hash_vector(text: str, dim: int) -> list[float]:
    """Deterministic pseudo-embedding from text SHA-256, normalized to [-1, 1].

    This is NOT semantically meaningful but it is stable and unit-length, which
    is enough for local FAISS indexing and similarity-search plumbing.
    """
    vec = [0.0] * dim
    for i in range(8):
        chunk = hashlib.sha256(f"{text}::{i}".encode("utf-8")).digest()
        for j in range(dim):
            vec[j] += (chunk[j % len(chunk)] - 128) / 128.0
    norm = sum(x * x for x in vec) ** 0.5 or 1.0
    return [x / norm for x in vec]


class EmbeddingClient:
    def __init__(self, model_path: Optional[str] = None,
                 dim: Optional[int] = None) -> None:
        self.model_path = str(model_path or cfg.embedding.model_path)
        self.dim = int(dim or cfg.embedding.dim)
        self._model = None
        self._load_error: Optional[str] = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def load_error(self) -> Optional[str]:
        return self._load_error

    def _ensure_loaded(self) -> None:
        if self._model is not None or self._load_error is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_path)
        except Exception as e:
            self._load_error = f"{type(e).__name__}: {e}"[:120]
            self._model = None

    def encode(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text; falls back to hash vectors offline."""
        if not texts:
            return []
        self._ensure_loaded()
        if self._model is None:
            return [_hash_vector(t, self.dim) for t in texts]
        try:
            vecs = self._model.encode(
                texts,
                batch_size=int(cfg.embedding.batch_size),
                convert_to_numpy=True,
                show_progress_bar=False,
                normalize_embeddings=True,
            )
            return [v.tolist()[: self.dim] for v in vecs]
        except Exception:
            return [_hash_vector(t, self.dim) for t in texts]