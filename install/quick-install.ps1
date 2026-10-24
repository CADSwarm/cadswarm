# One-line CADSwarm install for Windows (paste into PowerShell):
#   irm https://raw.githubusercontent.com/SachchitBalamurugan/cadswarm/main/install/quick-install.ps1 | iex
# Downloads CADSwarm, then runs install\install.ps1. $env:CADSWARM_REF picks a branch or tag.
$ErrorActionPreference = "Stop"
$Repo = "SachchitBalamurugan/cadswarm"
$Ref = if ($env:CADSWARM_REF) { $env:CADSWARM_REF } else { "main" }
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Tmp = Join-Path ([IO.Path]::GetTempPath()) ("cadswarm-install-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path $Tmp | Out-Null
try {
    Write-Host "Downloading CADSwarm ($Ref)..."
    $Zip = Join-Path $Tmp "cadswarm.zip"
    Invoke-WebRequest -UseBasicParsing -Uri "https://codeload.github.com/$Repo/zip/$Ref" -OutFile $Zip
    Expand-Archive -Path $Zip -DestinationPath $Tmp
    $Src = Get-ChildItem -Path $Tmp -Directory | Select-Object -First 1
    # Ship the release's database module inside the workbench, so it wins over any stale download
    # in the app folder. A local build (module\target) still wins inside the installer. Only main or
    # a release tag pairs a release module with the code; another branch may be ahead of it.
    if (($Ref -eq "main" -or $Ref -match '^v\d') -and -not (Test-Path (Join-Path $Src.FullName "module\cadswarm_module.wasm"))) {
        $Base = if ($env:CADSWARM_RELEASE_BASE) { $env:CADSWARM_RELEASE_BASE.TrimEnd('/') }
                elseif ($Ref -match '^v\d') { "https://github.com/$Repo/releases/download/$Ref" }
                else { "https://github.com/$Repo/releases/latest/download" }
        $Module = Join-Path $Src.FullName "module\cadswarm_module.wasm"
        try { Invoke-WebRequest -UseBasicParsing -Uri "$Base/cadswarm_module.wasm" -OutFile $Module }
        catch { Write-Host "Couldn't fetch the database module now; CADSwarm gets it on first use." }
    }
    $Installer = Join-Path $Src.FullName "install\install.ps1"
    if (-not (Test-Path $Installer)) { throw "The download didn't contain the installer." }
    & powershell -NoProfile -ExecutionPolicy Bypass -File $Installer
} finally {
    Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
}
