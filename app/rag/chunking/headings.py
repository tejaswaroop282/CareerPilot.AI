"""Heading-aware chunking for project docs, READMEs, and design docs.

Splits on Markdown headings (or detected plain-text headings), prepends the heading path, never splits
inside fenced code blocks or tables, and falls back to the recursive splitter for long sections.
"""
import re
from dataclasses import dataclass

from app.rag.chunking.base import count_tokens
from app.rag.chunking.recursive import split_recursive

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_NUMBERED = re.compile(r"^\d+(\.\d+)*[.)]?\s+[A-Z][^.!?]{1,70}$")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass
class Section:
    path: list[str]
    body: str


def _is_plain_heading(line: str, next_line: str | None) -> bool:
    s = line.strip()
    if not s or len(s) > 80 or s.endswith((".", ",", ";")):
        return False
    if _NUMBERED.match(s):
        return True
    letters = [c for c in s if c.isalpha()]
    # ALL CAPS short line, followed by content
    return bool(letters) and s.upper() == s and len(s.split()) <= 8 and next_line is not None and next_line.strip() != ""


def count_headings(text: str) -> int:
    lines = text.splitlines()
    n, in_code = 0, False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            in_code = not in_code
            continue
        if in_code:
            continue
        if _MD_HEADING.match(line) or _is_plain_heading(line, lines[i + 1] if i + 1 < len(lines) else None):
            n += 1
    return n


def split_sections(text: str) -> list[Section]:
    lines = text.splitlines()
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    buf: list[str] = []
    in_code = False

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            sections.append(Section([t for _, t in stack], body))
        buf.clear()

    for i, line in enumerate(lines):
        if _FENCE.match(line):
            in_code = not in_code
            buf.append(line)
            continue
        m = None if in_code else _MD_HEADING.match(line)
        level, title = (len(m.group(1)), m.group(2).strip("# ").strip()) if m else (0, "")
        if not m and not in_code and _is_plain_heading(line, lines[i + 1] if i + 1 < len(lines) else None):
            level, title = 1, line.strip()
        if level:
            flush()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        else:
            buf.append(line)
    flush()
    return sections


def _protected_blocks(body: str) -> list[str]:
    """Split a body into blocks where fenced code and tables stay whole."""
    blocks: list[str] = []
    cur: list[str] = []
    in_code = False

    def flush() -> None:
        if cur:
            blocks.append("\n".join(cur).strip())
            cur.clear()

    for line in body.splitlines():
        if _FENCE.match(line):
            if not in_code:
                flush()
            cur.append(line)
            in_code = not in_code
            if not in_code:
                flush()
            continue
        if in_code:
            cur.append(line)
        elif not line.strip():
            flush()
        else:
            cur.append(line)
    flush()
    return [b for b in blocks if b]


def chunk_headings(text: str, max_tokens: int = 600, overlap_ratio: float = 0.12) -> list[tuple[str, str]]:
    """Return (heading_path, text) pairs. Sections are packed up to max_tokens; oversized text blocks are
    split recursively, while code blocks and tables are kept whole."""
    out: list[tuple[str, str]] = []
    overlap = int(max_tokens * overlap_ratio)
    for sec in split_sections(text):
        path = " > ".join(sec.path)
        if count_tokens(sec.body) <= max_tokens:
            out.append((path, sec.body))
            continue
        cur: list[str] = []
        cur_t = 0

        def flush() -> None:
            nonlocal cur, cur_t
            if cur:
                out.append((path, "\n\n".join(cur)))
            cur, cur_t = [], 0

        for block in _protected_blocks(sec.body):
            t = count_tokens(block)
            is_protected = block.lstrip().startswith(("```", "~~~", "|"))
            if t > max_tokens and not is_protected:
                flush()
                for piece in split_recursive(block, max_tokens, overlap):
                    out.append((path, piece))
                continue
            if cur and cur_t + t > max_tokens:
                flush()
            cur.append(block)
            cur_t += t
        flush()
    return out
