$ErrorActionPreference = "Stop"
$project = Split-Path $PSScriptRoot -Parent
$python = Join-Path $project ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Create the project's .venv and install requirements first." }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskAction = New-ScheduledTaskAction -Execute $python -Argument "-m devcommand.worker" -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "DEV Command Worker" -Action $taskAction -Trigger $trigger -Principal $principal -Settings $settings -Force
Start-ScheduledTask -TaskName "DEV Command Worker"
