import { motion } from "framer-motion";
import { ExternalLink, MapPin, Sparkles, X } from "lucide-react";
import { Button, Chip, GlassCard, IconButton, ScoreRing } from "./ui";

/** A ranked job match: company, title, location, score ring, reasons, missing skills, Prepare and Apply. */
export default function JobCard({ match, index, onPrepare, onApplied, onDismiss }) {
  const j = match.job;
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2, delay: Math.min(index * 0.04, 0.4) }}>
      <GlassCard lift className="flex h-full flex-col gap-4">
        <div className="flex items-start gap-4">
          <div className="min-w-0 flex-1">
            <p className="text-sm text-ink-soft">{j.company}{j.site && <span> · via {j.site}</span>}</p>
            <h3 className="text-lg leading-snug">{j.title}</h3>
            <p className="mt-1 flex items-center gap-1 text-sm text-ink-soft"><MapPin className="h-3.5 w-3.5" aria-hidden />{j.location}</p>
          </div>
          <ScoreRing value={match.score} label={`Match score ${Math.round(match.score * 100)} percent`} />
          <IconButton label="Dismiss this job" icon={X} onClick={() => onDismiss(match.job_id)} className="-mr-2 -mt-2" />
        </div>
        {match.fit_note && <p className="text-sm text-ink-soft">{match.fit_note}</p>}
        <div className="flex flex-wrap gap-2">
          {match.reasons.slice(0, 4).map((r) => <Chip key={r} tone="ok">{r}</Chip>)}
          {match.missing.slice(0, 4).map((m) => <Chip key={m} muted>Missing: {m}</Chip>)}
        </div>
        <div className="mt-auto flex flex-wrap gap-3 pt-2">
          <Button icon={Sparkles} onClick={() => onPrepare(match)}>Prepare</Button>
          <a
            href={j.url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={() => onApplied(match)}
            className="btn glass-1 inline-flex min-h-[44px] items-center gap-2 rounded-md px-5 font-medium hover:bg-[var(--glass-2)]"
          >
            Apply <ExternalLink className="h-4 w-4" aria-hidden />
            <span className="sr-only">(opens the original listing in a new tab)</span>
          </a>
        </div>
      </GlassCard>
    </motion.div>
  );
}
