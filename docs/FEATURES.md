# What CareerPilot does today

## Accounts and data
- Register and log in (JWT). Every user's data is isolated by user and by track; ownership checks return 404 for anything not yours.
- Career tracks with a target role chosen from the supported roles (aliases such as "ai intern" resolve to the right role).
- Delete my data: files, database records and vectors for a track.

## Resume and profile
- Upload PDF or DOCX resumes (kept in S3), parsed and chunked, embedded locally and stored per user and track.
- GitHub: reads public repositories (README, file tree, files) through our MCP server, indexed with the resume.
- A profile (skills, projects, level) built from the resume; PII is stripped before anything is used for job scoring.

## Job discovery
- A job-search agent (ReAct loop with a step cap) calls the MCP server, which merges **five free sources** (RemoteOK, Remotive, Arbeitnow, Jobicy, Himalayas), plus Adzuna and Jooble when keyed.
- India is the default location; every job shows its source and links back to the original listing; sample data is labelled when no live source answers.
- Explainable match scores (required and nice-to-have skills versus your resume and notes), synonyms handled, soft skills ignored; partial fits shown when few clear the bar.
- Parsed postings are cached; at most 15 postings are scored per run; Refresh prompt when your resume changes.
- Links to LinkedIn, Naukri, Indeed, Foundit and Instahyre searches (no scraping).
- Application tracker (status per job).

## Interview preparation
- Question sets and mock interviews (multiple choice graded in code, free text graded by the model against key points, one follow-up per question).
- Project questions, role questions and company questions (Amazon, Apple, Google, Meta, Microsoft, Netflix).
- Reports with scores per key point.
- Plan confirmation and clarification pauses (human in the loop), resumable from MongoDB checkpoints; interrupted runs are marked failed on restart.

## Knowledge and retrieval
- Three knowledge bases: personal (your resume and notes), role (629 curated questions in 4 roles), company (429 records in 6 companies).
- Hybrid retrieval: embeddings plus keyword search fused with RRF, a reranker where it helps, quotas per knowledge base, de-duplication.
- Role questions are embedded together with their key points (measured to find paraphrased questions better).

## Quality and safety
- Every question carries citations; a grounding guardrail checks citations and, for model-written text, asks a judge whether each key point is supported; curated records copied word for word skip the judge.
- If nothing can be verified, curated records are shown with an explanation instead of an empty page.
- Prompt-injection structure (retrieved text and job postings are treated as data), per-user isolation tests, a private grading reference that is never shown.

## Platform
- Three LangGraph graphs (ingest, discover, prep), 12 nodes, two bounded ReAct agents.
- Our own MCP server (FastMCP) with nine tools; see `docs/MCP.md`.
- Models: Groq (main), OpenRouter (automatic fallback, optional), Gemini path kept; short rate limits are waited out.
- LangSmith tracing, feedback scores on live traces, datasets and experiments; see `docs/OBSERVABILITY.md`.
- React and Vite glass-style UI with live progress, FastAPI backend, MongoDB, S3-compatible storage, Chroma.
- Docker Compose for local use and a production package for AWS (`docs/DEPLOYMENT.md`), GitHub Actions tests, 102 automated tests and an eval harness (`docs/VALIDATION.md`).
