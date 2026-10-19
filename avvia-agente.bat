@echo off
REM Avvia Carpool Hunter (agente + tunnel Cloudflare) in background.
REM Da usare nella cartella di avvio: parte a ogni accesso dell'utente.
setlocal
cd /d "%~dp0"

REM Preferisce il Python dell'ambiente virtuale, se c'e'.
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

REM Il tunnel aggiorna da solo il .env, la variabile GitHub e il deploy
REM di GitHub Pages (per questo usa GITHUB_TOKEN, se impostato).
start "Carpool - tunnel" /min "%PY%" -u scripts\tunnel.py --port 8787

REM L'agente gira in questa finestra: chiuderla ferma tutto.
"%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8787
