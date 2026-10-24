# Run a command with CadSwarm's secrets from an env file loaded into its environment.
#
#   powershell -File scripts\with-env.ps1 python -m pytest -q
#   powershell -File scripts\with-env.ps1 host\target\release\cadswarm-host.exe --data .\team run
#   powershell -File scripts\with-env.ps1 -EnvFile C:\path\to\keys.env python -m pytest -q
#
# Where the file comes from, in order:
#   1. -EnvFile <path>
#   2. $env:CADSWARM_ENV_FILE
#   3. .env in this checkout (git-ignored)
#   4. cadswarm.env next to the checkout (outside every repo), so all worktrees share one file
# Never commit it. Values are set for this process only and are not echoed.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot

# No param block on purpose: it would bind the command's own flags (python -c, cargo --release).
$envFile, $argv = "", @()
$i = 0
while ($i -lt $args.Count) {
    if ($args[$i] -eq "-EnvFile" -and $i + 1 -lt $args.Count) {
        $envFile = $args[$i + 1]
        $i += 2
        continue
    }
    $argv += $args[$i]
    $i += 1
}

$candidates = @()
if ($envFile) { $candidates += $envFile }
if ($env:CADSWARM_ENV_FILE) { $candidates += $env:CADSWARM_ENV_FILE }
$candidates += (Join-Path $root ".env")
$candidates += (Join-Path (Split-Path -Parent $root) "cadswarm.env")
$file = $null
foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate)) { $file = $candidate; break }
}
if (-not $file) {
    throw "no env file found (tried: $($candidates -join ', ')); copy .env.example to one of them first"
}

foreach ($raw in [System.IO.File]::ReadAllLines($file)) {
    $line = $raw.Trim()
    if (-not $line -or $line.StartsWith("#")) { continue }
    $eq = $line.IndexOf("=")
    if ($eq -lt 1) { continue }
    $name = $line.Substring(0, $eq).Trim()
    $value = $line.Substring($eq + 1).Trim()
    if ($value.Length -ge 2 -and (($value[0] -eq '"' -and $value[-1] -eq '"') -or
                                  ($value[0] -eq "'" -and $value[-1] -eq "'"))) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    if ($name) { [Environment]::SetEnvironmentVariable($name, $value, "Process") }
}

if ($argv.Count -eq 0) {
    Write-Host "loaded $file"
    exit 0
}
$rest = if ($argv.Count -gt 1) { $argv[1..($argv.Count - 1)] } else { @() }
& $argv[0] @rest
exit $LASTEXITCODE
