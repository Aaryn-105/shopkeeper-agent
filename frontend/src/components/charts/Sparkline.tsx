import type { CSSProperties } from "react";

// Minimal SVG line chart. Designed for time-series trend indicators.
// Phase 8 — used by the /stats dashboard (token usage / LLM calls / etc).
export interface SparklineProps {
  values: number[];
  width?: number;
  height?: number;
  stroke?: string;
  fill?: string;
  strokeWidth?: number;
  showDots?: boolean;
  style?: CSSProperties;
  ariaLabel?: string;
}

export function Sparkline(props: SparklineProps) {
  const {
    values,
    width = 160,
    height = 36,
    stroke = "#10b981",
    fill = "rgba(16,185,129,0.12)",
    strokeWidth = 1.5,
    showDots = false,
    style,
    ariaLabel,
  } = props;

  if (!values.length) {
    return (
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={ariaLabel ?? "no data"}
        style={style}
      >
        <text
          x={width / 2}
          y={height / 2}
          textAnchor="middle"
          dominantBaseline="middle"
          fontSize={10}
          fill="#94a3b8"
        >
          暂无数据
        </text>
      </svg>
    );
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const stepX = width / Math.max(1, values.length - 1);

  const points = values.map((v, i) => ({
    x: i * stepX,
    y: height - ((v - min) / range) * (height - 4) - 2,
  }));

  const linePath = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(2)} ${p.y.toFixed(2)}`)
    .join(" ");

  const fillPath =
    `M ${points[0].x.toFixed(2)} ${height} ` +
    points.map((p) => `L ${p.x.toFixed(2)} ${p.y.toFixed(2)}`).join(" ") +
    ` L ${points[points.length - 1].x.toFixed(2)} ${height} Z`;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={ariaLabel ?? "sparkline"}
      style={style}
    >
      <path d={fillPath} fill={fill} stroke="none" />
      <path
        d={linePath}
        fill="none"
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {showDots &&
        points.map((p, i) => (
          <circle key={i} cx={p.x} cy={p.y} r={1.5} fill={stroke} />
        ))}
    </svg>
  );
}