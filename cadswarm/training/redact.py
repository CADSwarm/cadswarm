"""Strip machine-specific and private strings before a record is written to a dataset."""
import re

PATTERNS = [
    (re.compile(r"[A-Za-z]:\\(?:[^\\\n\"'<>|?*]+\\)*[^\\\n\"'<>|?*]*"), "<path>"),
    (re.compile(r"/(?:home|Users)/[^/\s\"']+(?:/[^\s\"']*)?"), "<path>"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "<email>"),
]


def text(value):
    """The same string with paths and e-mails replaced. Non-strings pass through."""
    if not isinstance(value, str):
        return value
    for pattern, replacement in PATTERNS:
        value = pattern.sub(replacement, value)
    return value
