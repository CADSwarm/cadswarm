"""QR codes for the panel, drawn by the team server program the team already installs.

The panel runs inside FreeCAD, whose Python has no image or QR package, so we don't add one:
`cadswarm-host qr --text <link>` prints the modules as rows of 1s and 0s (quiet zone included)
and the panel paints them. When the program is missing or too old to know `qr`, `rows()` returns
[] and the panel shows the link and code instead, so scanning is a convenience, never a block.

Stdlib only.
"""
import os
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


def programs():
    """The host program(s) to ask, best first.

    In a checkout the developer's debug build can be newer than the released binary (a running
    team locks the release file on Windows), so it gets a turn too. An old program that has never
    heard of `qr` just fails and the next one is tried.
    """
    found = hosting.host_bin()
    if found:
        yield found
    dev = os.path.join(hosting.ROOT, "host", "target", "debug", "cadswarm-host" + (".exe" if os.name == "nt" else ""))
    if os.path.isfile(dev) and os.path.abspath(dev) != os.path.abspath(found or ""):
        yield dev


def rows(text):
    """Rows of "1" (dark) and "0" (light) for `text`; [] when no QR can be drawn."""
    if not text:
        return []
    if text in _cache:
        return _cache[text]
    for host in programs():
        try:
            r = subprocess.run([host, "qr", "--text", text], capture_output=True, text=True,
                               timeout=TIMEOUT, creationflags=hosting.NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode != 0:
            continue
        found = parse(r.stdout)
        if found:
            _cache[text] = found
            return found
    return []
