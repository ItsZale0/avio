param(
    [int]$Port = 8787,
    [switch]$NoInstall,
    [switch]$NoTunnel
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

# --- Agente ---------------------------------------------------------------
Write-Host ""
Write-Host "  Carpool Hunter -> http://127.0.0.1:$Port" -ForegroundColor Green

# Il tunnel parte in un processo separato: quando lo script viene chiuso,
# l'agente continua a girare (e' il servizio Windows che lo rilancia).
$tunnelProcess = $null
if (-not $NoTunnel) {
    Write-Host "  Avvio il tunnel Cloudflare..." -ForegroundColor Cyan
    $tunnelProcess = Start-Process -FilePath $venvPython `
        -ArgumentList "scripts\tunnel.py", "--port", $Port `
        -WorkingDirectory $PSScriptRoot `
        -PassThru -WindowStyle Minimized
}

Write-Host "  Premi CTRL+C per fermare l'agente." -ForegroundColor DarkGray
Write-Host ""

try {
    & $venvPython -m uvicorn app.main:app --host 127.0.0.1 --port $Port
}
finally {
    if ($tunnelProcess -and -not $tunnelProcess.HasExited) {
        Write-Host "Chiudo il tunnel..." -ForegroundColor DarkGray
        Stop-Process -Id $tunnelProcess.Id -Force -ErrorAction SilentlyContinue
    }
}
