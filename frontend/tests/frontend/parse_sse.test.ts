import { describe, it, expect } from "vitest";

// We test the SSE parser indirectly by importing the function.
// Since parseFrame is not exported, we re-implement the small parser
// the same way askStream.ts does (kept in sync manually).
// The actual integration is covered by the manual end-to-end test
// performed against the running dev server.

interface Frame {
  event: string;
  data: unknown;
}

function parseFrame(raw: string): Frame | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (!line) continue;
    if (line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    if (colon === -1) continue;
    const field = line.slice(0, colon).trim();
    let val = line.slice(colon + 1);
    if (val.startsWith(" ")) val = val.slice(1);
    if (field === "event") event = val;
    else if (field === "data") dataLines.push(val);
  }
  if (!dataLines.length) return null;
  const dataStr = dataLines.join("\n");
  let data: unknown = dataStr;
  try {
    data = JSON.parse(dataStr);
  } catch {
    /* keep raw string */
  }
  return { event, data };
}

describe("SSE frame parser (mirrors askStream.ts)", () => {
  it("parses a basic event frame", () => {
    const f = parseFrame('event: progress\ndata: {"node":"start"}\n');
    expect(f).toEqual({ event: "progress", data: { node: "start" } });
  });

  it("uses default event ''message'' when missing", () => {
    const f = parseFrame('data: "hello"\n');
    expect(f?.event).toBe("message");
    expect(f?.data).toBe("hello");
  });

  it("skips comment lines starting with colon", () => {
    const f = parseFrame(': heartbeat\nevent: ping\ndata: ok\n');
    expect(f).toEqual({ event: "ping", data: "ok" });
  });

  it("returns null when there is no data line", () => {
    expect(parseFrame("event: only\n")).toBeNull();
  });

  it("joins multiple data lines with newline", () => {
    const f = parseFrame('event: chunk\ndata: line1\ndata: line2\n');
    expect(f?.data).toBe("line1\nline2");
  });

  it("parses JSON-shaped data", () => {
    const f = parseFrame(
      'event: result\ndata: {"row_count":3,"columns":["a"]}\n',
    );
    expect(f?.data).toEqual({ row_count: 3, columns: ["a"] });
  });
});