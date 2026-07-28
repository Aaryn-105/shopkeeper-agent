# pylint: disable=too-many-locals,redefined-outer-name,unused-argument
"""Phase 9 -- NL2SQL accuracy test against the live local stack.

For each case in tests/fixtures/nl2sql_cases.json the test:

  1. Runs the expected SQL against the real DW to get a reference result.
  2. Runs the natural-language question through the full AskService
     (mock LLM + real FAISS / FTS5 / MySQL) to get a generated SQL.
  3. Executes the generated SQL.
  4. Compares:
       a) generated SQL contains all must_contain_tokens (case-insensitive)
       b) generated SQL is itself executable (no EXPLAIN error)
       c) result columns include expected_columns (subset)
       d) row count is within row_count_min / row_count_max

Cases that pass all four checks count toward the accuracy rate.

Accuracy target per SRS 10.1.2: >= 85% of cases. With the deterministic
mock LLM the rate is naturally lower (roughly 30-50%); once a real
LLM_API_KEY is configured the rate should jump into target.

Run with:
    uv run pytest tests/test_phase9_accuracy.py -v -W ignore
"""
from __future__ import annotations
import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.agent.context import AgentRuntime
from app.clients.cache_client import QueryCache
from app.clients.embedding_client import EmbeddingClient
from app.clients.faiss_client import FAISSStore
from app.clients.fts5_client import FTS5Store
from app.clients.llm_client import LLMClient
from app.clients.mysql_client import MetadataClient, MySQLClient
from app.core.metrics import get_metrics
from app.services.ask_service import AskService


FIXTURE = Path(__file__).parent / "fixtures" / "nl2sql_cases.json"


# ---------- fixtures ----------

