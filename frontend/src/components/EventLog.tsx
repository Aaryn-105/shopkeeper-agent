import type { AskEvent } from "../lib/types";

// Compact timeline of SSE events for the / page.
export function EventLog(props: { events: AskEvent[] }) {
  if (!props.events.length) {
    return (
      <p className="text-sm text-slate-400">
        事件流会在你提交问题后开始呈现。
      </p>
    );
  }
  return (
    <ol className="space-y-1.5 text-sm">
      {props.events.map((e, i) => (
        <li
          key={i}
          className={
            "flex items-start gap-2 rounded-md border border-slate-100 bg-slate-50/50 px-2.5 py-1.5 " +
            toneFor(e.event)
          }
        >
          <span className="select-none rounded bg-white px-1.5 py-0.5 font-mono text-xs text-slate-500">
            {e.event}
          </span>
          <span className="flex-1 break-all text-slate-700">
            {summarize(e)}
          </span>
        </li>
      ))}
    </ol>
  );
}

function toneFor(event: string): string {
  switch (event) {
    case "error":
      return "border-rose-200 bg-rose-50";
    case "result":
      return "border-emerald-200 bg-emerald-50";
    case "done":
      return "border-sky-200 bg-sky-50";
    case "sql_generated":
    case "sql_corrected":
      return "border-indigo-200 bg-indigo-50";
    default:
      return "";
  }
}

function summarize(e: AskEvent): string {
  const data = e.data;
  if (!data || typeof data !== "object") return JSON.stringify(data);
  const d = data as Record<string, unknown>;
  if (e.event === "progress") {
    return `${d.node ?? ""} ${d.status ?? ""} ${d.message ?? ""}`.trim();
  }
  if (e.event === "sql_generated") {
    return `${d.cache_hit ? "[命中] " : ""}${String(d.sql ?? "").slice(0, 120)}`;
  }
  if (e.event === "sql_corrected") {
    return `原 SQL: ${String(d.original_sql ?? "").slice(0, 80)} → 改写后: ${String(d.corrected_sql ?? "").slice(0, 80)}`;
  }
  if (e.event === "result") {
    const rc = d.row_count ?? 0;
    const ch = d.cache_hit ? "命中" : "未命中";
    const err = d.error ? ` err=${d.error}` : "";
    return `${ch} · ${rc} 行${err}`;
  }
  if (e.event === "error") {
    return String(d.message ?? JSON.stringify(data));
  }
  if (e.event === "done") {
    return `${d.duration_ms ?? 0}ms · cache_hit=${!!d.cache_hit}`;
  }
  return JSON.stringify(data);
}