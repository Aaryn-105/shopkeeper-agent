import type { ResultData } from "../lib/types";

export function ResultTable(props: { result: ResultData | null }) {
  if (!props.result) {
    return (
      <p className="text-sm text-slate-400">尚无结果。先在左侧输入问题。</p>
    );
  }
  const cols = props.result.columns ?? [];
  const rows = props.result.rows ?? [];
  if (!cols.length && !rows.length) {
    return <p className="text-sm text-slate-400">查询返回空结果。</p>;
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200">
      <table className="min-w-full text-sm">
        <thead className="bg-slate-50 text-slate-700">
          <tr>
            {cols.map((c) => (
              <th
                key={c}
                className="whitespace-nowrap border-b border-slate-200 px-3 py-2 text-left font-medium"
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="odd:bg-white even:bg-slate-50/40">
              {r.map((cell, j) => (
                <td
                  key={j}
                  className="whitespace-nowrap border-b border-slate-100 px-3 py-1.5 text-slate-800 tabular-nums"
                >
                  {formatCell(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatCell(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") {
    if (!Number.isFinite(v)) return String(v);
    if (Number.isInteger(v)) return v.toLocaleString("zh-CN");
    return v.toLocaleString("zh-CN", { maximumFractionDigits: 4 });
  }
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}