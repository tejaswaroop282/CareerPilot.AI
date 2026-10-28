import { Chip, GlassCard, ScoreRing } from "./ui";

/** Feedback for one interview answer: scores plus covered (green) and missed (rose) key points by index. */
export default function ScoreCard({ score, keyPoints }) {
  const avg = (score.correctness + score.depth + score.clarity) / 30;
  return (
    <GlassCard className="space-y-4">
      <div className="flex items-center gap-4">
        <ScoreRing value={avg} size={72} />
        <div className="flex flex-wrap gap-2 text-sm">
          <Chip>Correctness {score.correctness}/10</Chip>
          <Chip>Depth {score.depth}/10</Chip>
          <Chip>Clarity {score.clarity}/10</Chip>
        </div>
      </div>
      <p>{score.feedback}</p>
      <ul className="space-y-1.5">
        {keyPoints.map((k, i) => {
          const covered = score.covered_points.includes(i);
          return (
            <li key={i} className={`flex gap-2 rounded-md border px-3 py-2 text-sm ${covered ? "border-ok/40 bg-ok/10" : "border-danger/40 bg-danger/10"}`}>
              <span className={`font-semibold ${covered ? "text-ok" : "text-danger"}`}>{covered ? "Covered" : "Missed"}</span>
              <span>{k}</span>
            </li>
          );
        })}
      </ul>
    </GlassCard>
  );
}
