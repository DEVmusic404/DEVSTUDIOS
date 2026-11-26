$ErrorActionPreference = "Stop"
$project = Split-Path $PSScriptRoot -Parent
Set-Location $project
$python = Join-Path $project ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Run scripts\setup-windows.ps1 first." }
$url = "http://127.0.0.1:8765/"
function Test-DevDashboard {
    try { (Invoke-RestMethod "${url}health" -TimeoutSec 1).app -eq "DEV Command" }
    catch { $false }
}
if ((Test-NetConnection -ComputerName 127.0.0.1 -Port 8765 -InformationLevel Quiet -WarningAction SilentlyContinue)) {
    if (-not (Test-DevDashboard)) { throw "Port 8765 is in use by a different app." }
    Start-Process $url
    return
}
$server = Start-Process -FilePath $python -ArgumentList "-m uvicorn devcommand.app:app --host 127.0.0.1 --port 8765" -WorkingDirectory $project -PassThru -WindowStyle Hidden
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    if ($server.HasExited) { throw "The dashboard exited. Run python -m uvicorn devcommand.app:app --port 8765 to see the error." }
    if (Test-DevDashboard) {
        Start-Process $url
        return
    }
    Start-Sleep -Milliseconds 250
}
throw "The dashboard did not respond at $url."
