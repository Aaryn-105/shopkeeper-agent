import { describe, it, expect, vi, afterEach } from "vitest";
import { api, ApiError } from "../../src/lib/api";

afterEach(() => {
  vi.restoreAllMocks();
});

function mockFetchOnce(body: unknown, status = 200, contentType = "application/json") {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      return new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": contentType },
      });
    }),
  );
}

function mockFetchRaw(text: string, status = 200) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(text, { status })),
  );
}

describe("api wrapper", () => {
  it("calls /api/config and returns the JSON", async () => {
    mockFetchOnce({ app: { name: "x", version: "1.0.0", env: "local" }, ui: { welcome_message: "hi", usage_tips: [] }, samples: [] });
    const cfg = await api.config();
    expect(cfg.app.name).toBe("x");
    expect(cfg.samples).toEqual([]);
  });

  it("calls /api/stats and returns the snapshot", async () => {
    mockFetchOnce({
      uptime_seconds: 12.3,
      tokens: { prompt: 100, completion: 50, total: 150 },
      llm_calls: { total: 7, avg_latency_ms: 12.5 },
      cache: { hits: 4, misses: 1, total: 5, hit_rate: 0.8 },
      requests: {
        total: 10, success: 9, error: 1, success_rate: 0.9,
        avg_duration_ms: 22, p95_duration_ms: 88,
      },
      sql: {
        generated: 10, validated_first_pass: 8, corrected: 2,
        executed_ok: 9, executed_failed: 1, executed_total: 10,
        first_pass_rate: 0.8, correction_rate: 0.2, execution_success_rate: 0.9,
      },
      node_p95_latency_ms: { extract_keywords: 12.5 },
    });
    const s = await api.stats();
    expect(s.cache.hit_rate).toBe(0.8);
    expect(s.tokens.total).toBe(150);
  });

  it("builds history query string", async () => {
    const fn = vi.fn(async () =>
      new Response(JSON.stringify({ count: 0, total: 0, limit: 5, offset: 2, session_id: "s1", items: [] }), {
        status: 200, headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fn);
    await api.history({ session_id: "s1", limit: 5, offset: 2 });
    const calls = fn.mock.calls as unknown[][];
    const called = String(calls[0]?.[0] ?? "");
    expect(called).toBe("/api/history?session_id=s1&limit=5&offset=2");
  });

  it("throws ApiError on non-2xx with detail", async () => {
    mockFetchOnce({ detail: "bad query" }, 400);
    await expect(api.config()).rejects.toBeInstanceOf(ApiError);
    await expect(api.config()).rejects.toMatchObject({ status: 400, message: "bad query" });
  });

  it("falls back to HTTP status when detail is missing", async () => {
    mockFetchRaw("oops", 502);
    await expect(api.config()).rejects.toMatchObject({ status: 502, message: "HTTP 502" });
  });

  it("unwraps {count,items} envelope for /api/metadata/tables", async () => {
    mockFetchOnce({ count: 1, items: [{ id: "t1", name: "fact_order", role: "fact" }] });
    const r = await api.metadataTables();
    expect(r).toHaveLength(1);
    expect(r[0].id).toBe("t1");
  });
  it("returns [] when items is missing", async () => {
    mockFetchOnce({ count: 0 });
    const r = await api.metadataMetrics();
    expect(r).toEqual([]);
  });
});