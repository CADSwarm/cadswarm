"""UserPromptSubmit hook for Claude Code and Codex.

Prints the hub's team-context block (who holds which parts, open tasks, what
changed since this agent's last prompt, unread messages) to stdout, which both
CLIs add to the model's context. Contract borrowed from Amoeba's prompt hooks:

* FAIL OPEN. Any error, timeout or missing setup prints nothing and exits 0 —
  coordination context must never block a prompt.
* Bounded: one request with a ~1.8s budget, output capped well under Codex's
  ~2,500-token hook limit.
* Project folder: Claude's stdin carries `cwd`; Codex's UserPromptSubmit stdin
  does not, so fall back to the process cwd (Codex runs hooks from the project).
"""
import json
import os
import sys
import threading


def main():
    raw = ""
    try:
        # Bounded stdin read: a parent that keeps the pipe open must not wedge the prompt.
        box = {}
        t = threading.Thread(target=lambda: box.setdefault("v", sys.stdin.read()), daemon=True)
        t.start()
        t.join(0.5)
        raw = box.get("v", "")
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}
    start = payload.get("cwd") if isinstance(payload, dict) else None
    try:
        from cadswarm.client import find_project_dir, project_client
        project = find_project_dir(start or os.getcwd())
        if not project:
            return
        client, _cfg = project_client(project)
        if not client or not client.identity.get("participant_id"):
            return  # the MCP server has not joined yet
        if client.identity.get("fence") is None:
            return  # never join here: that would bump the fence and supersede the live MCP server
        client.timeout = 1.8
        text = client.get("/context")["context"]
        # Windows pipes default to the ANSI codepage; the CLIs read hook output as UTF-8.
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(text[:6000] + "\n")
        sys.stdout.flush()
    except Exception:
        return


if __name__ == "__main__":
    main()
    os._exit(0)
