import { useState } from "react";
import { Button, Field, GlassPanel, Input } from "./ui";

/** A single clarification question (from plan_prep or from the retrieval agent's ask_user). */
export default function ClarifyCard({ interrupt, busy, onSubmit }) {
  const [text, setText] = useState("");
  return (
    <GlassPanel className="mx-auto max-w-xl space-y-4">
      <h2 className="text-xl">One quick question</h2>
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) onSubmit({ text: text.trim() });
        }}
      >
        <Field label={interrupt.question} id="clarify">
          <Input id="clarify" autoFocus value={text} onChange={(e) => setText(e.target.value)} placeholder="Type your answer" />
        </Field>
        <div className="flex justify-end">
          <Button type="submit" loading={busy} disabled={!text.trim()}>Continue</Button>
        </div>
      </form>
    </GlassPanel>
  );
}
