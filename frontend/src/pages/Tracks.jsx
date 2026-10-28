import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, EmptyState, ErrorState, Field, GlassCard, IconButton, Input, Modal, Select, Skeleton, useToast } from "../components/ui";
import { api } from "../api/client";
import { useTrack } from "../hooks/useTrack";

const ROLE_HINT = "Pick the role whose question bank you want to practise";

export default function Tracks() {
  const { tracks, loading, error, refetch, select } = useTrack();
  const qc = useQueryClient();
  const toast = useToast();
  const nav = useNavigate();
  const [edit, setEdit] = useState(null); // {id?, name, target_role, github_username}
  const [del, setDel] = useState(null);
  const roles = useQuery({ queryKey: ["kb-roles"], queryFn: api.roles });
  const supported = roles.data?.roles || [];

  const done = (msg) => { qc.invalidateQueries({ queryKey: ["tracks"] }); toast(msg); };
  const save = useMutation({
    mutationFn: (t) => {
      const body = { name: t.name, target_role: t.target_role, github_username: t.github_username || null };
      return t.id ? api.updateTrack(t.id, body) : api.createTrack(body);
    },
    onSuccess: (t, v) => { setEdit(null); done(v.id ? "Track updated" : "Track created"); if (!v.id) { select(t.id); nav("/onboarding"); } },
    onError: (e) => toast(e.message, "danger"),
  });
  const dup = useMutation({ mutationFn: (id) => api.duplicateTrack(id), onSuccess: () => done("Track duplicated"), onError: (e) => toast(e.message, "danger") });
  const remove = useMutation({ mutationFn: (id) => api.deleteTrack(id), onSuccess: () => { setDel(null); done("Track deleted"); }, onError: (e) => toast(e.message, "danger") });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-3xl">Career tracks</h1>
          <p className="text-ink-soft">Each track has its own resume, documents, knowledge base and jobs.</p>
        </div>
        <Button icon={Plus} onClick={() => setEdit({ name: "", target_role: "", github_username: "" })}>New track</Button>
      </div>

      {loading && <div className="grid gap-4 md:grid-cols-2"><Skeleton className="h-28" /><Skeleton className="h-28" /></div>}
      {error && <ErrorState error={error} onRetry={refetch} />}
      {!loading && !error && tracks.length === 0 && (
        <EmptyState title="Create your first track" text="A track is a target role such as sde or genai. You upload your resume next." action={<Button icon={Plus} onClick={() => setEdit({ name: "", target_role: "", github_username: "" })}>New track</Button>} />
      )}
      <div className="grid gap-4 md:grid-cols-2">
        {tracks.map((t) => (
          <GlassCard key={t.id} lift className="flex items-start justify-between gap-3">
            <div>
              <h3 className="text-lg">{t.name}</h3>
              <p className="text-ink-soft">Target role: <span className="font-mono">{t.target_role}</span></p>
              {t.github_username && <p className="text-ink-soft">GitHub: {t.github_username}</p>}
            </div>
            <div className="flex">
              <IconButton label={`Edit ${t.name}`} icon={Pencil} onClick={() => setEdit({ ...t, github_username: t.github_username || "" })} />
              <IconButton label={`Duplicate ${t.name}`} icon={Copy} onClick={() => dup.mutate(t.id)} />
              <IconButton label={`Delete ${t.name}`} icon={Trash2} onClick={() => setDel(t)} />
            </div>
          </GlassCard>
        ))}
      </div>

      <Modal open={!!edit} onOpenChange={(o) => !o && setEdit(null)} title={edit?.id ? "Edit track" : "New track"}>
        {edit && (
          <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(edit); }}>
            <Field label="Track name" id="t-name"><Input id="t-name" required maxLength={80} value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
            <Field label="Target role" id="t-role" hint={ROLE_HINT}><Select id="t-role" required value={edit.target_role} onChange={(e) => setEdit({ ...edit, target_role: e.target.value })}>
                <option value="" disabled>Choose a role</option>
                {edit.target_role && !supported.some((r) => r.role === edit.target_role) && <option value={edit.target_role}>{edit.target_role} (not supported yet)</option>}
                {supported.map((r) => <option key={r.role} value={r.role}>{r.role}{r.aliases?.[0] ? ` (${r.aliases[0]})` : ""}</option>)}
              </Select></Field>
            <Field label="GitHub username (optional)" id="t-gh"><Input id="t-gh" maxLength={39} value={edit.github_username} onChange={(e) => setEdit({ ...edit, github_username: e.target.value })} /></Field>
            <div className="flex justify-end gap-3"><Button type="button" variant="ghost" onClick={() => setEdit(null)}>Cancel</Button><Button type="submit" loading={save.isPending}>Save</Button></div>
          </form>
        )}
      </Modal>
      <Modal open={!!del} onOpenChange={(o) => !o && setDel(null)} title="Delete this track?" description="Its resumes, documents, matches, reports and knowledge base are permanently deleted.">
        <div className="flex justify-end gap-3"><Button variant="ghost" onClick={() => setDel(null)}>Keep it</Button><Button variant="danger" loading={remove.isPending} onClick={() => remove.mutate(del.id)}>Delete track</Button></div>
      </Modal>
    </div>
  );
}
