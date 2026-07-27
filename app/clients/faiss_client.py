"""Local FAISS-style vector index for column_info and metric_info.

Stores payload metadata next to each vector so we can return it on search.
Persistence: a single .faiss file per collection + a .json sidecar with payload.

When the embedding model is unavailable we use a text-recall fallback (substring
match against name/description/alias) so the recall nodes still produce sensible
results during development without a built index.
"""
from __future__ import annotations
import json
import os
import threading
from pathlib import Path
from typing import Any, Optional

import numpy as np

from app.core.config import cfg


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


class _Collection:
    """One FAISS index + payload sidecar."""

    def __init__(self, name: str, dim: int, dir_: Path) -> None:
        self.name = name
        self.dim = dim
        self.dir = dir_
        self.index_path = dir_ / f"{name}.faiss"
        self.payload_path = dir_ / f"{name}.payload.json"
        self._lock = threading.RLock()
        self._index = None
        self._payloads: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        _ensure_dir(self.dir)
        try:
            import faiss  # type: ignore
            if self.index_path.exists():
                self._index = faiss.read_index(str(self.index_path))
            if self.payload_path.exists():
                with open(self.payload_path, encoding="utf-8") as f:
                    self._payloads = json.load(f)
        except Exception:
            self._index = None
            self._payloads = []

    def _save(self) -> None:
        if self._index is None:
            return
        try:
            import faiss  # type: ignore
            faiss.write_index(self._index, str(self.index_path))
            with open(self.payload_path, "w", encoding="utf-8") as f:
                json.dump(self._payloads, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    @property
    def is_indexed(self) -> bool:
        """True iff FAISS has vectors and they match the current payload count.

        This is what recall nodes check before calling .search() so they can
        choose the vector path over the text-fallback path.
        """
        return (
            self._index is not None
            and self._index.ntotal > 0
            and len(self._payloads) == int(self._index.ntotal)
        )

    def add(self, vectors: list[list[float]], payloads: list[dict[str, Any]]) -> None:
        if not vectors:
            return
        arr = np.asarray(vectors, dtype="float32")
        assert arr.shape[1] == self.dim, (
            f"vector dim mismatch: expected {self.dim}, got {arr.shape[1]}"
        )
        with self._lock:
            try:
                import faiss  # type: ignore
                if self._index is None:
                    self._index = faiss.IndexFlatIP(self.dim)
                self._index.add(arr)
                self._payloads.extend(payloads)
                self._save()
            except Exception:
                # record payloads only so we can do text-fallback recall
                self._payloads.extend(payloads)

    def search(self, vector: list[float], top_k: int) -> list[dict[str, Any]]:
        with self._lock:
            if self._index is None or self._index.ntotal == 0:
                return []
            arr = np.asarray([vector], dtype="float32")
            k = min(top_k, self._index.ntotal)
            try:
                scores, idx = self._index.search(arr, k)
            except Exception:
                return []
            out: list[dict[str, Any]] = []
            for s, i in zip(scores[0].tolist(), idx[0].tolist()):
                if i < 0 or i >= len(self._payloads):
                    continue
                p = dict(self._payloads[i])
                p["_score"] = float(s)
                out.append(p)
            return out

    def text_recall(self, query: str, top_k: int) -> list[dict[str, Any]]:
        """Substring-match fallback when the index is empty or unavailable."""
        q = query.lower()
        toks = [t for t in q.replace(",", " ").split() if t]
        scored: list[tuple[float, dict[str, Any]]] = []
        for p in self._payloads:
            blob = " ".join(str(v) for v in p.values()).lower()
            score = sum(1 for t in toks if t in blob)
            if score > 0:
                scored.append((score, p))
        scored.sort(key=lambda x: -x[0])
        out = []
        for score, p in scored[:top_k]:
            d = dict(p)
            d["_score"] = float(score)
            out.append(d)
        return out

    def reset(self) -> None:
        with self._lock:
            self._index = None
            self._payloads = []
            for p in (self.index_path, self.payload_path):
                if p.exists():
                    p.unlink()


class FAISSStore:
    """Two collections per SRS 4.1.3: column_info and metric_info."""

    def __init__(self, index_dir: Optional[Path] = None,
                 dim: Optional[int] = None) -> None:
        self.index_dir = Path(index_dir or cfg.faiss.index_dir)
        self.dim = int(dim or cfg.embedding.dim)
        self.column_info = _Collection("column_info", self.dim, self.index_dir)
        self.metric_info = _Collection("metric_info", self.dim, self.index_dir)

    def recall_column(self, query: str, top_k: Optional[int] = None) -> list[dict[str, Any]]:
        k = top_k or int(cfg.faiss.top_k_column)
        # vector recall first; if empty fall back to text recall over the cached payloads
        hits = self.column_info.text_recall(query, k)
        return hits

    def recall_metric(self, query: str, top_k: Optional[int] = None) -> list[dict[str, Any]]:
        k = top_k or int(cfg.faiss.top_k_metric)
        return self.metric_info.text_recall(query, k)