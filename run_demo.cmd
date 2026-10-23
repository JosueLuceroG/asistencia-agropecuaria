@echo off
setlocal
cd /d "%~dp0"
set "ASISTENCIA_CONFIG_DIR=%~dp0demo"
set "ASISTENCIA_EXCEL_PASSWORD=demo"
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0main v4.py"
) else (
    python "%~dp0main v4.py"
)
