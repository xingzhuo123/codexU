[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $Root '.venv'
$Python = Join-Path $Venv 'Scripts\python.exe'

$PythonVersion = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0) { throw 'Python could not be started.' }
if ([version]$PythonVersion -lt [version]'3.11') {
    throw "Python 3.11 or newer is required. Found $PythonVersion."
}

if (-not (Test-Path -LiteralPath $Python)) {
    python -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the virtual environment.' }
}

& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Could not upgrade pip.' }
& $Python -m pip install -r (Join-Path $Root 'requirements-dev.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install Windows dependencies.' }
Write-Host 'codexU Windows development environment is ready.'
