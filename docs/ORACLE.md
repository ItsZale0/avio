# Server sempre acceso su Oracle Cloud (gratis)

Oracle Cloud offre un piano **Always Free**: una VM Linux gratuita 24/7, con
IP pubblico, che puoi usare come server per l'agente. Cosi' l'agente gira
anche quando il PC e' spento.

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
criterio (finestra di 7 giorni): CPU, rete e memoria sotto il 15% al 95°
percentile. Il tuo agente fa ricerche ogni ora e tiene ~100 MB di RAM su
12 GB: probabilmente rientra nella soglia della CPU.

**Come evitarlo**: appena creato l'account, passa al piano **Pay As You Go**
(Billing → Plans). Non ti verrà addebitato nulla finché resti entro le quote
Always Free, ma l'istanza non verrà più spenta.

Se invece resta su Always Free e la spengono, i dati sono al sicuro (il
volume persiste): basta riavviarla dalla console.

## Manutenzione

```bash
sudo systemctl restart carpool      # riavvia l'agente
sudo journalctl -u carpool -f       # log in tempo reale
cd /opt/carpool && sudo git pull    # aggiornamenti (poi restart)
```

Il codice resta su GitHub: l'agente non ha bisogno del PC, ma il repo
resta il posto dove modifichi e versioni tutto.

## Costi

| Voce | Costo |
| --- | --- |
| VM A1 (2 OCPU, 12 GB) | 0 (Always Free) |
| Block Volume 200 GB | 0 (Always Free) |
| Egress 10 TB/mese | 0 (Always Free) |
| Dominio (se lo usi) | ~10 €/anno |
| GitHub Pages | 0 |
| Groq | 0 (free tier) |
