# CareerPilot

A job-search and interview-preparation copilot built on **LangGraph**. Upload a resume and CareerPilot builds a profile, finds and ranks live jobs with explainable match scores, and prepares you for interviews with questions that cite their sources.

## What it does

| | |
|---|---|
| **Job discovery** | A bounded ReAct agent searches five free job sources through its own MCP server. A model reads each posting into required and nice-to-have skills; **code** (not the model) scores the match against your resume and projects, so every score comes with reasons and missing skills. |
| **Interview prep** | Ask for project, role or company questions, or run a mock interview. A retrieval agent searches three knowledge bases, a generator writes questions with citations, and a grounding guardrail checks them before you see them. |
| **Mock interviews** | Multiple-choice answers are graded in code; free-text answers are graded against key points, with one follow-up per question and a scored report. |
| **Human in the loop** | The graph pauses to confirm its plan or ask a clarifying question, and resumes from MongoDB checkpoints. |
| **Your data stays yours** | JWT auth, per-user and per-track isolation, PII stripped before job scoring, and a "delete my data" action that removes files, records and vectors. |

## Architecture

```
Browser (React + Vite)
   |  HTTPS (Caddy in production, nginx in the frontend container)
FastAPI backend ── JWT auth, per-user isolation, REST + server-sent progress
   |── LangGraph: ingest | discover | prep   (3 graphs, 12 nodes)
   |      |── ReAct agent 1: retrieval agent   (searches the knowledge bases)
   |      '── ReAct agent 2: job-search agent  (calls the MCP server)
   |── LLM: Groq gpt-oss-120b → automatic OpenRouter fallback (Gemini path kept)
   |── Local models: BAAI/bge-base-en-v1.5 embeddings, ms-marco MiniLM cross-encoder
   '── Storage: MongoDB (data + checkpoints + caches), S3 (files), Chroma (vectors)
MCP server (FastMCP, streamable HTTP): 9 tools
   '── RemoteOK, Remotive, Arbeitnow, Jobicy, Himalayas; optional Adzuna and Jooble; GitHub; application tracker
LangSmith: traces, datasets, experiments, live feedback scores
```

| Graph | Nodes | Notes |
|---|---|---|
| ingest | ingest, build_profile | parse, chunk, embed, store; profile by structured output |
| discover | search_jobs (ReAct agent), rank_jobs (`Send` fan-out) | at most 15 postings scored per run; parsed postings cached for 14 days |
| prep | plan_prep, confirm_plan, retrieve (ReAct agent), generate, check_grounding, await_answer, evaluate_answer, report | interrupts for plan confirmation, clarification and interview answers |

**Design choices**
- **Agents:** one shared explicit loop (`app/graph/agents/react_loop.py`) with a step cap, no-progress detection, typed tools and identity bound by code (never a tool argument). Retrieved text and job postings are treated as data, not instructions.
- **Retrieval:** metadata filter, dense search plus BM25 per partition, reciprocal rank fusion, cross-encoder reranking where it helps, per-knowledge-base quotas and de-duplication. Role questions are embedded together with their key points (this fixed paraphrase misses; see Evaluation).
- **Knowledge bases:** personal (your resume and notes), role (629 questions across four roles), company (429 records across six companies).
- **Grounding:** every question carries citations; a judge checks model-written text against the cited record, and curated records copied word for word skip the judge. If nothing can be verified, curated records are shown with an explanation instead of an empty page.
- **Cost control:** a cap on scored postings, a cache of parsed postings, cooldown for failing job sources, local embeddings, a per-user daily limit on AI runs, and a hard cap on the optional paid-quota job API.

## Tech stack

Python 3.11 · FastAPI · LangGraph · LangChain · Groq / OpenRouter / Gemini · sentence-transformers · ChromaDB · MongoDB · S3 · MCP (FastMCP, `langchain-mcp-adapters`) · LangSmith · React · Vite · Tailwind · Docker Compose · AWS (EC2, ECR, S3, Systems Manager Parameter Store) · MongoDB Atlas

## Run it locally

Requirements: Docker Desktop and a Groq API key (free tier works).

```bash
cp .env.example .env            # set GROQ_API_KEY and JWT_SECRET; the rest is optional
docker compose up --build
docker compose exec backend python -m scripts.seed_role_kb
docker compose exec backend python -m scripts.seed_company_kb
```

Open http://localhost:8180 (API on http://localhost:8010). For hot reload use `docker compose -f docker-compose.yml -f docker-compose.dev.yml up`, and `npm run dev` inside `frontend/`.

Running without Docker, or without a key, degrades gracefully: LangSmith, GitHub and the paid job APIs are optional, and `MCP_MODE=local` runs the job tools in-process.

