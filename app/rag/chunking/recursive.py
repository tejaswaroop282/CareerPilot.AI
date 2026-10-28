"""Recursive token splitter: paragraph, then sentence, then word. Sizes are measured in tokens."""
import re

from app.rag.chunking.base import count_tokens

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _split_units(text: str, max_tokens: int) -> list[str]:
    """Break text into units no larger than max_tokens, preferring paragraph, sentence, then word boundaries."""
    if count_tokens(text) <= max_tokens:
        return [text]
    for splitter in (lambda t: re.split(r"\n\s*\n", t), lambda t: _SENTENCE.split(t), lambda t: t.split()):
        parts = [p for p in splitter(text) if p.strip()]
        if len(parts) > 1:
            out: list[str] = []
            for p in parts:
                out.extend(_split_units(p, max_tokens))
            return out
    return [text]  # a single unsplittable token run


def split_recursive(text: str, max_tokens: int = 650, overlap: int = 100) -> list[str]:
    """Pack units into chunks of at most max_tokens, carrying about `overlap` tokens into the next chunk."""
    text = text.strip()
    if not text:
        return []
    units = _split_units(text, max_tokens)
    chunks: list[str] = []
    cur: list[str] = []
    cur_tokens = 0
    for u in units:
        t = count_tokens(u)
        if cur and cur_tokens + t > max_tokens:
            chunks.append(" ".join(cur))
            cur, cur_tokens = _overlap_tail(cur, overlap), 0
            cur_tokens = sum(count_tokens(x) for x in cur)
        cur.append(u)
        cur_tokens += t
    if cur:
        chunks.append(" ".join(cur))
    return [c.strip() for c in chunks if c.strip()]


def _overlap_tail(units: list[str], overlap: int) -> list[str]:
    tail: list[str] = []
    total = 0
    for u in reversed(units):
        t = count_tokens(u)
        if total + t > overlap:
            break
        tail.insert(0, u)
        total += t
    return tail
