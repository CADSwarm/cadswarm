from pathlib import Path
import shutil


ROOT = Path(r"C:\Users\sachc\Projects\cadswarm-fc")


def replace(rel, old, new):
    path = ROOT / rel
    raw = path.read_bytes()
    newline = b"\r\n" if raw.count(b"\r\n") > 100 else b"\n"
    text = raw.decode("utf-8").replace("\r\n", "\n")
    if old not in text:
        raise SystemExit(f"Expected text not found in {rel}: {old[:100]!r}")
    text = text.replace(old, new, 1)
    path.write_bytes(text.replace("\n", newline.decode("ascii")).encode("utf-8"))


# The CLI prints an explicit label so a custom MCP client can find/copy its config.
replace(
    "cadswarm/cli.py",
    'print("Next: add this MCP server to your agent\'s settings, then start it in this project folder:")',
    'print("Next: add this MCP server entry to your agent\'s settings, then start it in this project folder:")',
)

# Use the same original CADSwarm mark for the FreeCAD title bar/workbench and installers.
shutil.copyfile(ROOT / "design" / "icon" / "cadswarm.svg", ROOT / "cadswarm" / "gui" / "cadswarm.svg")

# Windows updates use private, unique staging/backup paths and roll back if promotion fails.
ps = ROOT / "install" / "install.ps1"
raw = ps.read_bytes()
newline = b"\r\n" if raw.count(b"\r\n") > 100 else b"\n"
text = raw.decode("utf-8").replace("\r\n", "\n")
start = text.index('    $Stage = "$InstallDir.new"')
end_marker = '    Ok "Installed CADSwarm into $InstallDir"\n}'
end = text.index(end_marker, start) + len(end_marker)
new_block = r'''    $Parent = Split-Path -Parent $InstallDir
    New-Item -ItemType Directory -Force -Path $Parent | Out-Null
    $Stage = Join-Path $Parent (".cadswarm-stage-" + [guid]::NewGuid().ToString("N"))
    $Backup = $null
    try {
        New-Item -ItemType Directory -Path $Stage | Out-Null
        robocopy $Here $Stage /E /NFL /NDL /NJH /NJS /NP `
            /XD .git .venv __pycache__ .pytest_cache cadswarm-data exports .cadswarm *.egg-info node_modules target `
                upstream .claude `
            /XF *.pyc .git | Out-Null  # .git is a file in a git worktree
        $CopyCode = $LASTEXITCODE
        if ($CopyCode -ge 8) { throw "Copying CADSwarm failed (robocopy code $CopyCode)." }
        # The team server's database module: a local build wins, else the bundled module.
        $Wasm = Join-Path $Here "module\target\wasm32-unknown-unknown\release\cadswarm_module.wasm"
        if (-not (Test-Path $Wasm)) { $Wasm = Join-Path $Here "module\cadswarm_module.wasm" }
        if (Test-Path $Wasm) { Copy-Item $Wasm (Join-Path $Stage "module\cadswarm_module.wasm") }
        if (Test-Path $InstallDir) {
            if ((IsLink $InstallDir) -or -not (LooksInstalled $InstallDir)) {
                throw "$InstallDir is not a regular CADSwarm install; it was left untouched."
            }
            $Backup = Join-Path $Parent (".cadswarm-backup-" + [guid]::NewGuid().ToString("N"))
            Rename-Item -LiteralPath $InstallDir -NewName (Split-Path -Leaf $Backup)
        }
        try {
            Rename-Item -LiteralPath $Stage -NewName (Split-Path -Leaf $InstallDir)
            $Stage = $null
        } catch {
            if ($Backup -and (Test-Path $Backup) -and -not (Test-Path $InstallDir)) {
                Rename-Item -LiteralPath $Backup -NewName (Split-Path -Leaf $InstallDir)
                $Backup = $null
            }
            throw
        }
        if ($Backup -and (Test-Path $Backup)) {
            try { Remove-Item -LiteralPath $Backup -Recurse -Force }
            catch { Say "The new app is installed; the previous CADSwarm copy remains at $Backup." "Yellow" }
            $Backup = $null
        }
        Ok "Installed CADSwarm into $InstallDir"
    } catch {
        if ($Backup -and (Test-Path $Backup) -and -not (Test-Path $InstallDir)) {
            try { Rename-Item -LiteralPath $Backup -NewName (Split-Path -Leaf $InstallDir) } catch {}
        }
        if ($Stage -and (Test-Path $Stage)) { Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue }
        Say "CADSwarm install failed: $_" "Red"
        Finish 1
    }
}'''
new_block = new_block.replace("\n+", "\n")
text = text[:start] + new_block + text[end:]
text = text.replace(
    "# The CADSwarm icon, once the brand assets include one; FreeCAD's until then.\n",
    "# The branded CADSwarm .ico is preferred; FreeCAD's icon is only a fallback for older bundles.\n",
)
ps.write_bytes(text.replace("\n", newline.decode("ascii")).encode("utf-8"))