### Key settings

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` | Main model (`GROQ_MODEL`, default `openai/gpt-oss-120b`) |
| `OPENROUTER_API_KEY` | Optional automatic fallback when Groq hits its limit |
| `JWT_SECRET` | Token signing key (32+ characters; required in production) |
| `MONGODB_URI`, `S3_BUCKET`, `S3_ENDPOINT_URL` | Local MongoDB and an S3-compatible store by default; leave `S3_ENDPOINT_URL` empty on AWS |
| `JOBS_API_KEY` (`app_id:app_key`), `JOOBLE_API_KEY` | Optional extra job sources (the Adzuna call count is hard-capped) |
| `LANGSMITH_API_KEY`, `LANGSMITH_TRACING` | Optional tracing and evaluation |
| `APP_ENV=production` | Turns on start-up safety checks (refuses weak secrets, localhost CORS, local databases) |

## Deploy to AWS

One EC2 instance with Docker Compose, images from ECR, MongoDB Atlas, real S3 through an instance role, secrets in Parameter Store (rotatable without a redeploy), HTTPS through Caddy. About $22 a month running all day, or about $6 when the instance is stopped between demos. Full steps, IAM policies, cost control, rollback and cleanup: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Evaluation

Every number can be reproduced with the commands in [docs/VALIDATION.md](docs/VALIDATION.md). The evaluation sets are small (8 to 60 queries) and written by the author, so treat differences of a few points as noise.

| Check | Result |
|---|---|
| Automated tests | 108 pass (`docker compose exec backend python -m pytest -q`) |
| Stack check (health, auth, MongoDB, S3, knowledge bases, retrieval, MCP) | 9 of 9 pass, locally and on the AWS deployment |
| Role retrieval, 49 development queries | Hit@5 0.857 → **1.0** after embedding questions with their key points |
| Role retrieval, 30 held-out queries written after the fix | Hit@5 0.767 → **0.933**; hit@1 0.40 → 0.53 |
| Company knowledge base, 8 queries | Hit@1 1.0 |
| Job ranking, 40 labelled pairs | precision@3 0.958, NDCG@3 0.963 |
| AI grader vs the author's 30 human scores | Spearman 0.857 |
| Tenant isolation | 8 of 8 tests |
| Prompt injection (live) | 0 of 2 hostile inputs obeyed |
| Pause and resume flows (live) | 6 of 6 scenarios |
| HTTP load test (200 requests per endpoint, concurrency 10) | 0 errors, p95 under 21 ms |
| Job scoring cost | at most 15 postings scored (was up to 40), about 62% fewer input tokens; a repeat refresh makes 0 model calls |

**How the retrieval problem was found.** LangSmith experiments labelled each failing query by stage (retrieval, prompt, generation). Four were retrieval misses and none was a prompt drop, because only the short question had been embedded. The fix changed the embedding input only. The development queries cannot prove their own fix, so the held-out set is the fair number.

**Not measured, on purpose:** a hallucination rate for the grounding judge (a test is built, and ran on 5 of 30 examples before the free-tier limit), the live agent evaluation at full size, and behaviour under production traffic. See [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) for the LangSmith work and its limits.

## Repository layout

```
app/            FastAPI app, LangGraph graphs and nodes, agents, retrieval, jobs, MCP server
frontend/       React + Vite UI (nginx image)
data/           knowledge-base seeds, sample resumes, job fallback set
evals/          retrieval, ranking, agent, human-in-the-loop, injection and LangSmith experiments
scripts/        seeding, validation (`validate_stack`), load test, cost report
deploy/         ECR push, EC2 bootstrap, Caddyfile, IAM policies
docs/           spec, features, validation, observability, MCP, deployment
tests/          offline test suite
```

Further reading: [docs/FEATURES.md](docs/FEATURES.md) · [docs/MCP.md](docs/MCP.md) · [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) · [docs/CareerPilot_Project_Spec.pdf](docs/CareerPilot_Project_Spec.pdf)

## Content and data notes

- Seed content lives in `data/role_kb/` and `data/company_kb/`. Only seed material you may reuse. A copyrighted book kept for private study is handled differently: its answer text is stored only as a private grading reference, never displayed or published, and its file is gitignored and excluded from Docker images.
- Company key points are model-written and labelled as such.
- Job postings keep their original links and source labels. LinkedIn and Naukri are not scraped (their terms forbid it); the Jobs page links to their search pages instead.

## Limitations

Small, author-written evaluation sets; the grounding judge is not measured; a single-instance demo deployment; free-tier model limits; resume text is sent to the model provider (and to LangSmith when tracing is on), so real users would need consent and redaction.
