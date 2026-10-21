# Server sempre acceso su Oracle Cloud (gratis)

Oracle Cloud offre un piano **Always Free**: una VM Linux gratuita 24/7, con
IP pubblico, che puoi usare come server per l'agente. Cosi' l'agente gira
anche quando il PC e' spento.

> **Non serve un dominio.** Da gennaio 2026 Let's Encrypt emette certificati
> anche per gli indirizzi IP: la VM puo' rispondere su `https://<IP>` con un
> certificato valido, rinnovato da solo. Vedi § "Senza dominio" piu' in basso.

| Risorsa | Quota Always Free | Serve a noi |
| --- | --- | --- |
| VM.Standard.A1.Flex (ARM) | 2 OCPU + 12 GB RAM (era 4/24, dimezzato a luglio 2026) | l'agente |
| VM.Standard.E2.1.Micro (x86) | 2 istanze da 1/8 OCPU + 1 GB | alternativa |
| Block Volume | 200 GB | i database |
| Egress | 10 TB/mese | il traffico dell'agente |

**Serve una carta di credito** per la registrazione (non viene addebitata se
resti entro le quote). Converte il piano in **Pay As You Go** appena creato
l'account: paghi solo quello che eccedi le quote, e in più **eviti il
ripristino forzato delle istanze inattive** (vedi "Attenzione" qui sotto).

## 1. Creare l'account

1. Vai su <https://www.oracle.com/cloud/free/> e clicca **Start for free**.
2. Compila con un'email valida e una carta (credito o debito, non prepagata).
3. Scegli la **home region**: e' importante, non puoi cambiarla dopo.
   - Per l'Italia vanno bene `europe-west-netherlands` (Amsterdam) o
     `europe-west-frankfurt` (Francoforte).
   - Se in quella regione non c'e' piu' capacita' per la shape ARM (errore
     *"out of host capacity"*), ricreare l'account in un'altra regione e'
     l'unica via: usa allora la micro AMD, che ha sempre spazio.
4. Completa la verifica (email + telefono).

## 2. Creare la VM

Nella console OCI: **Compute → Instances → Create instance**.

| Campo | Valore |
| --- | --- |
| Name | `carpool` |
| Image | Ubuntu 24.04 (aarch64, se scegli A1) oppure x86 |
| Shape | `VM.Standard.A1.Flex` con **2 OCPU** e **12 GB** di memoria |
| Networking | crea una VCN pubblica con un IP pubblico (default) |
| SSH keys | carica la tua chiave pubblica (o usa "Generate a key pair") |

> **"Out of host capacity"**: capita spesso con le shape ARM in regioni
> affollate. Riprova con un altro availability domain, o aspetta qualche ora.
> Alternativa sicura: **VM.Standard.E2.1.Micro** (x86, 1 GB RAM) — l'app usa
> ~100 MB, quindi sta al pelo ma funziona. Con 1 GB conviene non importare i
> GTFS (100 MB di dati e query in memoria).

Collegati:

```powershell
ssh -i ~/Downloads/ssh-key-ocid.key ubuntu@<IP_PUBBLICO>
```

## 3. Installare l'app

Sul PC (o direttamente nella VM):

```powershell
git clone https://github.com/ItsZale0/carpool-intelligence.git
cd carpool-intelligence
```

Poi, **sulla VM**:

```bash
sudo bash scripts/install_oracle.sh
```

Lo script fa tutto: installa Python e le dipendenze, mette l'app in
`/opt/carpool`, i dati in `/var/lib/carpool`, crea il servizio systemd
(`systemctl status carpool`) e installa Nginx come reverse proxy.

Per gli orari GTFS (opzionali, ~100 MB):

```bash
sudo -u carpool env CARPOOL_DATA_DIR=/var/lib/carpool \
  /opt/carpool/.venv/bin/python -m app.cli gtfs-update
```

## 4. Importare i tuoi dati

