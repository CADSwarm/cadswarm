"""Launch the CADSwarm-owned desktop application."""

from __future__ import annotations

import sys


def main(argv=None):
    from cadswarm.app.desktop import main as desktop_main

    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) > 1:
        raise SystemExit("usage: cadswarm-app [project.cadswarm]")
    return desktop_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
