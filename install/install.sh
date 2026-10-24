#!/usr/bin/env bash
# CADSwarm installer for macOS and Linux. Run via "Install CadSwarm.command" (macOS, double-click),
# "Install CadSwarm.sh" (Linux), or the one-line quick install (install/quick-install.sh).
# Finds FreeCAD, copies this folder into FreeCAD's add-on folder, optionally adds a shortcut, then opens
# FreeCAD in CADSwarm, where a step-by-step guide takes over.
#
#   --no-launch     install only (don't open FreeCAD)
#   --yes           don't ask: add the shortcut; never download or unpack FreeCAD unless --get-freecad
#   --get-freecad   download the official FreeCAD if needed, without asking
#   --no-shortcut   don't add a desktop/menu shortcut
#
# Environment: CADSWARM_FREECAD=<FreeCAD app/AppImage/executable> picks a specific FreeCAD.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OS="$(uname -s)"
NO_LAUNCH=0 ASSUME_YES=0 GET_FREECAD=0 SHORTCUT=1
for arg in "$@"; do
    case "$arg" in
        --no-launch) NO_LAUNCH=1 ;;
        --yes|-y) ASSUME_YES=1 ;;
        --get-freecad) GET_FREECAD=1 ;;
        --no-shortcut) SHORTCUT=0 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

FREECAD_VERSION="1.1.4"
APPIMAGE_SHA_x86_64="f6dc6ba676e5ac96a565ebc8d657232f94c6158e85b4352141bd1a46f6b43434"
APPIMAGE_SHA_aarch64="675d1a4295b2183e0e1d816a4b5557dcb63aeced4e60aaa28d6c3686f4c71dc9"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
MANAGED_DIR="$DATA_HOME/CadSwarm/FreeCAD-$FREECAD_VERSION"

if [ -t 1 ]; then C_OK=$'\e[32m' C_WARN=$'\e[33m' C_ERR=$'\e[31m' C_HEAD=$'\e[36m' C_DIM=$'\e[90m' C_END=$'\e[0m'
else C_OK="" C_WARN="" C_ERR="" C_HEAD="" C_DIM="" C_END=""; fi
say()  { printf '%s\n' "$*"; }
ok()   { printf '  %s[ok]%s %s\n' "$C_OK" "$C_END" "$*"; }
warn() { printf '%s%s%s\n' "$C_WARN" "$*" "$C_END"; }
die()  { printf '%s%s%s\n' "$C_ERR" "$*" "$C_END"; exit 1; }

# Questions read from the terminal, so they also work when this script arrives through `curl | bash`.
ask() {  # ask "question" default(y|n) -> 0 for yes
    local q="$1" def="$2" reply=""
    if [ "$ASSUME_YES" = 1 ] || ! { : </dev/tty; } 2>/dev/null; then [ "$def" = y ]; return; fi
    printf '  %s %s ' "$q" "$([ "$def" = y ] && echo '(Y/n)' || echo '(y/N)')" >/dev/tty
    read -r reply </dev/tty || reply=""
    case "$reply" in [yY]*) return 0 ;; [nN]*) return 1 ;; *) [ "$def" = y ] ;; esac
}
pause() { if [ "$ASSUME_YES" = 0 ] && { : </dev/tty; } 2>/dev/null; then read -r _ </dev/tty || true; fi; }
open_url() {
    if [ "$OS" = Darwin ]; then open "$1"; elif command -v xdg-open >/dev/null; then xdg-open "$1" >/dev/null 2>&1 & fi
}

say ""
say "  ${C_HEAD}CADSwarm setup${C_END}"
say "  ${C_DIM}Build 3D designs together with your team's AI helpers.${C_END}"
say ""

# ---------------------------------------------------------------- 1. find FreeCAD
# KIND: app (macOS bundle) | appimage (unpacked official AppImage) | system | snap | flatpak
# Only "app" and "appimage" bring their own Python, which hosting a team and connecting AI helpers need.
KIND="" LAUNCH=() CMD=()

use_app() {  # $1 = FreeCAD.app
    KIND=app LAUNCH=(open -a "$1" --args) CMD=("$1/Contents/Resources/bin/freecadcmd")
    [ -x "${CMD[0]}" ] || CMD=("$1/Contents/MacOS/FreeCADCmd")
}
use_unpacked() {  # $1 = squashfs-root of an unpacked AppImage
    KIND=appimage LAUNCH=("$1/AppRun") CMD=("$1/AppRun" freecadcmd)
}