# Unix installers: preserve unrelated macOS apps, replace only a verified CADSwarm bundle, and
# quote app-launcher arguments as shell words instead of interpolating them in double quotes.
old_mac = '''make_mac_app() {  # $1 = path of the .app bundle; a real bundle around FreeCAD's python
    local app="$1" icns=""
    rm -rf "$app" && mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources" || return 1
    for icns in "$DEST/design/cadswarm.icns" "$DEST/cadswarm/gui/cadswarm.icns" ""; do [ -z "$icns" ] || [ -f "$icns" ] && break; done
    [ -n "$icns" ] && cp "$icns" "$app/Contents/Resources/cadswarm.icns"
    {
        echo '#!/bin/bash'
        echo '# CADSwarm: FreeCAD (unmodified) running the CADSwarm app. Written by the CADSwarm installer.'
        echo "cd \\"\\$HOME\\" && exec $(quote_args "${RUN[@]}") \\"\\$@\\""
    } > "$app/Contents/MacOS/CADSwarm"
    chmod +x "$app/Contents/MacOS/CADSwarm"
    cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>CADSwarm</string>
    <key>CFBundleDisplayName</key><string>CADSwarm</string>
    <key>CFBundleIdentifier</key><string>org.cadswarm.desktop</string>
    <key>CFBundleExecutable</key><string>CADSwarm</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleIconFile</key><string>cadswarm</string>
    <key>LSMinimumSystemVersion</key><string>11.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>LSApplicationCategoryType</key><string>public.app-category.graphics-design</string>
    <key>CFBundleDocumentTypes</key>
    <array><dict>
        <key>CFBundleTypeName</key><string>CADSwarm design</string>
        <key>CFBundleTypeExtensions</key><array><string>FCStd</string><string>fcstd</string></array>
        <key>CFBundleTypeRole</key><string>Editor</string>
        <key>LSHandlerRank</key><string>Alternate</string>
    </dict></array>
    <key>CADSwarmInstallDir</key><string>$(xml_escape "$DEST")</string>
</dict>
</plist>
PLIST
}'''
new_mac = r'''mac_bundle_id() {
    /usr/libexec/PlistBuddy -c 'Print CFBundleIdentifier' "$1/Contents/Info.plist" 2>/dev/null || true
}

replace_mac_app() {  # stage is ours; an existing destination must be our identified app
    local stage="$1" app="$2" backup="" ident=""
    if [ -e "$app" ]; then
        ident="$(mac_bundle_id "$app")"
        if [ "$ident" != "org.cadswarm.desktop" ]; then
            warn "  $app already exists and isn't a CADSwarm app; it was left untouched."
            return 1
        fi
        backup="${app}.cadswarm-backup.$$"
        [ ! -e "$backup" ] || return 1
        mv "$app" "$backup" || return 1
    fi
    if mv "$stage" "$app"; then
        [ -z "$backup" ] || rm -rf "$backup"
        return 0
    fi
    [ -z "$backup" ] || mv "$backup" "$app"
    return 1
}

link_mac_desktop() {
    local app="$1" link="$HOME/Desktop/CADSwarm.app"
    if [ -e "$link" ] || [ -L "$link" ]; then
        if [ -L "$link" ] && [ "$(readlink "$link")" = "$app" ]; then return 0; fi
        warn "  $link already exists; it was left untouched."
        return 1
    fi
    mkdir -p "$HOME/Desktop" && ln -s "$app" "$link"
}

make_mac_app() {  # $1 = a new staging .app bundle; never removes an existing path
    local app="$1" icns="" candidate="" arg icon_key=""
    [ ! -e "$app" ] && [ ! -L "$app" ] || return 1
    mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources" || return 1
    for candidate in "$DEST/design/cadswarm.icns" "$DEST/cadswarm/gui/cadswarm.icns"; do
        if [ -f "$candidate" ]; then icns="$candidate"; break; fi
    done
    if [ -n "$icns" ]; then
        cp "$icns" "$app/Contents/Resources/cadswarm.icns" || return 1
        icon_key='    <key>CFBundleIconFile</key><string>cadswarm</string>'
    fi
    {
        echo '#!/bin/bash'
        echo '# CADSwarm desktop launcher.'
        printf 'cd "$HOME" && exec'
        for arg in "${RUN[@]}"; do printf ' %s' "$(shell_quote "$arg")"; done
        printf ' "$@"\n'
    } > "$app/Contents/MacOS/CADSwarm"
    chmod +x "$app/Contents/MacOS/CADSwarm"
    cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>CADSwarm</string>
    <key>CFBundleDisplayName</key><string>CADSwarm</string>
    <key>CFBundleIdentifier</key><string>org.cadswarm.desktop</string>
    <key>CFBundleExecutable</key><string>CADSwarm</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
${icon_key}
    <key>LSMinimumSystemVersion</key><string>11.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>LSApplicationCategoryType</key><string>public.app-category.graphics-design</string>
    <key>CFBundleDocumentTypes</key>
    <array><dict>
        <key>CFBundleTypeName</key><string>CADSwarm design</string>
        <key>CFBundleTypeExtensions</key><array><string>FCStd</string><string>fcstd</string></array>
        <key>CFBundleTypeRole</key><string>Editor</string>
        <key>LSHandlerRank</key><string>Alternate</string>
    </dict></array>
    <key>CADSwarmInstallDir</key><string>$(xml_escape "$DEST")</string>
</dict>
</plist>
PLIST
}'''
replace("install/install.sh", old_mac, new_mac)

