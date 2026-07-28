import { useEffect, useState } from "react";
import { Card } from "../components/Card";
import { StatCard } from "../components/StatCard";
import { api, ApiError } from "../lib/api";
import type { StatsSnapshot } from "../lib/types";

// /stats page - the dashboard explicitly required by the user:
//   "需要增加一个页面，该页面负责显示 token 消耗、大模型调用次数、缓存命中率"
export function StatsPage() {
  const [snapshot, setSnapshot] = useState<StatsSnapshot | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [auto, setAuto] = useState(true);

  async function load() {
    try {
      const s = await api.stats();
      setSnapshot(s);
      setErr(null);
    } catch (e) {
      setErr(e instanceof ApiError ? `${e.status} ${e.message}` : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    if (!auto) return;
    const t = window.setInterval(load, 5000);
    return () => window.clearInterval(t);
  }, [auto]);

  if (loading) {
    return <p className="text-sm text-slate-400">加载 /api/stats ...</p>;
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
  if (!snapshot) return null;

  const cacheRate = snapshot.cache.hit_rate;
  const cacheTone =
    snapshot.cache.total === 0
      ? "default"
      : cacheRate >= 0.8
      ? "good"
      : cacheRate >= 0.5
      ? "warn"
      : "bad";

  return (
    <div className="space-y-5">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">运营指标</h1>
          <p className="text-sm text-slate-500">
            数据来源：后端 <code className="font-mono">/api/stats</code> ·
            进程已运行 {formatDuration(snapshot.uptime_seconds)}
          </p>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <label className="flex items-center gap-1.5 text-slate-600">
            <input
              type="checkbox"
              checked={auto}
              onChange={(e) => setAuto(e.target.checked)}
            />
            自动刷新 (5s)
          </label>
          <button
            onClick={load}
            className="rounded-md border border-slate-300 px-3 py-1 hover:bg-slate-50"
          >
            立即刷新
          </button>
        </div>
      </header>

      {/* Row 1: the three KPI cards explicitly required by the user */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <StatCard
          label="Token 消耗"
          value={snapshot.tokens.total.toLocaleString("zh-CN")}
          sub={
            <span>
              提示词 <b className="text-slate-700">{snapshot.tokens.prompt.toLocaleString("zh-CN")}</b>
              {" · "}
              生成 <b className="text-slate-700">{snapshot.tokens.completion.toLocaleString("zh-CN")}</b>
            </span>
          }
        />
        <StatCard
          label="大模型调用次数"
          value={snapshot.llm_calls.total.toLocaleString("zh-CN")}
          sub={
            <span>
              平均耗时{" "}
              <b className="text-slate-700">
                {snapshot.llm_calls.avg_latency_ms.toFixed(1)} ms
              </b>
            </span>
          }
        />
        <StatCard
          label="缓存命中率"
          value={(cacheRate * 100).toFixed(1)}
          suffix="%"
          tone={cacheTone as "default" | "good" | "warn" | "bad"}
          sub={
            <span>
              命中 {snapshot.cache.hits} · 未命中 {snapshot.cache.misses} ·
              共 {snapshot.cache.total}
            </span>
          }
        />
      </div>

      {/* Row 2: requests + sql */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard
          label="请求总数"
          value={snapshot.requests.total.toLocaleString("zh-CN")}
          sub={
            <span>
              成功 {snapshot.requests.success} · 失败 {snapshot.requests.error}
            </span>
          }
        />
        <StatCard
          label="请求成功率"
          value={(snapshot.requests.success_rate * 100).toFixed(1)}
          suffix="%"
          tone={
            snapshot.requests.total === 0
              ? "default"
              : snapshot.requests.success_rate >= 0.95
              ? "good"
              : snapshot.requests.success_rate >= 0.8
              ? "warn"
              : "bad"
          }
        />
        <StatCard
          label="平均请求耗时"
          value={snapshot.requests.avg_duration_ms.toFixed(0)}
          suffix="ms"
        />
        <StatCard
          label="P95 请求耗时"
          value={snapshot.requests.p95_duration_ms.toFixed(0)}
          suffix="ms"
        />
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard
          label="SQL 生成"
          value={snapshot.sql.generated.toLocaleString("zh-CN")}
        />
        <StatCard
          label="SQL 一次通过率"
          value={(snapshot.sql.first_pass_rate * 100).toFixed(1)}
          suffix="%"
        />
        <StatCard
          label="SQL 改写率"
          value={(snapshot.sql.correction_rate * 100).toFixed(1)}
          suffix="%"
        />
        <StatCard
          label="SQL 执行成功率"
          value={(snapshot.sql.execution_success_rate * 100).toFixed(1)}
          suffix="%"
          tone={
            snapshot.sql.executed_total === 0
              ? "default"
              : snapshot.sql.execution_success_rate >= 0.9
              ? "good"
              : snapshot.sql.execution_success_rate >= 0.7
              ? "warn"
              : "bad"
          }
          sub={
            <span>
              成功 {snapshot.sql.executed_ok} · 失败 {snapshot.sql.executed_failed}
            </span>
          }
        />
      </div>

      {/* Row 3: per-node P95 latency table (SRS 5.1) */}
      <Card
        title="节点 P95 耗时"
        subtitle="单位 ms；用于对照 SRS 5.1 的性能基线"
      >
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50 text-slate-600">
              <tr>
                <th className="px-3 py-2 text-left font-medium">节点</th>
                <th className="px-3 py-2 text-right font-medium">P95 (ms)</th>
                <th className="px-3 py-2 text-left font-medium">趋势</th>
              </tr>
            </thead>
            <tbody>
              {Object.keys(snapshot.node_p95_latency_ms)
                .sort()
                .map((node) => {
                  const v = snapshot.node_p95_latency_ms[node];
                  return (
                    <tr key={node} className="border-t border-slate-100">
                      <td className="px-3 py-1.5 font-mono text-xs text-slate-700">
                        {node}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        {v.toFixed(1)}
                      </td>
                      <td className="px-3 py-1.5">
                        <NodeBar value={v} />
                      </td>
                    </tr>
                  );
                })}
              {Object.keys(snapshot.node_p95_latency_ms).length === 0 && (
                <tr>
                  <td
                    colSpan={3}
                    className="px-3 py-2 text-center text-slate-400"
                  >
                    暂无数据
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

function NodeBar({ value }: { value: number }) {
  // simple relative bar (capped at 2000ms for visual scale)
  const pct = Math.min(100, Math.round((value / 2000) * 100));
  const color =
    value >= 1000 ? "bg-rose-400" : value >= 500 ? "bg-amber-400" : "bg-emerald-400";
  return (
    <div className="h-1.5 w-40 overflow-hidden rounded bg-slate-100">
      <div
        className={"h-full " + color}
        style={{ width: pct + "%" }}
        aria-hidden="true"
      />
    </div>
  );
}

function formatDuration(seconds: number): string {
  if (!seconds || seconds < 0) return "0s";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}