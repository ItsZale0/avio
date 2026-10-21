#!/usr/bin/env bash
# HTTPS sulla VM Oracle senza dominio: certificato Let's Encrypt per l'IP.
#
# Da gennaio 2026 Let's Encrypt emette certificati anche per indirizzi IP
# (profilo "shortlived", validi 6 giorni, rinnovati in automatico dal timer
# di Certbot). Serve Certbot >= 5.4 (flag --ip-address) e il plugin nginx non
# supporta ancora gli IP: si usa il webroot e Nginx legge i file a mano.
#
# Con un dominio tuo salta tutto questo:
#   sudo certbot --nginx -d nome.dominio
#
# Uso (sulla VM, come root):
#   sudo bash scripts/setup_oracle_https.sh
#
# Cosa fa:
#   1. installa Certbot >= 5.4 (snap, o venv pip se lo snap non c'e');
#   2. scopre l'IP pubblico della VM;
#   3. apre le porte 80 e 443 nel firewall della VM;
#   4. chiede il certificato per l'IP (staging, poi quello vero);
#   5. mette Nginx su HTTPS con quel certificato;
#   6. installa due timer: il controllo orario della scadenza (con avviso
#      Telegram/email) e l'aggiornamento del codice dal branch main.
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/carpool}
DATA_DIR=${DATA_DIR:-/var/lib/carpool}
SERVICE_USER=${SERVICE_USER:-carpool}
WEBROOT=${WEBROOT:-/var/www/certbot}

