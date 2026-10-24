"""QR codes for the panel, drawn by the team server program the team already installs.

The panel runs inside FreeCAD, whose Python has no image or QR package, so we don't add one:
`cadswarm-host qr --text <link>` prints the modules as rows of 1s and 0s (quiet zone included)
and the panel paints them. When the program is missing or too old to know `qr`, `rows()` returns
[] and the panel shows the link and code instead, so scanning is a convenience, never a block.

Stdlib only.
"""
import subprocess

from . import hosting

TIMEOUT = 10
_cache = {}


def parse(text):
    """Rows from the program's output, or [] when it isn't a square grid of 0s and 1s."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines or any(set(line) - {"0", "1"} for line in lines):
        return []
    width = len(lines[0])
    if width != len(lines) or any(len(line) != width for line in lines):
        return []
    return lines


def rows(text):
    """Rows of "1" (dark) and "0" (light) for `text`; [] when no QR can be drawn."""
    if not text:
        return []
    if text in _cache:
        return _cache[text]
    host = hosting.host_bin()
    if not host:
        return []
    try:
        r = subprocess.run([host, "qr", "--text", text], capture_output=True, text=True,
                           timeout=TIMEOUT, creationflags=hosting.NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    found = parse(r.stdout)
    if found:
        _cache[text] = found
    return found
