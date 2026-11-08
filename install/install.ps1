# CADSwarm installer for Windows. Run via "Install CadSwarm.cmd" (double-click) or the one-line quick install.
#
# Installs CADSwarm as a desktop app, per user, no admin rights needed:
#   * finds FreeCAD 1.x (CADSwarm's geometry engine, used unmodified), installing it with winget if missing;
#   * copies CADSwarm into %LOCALAPPDATA%\Programs\CADSwarm;
#   * adds "CADSwarm" to the Start menu and the desktop: FreeCAD's pythonw running `-m cadswarm.app` from that
#     folder, with the CADSwarm icon and AppUserModelID "CADSwarm.Desktop" (so the taskbar groups it as CADSwarm);
#   * registers "Open with CADSwarm" for .FCStd files (HKCU\Software\Classes; the default app isn't changed);
#   * keeps the FreeCAD workbench: FreeCAD's own add-on folder gets a link to the same CADSwarm folder.
#
# Options (all optional; the tests use them to install into a sandbox):
#   -Yes             don't ask; take the defaults (shortcuts, file association, workbench; never winget)
#   -NoLaunch        install only, don't start CADSwarm
#   -NoShortcut      no Start menu or desktop shortcut
#   -NoDesktop       Start menu shortcut only
#   -NoFileAssoc     don't register "Open with CADSwarm"
#   -NoWorkbench     don't link CADSwarm into FreeCAD's own add-on folder
#   -FreeCAD PATH    the FreeCAD to use (its folder or bin\freecad.exe); also $env:CADSWARM_FREECAD
#   -InstallDir, -StartMenuDir, -DesktopDir, -ClassesKey   where things go
param(
    [switch]$NoLaunch, [switch]$Yes, [switch]$NoShortcut, [switch]$NoDesktop, [switch]$NoFileAssoc,
    [switch]$NoWorkbench,
    [string]$FreeCAD = $env:CADSWARM_FREECAD,
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\CADSwarm"),
    [string]$StartMenuDir = [Environment]::GetFolderPath("Programs"),
    [string]$DesktopDir = [Environment]::GetFolderPath("Desktop"),
    [string]$ClassesKey = "HKCU:\Software\Classes"
)
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$AppId = "CADSwarm.Desktop"
$ProgId = "CADSwarm.FCStd"
$Description = "Build 3D designs with your team's AI helpers"

function Say($text, $color = "White") { Write-Host $text -ForegroundColor $color }
function Ok($text) { Say "  [ok] $text" "Green" }
function Finish($code) {
    if (-not $Yes) { Write-Host ""; Read-Host "Press Enter to close this window" | Out-Null }
    exit $code
}
function Ask($question) {  # default yes
    if ($Yes) { return $true }
    return ((Read-Host "  $question (Y/n)") -notmatch "^[nN]")
}

Say ""
Say "  CADSwarm setup" "Cyan"
Say "  Build 3D designs together with your team's AI helpers." "Gray"
Say ""

# ---------------------------------------------------------------- 1. find FreeCAD (the engine)
function FindFreeCAD {
    $found = @()
    foreach ($root in @("$env:LOCALAPPDATA\Programs", $env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ($root -and (Test-Path $root)) {
            $found += Get-ChildItem -Path $root -Directory -Filter "FreeCAD*" -ErrorAction SilentlyContinue |
                ForEach-Object { Join-Path $_.FullName "bin\freecad.exe" } | Where-Object { Test-Path $_ }
        }
    }
    return $found
}
# Newest version wins; sort by the number in the folder name, since as text "1.9" would beat "1.10".
function FolderVersion($exe) {
    $m = [regex]::Match((Split-Path (Split-Path $exe)), '(\d+(?:\.\d+)+)')
    if ($m.Success) { try { return [version]$m.Groups[1].Value } catch {} }
    return [version]"0.0"
}
if ($FreeCAD) {
    $p = $FreeCAD.Trim('"')
    if (Test-Path -PathType Container $p) {
        $p = if (Test-Path (Join-Path $p "bin\freecad.exe")) { Join-Path $p "bin\freecad.exe" } else { Join-Path $p "freecad.exe" }
    }
    if (-not (Test-Path $p)) { Say "There's no FreeCAD at $FreeCAD." "Red"; Finish 1 }
    $candidates = @($p)
} else {
    $candidates = FindFreeCAD
}
if (-not $candidates) {
    Say "CADSwarm runs on FreeCAD, which isn't installed on this computer yet." "Yellow"
    if (-not $Yes -and (Get-Command winget -ErrorAction SilentlyContinue)) {
        if (Ask "Install the official FreeCAD now with winget?") {
            winget install --id FreeCAD.FreeCAD --exact --accept-package-agreements --accept-source-agreements
            $candidates = FindFreeCAD
        }
    }
}
if (-not $candidates) {
    Say "Opening the FreeCAD download page. Install it (the default options are fine),"
    Say "then double-click 'Install CadSwarm' again."
    if (-not $Yes) { Start-Process "https://www.freecad.org/downloads.php" }
    Finish 1
}
$FreeCADExe = ($candidates | Sort-Object { FolderVersion $_ } -Descending | Select-Object -First 1)
$Bin = Split-Path $FreeCADExe
$Python = Join-Path $Bin "python.exe"
$Pythonw = Join-Path $Bin "pythonw.exe"
if (-not (Test-Path $Pythonw)) {
    Say "This FreeCAD ($FreeCADExe) has no Python of its own, which CADSwarm needs." "Red"
    Say "Install the official FreeCAD 1.x from https://www.freecad.org/downloads.php, then run this again."
    Finish 1
}
Ok "Found FreeCAD: $FreeCADExe"

# ---------------------------------------------------------------- 2. copy CADSwarm into its app folder
# A clean copy each time (updates included). Only code lives here: projects are in Documents, settings in
# %APPDATA%\CadSwarm. Copied to a staging folder first, so a failed copy never leaves a half-installed app.
function SamePath($a, $b) {
    $norm = { param($p) [IO.Path]::GetFullPath($p).TrimEnd([char[]]'\/') }
    return (& $norm $a) -ieq (& $norm $b)
}
function IsLink($path) {
    $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    return [bool]($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint))
}
function LooksInstalled($path) { return (Test-Path (Join-Path $path "cadswarm\app\__main__.py")) }

