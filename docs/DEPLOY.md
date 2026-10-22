# Deploy e operatività

L'app è online su una **VM Oracle Cloud** (piano Always Free, 0 €) e
l'interfaccia è su **GitHub Pages**. Questo documento spiega come si pubblica
una modifica e come si tiene sotto controllo il servizio.

---

## 1. Com'è fatto

```
 GitHub (codice)  ──►  VM Oracle (API + agente + database + Nginx + HTTPS)
        │                          │
        │                          └──► https://129.152.8.10   (app e API, stessa origine)
        │
        └──►  GitHub Pages (interfaccia)  ──►  https://itszale0.github.io/avio/
                        (il frontend, all'apertura, porta sulla VM)
```

Tre pezzi, tre compiti:

| pezzo | dove | chi lo aggiorna |
|---|---|---|
| codice | `main` su GitHub | `carpool-update.timer` sulla VM: `git pull` ogni ora + riavvio |
| interfaccia | GitHub Pages | il workflow `pages.yml` a ogni push su `static/` |
| indirizzo del backend | variabile `CARPOOL_API_BASE` + `backend.json` | `scripts/publish_backend.py` |

Il frontend non chiama l'API da un'altra origine: all'apertura verifica quale
backend è vivo e ci porta. Così il cookie di sessione è sempre di prima parte
e niente preflight CORS.

---

## 2. Pubblicare una modifica

```powershell
git add -A
git commit -m "..."
git push origin main
```

Poi:

- **frontend** (`static/`): il workflow `pages.yml` fa il deploy da solo (pochi
  minuti);
- **backend** (tutto il resto): la VM lo prende da sola entro un'ora
  (`carpool-update.timer`). Per non aspettare:

  ```powershell
  ssh ubuntu@129.152.8.10 "sudo systemctl start carpool-update.service"
  ```

Prima di pubblicare, in locale:

```powershell
.venv\Scripts\python -m pytest -q
node --check static\app.js
```

---

## 3. Se l'indirizzo della VM cambia

Succede solo se l'istanza viene **ricreata** (non basta spostarla: l'IP
effimero resta assegnato anche a istanza ferma).

```powershell
# 1. pubblica il nuovo indirizzo (variabile + backend.json + deploy del frontend)
python scripts\publish_backend.py https://<NUOVO_IP>
```

```bash
# 2. sulla VM: certificato per il nuovo IP
sudo certbot renew --cert-name <NUOVO_IP> --force-renewal
sudo systemctl reload nginx
```

La variabile `CARPOOL_API_BASE` del repository è quella che fa da sorgente al
frontend: se il valore non è aggiornato, il link GitHub porta sul backend
vecchio.

---

## 4. Il sorvegliante (fuori dalla VM)

`.github/workflows/uptime.yml` gira su GitHub Actions, **fuori dalla VM**: e'
l'unico controllo che funziona anche quando la VM e' spenta.

Ogni 30 minuti chiede `GET /api/ping` (endpoint pubblico, risponde solo
`ok`, ora e versione). Se non risponde:

1. prova a **riavviare l'istanza** via API OCI (se i secret sono impostati);
2. **avvisa su Telegram** (secret `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`);
3. fa fallire il controllo, così GitHub manda anche l'email.

Per abilitare il riavvio automatico servono tre secret (i dettagli in
[`ORACLE.md`](ORACLE.md), sezione "Se l'app non risponde"):

| secret | cos'è |
|---|---|
| `OCI_USER_OCID` | OCID del tuo utente IAM |
| `OCI_FINGERPRINT` | impronta della chiave API |
| `OCI_PRIVATE_KEY` | la chiave privata `.pem` (scaricata dalla console) |

`OCI_TENANCY_OCID` e `OCI_INSTANCE_OCID` sono gia' impostati.

---

## 5. Il backup dei dati

`carpool-backup.timer` gira ogni notte alle 3:17 sulla VM:

1. copia coerente del database (SQLite, non un `cp` a caldo);
2. cifratura AES-256 (openssl, PBKDF2, 200k iterazioni);
3. invio su Telegram come documento;
4. tre copie locali su disco, le vecchie vengono cancellate.

La chiave sta in `/var/lib/carpool/backup.key` (600) e viene mandata su
Telegram **una volta sola**: va conservata nel gestore di password, senza
di lei i backup non si aprono.

Per aprire un backup:

```bash
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
    -in carpool-backup-AAAAMMGG-HHMM.tar.gz.enc \
    -out carpool-backup.tar.gz -pass file:backup.key
tar xzf carpool-backup.tar.gz
```

---

## 6. Se l'app non risponde

1. Il link del giorno: <https://itszale0.github.io/avio/>.
   Se non risponde, il problema e' la VM.
2. Console Oracle → Compute → Instances → **Start** (se e' *Stopped*). Dopo
   1-2 minuti l'app torna: `carpool`, Nginx e i timer sono abilitati al boot.
3. Da SSH: `systemctl status carpool nginx`,
   `sudo journalctl -u carpool -n 50`.
4. Certificato scaduto (l'app e' stata ferma giorni):
   `sudo certbot renew --cert-name <IP> --force-renewal && sudo systemctl reload nginx`.
5. Dati persi? Il backup della sezione 5.

La guida completa della VM: [`ORACLE.md`](ORACLE.md).

---

## 7. Sul telefono

- apri <https://itszale0.github.io/avio/> → **Aggiungi a
  schermata Home** → PWA installabile;
- la chiave segreta (`?key=...`) evita di digitare la password: la trovi in
  *Impostazioni → Genera chiave* (si vede una volta sola).

---

## 8. Sviluppo in locale

Il PC non serve a far girare l'app: serve a **sviluppare**.

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
Copy-Item .env.example .env
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8787
```

http://127.0.0.1:8787 — i dati locali stanno in `data/` (gitignorata).

Prima di ogni push:

```powershell
.venv\Scripts\python -m pytest -q
node --check static\app.js
```
