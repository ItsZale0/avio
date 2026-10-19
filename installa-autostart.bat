@echo off
REM Installa l'avvio automatico dell'agente (Task Scheduler).
REM Funziona da qualsiasi cartella: usa il percorso assoluto di questo file.
REM Serve un PowerShell come amministratore: se non lo sei, lo chiede lui.
setlocal
set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-Command','cd \"%SCRIPT_DIR%\"; python scripts\install_autostart.py %*'"
endlocal
