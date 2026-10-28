import { useCallback, useRef, useState } from "react";
import { api } from "../api/client";
import { streamProgress } from "../api/sse";

/**
 * Drives a graph run that was started with wait=false: follows its SSE progress, then loads the final session.
 * Returns { start, progress, running, result, error, reset }. `result` is the RunResult shape (see api/client.js).
 */
export function useRun() {
  const [progress, setProgress] = useState([]);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const abort = useRef(null);

  const follow = useCallback(async (threadId) => {
    abort.current?.abort();
    const ctl = new AbortController();
    abort.current = ctl;
    try {
      await streamProgress(threadId, (ev) => ev.node && setProgress((p) => [...p, ev.node]), ctl.signal);
    } catch {
      /* the stream can drop; the session below is the source of truth */
    }
    for (let i = 0; i < 40; i++) {
      const s = await api.session(threadId);
      if (s.status !== "running") {
        return { status: s.status, thread_id: threadId, interrupt: s.interrupt, error: s.error, ...(s.result || {}) };
      }
      await new Promise((r) => setTimeout(r, 750));
    }
    throw new Error("The run is taking too long. Check Sessions later.");
  }, []);

  const start = useCallback(
    async (launch) => {
      setRunning(true);
      setError(null);
      setProgress([]);
      try {
        const first = await launch();
        const out = await follow(first.thread_id);
        if (out.status === "failed") throw new Error(out.error || "The run failed.");
        setResult(out);
        return out;
      } catch (e) {
        setError(e);
        return null;
      } finally {
        setRunning(false);
      }
    },
    [follow]
  );

  const reset = useCallback(() => {
    setResult(null);
    setError(null);
    setProgress([]);
  }, []);
  return { start, progress, running, result, error, reset, setResult };
}
