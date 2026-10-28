import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Briefcase, ChevronDown, ExternalLink, RefreshCw } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import AgentTrace from "../components/AgentTrace";
import JobCard from "../components/JobCard";
import { Badge, Button, Callout, EmptyState, ErrorState, GlassCard, Skeleton, Stepper, useToast } from "../components/ui";
import { useTrack } from "../hooks/useTrack";

export default function Jobs() {
  const { track } = useTrack();
  const qc = useQueryClient();
  const nav = useNavigate();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const q = useQuery({
    queryKey: ["jobs", track?.id],
    queryFn: () => api.jobs(track.id),
    enabled: !!track,
    // while discovery runs (it starts by itself after the resume is indexed) keep polling; reopening never re-runs agents
    refetchInterval: (query) => (query.state.data?.discovery?.status === "running" ? 1500 : false),
  });
  if (!track) return <EmptyState icon={Briefcase} title="No track yet" action={<Button onClick={() => nav("/tracks")}>Create a track</Button>} />;

  const running = q.data?.discovery?.status === "running";
  const matches = q.data?.matches || [];
  const refresh = async () => { await api.refreshJobs(track.id); qc.invalidateQueries({ queryKey: ["jobs", track.id] }); };
  const prepare = (m) => nav("/prep", { state: { job_id: m.job_id, title: `${m.job.title} at ${m.job.company}` } });
  const applied = async (m) => { try { await api.markApplied(track.id, m.job_id); toast("Saved to your tracker as applied"); } catch { /* the listing still opens */ } };
  const dismiss = async (id) => { await api.dismissJob(track.id, id); qc.invalidateQueries({ queryKey: ["jobs", track.id] }); };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-3xl">Job matches</h1>
          <div className="flex items-center gap-2 text-ink-soft">
            Ranked against your profile and knowledge base
            {q.data?.sample_data && <Badge tone="warn">Sample data</Badge>}
          </div>
        </div>
        <Button variant="secondary" icon={RefreshCw} loading={running} onClick={refresh}>Refresh</Button>
      </div>

      {running && (
        <GlassCard className="space-y-3" aria-live="polite">
          <Stepper steps={["Searching jobs", "Ranking matches"]} current={0} />
          <p className="text-ink-soft">The job search agent is working. Matches appear here when it finishes.</p>
        </GlassCard>
      )}
      {q.isLoading && <div className="grid gap-4 md:grid-cols-2"><Skeleton className="h-52" /><Skeleton className="h-52" /></div>}
      {q.error && <ErrorState error={q.error} onRetry={q.refetch} />}
      {running && !matches.length && <div className="grid gap-4 md:grid-cols-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-52" />)}</div>}
      {!q.isLoading && !running && !matches.length && !q.error && (
        <EmptyState icon={Briefcase} title="No matches yet" text={q.data?.discovery ? "Nothing scored above the match threshold. Try refreshing after adding more to your resume." : "Upload and index your resume and discovery starts on its own."} action={<Button onClick={() => nav("/onboarding")}>Go to onboarding</Button>} />
      )}
      {q.data?.needs_refresh && !running && (
        <Callout tone="info" title="Your primary resume changed">These matches were found with the previous resume. <button className="underline" onClick={refresh}>Refresh matches</button> to use the new one.</Callout>
      )}
      {q.data?.sample_data && <Callout tone="info" title="Showing sample listings">The live job sites were unreachable or had nothing matching your profile, so these come from a built-in sample set. Try Refresh later.</Callout>}

      <div className="grid gap-4 md:grid-cols-2">
        {matches.map((m, i) => <JobCard key={m.job_id} match={m} index={i} onPrepare={prepare} onApplied={applied} onDismiss={dismiss} />)}
      </div>

      {q.data?.search_links?.length > 0 && (
        <GlassCard className="space-y-3">
          <h2 className="text-lg font-medium">Search more on the big boards</h2>
          <p className="text-sm text-ink-soft">These open each site's own search for your role, including India listings. Apply there directly.</p>
          <div className="flex flex-wrap gap-3">
            {q.data.search_links.map((l) => (
              <a key={l.site} href={l.url} target="_blank" rel="noopener noreferrer" className="btn glass-1 inline-flex min-h-[44px] items-center gap-2 rounded-md px-4 font-medium hover:bg-[var(--glass-2)]">
                {l.site} <ExternalLink className="h-4 w-4" aria-hidden />
                <span className="sr-only">(opens in a new tab)</span>
              </a>
            ))}
          </div>
        </GlassCard>
      )}

      {q.data?.discovery?.trace?.length > 0 && (
        <GlassCard>
          <button onClick={() => setOpen(!open)} aria-expanded={open} className="flex min-h-[44px] w-full items-center justify-between text-left">
            <span className="text-lg font-medium">How we searched</span>
            <ChevronDown className={`h-5 w-5 transition ${open ? "rotate-180" : ""}`} aria-hidden />
          </button>
          {open && <div className="mt-4"><AgentTrace trace={q.data.discovery.trace} />{q.data.discovery.notes.map((n) => <p key={n} className="mt-3 text-sm text-ink-soft">{n}</p>)}</div>}
        </GlassCard>
      )}
    </div>
  );
}
