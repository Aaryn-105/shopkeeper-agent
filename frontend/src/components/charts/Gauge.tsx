import type { CSSProperties } from "react";

// Half-circle gauge for percentages (0-100%).
export interface GaugeProps {
  value: number; // 0..1
  size?: number;
  thickness?: number;
  label?: string;
  style?: CSSProperties;
  tone?: "good" | "warn" | "bad" | "default";
}

const COLOR: Record<NonNullable<GaugeProps["tone"]>, string> = {
  good: "#10b981",
  warn: "#f59e0b",
  bad: "#f43f5e",
  default: "#0ea5e9",
};

export function Gauge(props: GaugeProps) {
  const {
    value,
    size = 110,
    thickness = 12,
    label,
    style,
    tone = "default",
  } = props;
  const clamped = Math.max(0, Math.min(1, value));
  const r = size / 2 - thickness;
  const cx = size / 2;
  const cy = size / 2;
  // half-circle from 180deg to 0deg (math: pi to 0)
  const arcLen = Math.PI * r;
  const offset = arcLen * (1 - clamped);
  const color = COLOR[tone];

  return (
    <svg
      width={size}
      height={size / 2 + thickness + 14}
      viewBox={`0 0 ${size} ${size / 2 + thickness + 14}`}
      role="img"
      aria-label={label ?? `${(clamped * 100).toFixed(1)} percent`}
      style={style}
    >
      <path
        d={`M ${thickness} ${cy} A ${r} ${r} 0 0 1 ${size - thickness} ${cy}`}
        fill="none"
        stroke="#e2e8f0"
        strokeWidth={thickness}
        strokeLinecap="round"
      />
      <path
        d={`M ${thickness} ${cy} A ${r} ${r} 0 0 1 ${size - thickness} ${cy}`}
        fill="none"
        stroke={color}
        strokeWidth={thickness}
        strokeLinecap="round"
        strokeDasharray={`${arcLen} ${arcLen}`}
        strokeDashoffset={offset}
      />
      <text
        x={cx}
        y={cy + 6}
        textAnchor="middle"
        fontSize={20}
        fontWeight={600}
        fill="#0f172a"
      >
        {(clamped * 100).toFixed(1)}%
      </text>
      {label && (
        <text
          x={cx}
          y={cy + 22}
          textAnchor="middle"
          fontSize={10}
          fill="#64748b"
        >
          {label}
        </text>
      )}
    </svg>
  );
}