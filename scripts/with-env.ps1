# Run a command with the repo's `.env` loaded into its environment.
#
#   powershell -File scripts\with-env.ps1 python -m pytest -q
#   powershell -File scripts\with-env.ps1 host\target\release\cadswarm-host.exe --data .\team run
#
# `.env` is git-ignored; copy `.env.example` and fill in the keys you use. Values are set for
# this process only, so nothing leaks into later shells or into the repo.
[CmdletBinding()]
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Argv)
$ErrorActionPreference = "Continue"

$root = Split-Path -Parent $PSScriptRoot
$file = Join-Path $root ".env"
if (-not (Test-Path $file)) { throw "no .env at $file (copy .env.example to .env first)" }

Get-Content $file | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    $eq = $line.IndexOf("=")
    if ($eq -lt 1) { return }
    $name = $line.Substring(0, $eq).Trim()
    $value = $line.Substring($eq + 1).Trim()
    if ($value.Length -ge 2 -and (($value[0] -eq '"' -and $value[-1] -eq '"') -or
                                  ($value[0] -eq "'" -and $value[-1] -eq "'"))) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    if ($name) { [Environment]::SetEnvironmentVariable($name, $value, "Process") }
}

if (-not $Argv -or $Argv.Count -eq 0) {
    Write-Host "loaded $file"
    exit 0
}
$rest = if ($Argv.Count -gt 1) { $Argv[1..($Argv.Count - 1)] } else { @() }
& $Argv[0] @rest
exit $LASTEXITCODE
