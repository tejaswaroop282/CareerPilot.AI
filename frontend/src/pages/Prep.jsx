import { Send, Sparkles, Workflow } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import AgentTrace from "../components/AgentTrace";
import ClarifyCard from "../components/ClarifyCard";
import PlanConfirmCard from "../components/PlanConfirmCard";
import ProgressStepper from "../components/ProgressStepper";
import QuestionCard from "../components/QuestionCard";
import ScoreCard from "../components/ScoreCard";
import { Button, Callout, Chip, Drawer, EmptyState, ErrorState, GlassCard, GlassPanel, KB_LABEL, Segmented, Textarea, useToast } from "../components/ui";
import { useRun } from "../hooks/useSSE";
import { useTrack } from "../hooks/useTrack";

const EXAMPLES = [
  "Give me 10 GenAI interview questions on RAG, embeddings and vector databases",
  "Ask me questions about my Ocular AI project",
  "Prepare me for an Amazon SDE interview",
  "Quiz me on operating systems: scheduling and deadlocks",
];

export default function Prep() {
  const { track } = useTrack();
  const toast = useToast();
  const nav = useNavigate();
  const { state: nav_state } = useLocation();
  const run = useRun();
  const [request, setRequest] = useState("");
  const [mode, setMode] = useState("question_set");
  const [jobId, setJobId] = useState(null);
  const [showTrace, setShowTrace] = useState(false);
  const answers = useRef({}); // interview answers by question index, for the transcript

  useEffect(() => {
    if (nav_state?.job_id) {
      setJobId(nav_state.job_id);
      setRequest(`Prepare me for the ${nav_state.title} interview`);
      window.history.replaceState({}, "");
    }
  }, [nav_state]);

  if (!track) return <EmptyState title="Create a track first" action={<Button onClick={() => nav("/tracks")}>Go to tracks</Button>} />;

  const r = run.result;
  const submit = () => { answers.current = {}; run.start(() => api.prep(track.id, { request, mode, job_id: jobId })); };
  const resume = (payload) => run.start(() => api.resume(r.thread_id, payload));
  const feedback = (idx, score) => api.feedback({ thread_id: r.thread_id, item_index: idx, score }).then(() => toast("Thanks for the feedback")).catch(() => {});

  const interrupt = r?.status === "awaiting_input" ? r.interrupt : null;
  const items = r?.output?.items || [];
  const mock = (r?.plan?.mode || mode) === "mock_interview";
  const notes = [...new Set([...(r?.output?.notes || []), ...(r?.notes || [])])];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl">Interview prep</h1>
        <p className="text-ink-soft">Ask for questions on your projects, a role or a company. Every question cites its source.</p>
      </div>

      {!interrupt && !(mock && r) && (
        <GlassPanel className="mx-auto max-w-3xl space-y-4">
          <Textarea aria-label="What do you want to prepare?" value={request} onChange={(e) => setRequest(e.target.value)} placeholder="e.g. Give me 10 GenAI interview questions on RAG and embeddings" />
          <div className="flex flex-wrap gap-2">
            {EXAMPLES.map((e) => (
              <button key={e} type="button" onClick={() => { setRequest(e); setJobId(null); }} className="min-h-[44px] text-left">
                <Chip className="cursor-pointer px-3 py-1.5 hover:bg-[var(--glass-2)]">{e}</Chip>
              </button>
            ))}
          </div>
          {jobId && <Callout tone="info" title="Preparing from a job match">I will use the stored job listing to pick the role, company and topics.</Callout>}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Segmented label="Mode" value={mode} onChange={setMode} options={[{ value: "question_set", label: "Question set" }, { value: "mock_interview", label: "Mock interview" }]} />
            <Button icon={Sparkles} loading={run.running} disabled={!request.trim()} onClick={submit}>Prepare</Button>
          </div>
        </GlassPanel>
      )}

      {run.running && (
        <GlassCard className="space-y-3" aria-live="polite">
          <ProgressStepper nodes={run.progress} />
          <p className="flex items-center gap-2 text-ink-soft">Working <span className="typing" aria-hidden><span /><span /><span /></span></p>
        </GlassCard>
      )}
      {run.error && <ErrorState error={run.error} />}

      {interrupt?.type === "clarification" && <ClarifyCard interrupt={interrupt} busy={run.running} onSubmit={resume} />}
      {interrupt?.type === "confirm_plan" && <PlanConfirmCard open interrupt={interrupt} busy={run.running} onSubmit={resume} />}

      {r && !interrupt && !mock && (
        <div className="space-y-4">
          {r.plan && (
            <div className="flex flex-wrap items-center gap-2 text-sm text-ink-soft">
              <span>{items.length} questions · role <span className="font-mono">{r.plan.role}</span>{r.plan.company ? ` · ${r.plan.company}` : ""}</span>
              {r.plan.kbs?.map((k) => <Chip key={k} kb={k}>{KB_LABEL[k]}</Chip>)}
              <Button variant="ghost" icon={Workflow} onClick={() => setShowTrace(true)} className="ml-auto">Agent trace</Button>
            </div>
          )}
          {notes.map((n) => <Callout key={n} title="Note">{n}</Callout>)}
          {items.length === 0 && !notes.length && <EmptyState title="No questions generated" text="Try a broader request." />}
          {items.map((it, i) => <QuestionCard key={i} item={it} index={i} chunks={r.chunks} onFeedback={feedback} />)}
          <div className="flex justify-center"><Button variant="secondary" onClick={run.reset}>New request</Button></div>
        </div>
      )}

      {r && mock && (
        <Interview r={r} interrupt={interrupt} busy={run.running} answers={answers} onAnswer={resume} onTrace={() => setShowTrace(true)} onNew={run.reset} notes={notes} nav={nav} />
      )}

      <Drawer open={showTrace} onOpenChange={setShowTrace} title="Agent trace">
        <AgentTrace trace={r?.trace} />
      </Drawer>
    </div>
  );
}

