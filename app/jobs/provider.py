"""Jobs data source behind the MCP search_jobs / get_job tools.

Live sources, all free and without a key: RemoteOK, Remotive, Arbeitnow, Jobicy and Himalayas (feeds cached per source and
filtered locally). Optional keyed sources: Adzuna (JOBS_API_KEY="app_id:app_key", JOBS_COUNTRY for the market) and Jooble
(JOOBLE_API_KEY; strong India coverage). Sources are fetched in parallel, a source that fails is skipped, results are
merged, de-duplicated and ranked by overlap with the query. If every live source fails or finds nothing, the local sample
dataset (data/jobs_fallback.json) is used and each such job says source='fallback' so the UI can show a Sample data badge.
These sites ask for attribution and a link back: every job keeps its original url and a `site` name. LinkedIn and Naukri
have no free public API and forbid scraping, so they are not queried here (the API offers search links instead).
"""
import hashlib
import html
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)
FALLBACK_PATH = Path(__file__).resolve().parents[2] / "data" / "jobs_fallback.json"
USER_AGENT = "CareerPilot/0.1 (interview-prep copilot)"
FREE_SOURCES = ("remoteok", "remotive", "arbeitnow", "jobicy", "himalayas")  # remotive and himalayas are searched per query
CACHE_SECONDS = {"remoteok": 900, "remotive": 6 * 3600, "arbeitnow": 3600, "jobicy": 3600, "himalayas": 3600}  # Remotive asks for at most ~4 calls a day
COUNTRY_CODES = {"india": "IN", "united states": "US", "usa": "US", "united kingdom": "GB", "uk": "GB", "germany": "DE",
                 "canada": "CA", "australia": "AU", "singapore": "SG"}
SITE_NAMES = {"remoteok": "RemoteOK", "remotive": "Remotive", "arbeitnow": "Arbeitnow", "jobicy": "Jobicy",
              "himalayas": "Himalayas", "adzuna": "Adzuna", "jooble": "Jooble"}
ID_PREFIX = {"remoteok": "rok-", "remotive": "rem-", "arbeitnow": "arb-", "jobicy": "jby-", "himalayas": "him-"}


class LiveJobsUnavailable(Exception):
    pass


@lru_cache
def _fallback_jobs() -> list[dict]:
    return json.loads(FALLBACK_PATH.read_text(encoding="utf-8"))


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9+#]+(?:\.[a-z0-9+#]+)*", text.lower()) if len(t) >= 2}  # keeps node.js, drops a trailing full stop


