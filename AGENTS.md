# Working in this repository

## Parallel sessions: use the session bus

This project is often worked on by several OpenCode sessions at once, including
sessions inside git worktrees. They all share one message bus. Stay in sync:

```sh
# Read the latest messages at the start of a turn
python "$HOME/.local/share/opencode/session-bus/session_bus.py" read --limit 20

# Before you finish: one line on what changed, branch/worktree, files, blockers
python "$HOME/.local/share/opencode/session-bus/session_bus.py" post "status"

# Message one session directly (queue by default; --steer to interrupt)
python "$HOME/.local/share/opencode/session-bus/session_bus.py" send --to "<session id or title>" "text"

# See every session and its status
python "$HOME/.local/share/opencode/session-bus/session_bus.py" list
```

On Windows, if `$HOME` is unavailable, run the same script at
`%USERPROFILE%\.local\share\opencode\session-bus\session_bus.py`.

Rules:

- Announce contract, schema, binding, settings, and generated-file changes
  before or immediately after making them.
- Answer direct messages. Do not auto-reply to broadcasts; post your status
  once and continue your own task.
- Use `--steer` only when a message is urgent enough to interrupt another
  session's current turn.

Full notes: [docs/session-bus.md](docs/session-bus.md).