Sul PC, esporta il database con account, ricerche e impostazioni:

```powershell
python scripts\export_deploy.py --with-gtfs
```

Trovi `data\deploy\carpool-deploy.zip`. Copialo sulla VM:

```powershell
scp -i ~/Downloads/ssh-key-ocid.key data\deploy\carpool-deploy.zip ubuntu@<IP_PUBBLICO>:/tmp/
```

E sulla VM:

```bash
sudo systemctl stop carpool
cd /var/lib/carpool
sudo unzip -o /tmp/carpool-deploy.zip
sudo chown -R carpool:carpool /var/lib/carpool
rm /tmp/carpool-deploy.zip
sudo systemctl start carpool
```

Ora l'account `zampe` e le tue ricerche esistono anche sul server.

## 5. HTTPS

Il frontend su GitHub Pages chiama l'API: serve HTTPS, altrimenti il browser
rifiuta il cookie di sessione.

### Senza dominio (0 €): certificato sull'IP pubblico

Da gennaio 2026 Let's Encrypt emette **certificati per indirizzi IP** (profilo
"shortlived", validi 6 giorni, rinnovati in automatico). Il comando e' gia' in
`scripts/setup_oracle_https.sh`:

```bash
sudo bash scripts/setup_oracle_https.sh
```

Lo script fa tutto:

