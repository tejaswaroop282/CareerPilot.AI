import { useQuery } from "@tanstack/react-query";
import { BarChart3 } from "lucide-react";
import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { PolarAngleAxis, PolarGrid, Radar, RadarChart, ResponsiveContainer } from "recharts";
import { api } from "../api/client";
import CitationChip from "../components/CitationChip";
import { Button, Chip, EmptyState, ErrorState, GlassCard, ScoreRing, Skeleton } from "../components/ui";
import { useTrack } from "../hooks/useTrack";

const avg = (s) => (s.correctness + s.depth + s.clarity) / 3;

export default function Reports() {
  const { track } = useTrack();
  const nav = useNavigate();
  const { state } = useLocation();
  const [id, setId] = useState(state?.id || null);
  const list = useQuery({ queryKey: ["reports", track?.id], queryFn: () => api.reports(track.id), enabled: !!track });
  useEffect(() => { if (!id && list.data?.length) setId(list.data[0].id); }, [list.data, id]);
  const detail = useQuery({ queryKey: ["report", id], queryFn: () => api.report(id), enabled: !!id });

  if (!track) return <EmptyState icon={BarChart3} title="No track yet" action={<Button onClick={() => nav("/tracks")}>Create a track</Button>} />;
  if (list.isLoading) return <Skeleton className="h-64" />;
  if (list.error) return <ErrorState error={list.error} onRetry={list.refetch} />;
  if (!list.data.length) return <EmptyState icon={BarChart3} title="No scorecards yet" text="Finish a mock interview and your scorecard appears here." action={<Button onClick={() => nav("/prep")}>Start a mock interview</Button>} />;

  const d = detail.data;
  const byTopic = {};
  d?.items.forEach((it, i) => { if (d.scores[i]) (byTopic[it.topic || "general"] ||= []).push(avg(d.scores[i])); });
  const radar = Object.entries(byTopic).map(([topic, v]) => ({ topic, score: Math.round((v.reduce((a, b) => a + b, 0) / v.length) * 10) / 10 }));

  return (
    <div className="space-y-6">
      <h1 className="text-3xl">Scorecards</h1>
      <div className="flex flex-wrap gap-2">
        {list.data.map((r) => (
          <button key={r.id} onClick={() => setId(r.id)} aria-pressed={id === r.id} className="min-h-[44px]">
            <Chip tone={id === r.id ? "info" : undefined} className="px-4 py-2 text-sm">{new Date(r.created_at).toLocaleDateString()} · {r.role}{r.company ? ` · ${r.company}` : ""} · {r.overall}/10</Chip>
          </button>
        ))}
      </div>
      {detail.isLoading && <Skeleton className="h-64" />}
      {d && (
        <>
          <div className="grid gap-4 md:grid-cols-[auto_1fr]">
            <GlassCard className="flex items-center gap-5">
              <ScoreRing value={d.report.overall / 10} size={110} label={`Overall ${d.report.overall} out of 10`} />
              <div><p className="text-sm text-ink-soft">Overall</p><p className="text-3xl font-semibold">{d.report.overall}<span className="text-lg text-ink-soft">/10</span></p></div>
            </GlassCard>
            <GlassCard className="min-h-[220px]">
              <h2 className="mb-2 text-lg">By topic</h2>
              {radar.length >= 3 ? (
                <div role="img" aria-label="Radar chart of average score per topic" style={{ height: 220 }}>
                  <ResponsiveContainer><RadarChart data={radar}><PolarGrid stroke="var(--glass-border)" /><PolarAngleAxis dataKey="topic" tick={{ fill: "var(--text-secondary)", fontSize: 12 }} /><Radar dataKey="score" stroke="var(--accent-via)" fill="var(--accent-via)" fillOpacity={0.35} /></RadarChart></ResponsiveContainer>
                </div>
              ) : (
                <ul className="space-y-2">{radar.map((t) => <li key={t.topic} className="flex items-center gap-3"><span className="w-32 truncate">{t.topic}</span><div className="h-2 flex-1 rounded-full bg-[var(--glass-1)]"><div className="h-full rounded-full bg-[image:var(--accent-grad)]" style={{ width: `${t.score * 10}%` }} /></div><span className="w-10 text-right">{t.score}</span></li>)}</ul>
              )}
            </GlassCard>
          </div>
          <GlassCard className="space-y-3">
            <h2 className="text-lg">Weak areas</h2>
            {d.report.weak_areas.length ? <div className="flex flex-wrap gap-2">{d.report.weak_areas.map((w) => <Chip key={w} tone="danger">{w}</Chip>)}</div> : <p className="text-ink-soft">No weak areas. Nice work.</p>}
            {d.report.study_pointers.length > 0 && (<><h3 className="pt-2 text-base">Study pointers</h3><div className="flex flex-wrap gap-2">{d.report.study_pointers.map((p) => <CitationChip key={p.chunk_id} id={p.chunk_id} kb={p.kb} chunk={{ header: p.topic, snippet: `Review ${p.topic}` }} />)}</div></>)}
          </GlassCard>
        </>
      )}
    </div>
  );
}
