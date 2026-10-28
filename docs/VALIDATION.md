# Numerical validations

Every number below was measured on this project and can be reproduced with the command shown. Eval sets are small (8 to 60 queries), so
treat differences of a few points as noise. "Not measured" lines are listed on purpose.

## Correctness and safety
| What it proves | Result | Pass bar | Command |
|---|---|---|---|
| Automated tests | **102 passed**, 0 failed | all | `python -m pytest -q` |
| One user cannot see another's data | **8 of 8** isolation tests | 100% | `python -m evals.run_evals --offline` |
| Unauthenticated requests are refused | HTTP 401 | 401/403 | `python -m scripts.validate_stack` |
| Prompt injection is ignored | **0 of 2** hostile-input cases obeyed | 0 obeyed | `evals/injection_eval.py` (live, Groq) |
| Pause and resume flows (confirm plan, clarification, mock interview, expired sessions) | **6 of 6** scenarios, expired sessions cannot resume | all | `evals/hitl_eval.py` (live, Groq) |
| Production refuses unsafe settings (default secret, localhost CORS, local DB or store) | 9 of 9 deployment-safety tests | all | `tests/test_deploy.py` |
| Adzuna never exceeds its call cap (free key allows 25) | cap 20 in total, shared across restarts, 7-day cache, India only | never above 20 | `tests/test_jobs.py::test_adzuna_calls_are_capped_cached_and_india_only` |

## Retrieval quality
| Knowledge base | Queries | hit@1 | hit@3 | hit@5 | MRR | Command |
|---|---|---|---|---|---|---|
| Personal (resume), hybrid + rerank | 34 | 0.941 | 0.971 | 1.0 | 0.963 | `python -m evals.retrieval_eval` |
| Role, production setup | 49 | 0.673 | 0.959 | **1.0** | 0.816 | same |
| Company, production setup | 8 | 1.0 | 1.0 | 1.0 | 1.0 | same |
| Role, **held-out** paraphrases (written after the fix) | 30 | **0.533** | 0.833 | **0.933** | 0.684 | `python -m evals.heldout_retrieval` |
| Role, held-out, *before* the fix (question-only embeddings) | 30 | 0.400 | 0.700 | 0.767 | 0.543 | same |

The role result improved because each question is now embedded together with its key points. On the 49-query set (used to find the failures)
Hit@5 went from 0.857 to 1.0. That set cannot prove its own fix, so the 30 held-out queries are the fair number: **0.767 to 0.933**.
No regression elsewhere: exact-question search keeps hit@5 = 1.0 on all 629 role questions, and single rare-word queries keep hit@5 = 0.90.
Known weakness: dense-only search still has a better hit@1 on paraphrases (0.733 vs 0.533 hybrid); keyword search helps exact terms
and costs rank-1 accuracy on paraphrases.

## Ranking and grading
| What | Result | Command |
|---|---|---|
| Job-match ranking, 40 labelled pairs | precision@3 **0.958**, NDCG@3 **0.963** | `python -m evals.run_evals --offline` |
| AI grader vs your 30 human scores | Spearman **0.857** (0.926 vs the intended quality order) | `evals/agreement_eval.py` (live, Groq) |

## Content
| What | Result | Command |
|---|---|---|
| Roles / questions | 4 roles, 629 questions (sde 250, genai 50, aiml 135, devops 194) | `python -m scripts.validate_seeds` |
| Companies / records | 6 companies, 429 records (key points model-written, labelled) | same |
| Live job sources | 5 free sources, 8 to 13 India roles per 25-result search | `python -m scripts.validate_stack` |

## Speed (local, one machine, no model calls)
| Measurement | Result | Command |
|---|---|---|
| HTTP, 200 requests each at concurrency 10 | **0 errors**, p50 5 to 12 ms, p95 13 to 21 ms, 730 to 1,340 requests/s | `python -m scripts.load_test` |
| Retrieval latency (local embeddings on CPU) | role 711 ms, company with reranker 674 ms (median) | same |
| Job search, 5 sources merged | about 4 s when a source is unreachable; a failed source now cools down for 120 s so it no longer repeats the timeout | same |
| Job scoring per discovery run | at most 15 postings are scored (was up to 40): about **62% fewer input tokens**; a repeat refresh makes **0** model calls (cache) | `tests/test_jobs.py`, 499 live postings measured |

Correction: capping each posting at 3,500 characters saves nothing today, because the feeds already cut descriptions to 3,000 characters
(none of 499 postings was longer). The savings come from the 15-posting cap and the cache.

## Stack check (any environment)
`python -m scripts.validate_stack` prints a PASS/FAIL table; on the local stack **9 of 9** pass: API health (20 requests, p50 1 ms), sign-up and sign-in round trip,
MongoDB ping (4 ms), S3 round trip (185 ms), 4 roles and 6 companies, 629 and 429 vector records, a retrieval spot check, the production-settings guard, and an MCP job search.

## Not measured, or only partly (and why)
- **Hallucination detection (grounding judge).** Built (`evals/langsmith_groundedness.py`); only 5 of 30 examples ran before Groq's free daily limit (200,000 tokens) was used up: all 5 seeded hallucinations were caught, but that is far too few to report a recall.
- **Generation stage of the RAG diagnosis.** Built (`evals/langsmith_rag_diagnosis.py`); 3 of 7 examples generated: in all 3 the correct record was in the prompt at rank 2 to 3 but the model chose other records for its questions. Preliminary.
- **Live agent and generation evals.** The full agent eval is about 600 model calls and cannot run on a free tier; `AGENT_EVAL_LIMIT` / `AGENT_EVAL_RUNS` shrink it. Not run today.
- **Effect of low reasoning effort on job parsing.** Implemented, not yet measured.
- **Cloud deployment (AWS, Atlas, HTTPS).** Package and guide are ready; not yet run on real accounts.
