#!/usr/bin/env bash
# One-line CADSwarm install for macOS and Linux:
#   curl -fsSL https://raw.githubusercontent.com/SachchitBalamurugan/cadswarm/main/install/quick-install.sh | bash
# Downloads CADSwarm, then runs install/install.sh (which asks its questions on the terminal).
# Options after `bash -s --` are passed on, e.g. `| bash -s -- --yes`. CADSWARM_REF picks a branch or tag;
# CADSWARM_TARBALL_URL downloads from somewhere else entirely (a mirror, or a file:// path when offline).
set -eu
REPO="SachchitBalamurugan/cadswarm"
REF="${CADSWARM_REF:-main}"
command -v curl >/dev/null || { echo "Please install curl first." >&2; exit 1; }
command -v tar >/dev/null || { echo "Please install tar first." >&2; exit 1; }
TMP="$(mktemp -d "${TMPDIR:-/tmp}/cadswarm-install.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
echo "Downloading CADSwarm ($REF)…"
curl -fsSL "${CADSWARM_TARBALL_URL:-https://codeload.github.com/$REPO/tar.gz/$REF}" | tar -xzf - -C "$TMP"
SRC="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -1)"
# Ship the release's database module inside the workbench, so it wins over any stale download in
# the app folder. A local build (module/target) still wins inside the installer. Only main or a
# release tag pairs a release module with the code; another branch may be ahead of it.
case "$REF" in
    main|v[0-9]*)
        if [ -z "${CADSWARM_TARBALL_URL:-}" ] && [ ! -f "$SRC/module/cadswarm_module.wasm" ]; then
            BASE="${CADSWARM_RELEASE_BASE:-}"
            if [ -z "$BASE" ]; then
                case "$REF" in
                    v[0-9]*) BASE="https://github.com/$REPO/releases/download/$REF" ;;
                    *) BASE="https://github.com/$REPO/releases/latest/download" ;;
                esac
            fi
            curl -fsSL "$BASE/cadswarm_module.wasm" -o "$SRC/module/cadswarm_module.wasm" \
                || { rm -f "$SRC/module/cadswarm_module.wasm"; echo "Couldn't fetch the database module now; CADSwarm gets it on first use."; }
        fi
        ;;
esac
[ -f "$SRC/install/install.sh" ] || { echo "The download didn't contain the installer." >&2; exit 1; }
bash "$SRC/install/install.sh" "$@"
