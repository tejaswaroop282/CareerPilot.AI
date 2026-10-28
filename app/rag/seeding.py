"""Validate, store, chunk, embed, and index seed JSON into kb_role / kb_company, and update the manifest."""
import json
import logging
from pathlib import Path

from app.rag import manifest as mf
from app.rag import stores
from app.rag.chunking.atomic import company_record_chunk, role_question_chunk
from app.rag.chunking.base import CHUNKER_VERSION, Chunk, content_hash
from app.rag.chunking.headings import chunk_headings
from app.rag.seeds import CompanySeed, RoleResource, RoleSeed, load_json, validate_company_seed, validate_role_seed
from app.storage import keys, s3

log = logging.getLogger(__name__)


class SeedError(Exception):
    pass


def resource_chunks(res: RoleResource) -> list[Chunk]:
    """Role learning resources: heading-aware like project docs; title, URL and summary stay in metadata."""
    body = f"{res.title}\n\n{res.summary}"
    out: list[Chunk] = []
    for i, (path, text) in enumerate(chunk_headings(body, max_tokens=600)):
        out.append(
            Chunk(
                chunk_id=res.id if i == 0 else f"{res.id}:{i}",
                kb="role",
                text=text,
                header=f"Role KB > {res.role} > {res.topic} > resource",
                source_type="role_resource",
                source_name=res.source,
                metadata={
                    "role": res.role,
                    "topic": res.topic,
                    "title": res.title,
                    "url": res.url,
                    "license": res.license,
                    "record": json.dumps(res.model_dump(), ensure_ascii=False),
                },
                chunker_version=CHUNKER_VERSION,
                content_hash=content_hash(text),
            )
        )
    return out


def validate_dir(root: Path) -> dict[str, list[str]]:
    """Validate every seed file under root/role_kb and root/company_kb. Returns {file: [errors]} (only failures)."""
    failures: dict[str, list[str]] = {}
    ids: dict[str, str] = {}
    for sub, validate in (("role_kb", validate_role_seed), ("company_kb", validate_company_seed)):
        for path in sorted((root / sub).glob("*.json")):
            seed, errors = validate(load_json(path))
            if seed is not None:
                records = [*getattr(seed, "questions", []), *getattr(seed, "resources", []), *getattr(seed, "records", [])]
                for r in records:
                    key = f"{sub}:{r.id}"
                    if key in ids:
                        errors.append(f"id {r.id} also used in {ids[key]}")
                    ids[key] = path.name
            if errors:
                failures[f"{sub}/{path.name}"] = errors
    return failures


def seed_role_file(path: Path, *, upload: bool = True) -> dict:
    seed, errors = validate_role_seed(load_json(path))
    if errors or seed is None:
        raise SeedError(f"{path.name}: " + "; ".join(errors))
    s3.ensure_bucket()
    chunks = [role_question_chunk(q.model_dump()) for q in seed.questions]
    for r in seed.resources:
        chunks.extend(resource_chunks(r))
    stores.delete_where(stores.ROLE_COLLECTION, {"role": seed.role})  # replace the role's records
    stores.upsert_chunks(stores.ROLE_COLLECTION, chunks)
    if upload:
        s3.put_object(keys.role_kb_key(seed.role), path.read_bytes(), "application/json")
    manifest = mf.load_manifest(force=True)
    manifest["roles"][seed.role] = {
        "aliases": seed.aliases,
        "topics": sorted({q.topic for q in seed.questions} | {r.topic for r in seed.resources}),
        "questions": len(seed.questions),
        "resources": len(seed.resources),
    }
    mf.save_manifest(manifest)
    return manifest["roles"][seed.role]


def seed_company_file(path: Path, *, upload: bool = True) -> dict:
    seed, errors = validate_company_seed(load_json(path))
    if errors or seed is None:
        raise SeedError(f"{path.name}: " + "; ".join(errors))
    s3.ensure_bucket()
    chunks = [company_record_chunk(r.model_dump()) for r in seed.records]
    stores.delete_where(stores.COMPANY_COLLECTION, {"company": seed.company})
    stores.upsert_chunks(stores.COMPANY_COLLECTION, chunks)
    if upload:
        s3.put_object(keys.company_kb_key(seed.company), path.read_bytes(), "application/json")
    roles: dict[str, int] = {}
    for r in seed.records:
        roles[r.role] = roles.get(r.role, 0) + 1
    manifest = mf.load_manifest(force=True)
    manifest["companies"][seed.company] = {"aliases": seed.aliases, "roles": roles, "records": len(seed.records)}
    mf.save_manifest(manifest)
    return manifest["companies"][seed.company]


def seed_all(root: Path) -> dict:
    failures = validate_dir(root)
    if failures:
        raise SeedError(json.dumps(failures, indent=2))
    result: dict = {"roles": {}, "companies": {}}
    for path in sorted((root / "role_kb").glob("*.json")):
        result["roles"][path.stem] = seed_role_file(path)
    for path in sorted((root / "company_kb").glob("*.json")):
        result["companies"][path.stem] = seed_company_file(path)
    return result