1. installa Certbot via snap (serve >= 5.4 per il flag `--ip-address`);
2. scopre l'IP pubblico della VM;
3. scrive la configurazione Nginx (porta 80 per la challenge ACME, 443 per l'app);
4. chiede il certificato per l'IP (prima in staging, poi quello vero);
5. mette `CARPOOL_PUBLIC_URL=https://<IP>` nel `.env`;
6. installa due timer:
   - **`carpool-certcheck.timer`** (ogni ora): controlla la scadenza del
     certificato e, se mancano meno di 3 giorni, **ti avvisa su Telegram**
     (o email) con i canali gia' configurati nell'app;
   - **`carpool-update.timer`** (ogni ora): `git pull` + `systemctl restart
     carpool` — il backend si aggiorna da solo come il frontend su Pages.

In pratica non scarichi mai un certificato a mano: il timer di Certbot rinnova
ogni ~4 giorni e il deploy-hook ricarica Nginx.

> Con un dominio tuo salta tutta questa parte: metti il record DNS sul tuo
> dominio e usa `sudo certbot --nginx -d nome.dominio` (certificato da 90
> giorni, la via comoda).

### Con un dominio tuo (metodo consigliato, URL stabile)

1. Porta il dominio su Cloudflare (nameserver autoritativi).
2. Punta un record DNS al IP pubblico della VM.
3. Sulla VM:

```bash
sudo certbot --nginx -d carpool.tuodominio.it
```

4. In `/opt/carpool/.env`:

```
CARPOOL_PUBLIC_URL=https://carpool.tuodominio.it
CARPOOL_CORS_ORIGINS=https://itszale0.github.io
```

5. `sudo systemctl restart carpool`

### Senza dominio: tunnel Cloudflare

```bash
sudo cloudflared tunnel login
sudo cloudflared tunnel create carpool
sudo cloudflared tunnel route dns carpool carpool.tuodominio.it
sudo cloudflared service install
```

Poi configura Nginx per il tunnel, oppure fai girare il tunnel verso la porta
8787 direttamente. L'URL e' stabile, il certificato e' incluso.

> Il quick tunnel (`cloudflared tunnel --url http://127.0.0.1:8787`) funziona
> ma cambia URL a ogni riavvio: scomodo per la PWA.

## 6. Collegare GitHub Pages

L'API ora vive sulla VM: il frontend su GitHub Pages deve puntare li'.

### Il modo veloce (dalla VM, con un token)

```bash
# sulla VM, con GITHUB_TOKEN nell'ambiente (scope repo)
python /opt/carpool/scripts/publish_backend.py https://<IP_PUBBLICO>
```

Lo script scrive `backend.json` sul branch (il frontend lo vede subito) e
aggiorna la variabile `CARPOOL_API_BASE`, poi rilancia il deploy di Pages.

### Il modo manuale

Nel repo → **Settings → Secrets and variables → Actions → Variables**:

| Nome | Valore |
| --- | --- |
| `CARPOOL_API_BASE` | `https://carpool.tuodominio.it` |

Poi forza il redeploy del frontend dalla tab **Actions** (ri-esegui
"Deploy frontend su GitHub Pages").

Verifica: <https://itszale0.github.io/carpool-intelligence/> deve chiederti
l'accesso e mostrare le tue ricerche.

## 7. Firewall

Due livelli, entrambi da aprire:

1. **Security List** della VCN (nella console OCI): aggiungi una regola
   **ingress TCP 443** (e 80, per il redirect di Let's Encrypt).
2. **iptables** sulla VM: le immagini Ubuntu di Oracle accettano solo SSH.

```bash
sudo iptables -I INPUT 6 -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

## Attenzione: il ripristino delle istanze inattive

Oracle può **spegnere** le istanze Always Free che considera inattive. Il
criterio (finestra di 7 giorni): CPU, rete e memoria sotto il 20% al 95°
percentile. Oracle avvisa **una settimana prima** di spegnerla.

**Cosa succede davvero quando la spegne**: l'istanza viene *spenta*, non
cancellata. Restano il volume (quindi ricerche, preferenze e chiavi), l'IP
pubblico (l'IP effimero resta assegnato all'istanza ferma) e il certificato
HTTPS. Per rimettere tutto online basta premere **Start** dalla console: i
servizi systemd (`carpool`, Nginx, i timer) sono abilitati al boot e
ripartono da soli.

**Come evitarla del tutto**: passare al piano **Pay As You Go**
(Billing & Cost Management → *Upgrade and Manage Payment*). L'upgrade chiede
un metodo di pagamento e blocca **100 $ (93 €)** sulla carta come
*autorizzazione di verifica*: con una carta di credito il blocco è sul
plafond e non servono soldi sul conto, con una carta di debito il conto
deve avere quei 93 €. L'autorizzazione è annullata subito da Oracle e
scompare dallo stesso estratto conto in 3-5 giorni. Dopo l'upgrade **non
ti viene addebitato nulla** finché resti entro le quote Always Free (la tua
VM: 1 micro istanza, 47 GB di volume, nessun NAT gateway → tutto gratis).

Restando su Always Free, la rete di sicurezza è questa:

| Protezione | Come funziona |
| --- | --- |
| Sorvegliante esterno | `uptime.yml` su GitHub Actions controlla `/api/ping` ogni 30 minuti: se l'app non risponde prova a **riavviare l'istanza** e avvisa su Telegram |
| Backup dei dati | ogni notte il database viene copiato, cifrato e spedito su Telegram (`carpool-backup.timer`) |
| Ripristino manuale | premere **Start** nella console: l'app torna in piedi da sola |
| Avviso di Oracle | Oracle manda una email una settimana prima di spegnere |

> Attenzione: se l'istanza viene **cancellata** (non solo spenta), l'IP
> pubblico cambia e con lui il certificato. In quel caso: riemetti il
> certificato (`sudo certbot renew --cert-name <IP>`) e rilancia
> `python /opt/carpool/scripts/publish_backend.py https://<NUOVO_IP>`.
> I dati non si perdono: sono nel backup cifrato.

## Se l'app non risponde

1. **Il link del giorno**: `https://itszale0.github.io/carpool-intelligence/`
   porta sulla VM. Se non risponde, il problema è la VM (non il frontend).
2. **La console Oracle**: Compute → Instances → la tua istanza → **Start**
   (se è *Stopped*). Attendi 1-2 minuti: Nginx, l'app e i timer ripartono
   da soli.
3. **Controlla da SSH**: `systemctl status carpool nginx` e
   `sudo journalctl -u carpool -n 50`.
4. **Se il certificato è scaduto** (l'app è stata spenta per giorni):
   `sudo certbot renew --cert-name <IP> --force-renewal` e poi
   `sudo systemctl reload nginx`.
5. **Per non dipendere da te**: aggiungi le credenziali OCI come secret del
   repository e il sorvegliante riavvia l'istanza da solo. Servono tre cose,
   che trovi nella console Oracle:
   - `OCI_USER_OCID` — Identity → Domains → *Users* → il tuo utente → OCID
   - `OCI_FINGERPRINT` e `OCI_PRIVATE_KEY` — Identity → Domains → *Users* →
     il tuo utente → *API Keys* → **Add API key** → *Generate API key*:
     scarica il file `.pem` (è la chiave privata) e copia il *fingerprint*
     che compare nella schermata.
   Gli altri due secret (`OCI_TENANCY_OCID`, `OCI_INSTANCE_OCID`) sono già
   impostati. Per il `.pem`: **Settings → Secrets and variables → Actions →
   New repository secret**, nome `OCI_PRIVATE_KEY`, valore = tutto il contenuto
   del file (compreso `-----BEGIN PRIVATE KEY-----` e `-----END PRIVATE KEY-----`).

## Sicurezza della VM (cosa fanno gli script)

L'audit di sicurezza ha chiuso i punti aperti: sulla VM, dopo l'installazione,
valgono gia' queste regole.

| Cosa | Come |
| --- | --- |
| Forza bruta | `/api/auth/unlock` accetta 10 tentativi falliti per IP ogni 10 minuti, poi risponde 429 |
| La chiave nei log | Nginx usa un `log_format` che registra il percorso ma **non** la query string |
| Ruoli | cambiare la password o la chiave segreta richiede il ruolo admin |
| CSRF | il cookie di sessione e' `HttpOnly` + `SameSite=lax` (l'app e l'API sono sulla stessa origine) |
| Permessi | `/var/lib/carpool` e' 750, il database 640, e il servizio ha `UMask=0077` |
| Header | HSTS, `Referrer-Policy: no-referrer`, `nosniff`, `frame-deny`, `Permissions-Policy` |
| Header inoltrati | uvicorn si fida solo di `X-Forwarded-*` che arrivano da `127.0.0.1` (Nginx riscrive l'IP reale) |
| Dati pubblici | `/api/auth/status` non rivela utente, ruolo e chat Telegram senza sessione |

**Da rifare a mano se cambi qualcosa**: Nginx (`/etc/nginx/sites-available/carpool`),
il servizio (`/etc/systemd/system/carpool.service`) e i timer
(`carpool-certcheck.timer`, `carpool-update.timer`).

## Manutenzione

```bash
sudo systemctl restart carpool      # riavvia l'agente
sudo journalctl -u carpool -f       # log in tempo reale
cd /opt/carpool && sudo git pull    # aggiornamenti (poi restart)
sudo certbot certificates           # i certificati e le scadenze
systemctl list-timers               # i timer (certificato, aggiornamenti)
```

Il codice resta su GitHub: l'agente non ha bisogno del PC, ma il repo
resta il posto dove modifichi e versioni tutto. E con `carpool-update.timer`
il backend si aggiorna da solo, senza che tu debba ricordarti di fare
`git pull` sulla VM.

## Costi

| Voce | Costo |
| --- | --- |
| VM A1 (2 OCPU, 12 GB) | 0 (Always Free) |
| Block Volume 200 GB | 0 (Always Free) |
| Egress 10 TB/mese | 0 (Always Free) |
| Dominio (se lo usi) | ~10 €/anno |
| GitHub Pages | 0 |
| Groq | 0 (free tier) |
