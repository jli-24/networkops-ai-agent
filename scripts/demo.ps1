$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    & python -m demo
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
