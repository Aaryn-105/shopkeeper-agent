import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Card } from "../components/Card";
import { api, ApiError } from "../lib/api";
import type { HistoryItem, HistoryPage } from "../lib/types";

const STATUS_TONE: Record<string, string> = {
  success: "bg-emerald-100 text-emerald-700",
  cache_hit: "bg-sky-100 text-sky-700",
  error: "bg-rose-100 text-rose-700",
  fail: "bg-rose-100 text-rose-700",
};
const STATUS_LABEL: Record<string, string> = {
  success: "成功",
  cache_hit: "缓存命中",
  error: "失败",
  fail: "失败",
};

export function HistoryPage() {
  const [page, setPage] = useState<HistoryPage | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [limit, setLimit] = useState(20);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  async function load() {
    try {
      const p = await api.history({ limit });
      setPage(p);
      setErr(null);
    } catch (e) {
      setErr(e instanceof ApiError ? `${e.status} ${e.message}` : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [limit]);

  function toggleExpand(id: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  if (loading) {
    return <p className="text-sm text-slate-400">加载 /api/history ...</p>;
  }
  if (err) {
    return (
      <Card title="加载失败">
        <p className="text-sm text-rose-700">{err}</p>
        <button
          onClick={load}
          className="mt-2 rounded-md border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50"
        >
          重试
        </button>
      </Card>
    );
  }
  if (!page) return null;

  return (
    <div className="space-y-5">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">历史问答</h1>
          <p className="text-sm text-slate-500">
            共 {page.total} 条 · 当前页 {page.items.length} 条 · 按 created_at DESC
          </p>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <label className="flex items-center gap-1.5 text-slate-600">
            每页
            <select
              value={limit}
              onChange={(e) => setLimit(parseInt(e.target.value, 10))}
              className="rounded border border-slate-300 px-2 py-1"
            >
              <option value={10}>10</option>
              <option value={20}>20</option>
              <option value={50}>50</option>
              <option value={100}>100</option>
            </select>
          </label>
          <button
            onClick={load}
            className="rounded-md border border-slate-300 px-3 py-1 hover:bg-slate-50"
          >
            刷新
          </button>
        </div>
      </header>

      {page.items.length === 0 ? (
        <Card>
          <p className="text-sm text-slate-500">
            还没有历史记录。在 <Link to="/" className="text-slate-700 underline">提问页</Link> 提交一条就会出现在这里。
          </p>
        </Card>
      ) : (
        <Card>
          <ul className="divide-y divide-slate-100">
            {page.items.map((it: HistoryItem) => {
              const isOpen = expanded.has(it.id);
              return (
                <li key={it.id} className="py-3">
                  <div className="flex items-start gap-3">
                    <div className="flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span
                          className={
                            "rounded px-1.5 py-0.5 text-xs font-medium " +
                            (STATUS_TONE[it.status] ?? "bg-slate-100 text-slate-600")
                          }
                        >
                          {STATUS_LABEL[it.status] ?? it.status}
                        </span>
                        <span className="text-xs text-slate-400">
                          {new Date(it.created_at).toLocaleString("zh-CN")}
                        </span>
                        <span className="text-xs text-slate-400">
                          {it.duration_ms}ms
                        </span>
                        {it.row_count != null && (
                          <span className="text-xs text-slate-400">
                            {it.row_count} 行
                          </span>
                        )}
                        {it.sql_corrected && (
                          <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-700">
                            改写
                          </span>
                        )}
                      </div>
                      <p className="mt-1.5 text-sm font-medium text-slate-900">
                        {it.query}
                      </p>
                      {it.error_message && (
                        <p className="mt-1 text-xs text-rose-600">
                          {it.error_message}
                        </p>
                      )}
                      {it.sql_text && (
                        <button
                          onClick={() => toggleExpand(it.id)}
                          className="mt-1.5 text-xs text-slate-500 underline hover:text-slate-700"
                        >
                          {isOpen ? "收起 SQL" : "展开 SQL"}
                        </button>
                      )}
                      {isOpen && it.sql_text && (
                        <pre className="mt-2 max-h-40 overflow-auto rounded bg-slate-900 px-3 py-2 font-mono text-xs text-slate-100">
                          {it.sql_text}
                        </pre>
                      )}
                    </div>
                    <Link
                      to={`/?q=${encodeURIComponent(it.query)}`}
                      state={{ query: it.query }}
                      className="shrink-0 rounded-md border border-slate-300 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50"
                    >
                      再问一次
                    </Link>
                  </div>
                </li>
              );
            })}
          </ul>
        </Card>
      )}
    </div>
  );
}