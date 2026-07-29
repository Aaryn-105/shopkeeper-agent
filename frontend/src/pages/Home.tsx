import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Card } from "../components/Card";
import { EventLog } from "../components/EventLog";
import { ResultTable } from "../components/ResultTable";
import { api, ApiError } from "../lib/api";
import { startAskStream } from "../lib/askStream";
import type { AskEvent, ConfigPayload } from "../lib/types";

export function HomePage() {
  const [config, setConfig] = useState<ConfigPayload | null>(null);
  const [configErr, setConfigErr] = useState<string | null>(null);
  const [searchParams] = useSearchParams();
  const [query, setQuery] = useState(() => searchParams.get("q") ?? "");
  const [running, setRunning] = useState(false);
  const [events, setEvents] = useState<AskEvent[]>([]);
  const [latestResult, setLatestResult] = useState<
    AskEvent["data"] | null
  >(null);
  const [latestSql, setLatestSql] = useState<string | null>(null);
  const [latestExplanation, setLatestExplanation] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const aborterRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    api
      .config()
      .then(setConfig)
      .catch((e: unknown) =>
        setConfigErr(e instanceof Error ? e.message : String(e)),
      );
  }, []);

  function submit(e?: React.FormEvent) {
    e?.preventDefault();
    const q = query.trim();
    if (!q || running) return;
    setRunning(true);
    setEvents([]);
    setLatestResult(null);
    setLatestSql(null);
    setLatestExplanation(null);
    setErrorMsg(null);
    const handle = startAskStream(
      { query: q, session_id: null },
      {
        onEvent: (ev) => {
          setEvents((prev) => [...prev, ev]);
          if (ev.event === "sql_generated") {
            const d = ev.data as { sql?: string };
            if (d?.sql) setLatestSql(d.sql);
          }
          if (ev.event === "sql_corrected") {
            const d = ev.data as { corrected_sql?: string };
            if (d?.corrected_sql) setLatestSql(d.corrected_sql);
          }
          if (ev.event === "result") {
            setLatestResult(ev.data);
          }
          if (ev.event === "done") {
            const d = ev.data as { explanation?: string };
            if (d?.explanation) setLatestExplanation(d.explanation);
          }
          if (ev.event === "error") {
            const d = ev.data as { message?: string };
            setErrorMsg(d?.message ?? "未知错误");
          }
        },
        onError: (err) => {
          setErrorMsg(err instanceof ApiError ? `${err.status} ${err.message}` : err.message);
        },
        onClose: () => setRunning(false),
      },
    );
    aborterRef.current = handle.abort;
  }

  function cancel() {
    aborterRef.current?.();
  }

  function useSample(q: string) {
    setQuery(q);
  }

  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_2fr]">
      <div className="space-y-5">
        <Card
          title={config?.app.name ?? "电商问数助手"}
          subtitle={config?.app.version}
        >
          {configErr && (
            <p className="mb-2 rounded bg-rose-50 px-2 py-1 text-sm text-rose-700">
              加载 /api/config 失败：{configErr}
            </p>
          )}
          <p className="text-sm text-slate-700">
            {config?.ui.welcome_message ?? "加载中..."}
          </p>
          {config?.ui.usage_tips && (
            <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-slate-600">
              {config.ui.usage_tips.map((t, i) => (
                <li key={i}>{t}</li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="提问">
          <form onSubmit={submit} className="space-y-3">
            <textarea
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none"
              rows={3}
              placeholder="例如：上个月华东地区的 GMV 是多少？"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              disabled={running}
              maxLength={500}
            />
            <div className="flex items-center justify-between">
              <p className="text-xs text-slate-400">{query.length} / 500</p>
              <div className="flex gap-2">
                {running && (
                  <button
                    type="button"
                    onClick={cancel}
                    className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
                  >
                    取消
                  </button>
                )}
                <button
                  type="submit"
                  disabled={running || !query.trim()}
                  className="rounded-md bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
                >
                  {running ? "查询中..." : "提交"}
                </button>
              </div>
            </div>
          </form>
          {config?.samples && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {config.samples.slice(0, 3).map((s) => (
                <button
                  key={s.id}
                  type="button"
                  className="rounded border border-slate-200 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
                  onClick={() => useSample(s.question)}
                >
                  {s.category}
                </button>
              ))}
            </div>
          )}
        </Card>

        {errorMsg && (
          <Card title="错误">
            <p className="text-sm text-rose-700">{errorMsg}</p>
          </Card>
        )}
      </div>

      <div className="space-y-5">
        <Card title="生成 SQL">
          <pre className="max-h-48 overflow-auto rounded bg-slate-900 px-3 py-2 font-mono text-xs text-slate-100">
            {latestSql ?? "// 等待生成 SQL"}
          </pre>
        </Card>

        <Card
          title="结果"
          subtitle={latestResult && typeof latestResult === "object"
            ? `${(latestResult as { row_count?: number }).row_count ?? 0} 行${
                (latestResult as { cache_hit?: boolean }).cache_hit ? " · 缓存命中" : ""
              }`
            : ""}
        >
          <ResultTable
            result={
              latestResult && typeof latestResult === "object"
                ? (latestResult as Parameters<typeof ResultTable>[0]["result"])
                : null
            }
          />
          {latestExplanation && (
            <p className="mt-3 rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-700">
              💬 {latestExplanation}
            </p>
          )}
        </Card>

        <Card title="事件流">
          <EventLog events={events} />
        </Card>
      </div>
    </div>
  );
}