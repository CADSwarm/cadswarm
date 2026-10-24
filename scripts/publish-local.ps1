# Publish the module to the local SpacetimeDB (wiping data) and regenerate client bindings.
# Run `spacetime start` in another terminal first.
param([string]$Server = "local", [string]$Db = "cadswarm")
# Not "Stop": Windows PowerShell 5.1 turns the CLI's progress lines on stderr into terminating errors.
# Each call's exit code is checked instead.
$ErrorActionPreference = "Continue"
spacetime publish --server $Server --module-path module --delete-data=always --yes=all $Db
if ($LASTEXITCODE -ne 0) { throw "publish failed" }
spacetime generate --lang rust --out-dir host/src/module_bindings --module-path module
if ($LASTEXITCODE -ne 0) { throw "generate (rust) failed" }
spacetime generate --lang typescript --out-dir web/src/module_bindings --module-path module
if ($LASTEXITCODE -ne 0) { throw "generate (typescript) failed" }
Write-Host "published $Db to $Server and regenerated bindings"
