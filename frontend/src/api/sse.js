import { BASE, getToken } from "./client";

/**
 * Stream node progress for a run (Server-Sent Events over fetch, so the Authorization header can be sent).
 * Calls onEvent({node?, event, status?}) per event and resolves when the stream ends. Abort with the signal.
 */
export async function streamProgress(threadId, onEvent, signal) {
  const res = await fetch(`${BASE}/stream/${encodeURIComponent(threadId)}`, {
    headers: { Authorization: `Bearer ${getToken()}`, Accept: "text/event-stream" },
    signal,
  });
  if (!res.ok || !res.body) return;
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop() || "";
    for (const part of parts) {
      const line = part.split("\n").find((l) => l.startsWith("data: "));
      if (line) {
        try {
          onEvent(JSON.parse(line.slice(6)));
        } catch {
          /* ignore malformed event */
        }
      }
    }
  }
}
