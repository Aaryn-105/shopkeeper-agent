"""In-process metrics collector with periodic JSONL flush.

Tracks per-node P95 latency, LLM call counts, token usage, cache hits/misses,
and per-request totals. Thread-safe; designed to live on app.state.

v1.0 (phase 2): flat-key summary covering node p95, llm calls/tokens, cache,
uptime and request totals.

v1.1 (phase 6.3): adds request-level success/error counters and per-request
duration, plus SQL pipeline counters (generated / first-pass-validated /
corrected / executed-ok / executed-failed) for the /api/stats endpoint and
for OPS-009 / OPS-010 observability. summary() output keeps its flat shape
for backwards compatibility with health.py and phase 2 tests; new fields are
accessible via the new Metrics attributes directly or via /api/stats.
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

        # phase 6.3 additions (counters for OPS-009 / OPS-010 + /api/stats)
        self._requests_success: int = 0
        self._requests_error: int = 0
        self._request_durations_ms: list[float] = []
        self._sql_generated: int = 0
        self._sql_validated_first_pass: int = 0  # passed validation without correction
        self._sql_corrected: int = 0              # needed correction
        self._sql_executed_ok: int = 0
        self._sql_executed_failed: int = 0

    # ---------- existing v1.0 methods (unchanged) ----------

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

    # ---------- phase 6.3 additions ----------

    def record_request_outcome(self, success: bool, duration_ms: float = 0.0) -> None:
        """Track per-request success/error and latency for OPS-009."""
        with self._lock:
            if success:
                self._requests_success += 1
            else:
                self._requests_error += 1
            if duration_ms > 0:
                self._request_durations_ms.append(float(duration_ms))

    def record_sql_generated(self) -> None:
        """Called once per generate_sql invocation (including retries)."""
        with self._lock:
            self._sql_generated += 1

    def record_sql_validated(self, corrected: bool) -> None:
        """Called by validate_sql. corrected=True means validation failed and
        we routed to correct_sql; corrected=False means first-pass pass.
        """
        with self._lock:
            if corrected:
                self._sql_corrected += 1
            else:
                self._sql_validated_first_pass += 1

    def record_sql_executed(self, success: bool) -> None:
        """Called by run_sql. success=True means query returned without error."""
        with self._lock:
            if success:
                self._sql_executed_ok += 1
            else:
                self._sql_executed_failed += 1

    # ---------- read accessors used by /api/stats ----------

    def stats_snapshot(self) -> dict[str, Any]:
        """Return a /api/stats-shaped snapshot built from current counters."""
        with self._lock:
            cache_total = self._cache_hits + self._cache_misses
            call_count = len(self._llm_calls)
            totals = {
                "prompt": sum(s.prompt_tokens for s in self._llm_calls),
                "completion": sum(s.completion_tokens for s in self._llm_calls),
                "all": sum(s.total_tokens for s in self._llm_calls),
            }
            avg_llm_latency = (
                sum(s.latency_ms for s in self._llm_calls) / call_count
                if call_count else 0.0
            )

            req_total = self._requests_total
            req_success = self._requests_success
            req_error = self._requests_error
            durations = list(self._request_durations_ms)
            avg_dur = sum(durations) / len(durations) if durations else 0.0
            p95_dur = self._percentile(durations, 0.95)

            sql_total = self._sql_generated
            sql_pass = self._sql_validated_first_pass
            sql_corrected = self._sql_corrected
            sql_exec_ok = self._sql_executed_ok
            sql_exec_fail = self._sql_executed_failed
            sql_exec_total = sql_exec_ok + sql_exec_fail

            return {
                "uptime_seconds": round(time.time() - self._started_at, 2),
                "tokens": {
                    "prompt": totals["prompt"],
                    "completion": totals["completion"],
                    "total": totals["all"],
                },
                "llm_calls": {
                    "total": call_count,
                    "avg_latency_ms": round(avg_llm_latency, 2),
                },
                "cache": {
                    "hits": self._cache_hits,
                    "misses": self._cache_misses,
                    "total": cache_total,
                    "hit_rate": round(self._cache_hits / cache_total, 4) if cache_total else 0.0,
                },
                "requests": {
                    "total": req_total,
                    "success": req_success,
                    "error": req_error,
                    "success_rate": round(req_success / req_total, 4) if req_total else 0.0,
                    "avg_duration_ms": round(avg_dur, 2),
                    "p95_duration_ms": p95_dur,
                },
                "sql": {
                    "generated": sql_total,
                    "validated_first_pass": sql_pass,
                    "corrected": sql_corrected,
                    "executed_ok": sql_exec_ok,
                    "executed_failed": sql_exec_fail,
                    "executed_total": sql_exec_total,
                    "first_pass_rate": round(sql_pass / sql_total, 4) if sql_total else 0.0,
                    "correction_rate": round(sql_corrected / sql_total, 4) if sql_total else 0.0,
                    "execution_success_rate": (
                        round(sql_exec_ok / sql_exec_total, 4) if sql_exec_total else 0.0
                    ),
                },
                "node_p95_latency_ms": {
                    node: self._percentile(lats, 0.95)
                    for node, lats in self._node_latencies.items()
                },
            }

    # ---------- helpers ----------

    @staticmethod
    def _percentile(values: list[float], p: float) -> float:
        if not values:
            return 0.0
        s = sorted(values)
        idx = int(p * (len(s) - 1))
        return round(s[idx], 2)

    def summary(self) -> dict[str, Any]:
        """Flat-key summary (v1.0 shape). Kept for backwards compatibility with
        health.py, test_phase2.py and the JSONL flush format."""
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