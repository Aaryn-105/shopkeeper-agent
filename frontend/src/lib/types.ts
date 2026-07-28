// Shared TypeScript types matching backend /api/* JSON payloads (SRS 4.3.x, 5.x).

export interface AskRequest {
  query: string;
  session_id?: string | null;
}

// SSE event shapes produced by POST /api/ask
// (see app/api/routes/ask.py and SRS 7.3.1).
export type AskEvent =
  | { event: "progress"; data: ProgressData }
  | { event: "result"; data: ResultData }
  | { event: "sql_generated"; data: SqlGeneratedData }
  | { event: "sql_corrected"; data: SqlCorrectedData }
  | { event: "error"; data: ErrorData }
  | { event: "done"; data: DoneData }
  | { event: string; data: unknown };

export interface ProgressData {
  node?: string;
  status?: string;
  message?: string;
}

export interface ResultData {
  columns?: string[];
  rows?: unknown[][];
  row_count?: number;
  truncated?: boolean;
  cache_hit?: boolean;
  error?: string | null;
  explanation?: string;
  sql?: string;
}

export interface SqlGeneratedData {
  sql?: string;
  cache_hit?: boolean;
  request_id?: string;
}

export interface SqlCorrectedData {
  original_sql?: string;
  corrected_sql?: string;
  error?: string | null;
  request_id?: string;
}

export interface ErrorData {
  message?: string;
  node?: string;
  request_id?: string;
}

export interface DoneData {
  request_id?: string;
  duration_ms?: number;
  cache_hit?: boolean;
  explanation?: string;
  sql?: string;
  row_count?: number;
}

// /api/stats payload (app.core.metrics.Metrics.stats_snapshot)
export interface StatsSnapshot {
  uptime_seconds: number;
  tokens: { prompt: number; completion: number; total: number };
  llm_calls: { total: number; avg_latency_ms: number };
  cache: { hits: number; misses: number; total: number; hit_rate: number };
  requests: {
    total: number;
    success: number;
    error: number;
    success_rate: number;
    avg_duration_ms: number;
    p95_duration_ms: number;
  };
  sql: {
    generated: number;
    validated_first_pass: number;
    corrected: number;
    executed_ok: number;
    executed_failed: number;
    executed_total: number;
    first_pass_rate: number;
    correction_rate: number;
    execution_success_rate: number;
  };
  node_p95_latency_ms: Record<string, number>;
}

// /api/config payload
export interface ConfigPayload {
  app: { name: string; version: string; env: string };
  ui: { welcome_message: string; usage_tips: string[] };
  samples: Array<{
    id: string;
    category: string;
    question: string;
    description: string;
  }>;
}

// /api/metadata/*
export interface TableInfo {
  id: string;
  name: string;
  role: string;
  description?: string;
}
export interface ColumnInfo {
  id: string;
  name: string;
  type: string;
  role: string;
  description?: string;
  examples?: string | string[];
  alias?: string | string[];
  table_id: string;
}
export interface MetricInfo {
  id: string;
  name: string;
  description?: string;
  related_columns?: string | string[];
  alias?: string | string[];
}

// /api/history payload
export interface HistoryItem {
  id: number;
  request_id: string;
  session_id?: string | null;
  query: string;
  sql_text?: string | null;
  status: string;
  error_message?: string | null;
  duration_ms: number;
  row_count?: number;
  sql_corrected: boolean;
  created_at: string;
}
export interface HistoryPage {
  count: number;
  total: number;
  limit: number;
  offset: number;
  session_id?: string | null;
  items: HistoryItem[];
}