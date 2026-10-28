"""Strip machine-specific and private strings before a record is written to a dataset.

Paths are matched to the end of the line: over-redaction is the safe direction, and a folder name
with spaces ("Lexus IS350") would otherwise be only half replaced.
"""
import re

PATTERNS = [
    # `\b` keeps `https://host/path` from being read as drive `s:/`.
    (re.compile(r"\b[A-Za-z]:[\\/][^\n\"']*"), "<path>"),
    (re.compile(r"\\\\[^\n\"']+"), "<path>"),
    (re.compile(r"/(?:home|Users)/[^\n\"']*"), "<path>"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "<email>"),
    (re.compile(r"\b[0-9a-fA-F]{64}\b"), "<token>"),
]


def text(value):
    """The same string with paths, e-mails and 64-hex tokens replaced. Non-strings pass through."""
    if not isinstance(value, str):
        return value
    for pattern, replacement in PATTERNS:
        value = pattern.sub(replacement, value)
    return value
