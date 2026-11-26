$ErrorActionPreference = "Stop"
$project = Split-Path $PSScriptRoot -Parent
$python = Join-Path $project ".venv\Scripts\python.exe"
$command = "-m uvicorn devcommand.app:app --host 127.0.0.1 --port 8765"
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object {
    $_.ExecutablePath -eq $python -and $_.CommandLine -and $_.CommandLine.EndsWith($command)
} | ForEach-Object { Stop-Process -Id $_.ProcessId }

$task = Get-ScheduledTask -TaskName "DEV Command Worker" -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -InputObject $task
    Start-ScheduledTask -InputObject $task
}
& (Join-Path $PSScriptRoot "start-dashboard.ps1")
