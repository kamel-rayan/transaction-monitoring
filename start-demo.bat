@echo off
REM Starts the triage API and n8n in their own windows, then opens the dashboard and n8n.
cd /d "%~dp0"
start "Triage API" powershell -NoExit -ExecutionPolicy Bypass -Command ".\.venv\Scripts\Activate.ps1; python -m scripts.api"
start "n8n" powershell -NoExit -Command "npx n8n"
echo Waiting for the services to start...
timeout /t 15 >nul
start "" http://127.0.0.1:5000/dashboard
start "" http://localhost:5678
