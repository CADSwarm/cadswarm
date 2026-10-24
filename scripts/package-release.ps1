# Build the release assets the FreeCAD guide downloads on first use:
#   cadswarm-host-<platform>[.exe]   the team server for one OS/CPU
#   cadswarm_module.wasm             the SpacetimeDB module it publishes
#   SHA256SUMS.txt                   what the guide checks the downloads against
#
#   pwsh scripts/package-release.ps1                          # this machine
#   pwsh scripts/package-release.ps1 -Target aarch64-apple-darwin
#   pwsh scripts/package-release.ps1 -ModuleOnly               # wasm only (any host)
param(
    [string]$Target = "",
    [string]$Out = "dist",
    [switch]$ModuleOnly
)
# Not "Stop": Windows PowerShell 5.1 turns the CLI's progress lines on stderr into terminating errors.
# Each call's exit code is checked instead.
$ErrorActionPreference = "Continue"

$root = Split-Path -Parent $PSScriptRoot
$outDir = if ([System.IO.Path]::IsPathRooted($Out)) { $Out } else { Join-Path $root $Out }
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

function Invoke-Cargo([string[]]$cargoArgs) {
    & cargo @cargoArgs
    if ($LASTEXITCODE -ne 0) { throw "cargo $($cargoArgs -join ' ') failed" }
}

function Get-ReleaseName([string]$triple) {
    # Matches cadswarm/hosting.py RELEASE_ASSETS.
    switch -Regex ($triple) {
        '^x86_64-pc-windows' { return @{ Key = "windows-x86_64"; Ext = ".exe" } }
        '^aarch64-pc-windows' { return @{ Key = "windows-aarch64"; Ext = ".exe" } }
        '^x86_64-unknown-linux' { return @{ Key = "linux-x86_64"; Ext = "" } }
        '^aarch64-unknown-linux' { return @{ Key = "linux-aarch64"; Ext = "" } }
        '^x86_64-apple-darwin' { return @{ Key = "macos-x86_64"; Ext = "" } }
        '^aarch64-apple-darwin' { return @{ Key = "macos-aarch64"; Ext = "" } }
    }
    throw "CadSwarm has no release name for target '$triple'"
}

if ($ModuleOnly) {
    Invoke-Cargo @("build", "--release", "--locked", "--manifest-path", (Join-Path $root "module\Cargo.toml"),
        "--target", "wasm32-unknown-unknown")
    $wasm = Join-Path $root "module\target\wasm32-unknown-unknown\release\cadswarm_module.wasm"
    if (-not (Test-Path $wasm)) { throw "the module build produced no wasm at $wasm" }
    Copy-Item $wasm (Join-Path $outDir "cadswarm_module.wasm") -Force
} else {
    if (-not $Target) {
        $hostLine = (& rustc -vV) | Select-String -Pattern '^host:'
        if (-not $hostLine) { throw "rustc -vV did not report a host triple" }
        $Target = ($hostLine.Line -split ':', 2)[1].Trim()
    }
    $name = Get-ReleaseName $Target

    $cargoArgs = @("build", "--release", "--locked", "--manifest-path", (Join-Path $root "host\Cargo.toml"))
    if ($Target) { $cargoArgs += @("--target", $Target) }
    Invoke-Cargo $cargoArgs

    $releaseDir = if ($Target) { Join-Path $root "host\target\$Target\release" } else { Join-Path $root "host\target\release" }
    $binary = Join-Path $releaseDir "cadswarm-host.exe"
    if (-not (Test-Path $binary)) { $binary = Join-Path $releaseDir "cadswarm-host" }
    if (-not (Test-Path $binary)) { throw "the host build produced no binary in $releaseDir" }

    $asset = "cadswarm-host-$($name.Key)$($name.Ext)"
    Copy-Item $binary (Join-Path $outDir $asset) -Force

    Invoke-Cargo @("build", "--release", "--locked", "--manifest-path", (Join-Path $root "module\Cargo.toml"),
        "--target", "wasm32-unknown-unknown")
    $wasm = Join-Path $root "module\target\wasm32-unknown-unknown\release\cadswarm_module.wasm"
    if (-not (Test-Path $wasm)) { throw "the module build produced no wasm at $wasm" }
    Copy-Item $wasm (Join-Path $outDir "cadswarm_module.wasm") -Force
}

# Checksums over every CadSwarm asset in the folder, in a stable order. The guide reads this file
# (and the one CI uploads next to all platforms' assets) before trusting a download.
$assets = Get-ChildItem -Path $outDir -File |
    Where-Object { $_.Name -like "cadswarm-host-*" -or $_.Name -eq "cadswarm_module.wasm" } |
    Sort-Object Name
if ($assets.Count -eq 0) { throw "no release assets were produced in $outDir" }
$sumLines = $assets | ForEach-Object {
    $hash = (Get-FileHash -Path $_.FullName -Algorithm SHA256).Hash.ToLower()
    "{0}  {1}" -f $hash, $_.Name
}
Set-Content -Path (Join-Path $outDir "SHA256SUMS.txt") -Value $sumLines -Encoding ascii
$sumLines | ForEach-Object { Write-Host $_ }
Write-Host "release assets in $outDir"
