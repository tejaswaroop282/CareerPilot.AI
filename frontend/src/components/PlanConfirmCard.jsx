import { useState } from "react";
import { Button, Callout, Chip, Field, Input, KB_LABEL, Modal, Segmented } from "./ui";

// knowledge bases each kind of request cannot do without (company is optional: it needs data for that company)
const REQUIRED = { project_questions: ["personal", "role"], role_questions: ["role"], company_prep: ["role", "personal"] };

/** Plan confirmation: editable KBs, topics, count, mode (and role/company), with the expected fallback shown. */
export default function PlanConfirmCard({ open, interrupt, busy, onSubmit }) {
  const plan = interrupt.plan;
  const [mode, setMode] = useState(plan.mode);
  const [count, setCount] = useState(plan.count);
  const [topics, setTopics] = useState((plan.topics || []).join(", "));
  const [role, setRole] = useState(plan.role || "");
  const [company, setCompany] = useState(plan.company || "");
  const [kbs, setKbs] = useState(plan.kbs || []);
  const required = REQUIRED[plan.intent] || [];
  const toggle = (k) => !required.includes(k) && setKbs((x) => (x.includes(k) ? x.filter((i) => i !== k) : [...x, k]));

  const submit = (approved) => {
    if (!approved) return onSubmit({ approved: false });
    onSubmit({
      approved: true,
      edits: {
        mode, count: Number(count), role, kbs: [...new Set([...kbs, ...required])],
        topics: topics.split(",").map((t) => t.trim()).filter(Boolean),
        company: company.trim() || "",
      },
    });
  };

  return (
    <Modal open={open} onOpenChange={(o) => !o && !busy && submit(false)} title="Confirm your prep plan" description="Review what I will search before I start. You can edit anything." wide>
      <div className="space-y-5">
        {interrupt.expected_fallback && <Callout title="Heads up">{interrupt.expected_fallback}</Callout>}
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Role" id="pc-role" hint={`Supported: ${interrupt.supported_roles.join(", ")}`}>
            <Input id="pc-role" value={role} onChange={(e) => setRole(e.target.value)} />
          </Field>
          <Field label="Company (optional)" id="pc-company" hint={interrupt.supported_companies.length ? `With data: ${interrupt.supported_companies.join(", ")}` : "No company data is loaded yet"}>
            <Input id="pc-company" value={company} onChange={(e) => setCompany(e.target.value)} />
          </Field>
          <Field label="Topics (comma separated)" id="pc-topics">
            <Input id="pc-topics" value={topics} onChange={(e) => setTopics(e.target.value)} />
          </Field>
          <Field label="Number of questions" id="pc-count">
            <Input id="pc-count" type="number" min={1} max={20} value={count} onChange={(e) => setCount(e.target.value)} />
          </Field>
        </div>
        <div className="space-y-2">
          <p className="text-sm font-medium text-ink-soft">Knowledge bases</p>
          <div className="flex flex-wrap gap-2">
            {["personal", "role", "company"].map((k) => (
              <button key={k} type="button" aria-pressed={kbs.includes(k) || required.includes(k)} onClick={() => toggle(k)} title={required.includes(k) ? "Needed for this kind of request" : undefined} className={`min-h-[44px] rounded-full ${kbs.includes(k) || required.includes(k) ? "" : "opacity-45"}`}>
                <Chip kb={k} className="px-4 py-2 text-sm">{KB_LABEL[k]}</Chip>
              </button>
            ))}
          </div>
        </div>
        <div className="space-y-2">
          <p className="text-sm font-medium text-ink-soft">Mode</p>
          <Segmented label="Mode" value={mode} onChange={setMode} options={[{ value: "question_set", label: "Question set" }, { value: "mock_interview", label: "Mock interview" }]} />
        </div>
        <div className="flex flex-wrap justify-end gap-3 pt-2">
          <Button variant="ghost" disabled={busy} onClick={() => submit(false)}>Cancel</Button>
          <Button loading={busy} onClick={() => submit(true)}>Looks good, start</Button>
        </div>
      </div>
    </Modal>
  );
}
