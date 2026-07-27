"""In-process metrics collector with periodic JSONL flush.

Tracks per-node P95 latency, LLM call counts, token usage, cache hits/misses,
and per-request totals. Thread-safe; designed to live on app.state.
"""
from __future__ import annotations
import asyncio
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from threading import RLock
from typing import Any, Optional


@dataclass
class LLMCallStat:
    node_name: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    cache_hit: bool = False
    created_at: float = field(default_factory=time.time)


class Metrics:
    def __init__(self) -> None:
        self._node_latencies: dict[str, list[float]] = defaultdict(list)
        self._llm_calls: list[LLMCallStat] = []
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._requests_total: int = 0
        self._lock = RLock()
        self._started_at = time.time()

    def record_node_latency(self, node: str, ms: float) -> None:
        with self._lock:
            self._node_latencies[node].append(float(ms))

    def record_llm_call(self, stat: LLMCallStat) -> None:
        with self._lock:
            self._llm_calls.append(stat)

    def record_cache(self, hit: bool) -> None:
        with self._lock:
            if hit:
                self._cache_hits += 1
            else:
                self._cache_misses += 1

    def record_request(self) -> None:
        with self._lock:
            self._requests_total += 1

    @staticmethod
    def _percentile(values: list[float], p: float) -> float:
        if not values:
            return 0.0
        s = sorted(values)
        idx = int(p * (len(s) - 1))
        return round(s[idx], 2)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            total = self._cache_hits + self._cache_misses
            call_count = len(self._llm_calls)
            totals = {
                "prompt": sum(s.prompt_tokens for s in self._llm_calls),
                "completion": sum(s.completion_tokens for s in self._llm_calls),
                "all": sum(s.total_tokens for s in self._llm_calls),
            }
            avg_latency = (
                sum(s.latency_ms for s in self._llm_calls) / call_count
                if call_count else 0.0
            )
            return {
                "uptime_seconds": round(time.time() - self._started_at, 2),
                "requests_total": self._requests_total,
                "cache": {
                    "hits": self._cache_hits,
                    "misses": self._cache_misses,
                    "hit_rate": round(self._cache_hits / total, 4) if total else 0.0,
                },
                "llm": {
                    "calls": call_count,
                    "tokens": totals,
                    "avg_latency_ms": round(avg_latency, 2),
                },
                "node_p95_latency_ms": {
                    node: self._percentile(lats, 0.95)
                    for node, lats in self._node_latencies.items()
                },
            }

    def dump_jsonl(self, path: Path) -> int:
        """Append summary to JSONL; returns number of entries appended."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            entry = {"ts": round(time.time(), 2), **self.summary()}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return 1


_metrics: Optional[Metrics] = None


def get_metrics() -> Metrics:
    global _metrics
    if _metrics is None:
        _metrics = Metrics()
    return _metrics


async def metrics_periodic_flush(path: Path, interval_seconds: float = 30.0) -> None:
    """Background coroutine: flush metrics to JSONL until cancelled."""
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            get_metrics().dump_jsonl(path)
        except asyncio.CancelledError:
            get_metrics().dump_jsonl(path)
            raise
        except Exception:
            # do not let a transient flush error kill the loop
            continue
