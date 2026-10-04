@echo off
REM Opens PowerShell in this project with the Python environment already active.
cd /d "%~dp0"
powershell -NoExit -ExecutionPolicy Bypass -Command ".\.venv\Scripts\Activate.ps1"