def _clean(text: str) -> str:
    text = re.sub(r"<(br|/p|/li|/h\d)\s*/?>", "\n", text or "", flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", html.unescape(text))).strip()


def _join(value) -> str:
    return " ".join(str(v) for v in value) if isinstance(value, list) else str(value or "")


def _score(job: dict, query: str, location: str | None) -> int:
    q = _tokens(query)
    hay = _tokens(f"{job['title']} {job.get('tags', '')} {job['description']} {job['company']}")
    s = len(q & hay) + 2 * len(q & _tokens(job["title"]))
    if location:  # a listing in the wanted place beats a remote one, which beats an on-site job elsewhere
        where = job["location"].lower()
        if location.lower() in where:
            s += 4
        elif any(w in where for w in ("remote", "worldwide", "anywhere", "global")):
            s += 2
    return s


def _short_id(prefix: str, raw: str) -> str:
    return prefix + hashlib.sha1(raw.encode()).hexdigest()[:12]


def _job(source: str, job_id: str, title: str, company: str, location: str, category: str, description: str,
         tags: str, url: str) -> dict:
    return {
        "id": job_id, "title": _clean(title), "company": company or "Unknown", "location": location or "Remote",
        "category": category, "description": _clean(description)[:3000], "tags": tags, "url": url,
        "source": "live", "site": SITE_NAMES[source],
    }


# ---- free feeds (fetched whole, cached, filtered locally) --------------------

_cache: dict[str, tuple[float, list[dict]]] = {}
_seen: dict[str, dict] = {}  # every job returned by a search, so get_job can find it again


def _get_json(url: str, source: str, **params):
    try:
        r = httpx.get(url, params=params or None, headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True)
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001 - every network or parse failure means "this source is unavailable"
        raise LiveJobsUnavailable(f"{SITE_NAMES[source]}: {type(e).__name__}") from e


def _load_remoteok() -> list[dict]:
    rows = [x for x in _get_json("https://remoteok.com/api", "remoteok") if isinstance(x, dict) and x.get("id") and x.get("position")]
    return [
        _job("remoteok", f"rok-{x['id']}", x["position"], x.get("company"), x.get("location"), "remote",
             x.get("description", ""), " ".join(x.get("tags") or []), x.get("url") or x.get("apply_url") or "https://remoteok.com")
        for x in rows
    ]


def _load_arbeitnow() -> list[dict]:
    rows = _get_json("https://www.arbeitnow.com/api/job-board-api", "arbeitnow").get("data", [])
    return [
        _job("arbeitnow", f"arb-{x['slug']}"[:80], x.get("title", ""), x.get("company_name"),
             x.get("location") or ("Remote" if x.get("remote") else ""), "", x.get("description", ""),
             _join(x.get("tags")) + " " + _join(x.get("job_types")), x.get("url", ""))
        for x in rows if x.get("slug") and x.get("title")
    ]


def _load_jobicy() -> list[dict]:
    rows = _get_json("https://jobicy.com/api/v2/remote-jobs", "jobicy", count=100).get("jobs", [])
    return [
        _job("jobicy", f"jby-{x['id']}", x.get("jobTitle", ""), x.get("companyName"), _join(x.get("jobGeo")),
             _join(x.get("jobIndustry")), x.get("jobDescription") or x.get("jobExcerpt", ""),
             _join(x.get("jobType")) + " " + _join(x.get("jobLevel")), x.get("url", ""))
        for x in rows if x.get("id") and x.get("jobTitle")
    ]


FEEDS = {"remoteok": _load_remoteok, "arbeitnow": _load_arbeitnow, "jobicy": _load_jobicy}


def _feed(source: str) -> list[dict]:
    hit = _cache.get(source)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS[source]:
        return hit[1]
    jobs = FEEDS[source]()
    _cache[source] = (time.monotonic(), jobs)
    return jobs


def _search_feed(source: str, query: str, location: str | None, limit: int) -> list[dict]:
    scored = [(_score(j, query, location), j["id"], j) for j in _feed(source)]
    return [j for s, _, j in sorted((t for t in scored if t[0] > 0), key=lambda t: (-t[0], t[1]))[:limit]]


# ---- keyed query APIs ----------------------------------------------------------

def _search_adzuna(query: str, location: str | None, limit: int) -> list[dict]:
    s = get_settings()
    try:
        app_id, app_key = s.jobs_api_key.split(":", 1)
    except ValueError as e:
        raise LiveJobsUnavailable("JOBS_API_KEY must be 'app_id:app_key' for Adzuna") from e
    params = {"app_id": app_id, "app_key": app_key, "what": query, "results_per_page": limit, "content-type": "application/json"}
    if location:
        params["where"] = location
    results = _get_json(f"https://api.adzuna.com/v1/api/jobs/{s.jobs_country}/search/1", "adzuna", **params).get("results", [])
    return [
        _job("adzuna", f"adz-{x['id']}", x.get("title", ""), (x.get("company") or {}).get("display_name"),
             (x.get("location") or {}).get("display_name", ""), (x.get("category") or {}).get("label", ""),
             x.get("description", ""), "", x.get("redirect_url", ""))
        for x in results
    ]


# Adzuna's free key allows very few calls in total (25 for this project). Every call is counted in Mongo (shared by the API and
# the MCP server, and kept across restarts), results are cached for a week, and Adzuna is only asked about India.
_ADZUNA_TTL_S = 7 * 24 * 3600
_quota_client = None


def _quota_db():
    global _quota_client
    from pymongo import MongoClient  # noqa: PLC0415

    s = get_settings()
    _quota_client = _quota_client or MongoClient(s.mongodb_uri, serverSelectionTimeoutMS=3000)
    return _quota_client[s.mongodb_db]


def _adzuna_claim() -> bool:
    """Atomically reserve one call; False when the hard cap is reached."""
    cap = get_settings().jobs_adzuna_max_calls
    coll = _quota_db().api_quota
    coll.update_one({"_id": "adzuna"}, {"$setOnInsert": {"calls": 0}}, upsert=True)
    return coll.find_one_and_update({"_id": "adzuna", "calls": {"$lt": cap}}, {"$inc": {"calls": 1}}) is not None


def adzuna_calls_used() -> int:
    doc = _quota_db().api_quota.find_one({"_id": "adzuna"})
    return int(doc["calls"]) if doc else 0


def _search_adzuna_guarded(query: str, location: str | None, limit: int) -> list[dict]:
    s = get_settings()
    where = (location or s.jobs_default_location or "").lower()
    if "india" not in where:
        raise LiveJobsUnavailable("Adzuna is only used for India searches (call quota)")
    key = hashlib.sha1(f"{query.lower().strip()}|{where}".encode()).hexdigest()
    try:
        db = _quota_db()
        hit = db.adzuna_cache.find_one({"_id": key})
        if hit and time.time() - hit["at"] < _ADZUNA_TTL_S:
            return hit["jobs"]
        if not _adzuna_claim():
            raise LiveJobsUnavailable(f"Adzuna call quota used up ({s.jobs_adzuna_max_calls} calls)")
    except LiveJobsUnavailable:
        raise
    except Exception as e:  # noqa: BLE001 - without the counter we cannot guarantee the cap, so do not call
        raise LiveJobsUnavailable(f"Adzuna quota store unavailable: {type(e).__name__}") from e
    jobs = _search_adzuna(query, location, min(limit, 25))
    try:
        db.adzuna_cache.replace_one({"_id": key}, {"_id": key, "at": time.time(), "jobs": jobs}, upsert=True)
    except Exception:  # noqa: BLE001
        log.warning("could not cache the Adzuna result", exc_info=True)
    return jobs


def _search_jooble(query: str, location: str | None, limit: int) -> list[dict]:
    key = get_settings().jooble_api_key
    try:
        r = httpx.post(f"https://jooble.org/api/{key}", json={"keywords": query, "location": location or ""},
                       headers={"User-Agent": USER_AGENT}, timeout=20)
        r.raise_for_status()
        rows = r.json().get("jobs", [])
    except Exception as e:  # noqa: BLE001
        raise LiveJobsUnavailable(f"Jooble: {type(e).__name__}") from e
    return [
        _job("jooble", _short_id("joo-", str(x.get("id") or x.get("link"))), x.get("title", ""), x.get("company"),
             x.get("location", ""), x.get("type", ""), x.get("snippet", ""), "", x.get("link", ""))
        for x in rows[:limit] if x.get("title") and x.get("link")
    ]


def _search_remotive(query: str, location: str | None, limit: int) -> list[dict]:
    rows = _get_json("https://remotive.com/api/remote-jobs", "remotive", search=query).get("jobs", [])
    return [
        _job("remotive", f"rem-{x['id']}", x.get("title", ""), x.get("company_name"), x.get("candidate_required_location"),
             x.get("category", ""), x.get("description", ""), _join(x.get("tags")), x.get("url", ""))
        for x in rows[:limit] if x.get("id") and x.get("title")
    ]


def _search_himalayas(query: str, location: str | None, limit: int) -> list[dict]:
    params = {"q": query}
    if location and location.lower() in COUNTRY_CODES:
        params["country"] = COUNTRY_CODES[location.lower()]
    rows = _get_json("https://himalayas.app/jobs/api/search", "himalayas", **params).get("jobs", [])
    return [
        _job("himalayas", _short_id("him-", str(x.get("guid") or x.get("applicationLink"))), x.get("title", ""),
             x.get("companyName"), _join(x.get("locationRestrictions")), _join(x.get("categories")),
             x.get("description") or x.get("excerpt", ""), _join(x.get("categories")) + " " + _join(x.get("seniority")),
             x.get("applicationLink") or x.get("guid") or "")
        for x in rows[:limit] if x.get("title") and (x.get("applicationLink") or x.get("guid"))
    ]


_query_cache: dict[tuple, tuple[float, list[dict]]] = {}


def _cached_query(source: str, fn, query: str, location: str | None, limit: int) -> list[dict]:
    """Per-query cache for sources that search server-side (Remotive asks for few calls, so it keeps results for hours)."""
    key = (source, query.lower(), (location or "").lower())
    hit = _query_cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS[source]:
        return hit[1]
    jobs = fn(query, location, limit)
    _query_cache[key] = (time.monotonic(), jobs)
    return jobs


QUERY_APIS = {"adzuna": _search_adzuna, "jooble": _search_jooble, "remotive": _search_remotive, "himalayas": _search_himalayas}


# ---- merge ---------------------------------------------------------------------

def _enabled_sources() -> list[str]:
    s = get_settings()
    choice = s.jobs_source.strip().lower()
    if choice == "fallback":
        raise LiveJobsUnavailable("live jobs disabled (JOBS_SOURCE=fallback)")
    if choice in ("auto", "all", ""):
        extra = (["adzuna"] if s.jobs_api_key else []) + (["jooble"] if s.jooble_api_key else [])
        return list(FREE_SOURCES) + extra
    names = [n.strip() for n in choice.split(",") if n.strip()]
    return [n for n in names if n in FEEDS or n in QUERY_APIS]


_cooldown: dict[str, float] = {}  # source -> monotonic time until which it is skipped after a failure
COOLDOWN_SECONDS = 120


def _one_source(name: str, query: str, location: str | None, limit: int) -> list[dict]:
    """A source that just failed (unreachable, timed out) is skipped for a while: waiting for the same timeout on every search
    made each search 4 seconds slower even though the other sources had answered."""
    if time.monotonic() < _cooldown.get(name, 0.0):
        raise LiveJobsUnavailable(f"{SITE_NAMES[name]}: cooling down after a failure")
    try:
        return _query_source(name, query, location, limit)
    except LiveJobsUnavailable:
        _cooldown[name] = time.monotonic() + COOLDOWN_SECONDS
        raise


def _query_source(name: str, query: str, location: str | None, limit: int) -> list[dict]:
    if name in FEEDS:
        return _search_feed(name, query, location, limit)
    if name in CACHE_SECONDS:
        return _cached_query(name, QUERY_APIS[name], query, location, limit)
    if name == "adzuna":
        return _search_adzuna_guarded(query, location, limit)
    return QUERY_APIS[name](query, location, limit)


def _dedupe_key(job: dict) -> str:
    return re.sub(r"[^a-z0-9]+", "", f"{job['title']}{job['company']}".lower())


def _fetch_live(query: str, location: str | None, limit: int) -> list[dict]:
    sources = _enabled_sources()
    if not sources:
        raise LiveJobsUnavailable("no live job source is enabled")
    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        futures = {name: pool.submit(_one_source, name, query, location, limit) for name in sources}
    found: list[dict] = []
    failures: list[str] = []
    for name, fut in futures.items():
        try:
            found += fut.result()
        except LiveJobsUnavailable as e:
            failures.append(str(e))
    if failures:
        log.warning("job sources skipped: %s", "; ".join(failures))
    if not found and len(failures) == len(sources):
        raise LiveJobsUnavailable("; ".join(failures))
    seen: set[str] = set()
    merged: list[tuple[int, str, dict]] = []
    for j in found:
        k = _dedupe_key(j)
        if k not in seen:
            seen.add(k)
            merged.append((_score(j, query, location), j["id"], j))
    merged.sort(key=lambda t: (-t[0], t[1]))
    top = [{k: v for k, v in j.items() if k != "tags"} for _, _, j in merged[:limit]]
    for j in top:
        _seen[j["id"]] = j
    return top


def _search_fallback(query: str, location: str | None, limit: int) -> list[dict]:
    scored = [(_score(j, query, location), j["id"], j) for j in _fallback_jobs()]
    scored = [t for t in scored if t[0] > 0]
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [dict(j) for _, _, j in scored[:limit]]


def search_jobs(query: str, location: str | None = None, limit: int = 10) -> list[dict]:
    limit = max(1, min(limit, 25))
    try:
        found = _fetch_live(query, location, limit)
        if found:
            return found
        log.info("live job sources returned nothing for %r; using sample data", query)
    except LiveJobsUnavailable as e:
        log.warning("live job sources unavailable (%s); using sample data", e)
    return _search_fallback(query, location, limit)


def get_job(job_id: str) -> dict | None:
    if job_id in _seen:
        return dict(_seen[job_id])
    for source, prefix in ID_PREFIX.items():
        if job_id.startswith(prefix) and source in FEEDS:
            try:
                hit = next((j for j in _feed(source) if j["id"] == job_id), None)
            except LiveJobsUnavailable:
                return None
            return {k: v for k, v in hit.items() if k != "tags"} if hit else None
    return next((dict(j) for j in _fallback_jobs() if j["id"] == job_id), None)


def search_links(query: str, location: str | None = None) -> list[dict]:
    """Plain links to the big boards' own search pages (no scraping): the user clicks through and applies there."""
    q, loc = quote_plus(query), quote_plus(location or "India")
    return [
        {"site": "LinkedIn", "url": f"https://www.linkedin.com/jobs/search/?keywords={q}&location={loc}"},
        {"site": "Naukri", "url": f"https://www.naukri.com/{re.sub(r'[^a-z0-9]+', '-', query.lower()).strip('-')}-jobs"},
        {"site": "Indeed", "url": f"https://in.indeed.com/jobs?q={q}&l={loc}"},
        {"site": "Foundit", "url": f"https://www.foundit.in/srp/results?query={q}&locations={loc}"},
        {"site": "Instahyre", "url": f"https://www.instahyre.com/search-jobs/?q={q}"},
    ]
