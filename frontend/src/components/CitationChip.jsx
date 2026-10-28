import { Chip, KB_LABEL, Tip } from "./ui";

/** A citation: chunk id chip coloured by knowledge base, with a hover/focus preview of the cited snippet. */
export default function CitationChip({ id, kb, chunk }) {
  const preview = chunk ? (
    <div className="space-y-1">
      <p className="font-mono text-[11px] text-ink-soft">{chunk.header || id}</p>
      <p>{chunk.snippet}</p>
    </div>
  ) : (
    <p>{id}</p>
  );
  return (
    <Tip content={preview}>
      <button type="button" className="rounded-full focus-visible:outline-offset-2" aria-label={`Citation ${id} from ${KB_LABEL[kb] || "source"}`}>
        <Chip kb={kb} className="font-mono">
          {KB_LABEL[kb] || kb} · {id.length > 22 ? `${id.slice(0, 20)}…` : id}
        </Chip>
      </button>
    </Tip>
  );
}
