"""Strip PII and bias signals from text before it reaches job scoring (spec section 9): contact details, links,
gendered words and titles, photo references, and college or university tier signals. Deterministic regexes."""
import re

_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"(?<!\w)\+?\d[\d\s().-]{8,}\d"), "[phone]"),
    (re.compile(r"https?://\S+|www\.\S+"), "[link]"),
    (re.compile(r"\b(?:linkedin|github)\.com/\S+", re.I), "[link]"),
    (re.compile(r"\b(?:mr|mrs|ms|miss|mx)\.?\s+[A-Z][a-z]+", re.I), "[name]"),
    (re.compile(r"\b(?:he|she|him|her|his|hers|himself|herself|male|female|man|woman|gender|husband|wife)\b", re.I), "[gender]"),
    (re.compile(r"\b(?:photo|photograph|picture|headshot|passport[- ]size)\b[^.\n]*", re.I), "[photo]"),
    (re.compile(r"\b(?:IIT|IIIT|NIT|BITS|IIM|MIT|Stanford|Harvard|Oxford|Cambridge|Ivy League)\b[\w\s,.-]{0,40}", re.I), "[college]"),
    (re.compile(r"\b(?:tier[- ]?[123]|top[- ]tier)\s+(?:college|university|institute)\b", re.I), "[college]"),
    (re.compile(r"\b[A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,4}\s+(?:University|College|Institute of Technology|Institute)\b"), "[college]"),
]


def strip_pii(text: str) -> str:
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text
