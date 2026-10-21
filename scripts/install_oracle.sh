#!/usr/bin/env bash
# Installa Carpool Hunter su una VM Ubuntu (Oracle Cloud Always Free).
#
# Uso (sulla VM, come utente ubuntu):
#   git clone https://github.com/ItsZale0/carpool-intelligence.git
#   cd carpool-intelligence
#   sudo bash scripts/install_oracle.sh
#
# Lo script:
#   1. installa Python 3.11+ e le dipendenze;
#   2. mette l'app in /opt/carpool e i dati in /var/lib/carpool;
#   3. crea un servizio systemd che parte col boot e si riavvia da solo;
#   4. prepara il tunnel Cloudflare (se lo vuoi) e il proxy HTTPS.
set -euo pipefail

APP_DIR=/opt/carpool
DATA_DIR=/var/lib/carpool
REPO_URL=${REPO_URL:-https://github.com/ItsZale0/carpool-intelligence.git}
BRANCH=${BRANCH:-main}
SERVICE_USER=carpool

log() { printf '\n\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\n\033[1;31mERRORE:\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Esegui con sudo"

# --------------------------------------------------------------------------
# 1. Dipendenze di sistema
# --------------------------------------------------------------------------
log "Aggiornamento dei pacchetti"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
    python3 python3-venv python3-pip git curl ca-certificates unzip

python3 --version

# --------------------------------------------------------------------------
# 2. Codice e utente dedicato
# --------------------------------------------------------------------------
log "Preparazione di $APP_DIR"
if [[ -d "$APP_DIR/.git" ]]; then
    git -C "$APP_DIR" pull --ff-only
else
    id -u "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
    rm -rf "$APP_DIR"
    git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$APP_DIR"
fi

log "Dipendenze Python"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

mkdir -p "$DATA_DIR"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR" "$DATA_DIR"

# --------------------------------------------------------------------------
# 3. Configurazione
# --------------------------------------------------------------------------
if [[ ! -f "$APP_DIR/.env" ]]; then
    log "Creazione di $APP_DIR/.env"
    cat > "$APP_DIR/.env" <<EOF
HOST=0.0.0.0
PORT=8787
DEFAULT_INTERVAL_MIN=60
CARPOOL_DATA_DIR=$DATA_DIR
CARPOOL_PUBLIC_URL=
CARPOOL_CORS_ORIGINS=https://itszale0.github.io
EOF
    chmod 600 "$APP_DIR/.env"
fi
chown "$SERVICE_USER:$SERVICE_USER" "$APP_DIR/.env"

# --------------------------------------------------------------------------
# 4. Servizio systemd
# --------------------------------------------------------------------------
log "Creazione del servizio systemd"
cat > /etc/systemd/system/carpool.service <<EOF
[Unit]
Description=Carpool Hunter - agente di ricerca passaggi
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
Environment=PYTHONUNBUFFERED=1
Environment=TZ=Europe/Rome
ExecStart=$APP_DIR/.venv/bin/python -m uvicorn app.main:app \\
    --host 0.0.0.0 --port 8787 \\
    --proxy-headers --forwarded-allow-ips '*'
Restart=always
RestartSec=10
# Il servizio deve poter scrivere solo nella cartella dei dati.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=$DATA_DIR

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable carpool
systemctl restart carpool
sleep 4

if systemctl is-active --quiet carpool; then
    log "Servizio attivo su http://127.0.0.1:8787"
else
    journalctl -u carpool -n 30 --no-pager || true
    die "Il servizio non e' partito: guarda i log qui sopra"
fi

# --------------------------------------------------------------------------
# 5. Orari GTFS (opzionali)
# --------------------------------------------------------------------------
if [[ "${IMPORT_GTFS:-1}" == "1" ]]; then
    log "Import degli orari GTFS (puo' richiedere qualche minuto)"
    sudo -u "$SERVICE_USER" \
        env CARPOOL_DATA_DIR="$DATA_DIR" \
        "$APP_DIR/.venv/bin/python" -m app.cli gtfs-update || \
        log "Import GTFS saltato: puoi riprovare con 'python -m app.cli gtfs-update'"
fi

# --------------------------------------------------------------------------
# 6. HTTPS: reverse proxy con Nginx + Let's Encrypt, oppure tunnel
# --------------------------------------------------------------------------
log "Installazione di Nginx e Certbot"
apt-get install -y -qq --no-install-recommends nginx certbot python3-certbot-nginx
systemctl enable --now nginx

cat > /etc/nginx/sites-available/carpool <<'EOF'
server {
    listen 80;
    server_name _;
    client_max_body_size 8m;

    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        # Necessario al cookie di sessione: senza, il browser lo rifiuta.
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
    }
}
EOF
ln -sf /etc/nginx/sites-available/carpool /etc/nginx/sites-enabled/carpool
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

cat <<'EOF'

------------------------------------------------------------------
PROSSIMI PASSI (HTTPS)
------------------------------------------------------------------
Con un dominio tuo (es. carpool.tuodominio.it):
  1. porta il dominio su Cloudflare (DNS autoritativo)
  2. sudo certbot --nginx -d carpool.tuodominio.it
  3. metti in .env:  CARPOOL_PUBLIC_URL=https://carpool.tuodominio.it
  4. sudo systemctl restart carpool

Senza dominio (certificato sull'IP pubblico, gratis):
  sudo bash scripts/setup_oracle_https.sh
  (Certbot per l'IP, Nginx su 443, sorvegliante e aggiornamento automatico)

Poi aggiorna la variabile del repo GitHub:
  CARPOOL_API_BASE = https://<il-tuoo-url>
e riavvia il workflow .github/workflows/pages.yml
------------------------------------------------------------------
EOF

log "Installazione completata"
systemctl status carpool --no-pager -l | head -20 || true
