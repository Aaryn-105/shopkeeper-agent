import type { CSSProperties } from "react";

// Compact horizontal bar chart. Used to show per-node request counts.
export interface BarChartProps {
  data: Array<{ label: string; value: number; tone?: "good" | "warn" | "bad" | "default" }>;
  width?: number;
  height?: number;
  style?: CSSProperties;
}

const COLOR: Record<NonNullable<BarChartProps["data"][number]["tone"]>, string> = {
  good: "#10b981",
  warn: "#f59e0b",
  bad: "#f43f5e",
  default: "#0f172a",
};

export function BarChart(props: BarChartProps) {
  const { data, height = 24, style } = props;
  const labelWidth = 130;
  const barAreaWidth = 220;
  const rowHeight = height;
  const fullWidth = labelWidth + barAreaWidth + 60;

  if (!data.length) {
    return (
      <p className="text-xs text-slate-400" style={style}>
        暂无数据
      </p>
    );
  }

  const max = Math.max(...data.map((d) => d.value), 1);

  return (
    <svg
      width="100%"
      height={data.length * rowHeight}
      viewBox={`0 0 ${fullWidth} ${data.length * rowHeight}`}
      role="img"
      aria-label="bar chart"
      style={style}
    >
      {data.map((d, i) => {
        const y = i * rowHeight;
        const w = (d.value / max) * barAreaWidth;
        const color = COLOR[d.tone ?? "default"];
        return (
          <g key={i}>
            <text
              x={0}
              y={y + rowHeight / 2 + 4}
              fontSize={11}
              fill="#475569"
              fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
            >
              {d.label}
            </text>
            <rect
              x={labelWidth}
              y={y + 4}
              width={w}
              height={rowHeight - 8}
              rx={2}
              fill={color}
              opacity={0.85}
            />
            <text
              x={labelWidth + w + 6}
              y={y + rowHeight / 2 + 4}
              fontSize={11}
              fill="#0f172a"
            >
              {d.value.toLocaleString("zh-CN")}
            </text>
          </g>
        );
      })}
    </svg>
  );
}