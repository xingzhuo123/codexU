[CmdletBinding()]
param(
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Dist = Join-Path $Root 'dist'
$Build = Join-Path $Root 'build'
$Spec = Join-Path $Root 'codexU-Windows.spec'
$PortableDir = Join-Path $Dist 'codexU-Windows'

if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Run windows\setup.ps1 first.'
}

Push-Location $Root
try {
    if (-not $SkipTests) {
        & $Python -m pytest
        if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
    }

    & $Python -m PyInstaller --noconfirm --clean --distpath $Dist --workpath $Build $Spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }

    $BridgeDist = Join-Path $Build 'bridge-dist'
    $BridgeWork = Join-Path $Build 'bridge-work'
    $BridgeSpec = Join-Path $Build 'bridge-spec'
    & $Python -m PyInstaller --noconfirm --clean --onefile --console --noupx `
        --name codexU-claude-bridge `
        --distpath $BridgeDist `
        --workpath $BridgeWork `
        --specpath $BridgeSpec `
        (Join-Path $Root 'scripts\claude_statusline_bridge.py')
    if ($LASTEXITCODE -ne 0) { throw 'Claude statusline bridge build failed.' }

    $ToolsDir = Join-Path $PortableDir 'tools'
    New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null
    Copy-Item -LiteralPath (Join-Path $BridgeDist 'codexU-claude-bridge.exe') -Destination $ToolsDir -Force
    Copy-Item -LiteralPath (Join-Path $Root 'README.md') -Destination $PortableDir -Force
    Copy-Item -LiteralPath (Join-Path $Root '..\LICENSE') -Destination $PortableDir -Force

    $Package = Join-Path $Dist 'codexU-Windows-portable.zip'
    if (Test-Path -LiteralPath $Package) {
        Remove-Item -LiteralPath $Package -Force
    }
    Compress-Archive -Path (Join-Path $PortableDir '*') -DestinationPath $Package -CompressionLevel Optimal

    $Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $Package
    $HashLine = '{0}  {1}' -f $Hash.Hash.ToLowerInvariant(), (Split-Path -Leaf $Package)
    Set-Content -LiteralPath ($Package + '.sha256') -Value $HashLine -Encoding ascii
    Write-Host "Built $Package"
}
finally {
    Pop-Location
}
