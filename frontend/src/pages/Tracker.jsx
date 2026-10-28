import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ClipboardList, ExternalLink } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { Button, Chip, DataTable, EmptyState, ErrorState, Select, Skeleton, useToast } from "../components/ui";
import { useTrack } from "../hooks/useTrack";

const STATUSES = ["saved", "applied", "interview", "offer", "rejected"];
const TONE = { saved: undefined, applied: "info", interview: "warn", offer: "ok", rejected: "danger" };

export default function Tracker() {
  const { track } = useTrack();
  const qc = useQueryClient();
  const nav = useNavigate();
  const toast = useToast();
  const q = useQuery({ queryKey: ["applications", track?.id], queryFn: () => api.applications(track.id), enabled: !!track });
  const set = useMutation({
    mutationFn: ({ id, status }) => api.setApplicationStatus(track.id, id, status),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["applications", track.id] }),
    onError: (e) => toast(e.message, "danger"),
  });
  if (!track) return <EmptyState icon={ClipboardList} title="No track yet" action={<Button onClick={() => nav("/tracks")}>Create a track</Button>} />;
  if (q.isLoading) return <Skeleton className="h-48" />;
  if (q.error) return <ErrorState error={q.error} onRetry={q.refetch} />;

  const columns = [
    { key: "title", title: "Role", render: (r) => <div><p className="font-medium">{r.title || r.job_id}</p><p className="text-ink-soft">{r.company}</p></div> },
    { key: "status", title: "Status", render: (r) => (
      <div className="flex items-center gap-2">
        <Chip tone={TONE[r.status]}>{r.status}</Chip>
        <Select aria-label={`Change status for ${r.title || r.job_id}`} value={r.status} onChange={(e) => set.mutate({ id: r.id, status: e.target.value })} className="!min-h-[40px] max-w-[150px]">
          {STATUSES.map((s) => <option key={s}>{s}</option>)}
        </Select>
      </div>) },
    { key: "resume_id", title: "Resume used", render: (r) => <span className="font-mono text-xs text-ink-soft">{r.resume_id ? r.resume_id.slice(0, 8) : "n/a"}</span> },
    { key: "updated_at", title: "Updated", render: (r) => new Date(r.updated_at).toLocaleDateString() },
    { key: "url", title: "", render: (r) => r.url && <a href={r.url} target="_blank" rel="noopener noreferrer" aria-label="Open listing in a new tab" className="inline-flex h-11 w-11 items-center justify-center text-ink-soft hover:text-ink"><ExternalLink className="h-4 w-4" aria-hidden /></a> },
  ];
  return (
    <div className="space-y-6">
      <div><h1 className="text-3xl">Application tracker</h1><p className="text-ink-soft">Jobs you applied to or saved. Applying from the Jobs page adds a row here.</p></div>
      {q.data.length === 0 ? <EmptyState icon={ClipboardList} title="Nothing tracked yet" text="Click Apply on a job match and it is saved here." action={<Button onClick={() => nav("/jobs")}>See job matches</Button>} /> : <DataTable columns={columns} rows={q.data} />}
    </div>
  );
}
