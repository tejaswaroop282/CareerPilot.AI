import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.api import routes_auth, routes_files, routes_ingest, routes_jobs, routes_kb, routes_prep, routes_sessions, routes_tracks
from app.config import get_settings, production_problems
from app.db.client import close_client, ensure_indexes, get_db
from app.db.repos import fail_orphaned_sessions
from app.rag.llm import configure_tracing
from app.secrets import get_secret
from app.storage import s3



class JsonFormatter(logging.Formatter):
    """One JSON object per line on stdout, which CloudWatch and similar collectors parse without configuration."""

    def format(self, record: logging.LogRecord) -> str:
        out = {"time": self.formatTime(record), "level": record.levelname, "logger": record.name, "message": record.getMessage()}
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False)


settings = get_settings()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
if settings.log_format == "json":
    for handler in logging.getLogger().handlers:
        handler.setFormatter(JsonFormatter())

configure_tracing()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.app_env == "production" and (problems := production_problems(settings)):
        raise RuntimeError("Refusing to start in production: " + "; ".join(problems))
    await ensure_indexes()
    if (orphaned := await fail_orphaned_sessions()):
        logging.getLogger(__name__).warning("marked %d run(s) failed: the server restarted while they were running", orphaned)
    await asyncio.to_thread(s3.ensure_bucket)
    yield
    await close_client()


app = FastAPI(title="CareerPilot", version=settings.app_version, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
for r in (routes_auth.router, routes_tracks.router, routes_files.router, routes_ingest.router, routes_jobs.router, routes_kb.router, routes_prep.router, routes_sessions.router):
    app.include_router(r)


@app.get("/ready")
async def ready() -> JSONResponse:
    """Readiness for load balancers and orchestrators: can this instance actually serve? No model call is made."""
    checks: dict[str, str] = {}

    async def check(name: str, fn) -> None:
        try:
            await fn()
            checks[name] = "ok"
        except Exception as e:  # noqa: BLE001
            checks[name] = f"failed: {type(e).__name__}"

    async def mongo() -> None:
        await get_db().command("ping")

    async def bucket() -> None:
        await asyncio.to_thread(s3.get_s3().head_bucket, Bucket=settings.s3_bucket)

    await check("mongodb", mongo)
    await check("s3", bucket)
    if settings.llm_provider == "groq":
        has_key = bool(get_secret("GROQ_API_KEY", settings.groq_api_key))
        checks["llm_key"] = "ok" if has_key else "failed: no Groq key"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse({"status": "ready" if ok else "not ready", "checks": checks}, status_code=200 if ok else 503)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": settings.app_version}
