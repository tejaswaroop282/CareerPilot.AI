import { useQuery } from "@tanstack/react-query";
import { User } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { Button, Chip, EmptyState, ErrorState, GlassCard, Skeleton } from "../components/ui";
import { useTrack } from "../hooks/useTrack";

export default function Profile() {
  const { track } = useTrack();
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["profile", track?.id], queryFn: () => api.profile(track.id), enabled: !!track });
  if (!track) return <EmptyState icon={User} title="No track yet" action={<Button onClick={() => nav("/tracks")}>Create a track</Button>} />;
  if (q.isLoading) return <Skeleton className="h-48" />;
  if (q.error) return <ErrorState error={q.error} onRetry={q.refetch} />;
  const p = q.data;
  if (!p.skills.length && !p.projects.length) return <EmptyState icon={User} title="No profile yet" text="Index your resume to extract your skills, projects and level." action={<Button onClick={() => nav("/onboarding")}>Go to onboarding</Button>} />;
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3"><h1 className="text-3xl">Profile</h1>{p.level && <Chip tone="info">{p.level}</Chip>}</div>
      <GlassCard className="space-y-3"><h2 className="text-xl">Skills</h2><div className="flex flex-wrap gap-2">{p.skills.map((s) => <Chip key={s} kb="personal">{s}</Chip>)}</div></GlassCard>
      <div className="grid gap-4 md:grid-cols-2">
        {p.projects.map((pr) => (
          <GlassCard key={pr.name} lift className="space-y-2">
            <h3 className="text-lg">{pr.name}</h3><p className="text-ink-soft">{pr.summary}</p>
            <div className="flex flex-wrap gap-1.5">{pr.tech.map((t) => <Chip key={t} muted>{t}</Chip>)}</div>
          </GlassCard>
        ))}
      </div>
    </div>
  );
}