log() { printf '\n\033[1;32m==>\033[1m %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mERRORE:\033[1m %s\033[0m\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Esegui con sudo"
[[ -d "$APP_DIR" ]] || die "La cartella $APP_DIR non c'è: prima lancia install_oracle.sh"

# --------------------------------------------------------------------------
# 1. Certbot >= 5.4 (serve --ip-address)
# --------------------------------------------------------------------------
log "Installazione di Certbot (serve >= 5.4 per --ip-address)"
if ! command -v snap >/dev/null 2>&1; then
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends snapd
fi
snap install core24 >/dev/null 2>&1 || true
snap refresh core24 >/dev/null 2>&1 || true
snap install certbot --classic >/dev/null 2>&1 || true

# Lo snap e' la versione nuova; quella di apt spesso e' troppo vecchia.
CERTBOT=""
for candidato in /snap/bin/certbot "$(command -v certbot || true)"; do
    [[ -x "$candidato" ]] || continue
    versione=$("$candidato" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    if [[ -n "$versione" ]] && [[ "$(printf '%s\n5.4.0\n' "$versione" | sort -V | head -1)" == "5.4.0" ]]; then
        CERTBOT="$candidato"
        break
    fi
done
if [[ -z "$CERTBOT" ]]; then
    # Ultimo tentativo: pip in un ambiente dedicato.
    log "Certbot >= 5.4 non trovato: lo installo con pip"
    python3 -m venv /opt/certbot
    /opt/certbot/bin/pip install --quiet --upgrade pip
    /opt/certbot/bin/pip install --quiet certbot
    CERTBOT=/opt/certbot/bin/certbot
fi
"$CERTBOT" --version || true

# --------------------------------------------------------------------------
# 2. L'IP pubblico
# --------------------------------------------------------------------------
log "Scoperta dell'IP pubblico"
PUBLIC_IP=$(curl -s -H "Authorization: Bearer Oracle" \
    http://169.254.169.254/opc/v2/instance/ 2>/dev/null |
    python3 -c "import sys,json; print(json.load(sys.stdin)['metadata'].get('publicIp',''))" 2>/dev/null || true)
if [[ -z "$PUBLIC_IP" ]]; then
    PUBLIC_IP=$(curl -s --max-time 10 https://api.ipify.org || true)
fi
[[ -n "$PUBLIC_IP" ]] || die "Non riesco a trovare l'IP pubblico della VM"
log "IP pubblico: $PUBLIC_IP"

mkdir -p "$WEBROOT/.well-known/acme-challenge"

# --------------------------------------------------------------------------
# 3. Le porte: la 80 serve alla challenge di Let's Encrypt, la 443 all'app
# --------------------------------------------------------------------------
# Le immagini Ubuntu di Oracle accettano solo SSH: senza questo l'app non
# risponde e la challenge fallisce.
log "Apertura delle porte 80 e 443"
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi "attivo\|active"; then
    ufw allow 80/tcp || true
    ufw allow 443/tcp || true
else
    # Le regole vanno PRIMA del REJECT finale, altrimenti non valgono mai.
    # La posizione si calcola da quella del REJECT (in `iptables -S` la riga 1
    # e' la policy, quindi le regole sono numerate da 1 in poi).
    apri_porte() {
        local porta=$1
        iptables -D INPUT -p tcp --dport "$porta" -j ACCEPT 2>/dev/null || true
        local pos
        pos=$(iptables -S INPUT | grep -n 'REJECT' | head -1 | cut -d: -f1)
        if [[ -z "$pos" ]]; then
            iptables -A INPUT -p tcp --dport "$porta" -j ACCEPT
        else
            iptables -I INPUT "$((pos - 1))" -p tcp --dport "$porta" -j ACCEPT
        fi
    }
    apri_porte 80
    apri_porte 443
    if ! command -v netfilter-persistent >/dev/null 2>&1; then
        apt-get install -y -qq netfilter-persistent || true
    fi
    netfilter-persistent save >/dev/null 2>&1 || true
fi

# --------------------------------------------------------------------------
# 4. Nginx, prima fase: la porta 80 (challenge + app)
# --------------------------------------------------------------------------
log "Configurazione di Nginx (fase 1: porta 80)"
cat > /etc/nginx/sites-available/carpool <<EOF
server {
    listen 80;
    server_name _;
    client_max_body_size 8m;

    # La challenge di Let's Encrypt vive qui: la porta 80 resta aperta.
    location /.well-known/acme-challenge/ {
        root $WEBROOT;
    }

    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
    }
}
EOF
ln -sf /etc/nginx/sites-available/carpool /etc/nginx/sites-enabled/carpool
rm -f /etc/nginx/sites-enabled/default
systemctl enable --now nginx
nginx -t && systemctl reload nginx

# --------------------------------------------------------------------------
# 5. Il certificato (prima in staging, poi quello vero)
# --------------------------------------------------------------------------
log "Richiesta del certificato per l'IP $PUBLIC_IP"
# Prima una prova in staging (non costa nulla e non fissa il limite di
# emissione): con CERT_STAGING=1 la richiesta resta in staging.
if [[ "${CERT_STAGING:-0}" == "1" ]]; then
    "$CERTBOT" certonly --non-interactive --agree-tos --register-unsafely-without-email \
        --preferred-profile shortlived \
        --webroot --webroot-path "$WEBROOT" \
        --ip-address "$PUBLIC_IP" \
        --staging || log "Lo staging e' fallito: guarda i log di certbot."
    log "Finito in staging: il certificato non e' valido per i browser."
    exit 0
fi

# Se resta un certificato di staging con lo stesso nome, va rimosso:
# altrimenti certbot lo considera "gia' rinnovabile" e non riemette nulla.
"$CERTBOT" delete --cert-name "$PUBLIC_IP" --non-interactive >/dev/null 2>&1 || true

"$CERTBOT" certonly --non-interactive --agree-tos --register-unsafely-without-email \
    --preferred-profile shortlived \
    --webroot --webroot-path "$WEBROOT" \
    --ip-address "$PUBLIC_IP" \
    --cert-name "$PUBLIC_IP" \
    --deploy-hook "systemctl reload nginx" || \
    die "Certbot non ha emesso il certificato: guarda 'certbot certificates'"

[[ -f "/etc/letsencrypt/live/$PUBLIC_IP/fullchain.pem" ]] || \
    die "Il certificato non c'e': la porta 80 dev'essere raggiungibile da internet"

# Il deploy-hook resta in giro per i rinnovi futuri.
mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh <<'EOF'
#!/bin/sh
systemctl reload nginx
EOF
chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh

# --------------------------------------------------------------------------
# 6. Nginx, seconda fase: HTTPS con il certificato
# --------------------------------------------------------------------------
log "Configurazione di Nginx (fase 2: HTTPS)"
cat > /etc/nginx/sites-available/carpool <<EOF
server {
    listen 80;
    server_name _;
    client_max_body_size 8m;

    # La challenge di Let's Encrypt vive qui: la porta 80 resta aperta.
    location /.well-known/acme-challenge/ {
        root $WEBROOT;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl;
    server_name _;
    ssl_certificate     /etc/letsencrypt/live/$PUBLIC_IP/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$PUBLIC_IP/privkey.pem;
    client_max_body_size 8m;

    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        # Necessario al cookie di sessione: senza, il browser lo rifiuta.
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
    }
}
EOF
nginx -t && systemctl reload nginx

# --------------------------------------------------------------------------
# 7. L'URL pubblico nel .env
# --------------------------------------------------------------------------
if [[ -f "$APP_DIR/.env" ]]; then
    sed -i "s|^CARPOOL_PUBLIC_URL=.*|CARPOOL_PUBLIC_URL=https://$PUBLIC_IP|" "$APP_DIR/.env"
    if ! grep -q "^CARPOOL_TLS_CERT=" "$APP_DIR/.env"; then
        echo "CARPOOL_TLS_CERT=/etc/letsencrypt/live/$PUBLIC_IP/fullchain.pem" >> "$APP_DIR/.env"
    fi
    chown "$SERVICE_USER:$SERVICE_USER" "$APP_DIR/.env"
    systemctl restart carpool
fi

# --------------------------------------------------------------------------
# 8. Sorvegliante: ogni ora controlla la scadenza e avvisa su Telegram
# --------------------------------------------------------------------------
log "Timer di controllo del certificato"
cat > /etc/systemd/system/carpool-certcheck.service <<EOF
[Unit]
Description=Controlla la scadenza del certificato HTTPS di Carpool Hunter
After=network-online.target

[Service]
Type=oneshot
# Il certificato sta sotto /etc/letsencrypt (solo root): il controllo gira come root.
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/.venv/bin/python -m app.cli cert-check --warn-days 3
EOF

cat > /etc/systemd/system/carpool-certcheck.timer <<EOF
[Unit]
Description=Ogni ora controlla il certificato HTTPS di Carpool Hunter

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now carpool-certcheck.timer

# --------------------------------------------------------------------------
# 9. Aggiornamento automatico del codice (come il frontend su Pages)
# --------------------------------------------------------------------------
log "Timer di aggiornamento del codice"
# La cartella appartiene a 'carpool' ma il timer gira come root: git chiede
# la "safe.directory" per non bloccarsi.
git config --system --add safe.directory "$APP_DIR" 2>/dev/null || true
cat > /etc/systemd/system/carpool-update.service <<EOF
[Unit]
Description=Aggiorna Carpool Hunter dal branch main
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$APP_DIR
ExecStart=/bin/bash -c "git pull --ff-only && systemctl restart carpool"
EOF

cat > /etc/systemd/system/carpool-update.timer <<EOF
[Unit]
Description=Ogni ora aggiorna Carpool Hunter dal branch main

[Timer]
OnCalendar=*-*-* *:17:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now carpool-update.timer

log "Fatto. HTTPS attivo su https://$PUBLIC_IP"
cat <<EOF

----------------------------------------------------------------------
PASSI SUCCESSIVI (sul PC)
----------------------------------------------------------------------
  1. python scripts\\export_deploy.py --with-gtfs
  2. scp -i <chiave> data\\deploy\\carpool-deploy.zip ubuntu@$PUBLIC_IP:/tmp/
  3. ssh -i <chiave> ubuntu@$PUBLIC_IP
     sudo systemctl stop carpool
     cd /var/lib/carpool && sudo unzip -o /tmp/carpool-deploy.zip
     sudo chown -R carpool:carpool /var/lib/carpool && rm /tmp/carpool-deploy.zip
     sudo systemctl start carpool
  4. nel repo GitHub: Settings -> Secrets and variables -> Actions ->
     Variables -> CARPOOL_API_BASE = https://$PUBLIC_IP
     poi rilancia il workflow "Deploy frontend su GitHub Pages"

CONTROLLI UTILI (nella VM)
  systemctl status carpool                 # l'app
  systemctl status carpool-certcheck.timer  # il certificato
  systemctl status carpool-update.timer    # gli aggiornamenti
  sudo certbot certificates                # i certificati
  journalctl -u carpool -f                 # i log
----------------------------------------------------------------------
EOF
