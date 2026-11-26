param([switch]$SkipAccounts, [switch]$NoLaunch)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
Write-Host "DEV Command | Studio setup" -ForegroundColor Magenta

$python = Join-Path $PWD ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        throw "Install Python 3.12 for Windows with the Python launcher enabled, then run setup again."
    }
    & py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Python 3.12 could not create the environment." }
}

function Invoke-DevPython {
    & $python @args
    if ($LASTEXITCODE -ne 0) { throw "The previous step failed. Fix the message above and run setup again." }
}

Invoke-DevPython -m pip install -r requirements-media.txt
Invoke-DevPython -m devcommand.setup
Invoke-DevPython -m devcommand.setup --check

if (-not $SkipAccounts) {
    $google = (Read-Host "Google Desktop OAuth JSON path (Enter to skip YouTube)").Trim().Trim('"')
    if ($google) { Invoke-DevPython -m devcommand.connect youtube $google }
    if ((Read-Host "Connect Facebook and the linked Instagram account now? [y/N]") -eq "y") {
        Invoke-DevPython -m devcommand.connect meta
    }
}

if (-not $NoLaunch) {
    if ((Read-Host "Install the worker at Windows sign-in? [Y/n]") -ne "n") {
        & (Join-Path $PSScriptRoot "install-worker.ps1")
    }
    & (Join-Path $PSScriptRoot "start-dashboard.ps1")
}
