"""Single entry point for every CadSwarm helper process.

    python cadswarm_launch.py mcp            # an AI helper's MCP server (Claude Code / Codex start this)
    python cadswarm_launch.py hook           # UserPromptSubmit hook (stdlib only, fast)
    python cadswarm_launch.py hub ...        # the team server
    python cadswarm_launch.py check          # are the server-side packages importable?
    python cadswarm_launch.py <cli args>     # anything else goes to the `cadswarm` CLI

Agent configs point at this file by absolute path, so they work whether CadSwarm
lives in a dev checkout or inside FreeCAD's Mod folder, with any Python.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "hook":
        # Stdlib only, no dependency folder: this runs before every prompt and must be quick.
        from cadswarm.prompt_hook import main as hook
        hook()
        os._exit(0)
    from cadswarm.paths import activate_deps
    activate_deps()
    if cmd == "mcp":
        from cadswarm.mcp_server import main as mcp_main
        mcp_main()
    elif cmd == "check":
        try:
            import fastapi, uvicorn, httpx  # noqa: F401
            from mcp.server.mcpserver import MCPServer  # noqa: F401
        except Exception as e:
            print(f"missing: {e}")
            sys.exit(1)
        print("ok")
    else:
        from cadswarm.cli import main as cli_main
        cli_main(sys.argv[1:])


if __name__ == "__main__":
    main()
