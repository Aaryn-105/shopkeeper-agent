import type { ReactNode } from "react";

// Big-number card for the /stats dashboard.
// Highlights an integer or percentage with a label and optional sub-line.
export function StatCard(props: {
  label: string;
  value: ReactNode;
  suffix?: string;
  sub?: ReactNode;
  tone?: "default" | "good" | "warn" | "bad";
}) {
  const tone = props.tone ?? "default";
  const toneCls = {
    default: "text-slate-900",
    good: "text-emerald-600",
    warn: "text-amber-600",
    bad: "text-rose-600",
  }[tone];
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <p className="text-sm text-slate-500">{props.label}</p>
      <p className={"mt-2 text-3xl font-semibold tabular-nums " + toneCls}>
        {props.value}
        {props.suffix && (
          <span className="ml-1 text-base font-normal text-slate-500">
            {props.suffix}
          </span>
        )}
      </p>
      {props.sub && (
        <p className="mt-1 text-xs text-slate-500">{props.sub}</p>
      )}
    </div>
  );
}