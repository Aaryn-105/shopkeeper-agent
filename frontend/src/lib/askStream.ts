// SSE client for POST /api/ask.
// EventSource doesn't support POST, so we use fetch + ReadableStream
// and parse the SSE frame format manually.
// See SRS 7.3.1 for the event protocol.
import type { AskRequest, AskEvent } from "./types";

export interface AskStreamHandlers {
  onEvent?: (event: AskEvent) => void;
  onError?: (err: Error) => void;
  onClose?: () => void;
}

export interface AskStreamHandle {
  abort: () => void;
  done: Promise<void>;
}

export function startAskStream(
  req: AskRequest,
  handlers: AskStreamHandlers = {},
): AskStreamHandle {
  const ctrl = new AbortController();
  const done = (async () => {
    try {
      const res = await fetch("/api/ask", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
        },
        body: JSON.stringify(req),
        signal: ctrl.signal,
      });
      if (!res.ok || !res.body) {
        throw new Error(`HTTP ${res.status} ${res.statusText}`);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buf = "";
      // SSE frames are separated by blank lines, each frame has lines like
      // "event: <name>\ndata: <json>\n\n".
      while (true) {
        const { value, done: rdDone } = await reader.read();
        if (rdDone) break;
        buf += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buf.indexOf("\n\n")) !== -1) {
          const rawFrame = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const parsed = parseFrame(rawFrame);
          if (parsed) handlers.onEvent?.(parsed);
        }
      }
    } catch (e) {
      if ((e as Error).name === "AbortError") {
        handlers.onClose?.();
        return;
      }
      handlers.onError?.(e as Error);
    } finally {
      handlers.onClose?.();
    }
  })();
  return {
    abort: () => ctrl.abort(),
    done,
  };
}

function parseFrame(raw: string): AskEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (!line) continue;
    if (line.startsWith(":")) continue; // comment / heartbeat
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
  return { event, data } as AskEvent;
}