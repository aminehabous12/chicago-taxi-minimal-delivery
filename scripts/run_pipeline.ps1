param(
    [switch]$Overwrite
)

$ErrorActionPreference = 'Stop'

$projectDir = Split-Path -Parent $PSScriptRoot
Set-Location $projectDir

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker CLI was not found. Install Docker Desktop and make sure it is running.'
}

if (-not (Test-Path -LiteralPath '.env' -PathType Leaf)) {
    throw 'Missing .env. Copy .env.example to .env and configure the source token.'
}

function Invoke-Compose {
    param([Parameter(Mandatory)][string[]]$ComposeArgs)
    & docker compose @ComposeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose $($ComposeArgs -join ' ') failed with exit code $LASTEXITCODE."
    }
}

Invoke-Compose -ComposeArgs @('build', 'app')
Invoke-Compose -ComposeArgs @('run', '--rm', 'app', 'pytest', '-q')

$extractArgs = @('run', '--rm', 'app', 'python', '-m', 'chicago_taxi.ingestion.extract_raw_data')
if ($Overwrite) {
    $extractArgs += '--overwrite'
}
Invoke-Compose -ComposeArgs $extractArgs
Invoke-Compose -ComposeArgs @('run', '--rm', 'app', 'python', '-m', 'chicago_taxi.catalog.profile_data')

Write-Host 'Pipeline completed. Catalog files are in data/catalog/.'
