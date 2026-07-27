"""In-process query cache with TTL and similarity threshold.

Used by the /api/ask endpoint to short-circuit repeat or near-duplicate queries.

Key design:
  - exact match keyed on (normalized query, request_id prefix not used)
  - similarity match compares Levenshtein ratio; only used when
    cfg.cache.similarity_threshold > 0
  - entries have a TTL (seconds); expired entries are evicted lazily on read

This is intentionally simple. A production system would use Redis with a proper
embedding-based similarity key.
"""
from __future__ import annotations
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Optional

from app.core.config import cfg


@dataclass
class CacheEntry:
    payload: dict[str, Any]
    stored_at: float


def _normalize(q: str) -> str:
    return " ".join(q.strip().lower().split())


def _similarity(a: str, b: str) -> float:
    """Quick similarity ratio (Levenshtein-based) in [0, 1]."""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    # trivial ratio = 1 - edit_distance / max_len
    la, lb = len(a), len(b)
    if abs(la - lb) > max(la, lb):
        return 0.0
    # iterative Levenshtein
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * lb
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = cur
    dist = prev[-1]
    return 1.0 - dist / max(la, lb)


class QueryCache:
    def __init__(self, ttl_seconds: Optional[int] = None,
                 similarity_threshold: Optional[float] = None) -> None:
        self._ttl = ttl_seconds if ttl_seconds is not None else int(cfg.cache.ttl_seconds)
        self._threshold = (
            similarity_threshold
            if similarity_threshold is not None
            else float(cfg.cache.similarity_threshold)
        )
        self._store: dict[str, CacheEntry] = {}
        self._lock = Lock()

    def get_exact(self, query: str) -> Optional[dict[str, Any]]:
        key = _normalize(query)
        now = time.time()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if now - entry.stored_at >= self._ttl:
                del self._store[key]
                return None
            return entry.payload

    def get_similar(self, query: str) -> tuple[Optional[str], Optional[dict[str, Any]]]:
        """Return (matched_key, payload) if a similar cached query exists."""
        if self._threshold <= 0:
            return None, None
        target = _normalize(query)
        now = time.time()
        best_key: Optional[str] = None
        best_score = 0.0
        with self._lock:
            for key, entry in self._store.items():
                if now - entry.stored_at >= self._ttl:
                    continue
                score = _similarity(target, key)
                if score >= self._threshold and score > best_score:
                    best_key = key
                    best_score = score
            if best_key is None:
                return None, None
            return best_key, self._store[best_key].payload

    def put(self, query: str, payload: dict[str, Any]) -> None:
        key = _normalize(query)
        with self._lock:
            self._store[key] = CacheEntry(payload=payload, stored_at=time.time())

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"size": len(self._store)}
