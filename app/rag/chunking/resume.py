"""Section-aware resume chunking: one chunk per entry, one chunk for Skills, contextual headers.

Header example: "Resume > Projects > Ocular AI". Contact details before the first section are dropped
(PII hygiene; they carry no interview signal).
"""
import re
from dataclasses import dataclass, field

from app.rag.chunking.base import count_tokens

SECTION_ALIASES: dict[str, set[str]] = {
    "Summary": {"summary", "professional summary", "objective", "career objective", "profile", "about", "about me"},
    "Experience": {
        "experience", "work experience", "professional experience", "employment", "work history",
        "internships", "internship experience", "employment history",
    },
    "Projects": {"projects", "personal projects", "academic projects", "key projects", "selected projects"},
    "Education": {"education", "academic background", "academics", "education and training"},
    "Skills": {"skills", "technical skills", "core competencies", "key skills", "tech stack", "technologies"},
    "Certifications": {"certifications", "certificates", "licenses", "courses"},
    "Achievements": {"achievements", "awards", "honors", "accomplishments", "honours and awards", "publications"},
}
_ALIAS_LOOKUP = {alias: name for name, aliases in SECTION_ALIASES.items() for alias in aliases}
ONE_CHUNK_SECTIONS = {"Skills", "Summary"}
MAX_ENTRY_TOKENS = 700

_BULLET = re.compile(r"^\s*([•\-\*–—▪●◦·■□➢>]|\d+[.)])\s+")
_TITLE_SPLIT = re.compile(r"\s+[|–—]\s+|\s+-\s+|\s*\(|\s{3,}|:\s")


@dataclass
class ResumeEntry:
    section: str
    title: str
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip()


def section_name(line: str) -> str | None:
    norm = re.sub(r"[^a-z& ]", "", line.strip().lower()).strip()
    norm = norm.replace("&", "and")
    return _ALIAS_LOOKUP.get(norm) if len(line.strip()) <= 40 else None


def _title_of(first_line: str) -> str:
    title = _TITLE_SPLIT.split(first_line.strip(), maxsplit=1)[0].strip(" -–—|:")
    return (title or first_line.strip())[:60]


def _split_entries(section: str, lines: list[str]) -> list[ResumeEntry]:
    entries: list[ResumeEntry] = []
    cur: ResumeEntry | None = None
    prev_blank = True
    prev_bullet = False
    prev_line = ""
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            prev_blank = True
            continue
        is_bullet = bool(_BULLET.match(line))
        if cur is None:
            new_entry = True
        elif is_bullet:
            new_entry = False
        elif prev_bullet and not prev_blank:
            # Wrapped bullet text continues in lower case or after a line with no terminal punctuation.
            wrapped = line.strip()[:1].islower() or not prev_line.rstrip().endswith((".", ":", ";", "!", "?"))
            new_entry = not wrapped
        else:
            has_bullets = any(_BULLET.match(x) for x in cur.lines)
            new_entry = prev_blank or has_bullets
        if new_entry:
            cur = ResumeEntry(section, _title_of(line), [])
            entries.append(cur)
        assert cur is not None
        cur.lines.append(line)
        prev_blank, prev_bullet, prev_line = False, is_bullet, line
    return entries


def _split_oversized(entry: ResumeEntry) -> list[str]:
    """Split an entry over ~700 tokens bullet by bullet; the caller repeats the header on every piece."""
    if count_tokens(entry.text) <= MAX_ENTRY_TOKENS:
        return [entry.text]
    pieces: list[list[str]] = [[]]
    for line in entry.lines:
        candidate = "\n".join(pieces[-1] + [line])
        if pieces[-1] and count_tokens(candidate) > MAX_ENTRY_TOKENS and _BULLET.match(line):
            pieces.append([])
        pieces[-1].append(line)
    return ["\n".join(p) for p in pieces if p]


def chunk_resume(text: str) -> list[tuple[str, str, dict]]:
    """Return (header, text, metadata) per chunk. metadata has `section` and, for projects, `project`."""
    lines = text.splitlines()
    sections: list[tuple[str, list[str]]] = []
    for line in lines:
        name = section_name(line)
        if name:
            sections.append((name, []))
        elif sections:
            sections[-1][1].append(line)
        # lines before the first section heading are contact details and are dropped

    out: list[tuple[str, str, dict]] = []
    for name, body in sections:
        if name in ONE_CHUNK_SECTIONS:
            body_text = "\n".join(l for l in body if l.strip()).strip()
            if body_text:
                out.append((f"Resume > {name}", body_text, {"section": name}))
            continue
        for entry in _split_entries(name, body):
            header = f"Resume > {name} > {entry.title}"
            meta = {"section": name}
            if name == "Projects":
                meta["project"] = entry.title
            for piece in _split_oversized(entry):
                out.append((header, piece, dict(meta)))
    return out
