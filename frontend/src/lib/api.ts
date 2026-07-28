// Fetch wrapper for /api/* JSON endpoints (non-streaming).
// Throws on non-2xx responses with the server message in the error.
import type {
  StatsSnapshot,
  ConfigPayload,
  TableInfo,
  ColumnInfo,
  MetricInfo,
  HistoryPage,
  HistoryItem,
} from "./types";

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
    this.name = "ApiError";
  }
}

async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!res.ok) {
    const msg =
      (body && typeof body === "object" && "detail" in (body as object)
        ? String((body as { detail: unknown }).detail)
        : `HTTP ${res.status}`) || `HTTP ${res.status}`;
    throw new ApiError(res.status, msg, body);
  }
  return body as T;
}

// The /api/metadata/* endpoints wrap their list in { count, items, ... }
// (see app/api/routes/metadata.py). The frontend just wants the inner array,
// so we unwrap once.
async function listUnwrap<T>(path: string): Promise<T[]> {
  const res = await jsonFetch<{ count?: number; items?: T[] }>(path);
  return Array.isArray(res.items) ? res.items : [];
}

export const api = {
  config: () => jsonFetch<ConfigPayload>("/api/config"),
  stats: () => jsonFetch<StatsSnapshot>("/api/stats"),
  metadataTables: () => listUnwrap<TableInfo>("/api/metadata/tables"),
  metadataTable: (id: string) =>
    jsonFetch<{ table: TableInfo; columns: ColumnInfo[] }>(
      `/api/metadata/tables/${encodeURIComponent(id)}`,
    ),
  metadataColumns: (tableId?: string) =>
    listUnwrap<ColumnInfo>(
      `/api/metadata/columns${tableId ? `?table_id=${encodeURIComponent(tableId)}` : ""}`,
    ),
  metadataMetrics: () => listUnwrap<MetricInfo>("/api/metadata/metrics"),
  history: (params: { session_id?: string; limit?: number; offset?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.session_id) q.set("session_id", params.session_id);
    if (params.limit != null) q.set("limit", String(params.limit));
    if (params.offset != null) q.set("offset", String(params.offset));
    const qs = q.toString();
    return jsonFetch<HistoryPage>(`/api/history${qs ? `?${qs}` : ""}`);
  },
  historyItem: (id: number) =>
    jsonFetch<HistoryItem>(`/api/history/${id}`),
};