find_freecad() {
    local c
    if [ -n "${CADSWARM_FREECAD:-}" ]; then
        c="$CADSWARM_FREECAD"
        case "$c" in
            *.app|*.app/) use_app "${c%/}"; return ;;
            *.AppImage) unpack_appimage "$c" && return ;;
            */squashfs-root|*/squashfs-root/) use_unpacked "${c%/}"; return ;;
        esac
        KIND=system LAUNCH=("$c") CMD=("$(dirname "$c")/freecadcmd"); return
    fi
    if [ "$OS" = Darwin ]; then
        for c in /Applications/FreeCAD*.app "$HOME"/Applications/FreeCAD*.app; do
            [ -d "$c" ] && { use_app "$c"; return; }
        done
        return
    fi
    for c in "$DATA_HOME"/CadSwarm/FreeCAD-*/squashfs-root; do
        [ -x "$c/AppRun" ] && { use_unpacked "$c"; return; }
    done
    # An AppImage someone unpacked by hand (`--appimage-extract`, then moved the folder).
    for c in "$HOME"/Applications/FreeCAD* /opt/FreeCAD* /opt/freecad*; do
        [ -d "$c" ] && [ -x "$c/AppRun" ] && [ -x "$c/usr/bin/python" ] && { use_unpacked "$c"; return; }
    done
    for c in "$HOME"/Applications/FreeCAD*.AppImage "$HOME"/Downloads/FreeCAD*.AppImage \
             "$HOME"/.local/bin/FreeCAD*.AppImage "$HOME"/Desktop/FreeCAD*.AppImage; do
        if [ -f "$c" ]; then
            say "  Found the FreeCAD download $c"
            if { [ "$GET_FREECAD" = 1 ] || { [ "$ASSUME_YES" = 0 ] && ask "Unpack it so CADSwarm can use it (about 3 GB, once)?" y; }; } \
                    && unpack_appimage "$c"; then return; fi
        fi
    done
    if command -v freecad >/dev/null || command -v FreeCAD >/dev/null; then
        c="$(command -v freecad || command -v FreeCAD)"
        case "$(readlink -f "$c")" in
            */snap/*) KIND=snap LAUNCH=(/snap/bin/freecad) CMD=(/snap/bin/freecad.cmd) ;;
            *) KIND=system LAUNCH=("$c") CMD=("$(command -v freecadcmd || command -v FreeCADCmd || echo freecadcmd)") ;;
        esac
        return
    fi
    if [ -x /snap/bin/freecad ]; then KIND=snap LAUNCH=(/snap/bin/freecad) CMD=(/snap/bin/freecad.cmd); return; fi
    if command -v flatpak >/dev/null && flatpak info org.freecad.FreeCAD >/dev/null 2>&1; then
        KIND=flatpak LAUNCH=(flatpak run org.freecad.FreeCAD) CMD=(flatpak run --command=FreeCADCmd org.freecad.FreeCAD)
    fi
}

sha256_of() { if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }

unpack_appimage() {  # $1 = AppImage file; unpacks into $MANAGED_DIR (no FUSE needed)
    local img="$1" dest="$MANAGED_DIR"
    say "  Unpacking FreeCAD (a few minutes)…"
    rm -rf "$dest.partial" && mkdir -p "$dest.partial" || return 1
    chmod +x "$img" 2>/dev/null
    if ! (cd "$dest.partial" && "$img" --appimage-extract >/dev/null 2>&1) || [ ! -x "$dest.partial/squashfs-root/AppRun" ]; then
        rm -rf "$dest.partial"; warn "  Couldn't unpack $img."; return 1
    fi
    rm -rf "$dest" && mv "$dest.partial" "$dest"
    ok "Unpacked FreeCAD into $dest"
    use_unpacked "$dest/squashfs-root"
}

download_freecad() {  # the official build, which includes its own Python
    if [ "$OS" = Darwin ]; then
        if command -v brew >/dev/null; then
            say "  Installing FreeCAD with Homebrew…"
            brew install --cask freecad && for c in /Applications/FreeCAD*.app; do [ -d "$c" ] && use_app "$c" && return 0; done
        fi
        say "  Opening the FreeCAD download page. Install it (drag FreeCAD into Applications),"
        say "  then run this installer again."
        open_url "https://www.freecad.org/downloads.php"
        return 1
    fi
    local arch sha url img
    arch="$(uname -m)"
    case "$arch" in
        x86_64|amd64) arch=x86_64 sha="$APPIMAGE_SHA_x86_64" ;;
        aarch64|arm64) arch=aarch64 sha="$APPIMAGE_SHA_aarch64" ;;
        *) warn "  There's no official FreeCAD download for $arch."; open_url "https://www.freecad.org/downloads.php"; return 1 ;;
    esac
    command -v curl >/dev/null || { warn "  curl is needed to download FreeCAD."; return 1; }
    url="https://github.com/FreeCAD/FreeCAD/releases/download/$FREECAD_VERSION/FreeCAD_$FREECAD_VERSION-Linux-$arch-py311.AppImage"
    img="$DATA_HOME/CadSwarm/FreeCAD_$FREECAD_VERSION-Linux-$arch.AppImage"
    mkdir -p "$DATA_HOME/CadSwarm"
    say "  Downloading FreeCAD $FREECAD_VERSION (about 800 MB)…"
    curl -fL --progress-bar -C - -o "$img" "$url" || { warn "  The download failed. Check your internet connection."; return 1; }
    if [ "$(sha256_of "$img")" != "$sha" ]; then
        rm -f "$img"; warn "  The FreeCAD download didn't match its official checksum, so it was deleted."; return 1
    fi
    ok "Downloaded and checked FreeCAD $FREECAD_VERSION"
    unpack_appimage "$img" && rm -f "$img"
}

find_freecad
if [ -z "$KIND" ]; then
    warn "FreeCAD isn't installed on this computer yet."
    if [ "$GET_FREECAD" = 1 ] || { [ "$ASSUME_YES" = 0 ] && ask "Download the official FreeCAD now?" y; }; then
        download_freecad || true
    fi
    if [ -z "$KIND" ]; then
        [ "$OS" = Darwin ] || { say "  Get it from https://www.freecad.org/downloads.php, then run this installer again."; open_url "https://www.freecad.org/downloads.php"; }
        exit 1
    fi
elif [ "$KIND" = snap ] || [ "$KIND" = flatpak ] || [ "$KIND" = system ]; then
    say "  Found FreeCAD ($KIND package)."
    warn "  This FreeCAD has no Python of its own, so from it you can join a teammate's project but not"
    warn "  start a team or connect an AI helper. The official FreeCAD download can do both."
    if [ "$GET_FREECAD" = 1 ] || { [ "$ASSUME_YES" = 0 ] && ask "Download the official FreeCAD for CADSwarm (about 800 MB, once)?" n; }; then
        download_freecad || warn "  Keeping the FreeCAD you already have."
    fi
fi
ok "Using FreeCAD: ${LAUNCH[*]}"

# ---------------------------------------------------------------- 2. where its add-ons go
USER_DIR="$("${CMD[@]}" -c "import FreeCAD; print('CADSWARM_DIR=' + FreeCAD.getUserAppDataDir())" 2>/dev/null \
            | sed -n 's/^CADSWARM_DIR=//p' | tail -1)"
if [ -z "$USER_DIR" ]; then
    case "$KIND" in  # FreeCAD's usual places, if it couldn't be asked
        app) USER_DIR="$HOME/Library/Application Support/FreeCAD" ;;
        snap) USER_DIR="$HOME/snap/freecad/common" ;;
        flatpak) USER_DIR="$HOME/.var/app/org.freecad.FreeCAD/data/FreeCAD" ;;
        *) USER_DIR="$DATA_HOME/FreeCAD" ;;
    esac
    warn "  Couldn't ask FreeCAD where its add-ons go; using $USER_DIR"
fi
DEST="${USER_DIR%/}/Mod/CadSwarm"

if pgrep -x FreeCAD >/dev/null 2>&1 || pgrep -x freecad >/dev/null 2>&1; then
    warn "FreeCAD is open. Please close it (save your work first), then press Enter."
    pause
fi

# ---------------------------------------------------------------- 3. copy CADSwarm into FreeCAD
# A clean copy each time (updates included). Only code lives here: projects are in Documents.
if [ "$(cd "$HERE" && pwd -P)" = "$( (cd "$DEST" 2>/dev/null && pwd -P) || echo none)" ]; then
    ok "CADSwarm is already installed in FreeCAD"
else
    # Start from an empty staging folder: a failed earlier run must not leave old files in the copy.
    rm -rf "$DEST.new" && mkdir -p "$DEST.new" || die "Couldn't create $DEST"
    (cd "$HERE" && tar -cf - --exclude=.git --exclude=.venv --exclude=__pycache__ --exclude=.pytest_cache \
        --exclude=cadswarm-data --exclude=exports --exclude=.cadswarm --exclude='*.egg-info' --exclude='*.pyc' \
        --exclude=node_modules --exclude=target .) | (cd "$DEST.new" && tar -xf -) || die "Copying CADSwarm into FreeCAD failed."
    WASM="$HERE/module/target/wasm32-unknown-unknown/release/cadswarm_module.wasm"
    # A local build wins; otherwise use the module the installer put beside the code, so a fresh
    # workbench never falls back to a stale download in the app folder.
    [ -f "$WASM" ] || WASM="$HERE/module/cadswarm_module.wasm"
    [ -f "$WASM" ] && cp "$WASM" "$DEST.new/module/cadswarm_module.wasm"  # the team server's database module
    rm -rf "$DEST" && mv "$DEST.new" "$DEST" || die "Couldn't replace the old CADSwarm in $DEST"
    ok "Installed CADSwarm into FreeCAD ($DEST)"
fi
OPENER="$DEST/cadswarm/gui/open_cadswarm.py"

# ---------------------------------------------------------------- 4. shortcut that opens FreeCAD in CADSwarm
quote_args() { local out="" a; for a in "$@"; do out+="\"${a//\"/\\\"}\" "; done; printf '%s' "${out% }"; }
if [ "$SHORTCUT" = 1 ] && ask "Add a CADSwarm shortcut?" y; then
    if [ "$OS" = Darwin ]; then
        APP="$HOME/Applications/CadSwarm.app"
        mkdir -p "$HOME/Applications"
        SCRIPT="do shell script \"$(quote_args "${LAUNCH[@]}" "$OPENER" | sed 's/\\/\\\\/g; s/"/\\"/g') > /dev/null 2>&1 &\""
        if command -v osacompile >/dev/null && rm -rf "$APP" && osacompile -o "$APP" -e "$SCRIPT" 2>/dev/null; then
            ln -sfn "$APP" "$HOME/Desktop/CadSwarm.app" 2>/dev/null
            ok "Added CADSwarm to ~/Applications and your desktop"
        else
            warn "  Couldn't make the shortcut; open FreeCAD and pick CADSwarm instead."
        fi
    else
        APPS="$DATA_HOME/applications"
        mkdir -p "$APPS"
        ICON="$DEST/cadswarm/gui/cadswarm.svg"
        {
            echo "[Desktop Entry]"
            echo "Type=Application"
            echo "Name=CADSwarm"
            echo "Comment=Build 3D designs with your team's AI helpers"
            echo "Exec=$(quote_args "${LAUNCH[@]}" "$OPENER")"
            [ -f "$ICON" ] && echo "Icon=$ICON" || echo "Icon=org.freecad.FreeCAD"
            echo "Terminal=false"
            echo "Categories=Graphics;Engineering;"
        } > "$APPS/cadswarm.desktop"
        chmod +x "$APPS/cadswarm.desktop"
        DESK="$(command -v xdg-user-dir >/dev/null && xdg-user-dir DESKTOP || echo "$HOME/Desktop")"
        if [ -d "$DESK" ]; then
            cp "$APPS/cadswarm.desktop" "$DESK/CadSwarm.desktop" && chmod +x "$DESK/CadSwarm.desktop"
            command -v gio >/dev/null && gio set "$DESK/CadSwarm.desktop" metadata::trusted true 2>/dev/null
        fi
        ok "Added CADSwarm to your applications menu and desktop"
    fi
fi

say ""
if [ "$NO_LAUNCH" = 1 ]; then say "${C_HEAD}All set! Open FreeCAD and pick CADSwarm to see the guide.${C_END}"
else say "${C_HEAD}All set! FreeCAD will open now and show you the CADSwarm guide.${C_END}"; fi
say "${C_DIM}(The first time FreeCAD starts it may ask about language and theme; any choice is fine.)${C_END}"
if [ "$NO_LAUNCH" = 0 ]; then
    nohup "${LAUNCH[@]}" "$OPENER" >/dev/null 2>&1 &
    sleep 2
fi
exit 0
