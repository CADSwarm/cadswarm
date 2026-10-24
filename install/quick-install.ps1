# One-line CadSwarm install for Windows (paste into PowerShell):
#   irm https://raw.githubusercontent.com/SachchitBalamurugan/cadswarm/main/install/quick-install.ps1 | iex
# Downloads CadSwarm, then runs install\install.ps1. $env:CADSWARM_REF picks a branch or tag.
$ErrorActionPreference = "Stop"
$Repo = "SachchitBalamurugan/cadswarm"
$Ref = if ($env:CADSWARM_REF) { $env:CADSWARM_REF } else { "main" }
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Tmp = Join-Path ([IO.Path]::GetTempPath()) ("cadswarm-install-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path $Tmp | Out-Null
try {
    Write-Host "Downloading CadSwarm ($Ref)..."
    $Zip = Join-Path $Tmp "cadswarm.zip"
    Invoke-WebRequest -UseBasicParsing -Uri "https://codeload.github.com/$Repo/zip/$Ref" -OutFile $Zip
    Expand-Archive -Path $Zip -DestinationPath $Tmp
    $Src = Get-ChildItem -Path $Tmp -Directory | Select-Object -First 1
    $Installer = Join-Path $Src.FullName "install\install.ps1"
    if (-not (Test-Path $Installer)) { throw "The download didn't contain the installer." }
    & powershell -NoProfile -ExecutionPolicy Bypass -File $Installer
} finally {
    Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
}
