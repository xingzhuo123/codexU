[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$AppArguments
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Run windows\setup.ps1 first.'
}

$env:PYTHONPATH = Join-Path $Root 'src'
& $Python -m codexu_win @AppArguments
exit $LASTEXITCODE
