import { describe, expect, it } from "vitest";
import { extractSseFrames, parseFrame } from "../../src/lib/askStream";

describe("SSE frame parsing", () => {
  it("accepts the CRLF separators emitted by sse-starlette", () => {
    const raw = [
      "event: progress\r\ndata: {\"node\":\"start\"}\r\n\r\n",
      "event: result\r\ndata: {\"row_count\":1}\r\n\r\n",
    ].join("");

    const extracted = extractSseFrames(raw);

    expect(extracted.rest).toBe("");
    expect(extracted.frames).toHaveLength(2);
    expect(parseFrame(extracted.frames[0])).toEqual({
      event: "progress",
      data: { node: "start" },
    });
    expect(parseFrame(extracted.frames[1])).toEqual({
      event: "result",
      data: { row_count: 1 },
    });
  });

  it("keeps a separator fragment for the next network chunk", () => {
    const first = extractSseFrames(
      "event: done\r\ndata: {\"request_id\":\"r1\"}\r\n\r",
    );
    expect(first.frames).toHaveLength(0);

    const second = extractSseFrames(first.rest + "\n");
    expect(second.frames).toHaveLength(1);
    expect(parseFrame(second.frames[0])?.event).toBe("done");
  });
});