old_darwin = '''        APP="$HOME/Applications/CADSwarm.app"
        mkdir -p "$HOME/Applications"
        rm -rf "$HOME/Applications/CadSwarm.app" 2>/dev/null  # the old installer's launcher (same name on APFS)
        if [ "${#RUN[@]}" -gt 0 ] && make_mac_app "$APP"; then
            ln -sfn "$APP" "$HOME/Desktop/CADSwarm.app" 2>/dev/null
            ok "Added the CADSwarm app to ~/Applications and your desktop"
        else
            SCRIPT="do shell script \\"$(quote_args "${LAUNCH[@]}" "$OPENER" | sed 's/\\/\\\\/g; s/"/\\\\"/g') > /dev/null 2>&1 &\\""
            if command -v osacompile >/dev/null && rm -rf "$APP" && osacompile -o "$APP" -e "$SCRIPT" 2>/dev/null; then
                ln -sfn "$APP" "$HOME/Desktop/CADSwarm.app" 2>/dev/null
                ok "Added CADSwarm to ~/Applications and your desktop"
            else
                warn "  Couldn't make the shortcut; open FreeCAD and pick CADSwarm instead."
            fi
        fi'''
new_darwin = r'''        APP="$HOME/Applications/CADSwarm.app"
        mkdir -p "$HOME/Applications"
        if [ "${#RUN[@]}" -gt 0 ]; then
            APP_STAGE="$HOME/Applications/.cadswarm-stage-$$.app"
            if [ ! -e "$APP_STAGE" ] && make_mac_app "$APP_STAGE" && replace_mac_app "$APP_STAGE" "$APP"; then
                link_mac_desktop "$APP" || true
                ok "Added the CADSwarm app to ~/Applications"
            else
                [ ! -e "$APP_STAGE" ] || rm -rf "$APP_STAGE"
                warn "  Couldn't safely replace the CADSwarm app bundle."
            fi
        else
            APP_STAGE="$HOME/Applications/.cadswarm-stage-$$.app"
            SHELL_COMMAND=""
            for arg in "${LAUNCH[@]}" "$OPENER"; do SHELL_COMMAND+=" $(shell_quote "$arg")"; done
            SCRIPT="do shell script \\"${SHELL_COMMAND} > /dev/null 2>&1 &\\""
            if [ ! -e "$APP_STAGE" ] && command -v osacompile >/dev/null && osacompile -o "$APP_STAGE" -e "$SCRIPT" 2>/dev/null \
                    && replace_mac_app "$APP_STAGE" "$APP"; then
                link_mac_desktop "$APP" || true
                ok "Added CADSwarm to ~/Applications"
            else
                [ ! -e "$APP_STAGE" ] || rm -rf "$APP_STAGE"
                warn "  Couldn't safely make the shortcut; open FreeCAD and pick CADSwarm instead."
            fi
        fi'''
replace("install/install.sh", old_darwin, new_darwin)

print("Applied CADSwarm integration hardening and brand icon updates")
