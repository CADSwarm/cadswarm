#!/usr/bin/env python3
"""Cross-session message bus for parallel OpenCode agent sessions.

Several OpenCode sessions often work in this repository (and its worktrees) at
the same time.  They share one OpenCode server, so any session can deliver a
message to any other session through the server API, and all of them can read
and write one shared append-only log (the "bus").

Usage (run from any session's working directory):

    python scripts/session_bus.py whoami
    python scripts/session_bus.py list
    python scripts/session_bus.py read [--limit N] [--to-me] [--json]
    python scripts/session_bus.py post "message for the whole team"
    python scripts/session_bus.py send --to <id|prefix|title> "direct message"
    python scripts/session_bus.py broadcast "message for every other session"
    python scripts/session_bus.py watch [--limit N]   # follow new entries

Options common to `send` and `broadcast`:

    --steer        wake the target now instead of queueing behind its current turn
    --no-log       do not record the message in the shared log

The bus log lives outside the repository in OpenCode's data directory, keyed by
OpenCode project id, so sessions in git worktrees see the same log:

    ~/.local/share/opencode/session-bus/bus-<projectID>.jsonl

Environment:
    OPENCODE_SESSION_ID   set by OpenCode; identifies the sending session
    OPENCODE_BIN          path to the opencode CLI (auto-detected otherwise)
    OPENCODE_SESSION_BUS  override the bus log path
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------- CLI


def find_cli() -> str:
    """Locate the opencode executable that talks to the running server."""
    explicit = os.environ.get("OPENCODE_BIN")
    if explicit and Path(explicit).exists():
        return explicit
    for name in ("opencode", "opencode.exe"):
        found = shutil.which(name)
        if found:
            return found

    home = Path.home()
    candidates = [
        # Desktop app bundles (Windows / macOS / Linux)
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/@opencodedesktop/resources/opencode-cli.exe",
        Path("/Applications/OpenCode.app/Contents/Resources/opencode-cli"),
        home / "Applications/OpenCode.app/Contents/Resources/opencode-cli",
        # npm / bun / standalone installs
        home / ".opencode/bin/opencode",
        home / ".bun/bin/opencode",
        Path("/usr/local/bin/opencode"),
        Path("/opt/homebrew/bin/opencode"),
    ]
    for candidate in candidates:
        if str(candidate) and candidate.exists():
            return str(candidate)
    raise SystemExit(
        "Could not find the opencode CLI. Set OPENCODE_BIN to its path "
        "(for the desktop app it is usually resources/opencode-cli.exe)."
    )


CLI: str = ""


def api(method: str, path: str, body: dict | None = None, tries: int = 3):
    """Call the OpenCode HTTP API through the CLI (the CLI handles auth)."""
    cmd = [CLI, "api", method.lower(), path]
    if body is not None:
        cmd += ["--data", json.dumps(body)]
    last = ""
    for attempt in range(tries):
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if proc.returncode == 0:
            out = proc.stdout.strip()
            if not out:
                return None
            try:
                return json.loads(out)
            except json.JSONDecodeError:
                return out
        last = (proc.stderr or proc.stdout or "").strip()
        if "not recognized" in last or "No such file" in last:
            break
        time.sleep(0.4 * (attempt + 1))
    raise SystemExit(f"opencode API call failed: {method.upper()} {path}\n{last}")


# ----------------------------------------------------------------------- sessions


class Sessions:
    """Lazy snapshot of the server's sessions."""

    def __init__(self):
        self._all: list[dict] | None = None
        self._active: dict[str, str] = {}

    def all(self) -> list[dict]:
        if self._all is None:
            payload = api("GET", "/api/session") or {}
            self._all = payload.get("data", [])
        return self._all

    def active(self) -> dict[str, str]:
        if not self._active:
            payload = api("GET", "/api/session/active") or {}
            data = payload.get("data", {}) or {}
            # The server returns {id: {"type": "running"}}; some versions return
            # {id: "running"}. Normalise to plain strings.
            self._active = {
                key: (value.get("type") if isinstance(value, dict) else str(value))
                for key, value in data.items()
            }
        return self._active

    def detail(self, session_id: str) -> dict:
        payload = api("GET", f"/api/session/{session_id}") or {}
        return payload.get("data", {})

    def details(self) -> dict[str, dict]:
        ids = [s["id"] for s in self.all()]
        with ThreadPoolExecutor(max_workers=8) as pool:
            found = pool.map(self.detail, ids)
        return {d.get("id"): d for d in found if d.get("id")}

    def status(self, session_id: str) -> str:
        return self.active().get(session_id, "idle")

    def resolve(self, needle: str, include_self: bool = True, me: str = "") -> dict:
        """Find a session by id, id prefix, or a unique part of its title."""
        needle = needle.strip()
        if not needle:
            raise SystemExit("--to needs a session id, id prefix, or title")
        pool = [s for s in self.all() if include_self or s["id"] != me]
        exact = [s for s in pool if s["id"] == needle]
        if exact:
            return exact[0]
        by_prefix = [s for s in pool if s["id"].startswith(needle)]
        if len(by_prefix) == 1:
            return by_prefix[0]
        by_title = [s for s in pool if needle.lower() in (s.get("title") or "").lower()]
        if len(by_title) == 1:
            return by_title[0]
        matches = by_prefix or by_title
        if not matches:
            raise SystemExit(f"No session matches {needle!r}. Try `list`.")
        width = max(len(s.get("title") or "") for s in matches)
        lines = "\n".join(f"  {s['id']}  {s.get('title', ''):<{width}}" for s in matches)
        raise SystemExit(f"{needle!r} matches {len(matches)} sessions:\n{lines}")


