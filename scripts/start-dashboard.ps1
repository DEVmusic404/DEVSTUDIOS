$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
Start-Process "http://127.0.0.1:8765"
& ".\.venv\Scripts\python.exe" -m uvicorn devcommand.app:app --host 127.0.0.1 --port 8765
