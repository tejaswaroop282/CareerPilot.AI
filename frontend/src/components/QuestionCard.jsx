import { motion } from "framer-motion";
import { ChevronDown, ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";
import CitationChip from "./CitationChip";
import { Chip, GlassCard, IconButton, KB_LABEL } from "./ui";

/** One generated question: key points collapsed by default, labelled citation chips, thumbs feedback. */
export default function QuestionCard({ item, index, chunks, onFeedback }) {
  const [open, setOpen] = useState(false);
  const [voted, setVoted] = useState(null);
  const byId = Object.fromEntries((chunks || []).map((c) => [c.chunk_id, c]));
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2, delay: Math.min(index * 0.05, 0.4) }}>
      <GlassCard lift className="space-y-3">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 font-mono text-sm text-ink-soft">{String(index + 1).padStart(2, "0")}</span>
          <div className="flex-1 space-y-2">
            <p className="text-base font-medium leading-snug">{item.question}</p>
            <div className="flex flex-wrap items-center gap-2">
              <Chip kb={item.kb}>{KB_LABEL[item.kb]}</Chip>
              {item.topic && <Chip>{item.topic}</Chip>}
              {item.difficulty && <Chip>{item.difficulty}</Chip>}
              {item.kind === "mcq" && <Chip tone="info">Multiple choice</Chip>}
            </div>
          </div>
          <div className="flex">
            <IconButton label="Helpful" icon={ThumbsUp} aria-pressed={voted === 1} className={voted === 1 ? "text-ok" : ""} onClick={() => { setVoted(1); onFeedback?.(index, 1); }} />
            <IconButton label="Not helpful" icon={ThumbsDown} aria-pressed={voted === 0} className={voted === 0 ? "text-danger" : ""} onClick={() => { setVoted(0); onFeedback?.(index, 0); }} />
          </div>
        </div>

        {item.kind === "mcq" && (
          <ul className="space-y-1 pl-9 text-ink-soft">
            {item.options.map((o, i) => (
              <li key={i}>
                {String.fromCharCode(65 + i)}. {o}
              </li>
            ))}
          </ul>
        )}

        <button onClick={() => setOpen(!open)} aria-expanded={open} className="ml-9 inline-flex min-h-[44px] items-center gap-1 text-sm text-ink-soft hover:text-ink">
          <ChevronDown className={`h-4 w-4 transition ${open ? "rotate-180" : ""}`} aria-hidden />
          {open ? "Hide key points" : `Show ${item.key_points.length} key points`}
        </button>
        {open && (
          <ul className="scrim ml-9 list-disc space-y-1 p-4 pl-8">
            {item.key_points.map((k, i) => (
              <li key={i}>{k}</li>
            ))}
          </ul>
        )}
        <div className="ml-9 flex flex-wrap gap-2">
          {item.citations.map((c) => (
            <CitationChip key={c} id={c} kb={byId[c]?.kb || item.kb} chunk={byId[c]} />
          ))}
        </div>
      </GlassCard>
    </motion.div>
  );
}