function Interview({ r, interrupt, busy, answers, onAnswer, onTrace, onNew, notes, nav }) {
  const [text, setText] = useState("");
  const end = useRef(null);
  const items = r.output?.items || [];
  const scores = r.scores || [];
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [items.length, scores.length]);
  const send = (value) => {
    answers.current[scores.length] = value;
    setText("");
    onAnswer({ answer: value });
  };
  const total = interrupt?.total || r.plan?.count || items.length;
  const finished = r.status === "done";

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-ink-soft">{finished ? "Interview complete" : `Question ${interrupt?.turn ?? scores.length + 1} of ${total}`}</p>
        <Button variant="ghost" icon={Workflow} onClick={onTrace}>Agent trace</Button>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-[var(--glass-1)]" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={Math.min(scores.length, total)}>
        <div className="h-full bg-[image:var(--accent-grad)] transition-all" style={{ width: `${Math.min(100, (scores.length / Math.max(total, 1)) * 100)}%` }} />
      </div>
      {notes.map((n) => <Callout key={n} title="Note">{n}</Callout>)}

      <div className="space-y-4" aria-live="polite">
        {items.map((it, i) => (
          <div key={i} className="space-y-3">
            <div className="glass-1 max-w-[88%] rounded-lg rounded-tl-sm p-4">
              <p className="mb-1 text-xs text-ink-soft">Interviewer</p>
              <p className="font-medium">{it.question}</p>
              {it.kind === "mcq" && <ul className="mt-2 space-y-1 text-ink-soft">{it.options.map((o, k) => <li key={k}>{String.fromCharCode(65 + k)}. {o}</li>)}</ul>}
            </div>
            {answers.current[i] !== undefined && (
              <div className="ml-auto max-w-[88%] rounded-lg rounded-tr-sm bg-[image:var(--accent-grad)] p-4 text-white">
                <p className="mb-1 text-xs opacity-80">You</p>
                <p className="whitespace-pre-wrap">{it.kind === "mcq" ? `${String.fromCharCode(65 + Number(answers.current[i]))}. ${it.options[Number(answers.current[i])] ?? answers.current[i]}` : answers.current[i]}</p>
              </div>
            )}
            {scores[i] && <ScoreCard score={scores[i]} keyPoints={it.key_points} />}
          </div>
        ))}
        {busy && <div className="glass-1 inline-block rounded-lg p-4"><span className="typing" aria-label="Interviewer is typing"><span /><span /><span /></span></div>}
        <div ref={end} />
      </div>

      {interrupt?.type === "answer" && !busy && (
        <GlassPanel className="space-y-3">
          {interrupt.kind === "mcq" ? (
            <div className="grid gap-2" role="group" aria-label="Choose an answer">
              {interrupt.options.map((o, i) => (
                <Button key={i} variant="secondary" className="justify-start text-left" onClick={() => send(String(i))}>{String.fromCharCode(65 + i)}. {o}</Button>
              ))}
            </div>
          ) : (
            <>
              <Textarea aria-label="Your answer" value={text} onChange={(e) => setText(e.target.value)} placeholder="Type your answer. For coding questions, explain your approach, data structures and complexity." />
              <div className="flex justify-end"><Button icon={Send} disabled={!text.trim()} onClick={() => send(text.trim())}>Submit answer</Button></div>
            </>
          )}
        </GlassPanel>
      )}
      {finished && (
        <div className="flex flex-wrap justify-center gap-3">
          {r.report_id && <Button onClick={() => nav("/reports", { state: { id: r.report_id } })}>View scorecard</Button>}
          <Button variant="secondary" onClick={onNew}>New interview</Button>
        </div>
      )}
    </div>
  );
}
