# CADSwarm installer for Windows. Run via "Install CadSwarm.cmd" (double-click).
# Copies this folder into FreeCAD's add-on folder, optionally adds a desktop shortcut,
# then opens FreeCAD in CADSwarm, where a step-by-step guide takes over.
param([switch]$NoLaunch)  # -NoLaunch: install only (used for testing)
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Say($text, $color = "White") { Write-Host $text -ForegroundColor $color }
function Finish($code) { Write-Host ""; Read-Host "Press Enter to close this window" | Out-Null; exit $code }

Say ""
Say "  CADSwarm setup" "Cyan"
Say "  Build 3D designs together with your team's AI helpers." "Gray"
Say ""

# 1. Find FreeCAD
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
$candidates = FindFreeCAD
if (-not $candidates) {
    Say "FreeCAD isn't installed on this computer yet." "Yellow"
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $answer = Read-Host "  Install the official FreeCAD now with winget? (Y/n)"
        if ($answer -notmatch "^[nN]") {
            winget install --id FreeCAD.FreeCAD --exact --accept-package-agreements --accept-source-agreements
            $candidates = FindFreeCAD
        }
    }
}
if (-not $candidates) {
    Say "Opening the FreeCAD download page. Install it (the default options are fine),"
    Say "then double-click 'Install CadSwarm' again."
    Start-Process "https://www.freecad.org/downloads.php"
    Finish 1
}
# Newest version wins; sort by the number in the folder name, since as text "1.9" would beat "1.10".
function FolderVersion($exe) {
    $m = [regex]::Match((Split-Path (Split-Path $exe)), '(\d+(?:\.\d+)+)')
    if ($m.Success) { try { return [version]$m.Groups[1].Value } catch {} }
    return [version]"0.0"
}
$FreeCAD = ($candidates | Sort-Object { FolderVersion $_ } -Descending | Select-Object -First 1)
$Python = Join-Path (Split-Path $FreeCAD) "python.exe"
Say "  [ok] Found FreeCAD: $FreeCAD" "Green"

# 2. Where FreeCAD keeps add-ons for this user
# Windows PowerShell 5.1 turns any stderr output from a native program into a terminating error under
# "Stop", so relax it for this call: FreeCAD's python may print warnings, which are harmless.
$ErrorActionPreference = "Continue"
$UserDir = & $Python -c "import sys,os; sys.path.append(os.path.dirname(sys.executable)); import FreeCAD; print(FreeCAD.getUserAppDataDir())" 2>$null |
    Select-Object -Last 1
$ErrorActionPreference = "Stop"
if (-not $UserDir) { Say "Couldn't ask FreeCAD where its add-ons go." "Red"; Finish 1 }
$Dest = Join-Path $UserDir "Mod\CadSwarm"

if (Get-Process -Name "freecad" -ErrorAction SilentlyContinue) {
    Say "FreeCAD is open. Please close it (save your work first), then press Enter." "Yellow"
    Read-Host | Out-Null
}

# 3. Copy CADSwarm into FreeCAD
# A clean copy each time (updates included). Only code lives here: projects are in Documents.
# If this script was started from the installed copy there is nothing to copy (and deleting $Dest
# would delete the files we are running from).
function SamePath($a, $b) {
    $norm = { param($p) [IO.Path]::GetFullPath($p).TrimEnd([char[]]'\/') }
    return (& $norm $a) -ieq (& $norm $b)
}
if (SamePath $Here $Dest) {
    Say "  [ok] CADSwarm is already installed in FreeCAD" "Green"
} else {
    if (Test-Path $Dest) { Remove-Item -Recurse -Force $Dest }
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    robocopy $Here $Dest /E /NFL /NDL /NJH /NJS /NP `
        /XD .git .venv __pycache__ .pytest_cache cadswarm-data exports .cadswarm *.egg-info node_modules target `
        /XF *.pyc | Out-Null
    if ($LASTEXITCODE -ge 8) { Say "Copying CADSwarm into FreeCAD failed (robocopy code $LASTEXITCODE)." "Red"; Finish 1 }
    # The team server's database module: a local build if there is one, else the module the installer
    # downloaded beside the code (build folders aren't copied). Never leave a freshly installed
    # workbench to a stale download in the app folder.
    $Wasm = Join-Path $Here "module\target\wasm32-unknown-unknown\release\cadswarm_module.wasm"
    if (-not (Test-Path $Wasm)) { $Wasm = Join-Path $Here "module\cadswarm_module.wasm" }
    if (Test-Path $Wasm) { Copy-Item $Wasm (Join-Path $Dest "module\cadswarm_module.wasm") }
    Say "  [ok] Installed CADSwarm into FreeCAD" "Green"
}

$Opener = Join-Path $Dest "cadswarm\gui\open_cadswarm.py"

# 4. Optional desktop shortcut that opens FreeCAD straight into CADSwarm
$answer = Read-Host "  Add a 'CADSwarm' shortcut to your desktop? (Y/n)"
if ($answer -notmatch "^[nN]") {
    $Shell = New-Object -ComObject WScript.Shell
    $Link = $Shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "CadSwarm.lnk"))
    $Link.TargetPath = $FreeCAD
    $Link.Arguments = "`"$Opener`""
    $Link.IconLocation = "$FreeCAD,0"
    $Link.Description = "Build 3D designs with your team's AI helpers"
    $Link.Save()
    Say "  [ok] Added a CADSwarm shortcut to your desktop" "Green"
}

Say ""
if ($NoLaunch) { Say "All set! Open FreeCAD and pick CADSwarm to see the guide." "Cyan" }
else { Say "All set! FreeCAD will open now and show you the CADSwarm guide." "Cyan" }
Say "(The first time FreeCAD starts it may ask about language and theme; any choice is fine.)" "Gray"
if (-not $NoLaunch) { Start-Process -FilePath $FreeCAD -ArgumentList "`"$Opener`"" }
Start-Sleep -Seconds 2
exit 0
