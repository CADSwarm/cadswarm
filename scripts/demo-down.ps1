<#
.SYNOPSIS
    Stops the CadSwarm demo stack (preview + tunnel + host) started by scripts/demo-up.ps1.

.DESCRIPTION
    Reads the PID file written by scripts/demo-up.ps1 at
        <Data>\.cadswarm-team\demo-pids.json
    (a JSON object with keys: preview, tunnel, host) and stops those processes.

    Safety: processes are NEVER stopped by name. For each recorded PID the command
    line is re-read via Get-CimInstance Win32_Process and must contain an expected
    marker before the process is killed, so stale/recycled PIDs are left alone.
    Child processes of the host whose command line contains 'cadswarm-host' are
    stopped first.

.PARAMETER Data
    Data folder of the demo. Default: $env:USERPROFILE\Documents\Projects\cadswarm-demo.

.EXAMPLE
    powershell -File scripts\demo-down.ps1
#>

[CmdletBinding()]
param(
    [string]$Data = "$env:USERPROFILE\Documents\Projects\cadswarm-demo"
)

$pidFile = "$Data\.cadswarm-team\demo-pids.json"

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Output 'nothing to stop'
    exit 0
}

$pids = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json

function Get-CommandLine([int]$Id) {
    if ($Id -le 0) { return $null }
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$Id" -ErrorAction SilentlyContinue
    if ($null -eq $proc) { return $null }
    return $proc.CommandLine
}

function Stop-IfMatches([string]$Name, [int]$Id, [string[]]$Markers) {
    if ($Id -le 0) { return }

    $cl = Get-CommandLine $Id
    if ($null -eq $cl) {
        Write-Output "$Name pid $Id already stopped"
        return
    }

    $matched = $false
    foreach ($m in $Markers) {
        if ($cl.ToLower().Contains($m.ToLower())) { $matched = $true; break }
    }
    if (-not $matched) {
        Write-Output "skipped $Name pid ${Id}: command line does not match"
        return
    }

    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$Id" -ErrorAction SilentlyContinue
    foreach ($child in @($children)) {
        if ($null -eq $child) { continue }
        $childCl = $child.CommandLine
        if ($null -ne $childCl -and $childCl.ToLower().Contains('cadswarm-host')) {
            Write-Output "stopping child pid $($child.ProcessId) of $Name"
            Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }

    Stop-Process -Id $Id -Force -ErrorAction SilentlyContinue
    Write-Output "stopped $Name pid $Id"
}

Stop-IfMatches 'host'    $pids.host    @('cadswarm-host', 'with-env.ps1')
Stop-IfMatches 'tunnel'  $pids.tunnel  @('cloudflared')
Stop-IfMatches 'preview' $pids.preview @('vite')

Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
Write-Output 'demo stack stopped'
