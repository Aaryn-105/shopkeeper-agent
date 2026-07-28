import { useEffect, useMemo, useState } from "react";
import { Card } from "../components/Card";
import { StatCard } from "../components/StatCard";
import { Sparkline } from "../components/charts/Sparkline";
import { BarChart } from "../components/charts/BarChart";
import { Gauge } from "../components/charts/Gauge";
import { api, ApiError } from "../lib/api";
import type { StatsSnapshot, TimeseriesResponse } from "../lib/types";

// /stats page — phase 8 adds SVG charts (Sparkline / Bar / Gauge)
// backed by /api/stats/timeseries.
export function StatsPage() {
  const [snapshot, setSnapshot] = useState<StatsSnapshot | null>(null);
  const [series, setSeries] = useState<TimeseriesResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [auto, setAuto] = useState(true);

  async function load() {
    try {
      const [snap, ts] = await Promise.all([
        api.stats(),
        api.statsTimeseries(600),
      ]);
      setSnapshot(snap);
      setSeries(ts);
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

  // Derived time-series arrays for the charts
  const tokenSeries = useMemo(
    () => (series?.points ?? []).map((p) => p.tokens_total),
    [series],
  );
  const llmSeries = useMemo(
    () => (series?.points ?? []).map((p) => p.llm_calls),
    [series],
  );
  const reqSeries = useMemo(
    () => (series?.points ?? []).map((p) => p.requests_total),
    [series],
  );
  const hitSeries = useMemo(() => {
    return (series?.points ?? []).map((p) =>
      p.cache_hits + p.cache_misses === 0
        ? 0
        : p.cache_hits / (p.cache_hits + p.cache_misses),
    );
  }, [series]);

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
  if (!snapshot || !series) return null;

  const cacheRate = snapshot.cache.hit_rate;
  const cacheTone =
    snapshot.cache.total === 0
      ? "default"
      : cacheRate >= 0.8
      ? "good"
      : cacheRate >= 0.5
      ? "warn"
      : "bad";

  // Per-node request counts (approx via per-node P95 table — count nodes
  // with latency data as proxy for activity)
  const nodeBarData = Object.entries(snapshot.node_p95_latency_ms)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 8)
    .map(([label, value]): { label: string; value: number; tone: "good" | "warn" | "bad" } => ({
      label,
      value: Math.round(value),
      tone: value >= 1000 ? "bad" : value >= 500 ? "warn" : "good",
    }));

  return (
    <div className="space-y-5">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">运营指标</h1>
          <p className="text-sm text-slate-500">
            数据来源：<code className="font-mono">/api/stats</code> +{" "}
            <code className="font-mono">/api/stats/timeseries</code> · 进程已运行{" "}
            {formatDuration(snapshot.uptime_seconds)} · 时序点 {series.count}
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

      {/* Row 1: the three KPI cards with chart affordances */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <Card title="Token 消耗">
          <p className="text-3xl font-semibold tabular-nums text-slate-900">
            {snapshot.tokens.total.toLocaleString("zh-CN")}
          </p>
          <p className="mt-0.5 text-xs text-slate-500">
            提示词 {snapshot.tokens.prompt.toLocaleString("zh-CN")} · 生成{" "}
            {snapshot.tokens.completion.toLocaleString("zh-CN")}
          </p>
          <div className="mt-2">
            <Sparkline
              values={tokenSeries}
              stroke="#0ea5e9"
              fill="rgba(14,165,233,0.12)"
              ariaLabel="token usage trend"
            />
          </div>
        </Card>

        <Card title="大模型调用次数">
          <p className="text-3xl font-semibold tabular-nums text-slate-900">
            {snapshot.llm_calls.total.toLocaleString("zh-CN")}
          </p>
          <p className="mt-0.5 text-xs text-slate-500">
            平均耗时 {snapshot.llm_calls.avg_latency_ms.toFixed(1)} ms
          </p>
          <div className="mt-2">
            <Sparkline
              values={llmSeries}
              stroke="#8b5cf6"
              fill="rgba(139,92,246,0.12)"
              ariaLabel="LLM call count trend"
            />
          </div>
        </Card>

        <Card title="缓存命中率">
          <div className="flex items-end gap-4">
            <Gauge
              value={cacheRate}
              size={110}
              tone={cacheTone as "good" | "warn" | "bad" | "default"}
              label={`${snapshot.cache.hits}/${snapshot.cache.total}`}
            />
            <div className="pb-2 text-xs text-slate-500">
              <p>命中 {snapshot.cache.hits}</p>
              <p>未命中 {snapshot.cache.misses}</p>
              <p>共 {snapshot.cache.total}</p>
            </div>
          </div>
        </Card>
      </div>

      {/* Row 2: requests + sql */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Card title="请求总数">
          <p className="text-2xl font-semibold tabular-nums">
            {snapshot.requests.total.toLocaleString("zh-CN")}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            成功 {snapshot.requests.success} · 失败 {snapshot.requests.error}
          </p>
          <div className="mt-2">
            <Sparkline
              values={reqSeries}
              stroke="#10b981"
              fill="rgba(16,185,129,0.12)"
              height={28}
              ariaLabel="requests trend"
            />
          </div>
        </Card>
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

      {/* Row 3: per-node P95 latency (table) + bar chart */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="节点 P95 耗时" subtitle="单位 ms · SRS 5.1">
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-slate-50 text-slate-600">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">节点</th>
                  <th className="px-3 py-2 text-right font-medium">P95</th>
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
                      </tr>
                    );
                  })}
                {Object.keys(snapshot.node_p95_latency_ms).length === 0 && (
                  <tr>
                    <td
                      colSpan={2}
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

        <Card
          title="节点 P95 耗时 Top 8"
          subtitle="横向柱状图 · 颜色按耗时分档"
        >
          <BarChart data={nodeBarData} />
        </Card>
      </div>

      {/* Row 4: hit-rate sparkline */}
      {hitSeries.length > 1 && (
        <Card title="缓存命中率走势" subtitle="最近 {series.count} 个时序点">
          <Sparkline
            values={hitSeries.map((v) => v * 100)}
            stroke="#0ea5e9"
            fill="rgba(14,165,233,0.12)"
            width={600}
            height={48}
            strokeWidth={2}
            showDots
            ariaLabel="cache hit rate trend (percent)"
          />
        </Card>
      )}
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