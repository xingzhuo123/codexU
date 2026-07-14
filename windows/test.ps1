[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Run windows\setup.ps1 first.'
}

Push-Location $Root
try {
    & $Python -m pytest
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
