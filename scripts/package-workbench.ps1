# Build the workbench zip CADSwarm updates itself with:
#   cadswarm-workbench.zip    the add-on's own files, stamped with the release's version
#
# The release workflow runs this for every v* tag, before it writes SHA256SUMS.txt, so the app
# can verify and install exactly what the release published.
#
#   pwsh scripts/package-workbench.ps1                         # version from package.xml
#   pwsh scripts/package-workbench.ps1 -Version v0.1.18 -Out dist
param(
    [string]$Out = "dist",
    [string]$Version = ""
)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$outDir = if ([System.IO.Path]::IsPathRooted($Out)) { $Out } else { Join-Path $root $Out }
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

if (-not $Version) {
    if ($env:GITHUB_REF_NAME) { $Version = $env:GITHUB_REF_NAME }
    else {
        $package = [xml](Get-Content (Join-Path $root "package.xml") -Raw)
        $Version = $package.package.version
    }
}
$Version = ($Version -replace '^v', '').Trim()
if (-not $Version) { throw "no version to stamp (pass -Version or tag the checkout)" }

# Only build leftovers and this repo's own test data: `web/dist` is the built web app and must ship.
$skipDirs = @('.git', '.venv', '__pycache__', '.pytest_cache', 'cadswarm-data', 'exports', '.cadswarm',
              'node_modules', 'target', '.vite', '.impeccable')
$stage = Join-Path ([System.IO.Path]::GetTempPath()) ("cadswarm-workbench-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path $stage | Out-Null

function Copy-Tree([string]$From, [string]$To) {
    New-Item -ItemType Directory -Force -Path $To | Out-Null
    foreach ($item in Get-ChildItem -LiteralPath $From -Force) {
        if ($item.PSIsContainer) {
            if ($skipDirs -contains $item.Name) { continue }
            if ($From -eq $root -and ($item.Name -eq 'dist' -or
                    [System.IO.Path]::GetFullPath($item.FullName) -eq [System.IO.Path]::GetFullPath($outDir))) { continue }
            Copy-Tree $item.FullName (Join-Path $To $item.Name)
        } elseif ($item.Name -notlike '*.pyc' -and $item.Name -notlike '*.pyo') {
            Copy-Item -LiteralPath $item.FullName -Destination (Join-Path $To $item.Name)
        }
    }
}

try {
    Copy-Tree $root $stage

    # Everything the app sees says which release this is, whatever the checkout said.
    $versionFile = Join-Path (Join-Path $stage "cadswarm") "version.py"
    @"
"""The version of CADSwarm this copy is. Stamped from the release tag it was built from."""

__version__ = "$Version"
"@ | Set-Content -Path $versionFile -Encoding utf8
    $packageFile = Join-Path $stage "package.xml"
    $packageText = Get-Content $packageFile -Raw
    $packageText = $packageText -replace '<version>[^<]+</version>', "<version>$Version</version>"
    $packageText = $packageText -replace '<date>[^<]+</date>', "<date>$((Get-Date).ToString('yyyy-MM-dd'))</date>"
    Set-Content -Path $packageFile -Value $packageText -Encoding utf8

    # The database module this release published, so the workbench never falls back to an older one.
    $wasm = Join-Path $outDir "cadswarm_module.wasm"
    if (-not (Test-Path $wasm)) {
        $wasm = Join-Path $root "module\target\wasm32-unknown-unknown\release\cadswarm_module.wasm"
    }
    if (-not (Test-Path $wasm)) { $wasm = Join-Path $root "module\cadswarm_module.wasm" }
    if (Test-Path $wasm) {
        New-Item -ItemType Directory -Force -Path (Join-Path $stage "module") | Out-Null
        Copy-Item -LiteralPath $wasm -Destination (Join-Path (Join-Path $stage "module") "cadswarm_module.wasm")
    } else {
        Write-Warning "no cadswarm_module.wasm to ship; the guide will download one on first use"
    }

    $zip = Join-Path $outDir "cadswarm-workbench.zip"
    if (Test-Path $zip) { Remove-Item -LiteralPath $zip -Force }
    if (-not ("System.IO.Compression.ZipFile" -as [type])) { Add-Type -AssemblyName System.IO.Compression.FileSystem }
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $stage, $zip, [System.IO.Compression.CompressionLevel]::Optimal, $false)
    Write-Host "workbench zip $Version -> $zip"
} finally {
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
}
