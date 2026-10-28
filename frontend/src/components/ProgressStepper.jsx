import { Stepper } from "./ui";

const PREP = [
  ["plan_prep", "Planning"],
  ["retrieve", "Retrieving"],
  ["generate", "Writing questions"],
  ["check_grounding", "Checking grounding"],
];

/** Maps streamed node names to a stepper. `nodes` is the list of completed nodes. */
export default function ProgressStepper({ nodes, steps = PREP, done }) {
  const finished = steps.filter(([n]) => nodes.includes(n)).length;
  return <Stepper steps={steps.map((s) => s[1])} current={finished} done={done} />;
}

export const DISCOVER_STEPS = [
  ["search_jobs", "Searching jobs"],
  ["rank_jobs", "Ranking matches"],
];