$InstallDir = [IO.Path]::GetFullPath($InstallDir)
if (SamePath $Here $InstallDir) {
    Ok "CADSwarm is already installed in $InstallDir"
} else {
    if ((Test-Path $InstallDir) -and -not (LooksInstalled $InstallDir) -and
            (Get-ChildItem -Force $InstallDir -ErrorAction SilentlyContinue | Select-Object -First 1)) {
        Say "$InstallDir already exists and isn't a CADSwarm folder, so it was left alone." "Red"
        Say "Pick another folder with -InstallDir." "Red"
        Finish 1
    }
    $Stage = "$InstallDir.new"
    if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
    New-Item -ItemType Directory -Force -Path $Stage | Out-Null
    robocopy $Here $Stage /E /NFL /NDL /NJH /NJS /NP `
        /XD .git .venv __pycache__ .pytest_cache cadswarm-data exports .cadswarm *.egg-info node_modules target `
            upstream .claude `
        /XF *.pyc .git | Out-Null  # .git is a file in a git worktree
    if ($LASTEXITCODE -ge 8) { Say "Copying CADSwarm failed (robocopy code $LASTEXITCODE)." "Red"; Finish 1 }
    # The team server's database module: a local build if there is one, else the module the installer
    # downloaded beside the code (build folders aren't copied). Never leave a freshly installed
    # app to a stale download in the app folder.
    $Wasm = Join-Path $Here "module\target\wasm32-unknown-unknown\release\cadswarm_module.wasm"
    if (-not (Test-Path $Wasm)) { $Wasm = Join-Path $Here "module\cadswarm_module.wasm" }
    if (Test-Path $Wasm) { Copy-Item $Wasm (Join-Path $Stage "module\cadswarm_module.wasm") }
    if (Test-Path $InstallDir) {
        # Python files aren't kept open, so this works even while CADSwarm runs; anything still in use
        # stays in the .old folder until the next update.
        $Old = "$InstallDir.old"
        if (Test-Path $Old) { Remove-Item -Recurse -Force $Old -ErrorAction SilentlyContinue }
        try { Rename-Item -LiteralPath $InstallDir -NewName (Split-Path -Leaf $Old) }
        catch { Remove-Item -Recurse -Force $Stage; Say "Couldn't replace $InstallDir. Close CADSwarm and try again." "Red"; Finish 1 }
        Remove-Item -Recurse -Force $Old -ErrorAction SilentlyContinue
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $InstallDir) | Out-Null
    Rename-Item -LiteralPath $Stage -NewName (Split-Path -Leaf $InstallDir)
    Ok "Installed CADSwarm into $InstallDir"
}

# ---------------------------------------------------------------- 3. Start menu and desktop shortcuts
# The CADSwarm icon, once the brand assets include one; FreeCAD's until then.
$Icon = "$FreeCADExe"
foreach ($rel in @("cadswarm\gui\cadswarm.ico", "design\cadswarm.ico", "design\icons\cadswarm.ico")) {
    if (Test-Path (Join-Path $InstallDir $rel)) { $Icon = Join-Path $InstallDir $rel; break }
}
Add-Type -Path (Join-Path $InstallDir "install\shortcut.cs")
function MakeShortcut($dir) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    # Also replaces the old installer's "CadSwarm.lnk", which opened FreeCAD (same name to Windows).
    $path = Join-Path $dir "CADSwarm.lnk"
    if (Test-Path $path) { Remove-Item -Force $path }
    [CadSwarmSetup.Shortcut]::Create($path, $Pythonw, "-m cadswarm.app", $InstallDir, $Icon, 0, $Description, $AppId)
    return $path
}
$StartLink = $null
if (-not $NoShortcut) {
    $StartLink = MakeShortcut $StartMenuDir
    Ok "Added CADSwarm to the Start menu"
    if (-not $NoDesktop -and (Ask "Add a CADSwarm shortcut to your desktop?")) {
        MakeShortcut $DesktopDir | Out-Null
        Ok "Added a CADSwarm shortcut to your desktop"
    }
}

# ---------------------------------------------------------------- 4. "Open with CADSwarm" for .FCStd files
# Per user, no admin. CADSwarm is offered in "Open with"; the default app for .FCStd is not changed.
# The script path (not -m), because Explorer starts it in the document's folder.
if (-not $NoFileAssoc) {
    $Main = Join-Path $InstallDir "cadswarm\app\__main__.py"
    $Command = "`"$Pythonw`" `"$Main`" `"%1`""
    $Prog = Join-Path $ClassesKey $ProgId
    New-Item -Force -Path "$Prog\shell\open\command" | Out-Null
    New-Item -Force -Path "$Prog\DefaultIcon" | Out-Null
    Set-ItemProperty -Path $Prog -Name "(default)" -Value "CADSwarm design"
    Set-ItemProperty -Path $Prog -Name "FriendlyTypeName" -Value "CADSwarm design"
    Set-ItemProperty -Path $Prog -Name "AppUserModelID" -Value $AppId
    Set-ItemProperty -Path "$Prog\DefaultIcon" -Name "(default)" -Value "`"$Icon`",0"
    Set-ItemProperty -Path "$Prog\shell\open" -Name "(default)" -Value "Open with CADSwarm"
    Set-ItemProperty -Path "$Prog\shell\open\command" -Name "(default)" -Value $Command
    $Ext = Join-Path $ClassesKey ".FCStd\OpenWithProgids"
    if (-not (Test-Path $Ext)) { New-Item -Force -Path $Ext | Out-Null }
    New-ItemProperty -Force -Path $Ext -Name $ProgId -PropertyType String -Value "" | Out-Null
    try { [CadSwarmSetup.Shortcut]::NotifyAssociationsChanged() } catch {}
    Ok "CADSwarm is in 'Open with' for .FCStd files"
}

