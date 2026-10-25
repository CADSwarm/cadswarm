# Session bus: talking between OpenCode sessions

Several OpenCode sessions work on this project at the same time, often in
different git worktrees. They share one OpenCode server, so they can message
each other directly, and they share one append-only log (the **bus**).

The tool is `scripts/session_bus.py`. It is also copied to OpenCode's data
folder so every worktree can run it:

```
python "$HOME/.local/share/opencode/session-bus/session_bus.py" <command>
```

On Windows:

```
python C:\Users\waves\.local\share\opencode\session-bus\session_bus.py <command>
```

The log lives at
`$HOME/.local/share/opencode/session-bus/bus-<projectID>.jsonl`, outside the
repository, so it is never committed and a log written from one worktree is
visible from every other worktree.

## Commands

| Command | What it does |
|---|---|
| `whoami` | This session's id, title, status, directory, and bus path |
| `list` | Every session in the project, with status and directory |
| `read [--limit N] [--to-me]` | Recent bus messages |
| `post "text"` | Append a message to the shared log (no wake-up) |
| `send --to <id\|prefix\|title> "text"` | Deliver to one session (queued until it is idle) |
| `send ... --steer` | Deliver and wake the target immediately |
| `broadcast "text" [--top]` | Deliver to every other session and log it |
| `watch` | Follow new bus messages |
| `install` | Re-copy the tool next to the bus after editing it |

Targets may be a full session id, a unique id prefix, or a unique fragment of
the session title.

## Protocol

1. **Start of a turn:** run `read --limit 20`. Act on messages addressed to
   `all` or to your session.
2. **Before you finish:** run `post` with one line: what you changed, files or
   branch, and anything another session must know (contract or schema changes,
   shared files, migrations, config).
3. **Direct coordination:** `send --to <title>` when only one session needs the
   message. Use `--steer` only when it is urgent enough to interrupt that
   session's current turn.
4. **Do not auto-reply to a broadcast.** Post your status, then stop; broadcasts
   are introductions, not conversations. Reply directly only when there is a
   concrete question or conflict.
5. **Announce shared-file changes** (contracts, bindings, schemas, settings,
   generated files) before or immediately after changing them, so parallel
   sessions do not rebase onto a surprise.
