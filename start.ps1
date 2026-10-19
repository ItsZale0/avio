param(
    [int]$Port = 8787,
    [switch]$NoInstall
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$python = "python"
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "Creo l'ambiente virtuale .venv..." -ForegroundColor Cyan
    & $python -m venv .venv
}

if (-not $NoInstall) {
    Write-Host "Controllo le dipendenze..." -ForegroundColor Cyan
    & $venvPython -m pip install --quiet --disable-pip-version-check -r requirements.txt
}

if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Creato .env da .env.example (compila Telegram/SMTP quando vuoi)." -ForegroundColor Yellow
}

$env:PYTHONUTF8 = "1"
Write-Host ""
Write-Host "  Carpool Hunter -> http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "  Premi CTRL+C per fermare l'agente." -ForegroundColor DarkGray
Write-Host ""

& $venvPython -m uvicorn app.main:app --host 127.0.0.1 --port $Port