# ---------------------------------------------------------------- 5. the FreeCAD workbench
# FreeCAD itself also gets CADSwarm as a workbench: its add-on folder links to the same installed copy.
# (The CADSwarm app links it into its own engine profile when it starts, see branded.link_addon.)
if (-not $NoWorkbench) {
    # Windows PowerShell 5.1 turns any stderr output from a native program into a terminating error under
    # "Stop", so relax it for this call: FreeCAD's python may print warnings, which are harmless.
    $ErrorActionPreference = "Continue"
    $UserDir = & $Python -c "import sys,os; sys.path.append(os.path.dirname(sys.executable)); import FreeCAD; print('CADSWARM_DIR=' + FreeCAD.getUserAppDataDir())" 2>$null |
        Where-Object { "$_" -like "CADSWARM_DIR=*" } | Select-Object -Last 1
    $ErrorActionPreference = "Stop"
    if (-not $UserDir) {
        Say "  Couldn't ask FreeCAD where its add-ons go, so the FreeCAD workbench was skipped." "Yellow"
    } else {
        $ModDest = Join-Path "$UserDir".Substring("CADSWARM_DIR=".Length) "Mod\CadSwarm"
        if (SamePath $Here $ModDest) {
            Ok "CADSwarm is a workbench in FreeCAD (the folder this setup ran from)"
        } else {
            if (IsLink $ModDest) {
                [IO.Directory]::Delete($ModDest, $false)  # the link only, never what it points to
            } elseif (Test-Path $ModDest) {
                if (Test-Path (Join-Path $ModDest "InitGui.py")) { Remove-Item -Recurse -Force $ModDest }  # the old installer's copy
                else { Say "  $ModDest isn't CADSwarm, so it was left alone." "Yellow"; $ModDest = $null }
            }
            if ($ModDest) {
                New-Item -ItemType Directory -Force -Path (Split-Path $ModDest) | Out-Null
                New-Item -ItemType Junction -Path $ModDest -Target $InstallDir | Out-Null
                Ok "CADSwarm is also a workbench in FreeCAD"
            }
        }
    }
}

Say ""
if ($NoLaunch) { Say "All set! Start CADSwarm from the Start menu or your desktop." "Cyan" }
else { Say "All set! CADSwarm will open now." "Cyan" }
if (-not $NoLaunch) {
    if ($StartLink) { Start-Process -FilePath $StartLink }  # through the shortcut: CADSwarm's taskbar identity
    else { Start-Process -FilePath $Pythonw -ArgumentList "-m cadswarm.app" -WorkingDirectory $InstallDir }
    Start-Sleep -Seconds 2
}
exit 0
