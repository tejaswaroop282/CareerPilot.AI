import { Timeline } from "./ui";

/** Vertical timeline of agent steps: rationale, tool and arguments, short result. */
export default function AgentTrace({ trace }) {
  if (!trace?.length) return <p className="text-ink-soft">No agent steps were recorded.</p>;
  return (
    <div aria-live="polite">
      <Timeline
        items={trace.map((s) => (
          <div key={`${s.step}-${s.tool}`} className="space-y-1">
            <p className="text-sm">
              <span className="text-ink-soft">Step {s.step}</span> · <span className="font-medium">{s.rationale || "—"}</span>
            </p>
            <p className="font-mono text-xs text-kb-role">
              {s.tool}({Object.entries(s.args || {}).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ")})
            </p>
            {s.observation && <p className="glass-solid rounded-md p-2 font-mono text-xs text-ink-soft">{s.observation}</p>}
          </div>
        ))}
      />
    </div>
  );
}
