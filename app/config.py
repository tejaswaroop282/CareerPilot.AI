"""Settings and env loading. Retrieval defaults and agent limits live here so evals can vary them."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"  # "production" turns on the start-up safety checks below
    secrets_backend: str = "env"  # "ssm": LLM keys are read from AWS Parameter Store at run time (see app/secrets.py)
    secrets_prefix: str = "/careerpilot"
    user_daily_runs: int = 20  # AI runs (prep or job refresh) one user may start per day; 0 turns the limit off
    log_format: str = "text"  # "json" for CloudWatch and other log collectors
    # LLM: chat provider is groq, gemini or openrouter (an OpenRouter key also acts as the Groq fallback); embeddings are local (sentence-transformers) or gemini
    llm_provider: str = "gemini"
    groq_api_key: str = ""
    groq_api_keys: str = ""  # optional extra Groq keys, comma separated, tried in order when one is rate limited (limits are per organisation)
    groq_model: str = "openai/gpt-oss-120b"
    openrouter_api_key: str = ""  # optional: used when Groq is rate limited, or as the main model (LLM_PROVIDER=openrouter)
    openrouter_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    groq_fallback_models: str = ""  # optional, comma separated; off by default (gpt-oss-20b returned malformed structured output)
    embedding_provider: str = "gemini"
    local_embedding_model: str = "BAAI/bge-base-en-v1.5"
    google_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    gemini_embedding_model: str = "gemini-embedding-001"

    # LangSmith
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "careerpilot"

    # External
    github_token: str = ""
    jobs_api_key: str = ""  # Adzuna: "app_id:app_key"; not needed for RemoteOK
    jobs_default_location: str = "India"  # used when the job agent passes no location; empty means no preference
    jobs_adzuna_max_calls: int = 20  # hard cap on Adzuna API calls in total (the free key allows 25); counted in Mongo
    jooble_api_key: str = ""  # optional, free on request at jooble.org/api/about; good India coverage
    jobs_source: str = "auto"  # auto (all free feeds, plus Adzuna/Jooble when keyed) | comma list, e.g. remoteok,remotive | fallback
    jobs_country: str = "in"  # Adzuna market
    mcp_server_url: str = "http://localhost:8001/mcp"
    mcp_mode: str = "auto"  # auto: use the MCP server, fall back to in-process; local: always in-process

    # Storage
    chroma_dir: str = "./.chroma"
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "careerpilot"
    s3_bucket: str = "careerpilot-data"
    s3_endpoint_url: str = ""
    aws_region: str = "ap-south-1"
    minio_root_user: str = "minioadmin"
    minio_root_password: str = "minioadmin"

    manifest_path: str = "data/kb_manifest.json"

    # Auth / API
    jwt_secret: str = "change-me"
    jwt_expire_minutes: int = 60 * 12
    cors_origins: str = "http://localhost:5173"
    app_version: str = "0.1.0"

    # Reranker
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    hf_home: str = "./.hf_cache"

    # Agents and sessions
    agent_max_steps_retrieval: int = 5
    agent_max_steps_jobs: int = 4
    agent_temperature: float = 0.0
    session_expiry_hours: int = 24
    jobs_top_n: int = 20
    jobs_min_score: float = 0.5
    jobs_score_limit: int = 15  # postings scored per discovery run (each costs one model call)
    job_analysis_cache: bool = True  # reuse a posting's parsed skills instead of one model call per posting on every refresh
    llm_effort_extraction: str = "low"  # reasoning effort for plain extraction steps (job parsing); '' keeps the model default
    jobs_scoring_concurrency: int = 3  # parallel per-job scoring calls (keeps within per-minute token limits)
    jobs_min_shown: int = 5  # when fewer jobs clear jobs_min_score, the best partial fits are added up to this many
    jobs_floor_score: float = 0.15  # ...but never below this score

    # Retrieval defaults
    dense_top_k: int = 20
    bm25_top_k: int = 20
    rrf_k: int = 60
    rrf_dense_weight: float = 3.0  # dense list weight in RRF (BM25 weight is 1); tuned on evals/datasets
    role_dense_weight: float = 20.0  # role questions are short and queries paraphrase them; BM25 adds little (evals sweep)
    rerank_role: bool = False  # role questions are short records; measured: reranking does not help them (evals)
    rerank_blend: bool = True  # fuse the cross-encoder order with the first-stage order instead of replacing it
    rerank_candidates: int = 30
    retrieval_final_k: int = 8
    quota_company_prep: dict[str, int] = {"company": 5, "role": 3, "personal": 2}
    quota_role_questions: dict[str, int] = {"role": 8, "personal": 2}
    quota_project_questions: dict[str, int] = {"personal": 7, "role": 3}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


def production_problems(s: "Settings") -> list[str]:
    """Settings that must never reach a production deployment (checked when APP_ENV=production)."""
    problems = []
    if s.jwt_secret in ("", "change-me-in-real-envs") or len(s.jwt_secret) < 32:
        problems.append("JWT_SECRET must be a random value of at least 32 characters")
    if any(h in s.cors_origins for h in ("localhost", "127.0.0.1")):
        problems.append("CORS_ORIGINS must list the public site address, not localhost")
    if s.mongodb_uri.startswith(("mongodb://mongo", "mongodb://localhost", "mongodb://127.0.0.1")):
        problems.append("MONGODB_URI points at a local database; use the MongoDB Atlas connection string")
    if any(h in s.s3_endpoint_url for h in ("minio", "localhost", "127.0.0.1", "rustfs")):
        problems.append("S3_ENDPOINT_URL points at a local store; leave it empty on AWS")
    if s.llm_provider == "groq" and not s.groq_api_key and s.secrets_backend != "ssm":
        problems.append("GROQ_API_KEY is empty")
    return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