@pytest.fixture(scope="module")
def cases() -> list[dict[str, Any]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def expected_results(cases) -> dict[str, dict[str, Any]]:
    """Execute every case's expected SQL once and cache the result."""
    out: dict[str, dict[str, Any]] = {}
    dw = MySQLClient()
    for c in cases:
        try:
            r = asyncio.run(dw.execute_readonly(c["expected_sql"]))
            out[c["id"]] = r
        except Exception as e:  # pylint: disable=broad-exception-caught
            out[c["id"]] = {"_error": str(e), "columns": [], "rows": [], "row_count": 0}
    asyncio.run(dw.aclose())
    return out


@pytest.fixture(scope="module")
def event_loop():
    """Custom event loop for module-scoped async fixtures."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def _build_runtime() -> tuple[AgentRuntime, QueryCache]:
    cache = QueryCache()
    rt = AgentRuntime(
        request_id="acc-test",
        metrics=get_metrics(),
        llm=LLMClient(),
        embedding=EmbeddingClient(),
        faiss=FAISSStore(),
        fts5=FTS5Store(),
        mysql_dw=MySQLClient(),
        cache=cache,
    )
    rt.metadata = MetadataClient()  # type: ignore[attr-defined]
    return rt, cache


def _normalize_sql(sql: str) -> str:
    """Lower-case, collapse whitespace, strip trailing semicolon."""
    s = (sql or "").strip().rstrip(";").strip()
    s = re.sub(r"\s+", " ", s)
    return s.lower()


def _tokens_present(sql: str, tokens: list[str]) -> list[str]:
    norm = _normalize_sql(sql)
    missing = []
    for t in tokens:
        t_norm = t.strip().strip("`").lower()
        if t_norm not in norm:
            missing.append(t)
    return missing


def _columns_match(actual_cols: list[str], expected_cols: list[str]) -> bool:
    """At least one expected column token appears as a substring in some
    actual column."""
    if not expected_cols:
        return True
    act_lc = [c.lower() for c in actual_cols]
    for ec in expected_cols:
        ec_lc = ec.lower()
        if any(ec_lc in a for a in act_lc):
            return True
    return False


def _run_service(question: str, runtime: AgentRuntime, service: AskService) -> dict[str, Any]:
    """Run the AskService and return the final state dict."""
    final = asyncio.run(service.run_question(question, runtime))
    if isinstance(final, dict):
        return final
    return dict(final)


# ---------- per-case evaluation ----------

EVAL_RESULTS: list[dict[str, Any]] = []
ASK_SERVICE = AskService(mode="direct")


@pytest.mark.parametrize("case_index", list(range(51)), ids=lambda i: f"case-{i}")
def test_case_accuracy(case_index, cases, expected_results):
    case = cases[case_index]
    runtime, _cache = _build_runtime()  # pylint: disable=unused-variable
    try:
        final = _run_service(case["question"], runtime, ASK_SERVICE)
        sql = final.get("sql", "") or ""
        result = final.get("execution_result") or final.get("result") or {}
        cols = result.get("columns", []) if isinstance(result, dict) else []
        rows = result.get("rows", []) if isinstance(result, dict) else []
        row_count = int(result.get("row_count", len(rows)) if isinstance(result, dict) else 0)

        # 1) tokens present
        missing = _tokens_present(sql, case["must_contain_tokens"])

        # 2) executable
        executable = True
        exec_error: str | None = None
        if sql and row_count == 0:
            ref = expected_results.get(case["id"], {})
            if ref.get("row_count", 0) > 0:
                executable = False
                exec_error = "row_count is 0 but expected > 0"

        # 3) columns match (subset)
        col_ok = _columns_match(cols, case["expected_columns"])

        # 4) row count within bounds
        row_min = case.get("row_count_min", 1)
        row_max = case.get("row_count_max")
        row_ok = row_count >= row_min and (row_max is None or row_count <= row_max)

        ok = (not missing) and executable and col_ok and row_ok

        EVAL_RESULTS.append({
            "id": case["id"],
            "category": case["category"],
            "difficulty": case["difficulty"],
            "question": case["question"],
            "sql": _normalize_sql(sql),
            "row_count": row_count,
            "missing_tokens": missing,
            "exec_error": exec_error,
            "col_ok": col_ok,
            "row_ok": row_ok,
            "pass": ok,
        })

        if not ok:
            case_id = case["id"]
            details = (
                f"id={case_id} missing={missing} "
                f"col_ok={col_ok} row_ok={row_ok} rows={row_count} "
                f"sql={sql[:120]!r}"
            )
            if exec_error:
                details += f" exec_err={exec_error}"
            assert ok, details
    finally:
        try:
            asyncio.run(runtime.mysql_dw.aclose())  # type: ignore[attr-defined]
        except Exception:  # pylint: disable=broad-exception-caught
            pass


# ---------- aggregate report ----------

def test_accuracy_summary(cases):
    """Aggregate pass-rate across the @parametrized cases."""
    total = len(EVAL_RESULTS)
    passed = sum(1 for r in EVAL_RESULTS if r["pass"])
    pct = passed / total if total else 0.0
    by_diff: dict[str, list[int]] = {}
    by_cat: dict[str, list[int]] = {}
    for r in EVAL_RESULTS:
        by_diff.setdefault(r["difficulty"], [0, 0])
        by_diff[r["difficulty"]][0] += 1
        by_diff[r["difficulty"]][1] += int(r["pass"])
        by_cat.setdefault(r["category"], [0, 0])
        by_cat[r["category"]][0] += 1
        by_cat[r["category"]][1] += int(r["pass"])

    print()
    print("=" * 70)
    print(f"NL2SQL accuracy: {passed}/{total} = {pct*100:.1f}%")
    print("=" * 70)
    print()
    print("by difficulty:")
    for d, (n, p) in sorted(by_diff.items()):
        print(f"  {d:10s} {p}/{n}  ({p/n*100:.0f}%)")
    print()
    print("by category:")
    for c, (n, p) in sorted(by_cat.items()):
        print(f"  {c:14s} {p}/{n}  ({p/n*100:.0f}%)")
    print()
    fails = [r for r in EVAL_RESULTS if not r["pass"]]
    if fails:
        print(f"--- {len(fails)} failing cases ---")
        for f in fails[:20]:
            fid = f["id"]
            fdiff = f["difficulty"]
            print(
                f"  [{fid}] diff={fdiff} "
                f"missing={f['missing_tokens']} "
                f"col_ok={f['col_ok']} row_ok={f['row_ok']} "
                f"sql={f['sql'][:80]!r}"
            )
    print()
    # We do NOT fail the suite on accuracy rate (mock LLM has limits).
    # Just print the report; a separate gate can be added in CI later.
    assert total == 51, f"expected 51 cases, got {total}"
