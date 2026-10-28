# Observability and evaluation with LangSmith

What was done, what it found, and what can honestly be claimed. Numbers come from named LangSmith experiments in the `careerpilot`
project or from commands in this repository (see `docs/VALIDATION.md`).

## What is traced
- Every graph run (ingest, discover, prep) is traced automatically (`LANGSMITH_TRACING=true`): each node, tool call, retrieval and model call with inputs, outputs and latency.
- Added spans and metadata (`app/observability.py`): the exact retrieved chunk ids and scores, the size and chunk ids of the generation prompt, the grounding verdict.
- Live feedback on real traffic: the grounding check attaches `grounded`, `citation_valid` and `grounding_retries` to the trace, in the background, never blocking a user.
- User thumbs up and down are attached to the run as well.
- Tracing is off in tests; if LangSmith is unreachable the app is unaffected.

## Issue 1: why did role-question answers go wrong?
Starting point: role retrieval Hit@5 of 0.857 on 49 queries. That number says nothing about where a failure comes from, so each query was run through
the real stages (retrieve, build prompt, generate, judge) as a LangSmith experiment, labelling each example `ok`, `ranking_near_miss`,
`retrieval_miss`, `prompt_drop` or `generation_error` (`evals/langsmith_rag_diagnosis.py`).

| Experiment | Result on 49 queries |
|---|---|
| `role-rag-baseline-0d3fdb35` (question-only embeddings) | 42 ok, 3 near misses (rank 6 to 7, still inside the 8 chunks the model sees), 4 retrieval misses, 0 prompt drops |
| `role-rag-keypoints-3a6a76ed` (question and key points embedded and indexed for dense and keyword search) | 49 ok |
| `role-rag-kp_dense-55fb20fb` (only the embedding sees key points; stored text unchanged) | 49 ok |
| `role-rag-baseline-1c70c760` (production after the change) | 49 ok |

Finding: the failures were in **retrieval**. Only the short question was embedded, so queries describing the idea ("why does adding page frames
sometimes increase page faults") missed "What is Belady's anomaly?". Prompt construction dropped nothing. The fix was applied to the embedding only
(stored text and keyword index unchanged, so the UI and scoring are unaffected).

How it was checked, and the honest limits:
- The 49 queries were used to find the problem, so they cannot also prove the fix. On **30 new held-out queries** (`evals/heldout_retrieval.py`): Hit@5 **0.767 to 0.933**, hit@1 0.400 to 0.533, MRR 0.543 to 0.684.
- No regression on exact-question search (hit@5 1.0 on all 629 questions) or single rare-word queries (hit@5 0.90).
- Queries were written by the developer, not collected from users; 30 and 49 are small samples.
- The first local look at the failures used a plain script; LangSmith's part was the stage-labelled experiments, the traces, and the side-by-side comparison of variants.
- One early experiment run mislabelled rate-limit crashes as retrieval misses; it was fixed so a failed model call is reported as "generation unavailable".
- Preliminary, generation stage: of the 7 formerly failing queries only 3 generated before the free-tier daily limit ended. In all 3 the correct record was in the prompt at rank 2 to 3, but the model wrote its questions from other records. This points at how the generator selects among relevant chunks, not at retrieval, but 3 examples are not a conclusion.

## Issue 2: detecting unsupported answers
Built, not finished. `evals/langsmith_groundedness.py` tests the grounding judge as a classifier: supported items must not be flagged, and
three kinds of seeded hallucination (a claim from an unrelated record, a wrong citation, an invented statistic) must be flagged; it reports
precision, recall, false-positive rate and recall per kind, and can compare a `strict` judge prompt as a second experiment.
Only 5 of 30 examples completed before Groq's daily limit; all 5 hallucinations were caught (3 of 3 foreign claims, 1 of 1 invented statistic, 1 of 1 wrong citation),
which is too few to report as a rate. The live `grounded` feedback also exists, but there is not enough real traffic yet to chart.

## Latency and cost
Measured without model calls (`scripts/load_test.py`, 499 live postings):
- HTTP: 0 errors in 1,000 requests, p95 under 21 ms; retrieval about 700 ms per query on CPU (query embedding dominates).
- Job scoring: the agent could pass up to 40 postings to scoring, one model call each; now at most 15 (**62% fewer input tokens per run**), parsed postings are cached for two weeks (**a repeat refresh makes 0 model calls**), and concurrency is capped so per-minute token limits are not exceeded.
- A failing job source cools down for 120 s instead of repeating a 4 s timeout on every search.
- The grounding judge is skipped for curated records copied verbatim.
- Not yet measured: the effect of low reasoning effort on job parsing, and per-node token shares. LangSmith's token counts were missing for many agent calls, so `scripts/langsmith_cost_report.py` under-reports them; it is reliable for latency and for calls that carry usage.
- A correction: truncating postings to 3,500 characters saves nothing, since feeds already cut them to 3,000.

## How good is LangSmith as the observability layer here?
Good for LLM-specific needs: traces, datasets, experiments with side-by-side comparison, feedback. Weaker: incomplete token usage on some calls, query paging limits and timeouts when pulling many runs,
and it sends prompts (including resume text) to a third party, so real user data would need redaction or a self-hosted setup. It is not a metrics or alerting system; use CloudWatch for CPU, memory and uptime.

## What can be claimed
- Used LangSmith experiments to attribute RAG failures to a stage and found them in retrieval; changed the embedding to include key points; Hit@5 on a held-out set rose from 0.77 to 0.93.
- Added live grounding feedback and a framework for testing the hallucination judge.
- Do not claim a measured hallucination rate, a latency or cost percentage beyond those listed, or production traffic results.

## Reproduce
```bash
python -m evals.langsmith_rag_diagnosis --variant baseline --generate none      # stages, no model calls
python -m evals.heldout_retrieval                                               # held-out check, no model calls
python -m evals.langsmith_groundedness --prompt baseline                        # judge as a classifier (about 30 model calls)
python -m scripts.langsmith_cost_report --hours 24 --flow prep                  # latency and tokens per node
python -m scripts.load_test                                                     # HTTP, retrieval, job-search latency
```
