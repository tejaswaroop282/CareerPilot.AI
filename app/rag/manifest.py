"""kb_manifest.json: supported roles and companies, aliases and counts.

Source of truth is S3 (global/manifests/kb_manifest.json); a local copy at MANIFEST_PATH is used when S3 has
none (offline dev) and is written by the seed scripts.
{"roles": {role: {"aliases": [...], "topics": [...], "questions": n, "resources": n}},
 "companies": {company: {"aliases": [...], "roles": {role: n}, "records": n}}}
"""
import json
import re
import time
from pathlib import Path

from app.config import get_settings
from app.storage import keys, s3

_TTL = 30.0
_cache: tuple[float, dict] | None = None

EMPTY: dict = {"roles": {}, "companies": {}}


def local_path() -> Path:
    return Path(get_settings().manifest_path)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9+#]+", " ", s.lower()).strip()


def load_manifest(force: bool = False) -> dict:
    global _cache
    if not force and _cache and time.monotonic() - _cache[0] < _TTL:
        return _cache[1]
    manifest: dict | None = None
    try:
        manifest = json.loads(s3.get_object(keys.MANIFEST_KEY))
    except Exception:
        p = local_path()
        if p.exists():
            manifest = json.loads(p.read_text(encoding="utf-8"))
    manifest = manifest or {"roles": {}, "companies": {}}
    _cache = (time.monotonic(), manifest)
    return manifest


def save_manifest(manifest: dict) -> None:
    """Write the manifest to S3 and the local copy, and reset the cache."""
    global _cache
    body = json.dumps(manifest, indent=2, ensure_ascii=False)
    p = local_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    s3.put_object(keys.MANIFEST_KEY, body.encode(), "application/json")
    _cache = None


_LEVEL_WORDS = {"intern", "internship", "trainee", "fresher", "junior", "jr", "senior", "sr", "lead", "associate", "entry", "level", "i", "ii"}


def _match(table: dict, n: str) -> str | None:
    for key, info in table.items():
        if n == norm(key) or n in {norm(a) for a in info.get("aliases", [])}:
            return key
    return None


def _resolve(table: dict, name: str | None) -> str | None:
    if not name:
        return None
    n = norm(name)
    hit = _match(table, n)
    if hit:
        return hit
    stripped = " ".join(w for w in n.split() if w not in _LEVEL_WORDS)  # "ai engineer intern" -> "ai engineer"
    return _match(table, stripped) if stripped and stripped != n else None


def resolve_role(name: str | None) -> str | None:
    return _resolve(load_manifest()["roles"], name)


def resolve_company(name: str | None) -> str | None:
    """Map a company name (e.g. 'amazon web services') to its manifest key ('amazon') using aliases."""
    return _resolve(load_manifest()["companies"], name)


def supported_roles() -> list[str]:
    return sorted(load_manifest()["roles"])


def supported_companies(role: str | None = None) -> list[str]:
    companies = load_manifest()["companies"]
    if role:
        r = resolve_role(role)
        return sorted(c for c, info in companies.items() if r in info.get("roles", {}))
    return sorted(companies)
