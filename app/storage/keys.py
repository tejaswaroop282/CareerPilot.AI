"""S3 key layout (spec 4.5)."""


def track_prefix(user_id: str, track_id: str) -> str:
    return f"users/{user_id}/tracks/{track_id}/"


def resume_key(user_id: str, track_id: str, resume_id: str) -> str:
    return f"{track_prefix(user_id, track_id)}resumes/{resume_id}.pdf"


def doc_key(user_id: str, track_id: str, doc_id: str, ext: str) -> str:
    return f"{track_prefix(user_id, track_id)}docs/{doc_id}.{ext}"


def role_kb_key(role: str) -> str:
    return f"global/role_kb/{role}.json"


def company_kb_key(company: str) -> str:
    return f"global/company_kb/{company}.json"


MANIFEST_KEY = "global/manifests/kb_manifest.json"
