import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, FileText, Trash2 } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { Button, Chip, DataTable, EmptyState, ErrorState, GlassCard, IconButton, Modal, Skeleton, useToast } from "../components/ui";
import { useTrack } from "../hooks/useTrack";

export default function Knowledge() {
  const { track } = useTrack();
  const qc = useQueryClient();
  const toast = useToast();
  const [del, setDel] = useState(null);
  const docs = useQuery({ queryKey: ["documents", track?.id], queryFn: () => api.documents(track.id), enabled: !!track });
  const roles = useQuery({ queryKey: ["kb-roles"], queryFn: api.roles });
  const companies = useQuery({ queryKey: ["kb-companies"], queryFn: () => api.companies() });
  const remove = useMutation({
    mutationFn: (id) => api.deleteDocument(track.id, id),
    onSuccess: () => { setDel(null); qc.invalidateQueries({ queryKey: ["documents", track.id] }); toast("Document deleted"); },
    onError: (e) => toast(e.message, "danger"),
  });
  const cols = [
    { key: "filename", title: "Document", render: (d) => <span className="flex items-center gap-2"><FileText className="h-4 w-4 text-kb-personal" aria-hidden />{d.filename}</span> },
    { key: "type", title: "Type", render: (d) => <Chip>{d.type}</Chip> },
    { key: "chunk_count", title: "Chunks", render: (d) => (d.indexed ? d.chunk_count : "not indexed") },
    { key: "act", title: "", render: (d) => <IconButton label={`Delete ${d.filename}`} icon={Trash2} onClick={() => setDel(d)} /> },
  ];
  return (
    <div className="space-y-6">
      <div><h1 className="text-3xl">Knowledge</h1><p className="text-ink-soft">What I can search: your own material, plus shared role and company question banks.</p></div>
      <section className="space-y-3">
        <h2 className="text-xl">Your documents <Chip kb="personal">Personal</Chip></h2>
        {docs.isLoading && <Skeleton className="h-32" />}
        {docs.error && <ErrorState error={docs.error} onRetry={docs.refetch} />}
        {docs.data && <DataTable columns={cols} rows={docs.data} empty="No documents yet. Add some on the Onboarding page." />}
      </section>
      <div className="grid gap-4 md:grid-cols-2">
        <GlassCard className="space-y-3">
          <h2 className="text-xl">Supported roles <Chip kb="role">Role</Chip></h2>
          {roles.isLoading && <Skeleton />}
          {roles.data?.roles.map((r) => (
            <div key={r.role} className="space-y-1">
              <p className="font-medium">{r.role} <span className="text-ink-soft">· {r.questions} questions</span></p>
              <div className="flex flex-wrap gap-1.5">{r.topics.map((t) => <Chip key={t} muted>{t}</Chip>)}</div>
            </div>
          ))}
        </GlassCard>
        <GlassCard className="space-y-3">
          <h2 className="text-xl">Supported companies <Chip kb="company">Company</Chip></h2>
          {companies.isLoading && <Skeleton />}
          {companies.data && companies.data.companies.length === 0 && <EmptyState icon={BookOpen} title="No company data yet" text="Company-specific questions have not been added. Company prep falls back to role-level questions and says so." />}
          {companies.data?.companies.map((c) => <p key={c.company}><span className="font-medium">{c.company}</span> <span className="text-ink-soft">· {c.records} records</span></p>)}
        </GlassCard>
      </div>
      <Modal open={!!del} onOpenChange={(o) => !o && setDel(null)} title="Delete this document?" description={del ? `${del.filename} and its chunks are removed from your knowledge base.` : ""}>
        <div className="flex justify-end gap-3"><Button variant="ghost" onClick={() => setDel(null)}>Keep it</Button><Button variant="danger" loading={remove.isPending} onClick={() => remove.mutate(del.id)}>Delete</Button></div>
      </Modal>
    </div>
  );
}
