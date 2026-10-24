# CadSwarm installer for Windows. Run via "Install CadSwarm.cmd" (double-click).
# Copies this folder into FreeCAD's add-on folder, optionally adds a desktop shortcut,
# then opens FreeCAD in CadSwarm, where a step-by-step guide takes over.
param([switch]$NoLaunch)  # -NoLaunch: install only (used for testing)
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Say($text, $color = "White") { Write-Host $text -ForegroundColor $color }
function Finish($code) { Write-Host ""; Read-Host "Press Enter to close this window" | Out-Null; exit $code }

Say ""
Say "  CadSwarm setup" "Cyan"
Say "  Build 3D designs together with your team's AI helpers." "Gray"
Say ""

# 1. Find FreeCAD
$candidates = @()
foreach ($root in @("$env:LOCALAPPDATA\Programs", $env:ProgramFiles, ${env:ProgramFiles(x86)})) {
    if ($root -and (Test-Path $root)) {
        $candidates += Get-ChildItem -Path $root -Directory -Filter "FreeCAD*" -ErrorAction SilentlyContinue |
            ForEach-Object { Join-Path $_.FullName "bin\freecad.exe" } | Where-Object { Test-Path $_ }
    }
}
if (-not $candidates) {
    Say "FreeCAD isn't installed on this computer yet." "Yellow"
    Say "Opening the FreeCAD download page. Install it (the default options are fine),"
    Say "then double-click 'Install CadSwarm' again."
    Start-Process "https://www.freecad.org/downloads.php"
    Finish 1
}
$FreeCAD = ($candidates | Sort-Object -Descending | Select-Object -First 1)
$Python = Join-Path (Split-Path $FreeCAD) "python.exe"
Say "  [ok] Found FreeCAD: $FreeCAD" "Green"

# 2. Where FreeCAD keeps add-ons for this user
$UserDir = & $Python -c "import sys,os; sys.path.append(os.path.dirname(sys.executable)); import FreeCAD; print(FreeCAD.getUserAppDataDir())" 2>$null |
    Select-Object -Last 1
if (-not $UserDir) { Say "Couldn't ask FreeCAD where its add-ons go." "Red"; Finish 1 }
$Dest = Join-Path $UserDir "Mod\CadSwarm"

if (Get-Process -Name "freecad" -ErrorAction SilentlyContinue) {
    Say "FreeCAD is open. Please close it (save your work first), then press Enter." "Yellow"
    Read-Host | Out-Null
}

# 3. Copy CadSwarm into FreeCAD
# A clean copy each time (updates included). Only code lives here: projects are in Documents.
if (Test-Path $Dest) { Remove-Item -Recurse -Force $Dest }
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
robocopy $Here $Dest /E /NFL /NDL /NJH /NJS /NP `
    /XD .git .venv __pycache__ .pytest_cache cadswarm-data exports .cadswarm *.egg-info /XF *.pyc | Out-Null
if ($LASTEXITCODE -ge 8) { Say "Copying CadSwarm into FreeCAD failed (robocopy code $LASTEXITCODE)." "Red"; Finish 1 }
Say "  [ok] Installed CadSwarm into FreeCAD" "Green"

$Opener = Join-Path $Dest "cadswarm\gui\open_cadswarm.py"

# 4. Optional desktop shortcut that opens FreeCAD straight into CadSwarm
$answer = Read-Host "  Add a 'CadSwarm' shortcut to your desktop? (Y/n)"
if ($answer -notmatch "^[nN]") {
    $Shell = New-Object -ComObject WScript.Shell
    $Link = $Shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "CadSwarm.lnk"))
    $Link.TargetPath = $FreeCAD
    $Link.Arguments = "`"$Opener`""
    $Link.IconLocation = "$FreeCAD,0"
    $Link.Description = "Build 3D designs with your team's AI helpers"
    $Link.Save()
    Say "  [ok] Added a CadSwarm shortcut to your desktop" "Green"
}

Say ""
Say "All set! FreeCAD will open now and show you the CadSwarm guide." "Cyan"
Say "(The first time FreeCAD starts it may ask about language and theme; any choice is fine.)" "Gray"
if (-not $NoLaunch) { Start-Process -FilePath $FreeCAD -ArgumentList "`"$Opener`"" }
Start-Sleep -Seconds 2
exit 0
