# Windows installer for the standalone CADSwarm desktop application.
# Copies this checkout to the per-user app folder, creates an isolated Python runtime,
# installs CADSwarm's desktop dependencies, and adds Start Menu/Desktop shortcuts.
param([switch]$NoLaunch)
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$InstallRoot = Join-Path $env:LOCALAPPDATA "Programs\CADSwarm"
$AppDir = Join-Path $InstallRoot "app"
$Venv = Join-Path $InstallRoot "runtime"
$Python = Join-Path $Venv "Scripts\python.exe"
$PythonW = Join-Path $Venv "Scripts\pythonw.exe"

function Say($text, $color = "White") { Write-Host $text -ForegroundColor $color }
function Find-PythonLauncher {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $Candidate = & py -3.12 -c "import sys; print(sys.executable)" 2>$null | Select-Object -Last 1
        if ($LASTEXITCODE -eq 0 -and $Candidate) { return [string]$Candidate }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $Candidate = & python -c "import sys; print(sys.executable if sys.version_info >= (3, 11) else '')" 2>$null |
            Select-Object -Last 1
        if ($LASTEXITCODE -eq 0 -and $Candidate) { return [string]$Candidate }
    }
    return $null
}

Say ""
Say "  CADSwarm standalone setup" "Cyan"
Say "  Collaborative parametric CAD for Windows." "Gray"
Say ""

$Launcher = Find-PythonLauncher
if (-not $Launcher) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Python 3.11+ is required. Install Python from python.org, then run this installer again."
    }
    Say "Installing the managed Python runtime..." "Cyan"
    & winget install --id Python.Python.3.12 --exact --scope user --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "Python installation failed (winget exit $LASTEXITCODE)." }
    $env:PATH = "$env:LOCALAPPDATA\Programs\Python\Python312;$env:LOCALAPPDATA\Programs\Python\Python312\Scripts;$env:PATH"
    $Launcher = Find-PythonLauncher
    if (-not $Launcher) { throw "Python was installed, but its launcher could not be found. Sign out and back in, then retry." }
}

New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
if ((Resolve-Path -LiteralPath $Here).Path -ne (Resolve-Path -LiteralPath $AppDir -ErrorAction SilentlyContinue).Path) {
    if (Test-Path -LiteralPath $AppDir) { Remove-Item -LiteralPath $AppDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
    robocopy $Here $AppDir /E /NFL /NDL /NJH /NJS /NP `
        /XD .git .venv __pycache__ .pytest_cache cadswarm-data exports .cadswarm *.egg-info node_modules target .claude `
        /XF *.pyc | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copying CADSwarm failed (robocopy code $LASTEXITCODE)." }
}

if (-not (Test-Path -LiteralPath $Python)) {
    & $Launcher -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create CADSwarm's isolated Python runtime." }
}
Say "Installing CADSwarm and its CAD engine (this may take a few minutes)..." "Cyan"
& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Could not prepare the package installer." }
& $Python -m pip install "$($AppDir)[desktop]"
if ($LASTEXITCODE -ne 0) { throw "CADSwarm or its desktop dependencies could not be installed." }

$Programs = [Environment]::GetFolderPath("Programs")
$StartMenu = Join-Path $Programs "CADSwarm"
New-Item -ItemType Directory -Force -Path $StartMenu | Out-Null
$Shell = New-Object -ComObject WScript.Shell
foreach ($ShortcutPath in @((Join-Path $StartMenu "CADSwarm.lnk"),
        (Join-Path ([Environment]::GetFolderPath("Desktop")) "CADSwarm.lnk"))) {
    $Link = $Shell.CreateShortcut($ShortcutPath)
    $Link.TargetPath = $PythonW
    $Link.Arguments = "-m cadswarm.app"
    $Link.WorkingDirectory = $AppDir
    $Link.Description = "CADSwarm collaborative CAD"
    $Link.Save()
}
$DesktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "CADSwarm.lnk"
$UninstallScript = Join-Path $InstallRoot "uninstall.ps1"
$InstallRootLiteral = $InstallRoot.Replace("'", "''")
$StartMenuLiteral = $StartMenu.Replace("'", "''")
$DesktopShortcutLiteral = $DesktopShortcut.Replace("'", "''")
@"
Remove-Item -LiteralPath '$InstallRootLiteral' -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath '$StartMenuLiteral' -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath '$DesktopShortcutLiteral' -Force -ErrorAction SilentlyContinue
"@ | Set-Content -LiteralPath $UninstallScript -Encoding utf8
$Uninstall = Join-Path $StartMenu "Uninstall CADSwarm.lnk"
$Link = $Shell.CreateShortcut($Uninstall)
$Link.TargetPath = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
$Link.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$UninstallScript`""
$Link.Description = "Uninstall CADSwarm"
$Link.Save()

Say "  [ok] Installed CADSwarm without FreeCAD" "Green"
Say "  Start Menu: CADSwarm" "Gray"
if (-not $NoLaunch) { Start-Process -FilePath $PythonW -ArgumentList "-m cadswarm.app" -WorkingDirectory $AppDir }
