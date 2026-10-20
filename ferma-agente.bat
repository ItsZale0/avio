@echo off
REM Ferma l'agente Carpool Hunter (agente + tunnel).
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" scripts\stop_agent.py
) else (
    python scripts\stop_agent.py
)
timeout /t 2 /nobreak >nul
