import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Rocket } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import FileDropzone from "../components/FileDropzone";
import { Button, Chip, EmptyState, Field, GlassCard, Input, Stepper, useToast } from "../components/ui";
import { useTrack } from "../hooks/useTrack";

const STEPS = ["Uploading", "Reading your resume", "Building your profile", "Finding jobs"];

export default function Onboarding() {
  const { track } = useTrack();
  const qc = useQueryClient();
  const nav = useNavigate();
  const toast = useToast();
  const [gh, setGh] = useState("");
  const [stage, setStage] = useState(-1); // -1 idle, 0..3 running, 4 done
  const resumes = useQuery({ queryKey: ["resumes", track?.id], queryFn: () => api.resumes(track.id), enabled: !!track });
  const docs = useQuery({ queryKey: ["documents", track?.id], queryFn: () => api.documents(track.id), enabled: !!track });
  useEffect(() => setGh(track?.github_username || ""), [track?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!track) return <EmptyState title="Create a track first" text="Onboarding needs a track to attach your resume to." action={<Button onClick={() => nav("/tracks")}>Go to tracks</Button>} />;

  const refresh = () => { qc.invalidateQueries({ queryKey: ["resumes", track.id] }); qc.invalidateQueries({ queryKey: ["documents", track.id] }); };

  const run = async () => {
    try {
      if (gh !== (track.github_username || "")) await api.updateTrack(track.id, { github_username: gh || null });
      setStage(1);
      const { thread_id } = await api.ingest(track.id, true);
      let s;
      do {
        await new Promise((r) => setTimeout(r, 1200));
        s = await api.session(thread_id);
        if (s.status === "running") setStage((x) => Math.min(x + 0.2, 2.9));
      } while (s.status === "running");
      if (s.status !== "done") throw new Error(s.error || "Ingestion failed");
      setStage(3);
      qc.invalidateQueries({ queryKey: ["documents", track.id] });
      qc.invalidateQueries({ queryKey: ["profile", track.id] });
      toast("Resume indexed. Looking for jobs now.");
      nav("/jobs");
    } catch (e) {
      setStage(-1);
      toast(e.message, "danger");
    }
  };

  const primary = resumes.data?.find((r) => r.is_primary);
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl">Set up {track.name}</h1>
        <p className="text-ink-soft">Add your resume and any extra material, then I build your profile and find matching jobs.</p>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <GlassCard className="space-y-4">
          <h2 className="text-xl">1. Resume</h2>
          <FileDropzone label="Drop your resume" accept=".pdf,.md,.txt" multiple={false} upload={async (f) => { await api.uploadResume(track.id, f); refresh(); }} />
          <div className="flex flex-wrap gap-2">
            {resumes.data?.map((r) => (
              <button key={r.id} onClick={async () => { await api.makePrimary(track.id, r.id); refresh(); }} className="min-h-[44px]" aria-label={`Use ${r.filename} as primary resume`}>
                <Chip tone={r.is_primary ? "ok" : undefined}>{r.is_primary && <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />} {r.filename} v{r.version}</Chip>
              </button>
            ))}
          </div>
        </GlassCard>

        <GlassCard className="space-y-4">
          <h2 className="text-xl">2. GitHub and extra material</h2>
          <Field label="GitHub username (optional)" id="gh" hint="Public repos are read through the GitHub tool when a token is configured."><Input id="gh" value={gh} onChange={(e) => setGh(e.target.value)} placeholder="octocat" /></Field>
          <FileDropzone label="Project docs, notes, learning PDFs" accept=".pdf,.md,.txt" upload={async (f) => { await api.uploadDocument(track.id, f); refresh(); }} />
          <div className="flex flex-wrap gap-2">
            {docs.data?.map((d) => <Chip key={d.id} kb="personal">{d.filename}{d.indexed ? ` · ${d.chunk_count} chunks` : " · not indexed"}</Chip>)}
          </div>
        </GlassCard>
      </div>

      <GlassCard className="space-y-4">
        <h2 className="text-xl">3. Build my profile</h2>
        {stage >= 0 && <Stepper steps={STEPS} current={Math.floor(stage)} done={stage >= 4} />}
        <Button icon={Rocket} loading={stage >= 0 && stage < 4} disabled={!primary} onClick={run}>
          {primary ? "Index my files and find jobs" : "Upload a resume to continue"}
        </Button>
      </GlassCard>
    </div>
  );
}