# ---------------------------------------------------------------------- bus file


def data_root() -> Path:
    root = Path(os.environ.get("OPENCODE_DATA", "")) if os.environ.get("OPENCODE_DATA") else None
    if root and root.exists():
        return root
    # OpenCode uses ~/.local/share/opencode on every platform (including Windows).
    return Path.home() / ".local" / "share" / "opencode"


def bus_path(sessions: Sessions, me: str) -> Path:
    override = os.environ.get("OPENCODE_SESSION_BUS")
    if override:
        path = Path(override)
    else:
        project = ""
        if me:
            project = sessions.detail(me).get("projectID") or ""
        if not project:
            project = "shared"
        path = data_root() / "session-bus" / f"bus-{project}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def now_ms() -> int:
    return int(time.time() * 1000)


def append_entry(path: Path, entry: dict) -> None:
    line = json.dumps(entry, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def read_entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def fmt_time(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S")


def print_entries(entries: list[dict], me: str, titles: dict[str, str]) -> None:
    if not entries:
        print("(the session bus is empty)")
        return
    for e in entries:
        sender = titles.get(e.get("session", ""), e.get("session", "?"))
        mark = "→" if e.get("to") not in ("", "all") else "•"
        target = "" if e.get("to") in ("", "all") else f" → {e['to']}"
        mine = " (you)" if e.get("session") == me else ""
        print(f"[{fmt_time(e.get('ts', 0))}] {sender}{mine}{target} {mark} {e.get('text', '')}")


# ---------------------------------------------------------------------- commands


def cmd_install(args, sessions: Sessions, me: str) -> int:
    """Copy this script next to the bus so git worktrees can run the same tool."""
    target = data_root() / "session-bus" / "session_bus.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).resolve(), target)
    print(f"installed {target}")
    return 0


def cmd_whoami(args, sessions: Sessions, me: str) -> int:
    if not me:
        print("Not running inside an OpenCode session (OPENCODE_SESSION_ID is unset).")
        return 1
    d = sessions.detail(me)
    print(f"id:       {me}")
    print(f"title:    {d.get('title', '?')}")
    print(f"status:   {sessions.status(me)}")
    print(f"location: {(d.get('location') or {}).get('directory', '?')}")
    print(f"bus:      {bus_path(sessions, me)}")
    return 0


def cmd_list(args, sessions: Sessions, me: str) -> int:
    entries = sessions.all()
    if not entries:
        print("(no sessions)")
        return 0
    active = sessions.active()
    details = sessions.details()
    rows = []
    for s in entries:
        sid = s["id"]
        d = details.get(sid, {})
        rows.append(
            {
                "id": sid,
                "title": s.get("title") or d.get("title") or "?",
                "status": active.get(sid, "idle"),
                "parent": s.get("parentID") or "",
                "directory": (d.get("location") or {}).get("directory") or "",
                "me": sid == me,
            }
        )
    rows.sort(key=lambda r: (not r["me"], r["parent"] != "", r["title"].lower()))
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    width = min(52, max(20, max(len(r["title"]) for r in rows)))
    print(f"{'':2} {'SESSION':36} {'STATUS':9} {'DIRECTORY'}")
    for r in rows:
        mark = "*" if r["me"] else ("  " if not r["parent"] else "  ")
        title = r["title"][:width]
        directory = r["directory"]
        if len(directory) > 46:
            directory = "..." + directory[-43:]
        print(f"{mark:2} {r['id']:36} {r['status']:9} {directory}")
        print(f"   {title}")
    print("\n* = this session; subagent sessions are listed too.")
    return 0


def cmd_read(args, sessions: Sessions, me: str) -> int:
    path = bus_path(sessions, me)
    entries = read_entries(path)
    if args.to_me and me:
        entries = [e for e in entries if e.get("to") in ("", "all", me)]
    limit = args.limit if args.limit is not None else 50
    entries = entries[-limit:]
    if args.json:
        print(json.dumps(entries, indent=2, ensure_ascii=False))
        return 0
    if not entries:
        print(f"(no entries in {path})")
        return 0
    titles = {s["id"]: s.get("title", "?") for s in sessions.all()}
    print_entries(entries, me, titles)
    return 0


def cmd_watch(args, sessions: Sessions, me: str) -> int:
    path = bus_path(sessions, me)
    titles = {s["id"]: s.get("title", "?") for s in sessions.all()}
    seen = len(read_entries(path))
    limit = args.limit if args.limit is not None else 20
    print_entries(read_entries(path)[-limit:], me, titles)
    try:
        while True:
            time.sleep(1.0)
            entries = read_entries(path)
            if len(entries) > seen:
                print_entries(entries[seen:], me, titles)
                seen = len(entries)
    except KeyboardInterrupt:
        return 0


def _record(path: Path, me: str, title: str, to: str, text: str) -> None:
    append_entry(path, {"ts": now_ms(), "session": me, "title": title, "to": to, "text": text})


def _deliver(sessions: Sessions, target: dict, text: str, steer: bool) -> None:
    body: dict = {"text": text, "delivery": "steer" if steer else "queue"}
    api("POST", f"/api/session/{target['id']}/prompt", body)


def cmd_post(args, sessions: Sessions, me: str) -> int:
    path = bus_path(sessions, me)
    title = sessions.detail(me).get("title", "") if me else ""
    text = " ".join(args.text).strip()
    if not text:
        raise SystemExit("nothing to post")
    _record(path, me, title, "all", text)
    print(f"posted to the bus ({path})")
    return 0


def cmd_send(args, sessions: Sessions, me: str) -> int:
    target = sessions.resolve(args.to, include_self=args.allow_self, me=me)
    text = " ".join(args.text).strip()
    if not text:
        raise SystemExit("nothing to send")
    _deliver(sessions, target, text, args.steer)
    if not args.no_log:
        path = bus_path(sessions, me)
        title = sessions.detail(me).get("title", "") if me else ""
        _record(path, me, title, target["id"], text)
    print(f"sent to {target['id']} ({target.get('title', '?')}) [{target.get('id') == me and 'self' or ('steer' if args.steer else 'queued')}]")
    return 0


def cmd_broadcast(args, sessions: Sessions, me: str) -> int:
    text = " ".join(args.text).strip()
    if not text:
        raise SystemExit("nothing to broadcast")
    targets = [s for s in sessions.all() if s["id"] != me]
    if args.top:
        targets = [s for s in targets if not s.get("parentID")]
    for pattern in args.exclude or []:
        targets = [
            s for s in targets
            if pattern.lower() not in (s.get("title") or "").lower() and not s["id"].startswith(pattern)
        ]
    if args.dry_run:
        for s in targets:
            print(f"would send to {s['id']} {s.get('title', '')}")
        return 0
    path = bus_path(sessions, me)
    title = sessions.detail(me).get("title", "") if me else ""
    if not args.no_log:
        _record(path, me, title, "all", text)
    failures = []
    for s in targets:
        try:
            _deliver(sessions, s, text, args.steer)
            print(f"  [ok]   {s['id']}  {s.get('title', '')}")
        except SystemExit as exc:  # one dead session must not stop the rest
            failures.append((s, str(exc)))
            print(f"  [FAIL] {s['id']}  {s.get('title', '')}: {exc}")
    mine = f"({len(targets) - len(failures)}/{len(targets)} sessions)"
    print(f"broadcast {mine} via {'steer' if args.steer else 'queue'}")
    return 1 if failures else 0


# ------------------------------------------------------------------------- main


def main(argv: list[str]) -> int:
    global CLI
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    CLI = find_cli()

    parser = argparse.ArgumentParser(description="Cross-session message bus for OpenCode")
    parser.add_argument("--session", help="override the sending session id (default: OPENCODE_SESSION_ID)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("install", help="copy this tool next to the bus (reachable from worktrees)")
    p.set_defaults(fn=cmd_install)

    p = sub.add_parser("whoami", help="show this session's identity and the bus path")
    p.set_defaults(fn=cmd_whoami)

    p = sub.add_parser("list", help="list all sessions in this OpenCode project")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("read", help="print recent bus messages")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--to-me", action="store_true", help="only messages addressed to everyone or to this session")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_read)

    p = sub.add_parser("watch", help="print new bus messages as they arrive")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_watch)

    p = sub.add_parser("post", help="write a message to the shared log (no delivery)")
    p.add_argument("text", nargs="+")
    p.set_defaults(fn=cmd_post)

    p = sub.add_parser("send", help="deliver a message to one session")
    p.add_argument("--to", required=True, help="session id, id prefix, or unique title fragment")
    p.add_argument("--steer", action="store_true", help="wake the target now instead of queueing")
    p.add_argument("--allow-self", action="store_true")
    p.add_argument("--no-log", action="store_true", help="do not record it in the shared log")
    p.add_argument("text", nargs="+")
    p.set_defaults(fn=cmd_send)

    p = sub.add_parser("broadcast", help="deliver a message to every other session")
    p.add_argument("--steer", action="store_true", help="wake targets now instead of queueing")
    p.add_argument("--top", action="store_true", help="top-level sessions only (skip subagents)")
    p.add_argument("--exclude", action="append", metavar="TITLE|ID", help="skip matching sessions (repeatable)")
    p.add_argument("--no-log", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("text", nargs="+")
    p.set_defaults(fn=cmd_broadcast)

    args = parser.parse_args(argv)
    me = args.session or os.environ.get("OPENCODE_SESSION_ID", "")
    if not me and args.cmd in ("whoami", "post", "send", "broadcast", "watch"):
        # Read-only commands can work without an identity; the rest cannot.
        if args.cmd != "watch":
            print("Warning: not inside an OpenCode session; messages will be anonymous.", file=sys.stderr)
    sessions = Sessions()
    try:
        return args.fn(args, sessions, me)
    except SystemExit:
        raise
    except Exception as exc:  # keep agents from crashing on a transient server hiccup
        print(f"session_bus: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
