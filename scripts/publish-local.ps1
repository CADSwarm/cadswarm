# Publish the module to the local SpacetimeDB (wiping data) and regenerate client bindings.
# Run `spacetime start` in another terminal first.
$ErrorActionPreference = "Stop"
spacetime publish --server local --module-path module --delete-data=always --yes=all cadswarm
if ($LASTEXITCODE -ne 0) { throw "publish failed" }
spacetime generate --lang rust --out-dir host/src/module_bindings --module-path module
if ($LASTEXITCODE -ne 0) { throw "generate (rust) failed" }
spacetime generate --lang typescript --out-dir web/src/module_bindings --module-path module
if ($LASTEXITCODE -ne 0) { throw "generate (typescript) failed" }
Write-Host "published cadswarm and regenerated bindings"
