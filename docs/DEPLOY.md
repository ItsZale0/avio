# Mettere Carpool Hunter sul telefono (senza pagare server)

L'agente gira **sul tuo PC** (sempre acceso). Il telefono e il resto del mondo
ci arrivano attraverso un tunnel gratuito di Cloudflare. Nessun server da
affittare, nessun IP pubblico, nessuna porta aperta sul router.

```
   PC (agente + SQLite)  ──cloudflared──►  https://carpool.tuodominio.it
                                                  │
                                    ┌─────────────┴─────────────┐
                                    │                           │
                              PWA sul telefono        (opzionale) UI su
                              installata in home      GitHub Pages
```

## 0. Avvio con un comando

`start.bat` (o `start.ps1`) avvia **agente + tunnel** insieme, con la finestra
del terminale in primo piano (utile per vedere i log mentre si lavora):

```powershell
start.bat
```

Per farlo girare **in background, senza finestre di terminale**, usa il
supervisore:

```powershell
wscript.exe avvia-agente.vbs     # avvia senza finestre
python scripts\agent_status.py   # stato e ultimi log
python scripts\stop_agent.py     # ferma tutto
```

`avvia-agente.vbs` e' gia' installato come collegamento nella cartella di
avvio (vedi sezione 1): al login l'agente parte da solo, in silenzio.

Il tunnel "quick" assegna un URL nuovo a ogni avvio. Per questo
`scripts/tunnel.py`, appena avviato, fa quattro cose da solo:

1. scrive l'URL pubblico in `.env` (`CARPOOL_PUBLIC_URL`);
2. scrive `backend.json` nel repo e lo pubblica: e' il puntatore **fresco**
   che il frontend legge direttamente dal branch, senza aspettare il deploy;
3. aggiorna la variabile GitHub `CARPOOL_API_BASE`, cosi' la dashboard su
   GitHub Pages punta al tunnel giusto;
4. rilancia il deploy di GitHub Pages, che rigenera `config.json`.

Serve un token GitHub con scope `repo` nella variabile d'ambiente
`GITHUB_TOKEN` (senza, i passi 2-4 vengono saltati e basta riavviare il
deploy a mano dalla tab *Actions*).

```powershell
setx GITHUB_TOKEN "ghp_..."
```

### Il tunnel non si collega? (porta 7844 bloccata)

Il tunnel "quick" di Cloudflare usa la **porta 7844** in uscita. Se il router,
l'ISP o un filtro bloccano le porte non standard, il tunnel non si registra e
l'indirizzo pubblico risponde `530`: la dashboard su GitHub Pages resta muta
mentre l'app in locale funziona.

Come accorgersene:

```powershell
python scripts\agent_status.py
# Tunnel: NON collegato (HTTP 530): ...
```

Le soluzioni, in ordine di semplicità:

1. **cambiare rete** per qualche minuto (hotspot del telefono): se il tunnel
   si collega, il problema è la rete di casa;
2. **riavviare il router** o controllare le sue impostazioni di firewall
   (molti router hanno un filtro "porta non standard" attivabile);
3. **ngrok** (URL stabile, lavora sulla 443): scarica `ngrok.exe` nella
   cartella del progetto, crea un account gratuito su
   <https://dashboard.ngrok.com>, prendi l'authtoken e mettilo in `.env`:

   ```ini
   NGROK_AUTHTOKEN=2abcDEF...
   ```

   `scripts/tunnel.py` lopreferisce da solo quando è configurato: con
   `ngrok` l'indirizzo pubblico **non cambia più** a ogni riavvio.

Verifica rapida di cosa è bloccato:

```powershell
Test-NetConnection 198.41.192.107 -Port 7844   # edge Cloudflare
Test-NetConnection 1.1.1.1 -Port 443            # dovrebbe funzionare
```

### ngrok: tre cose da sapere

1. **La pagina di avviso**: con il piano gratuito ngrok mostra "You are about
   to visit…" al browser. Le richieste API non la vedono, ma il frontend su
   GitHub Pages si': per questo ngrok **vieta l'header di bypass nel
   preflight** e un frontend cross-origin non puo' aggirarla. La soluzione
   e' usare l'app servita dal tunnel stesso (stessa origine, nessun CORS):
   l'app serve gia' il frontend, quindi basta aprire https://<tunnel>/.
2. **L'URL stabile**: nel pannello ngrok, *Domains -> Create Domain* per
   prenotare il dominio statico incluso nel piano gratuito. Mettilo in
   Impostazioni -> *Accesso da fuori* -> "Dominio ngrok" (o NGROK_DOMAIN
   nel .env): da li' in poi l'indirizzo non cambia mai.
3. **Verificare da fuori**: il workflow *Verifica tunnel* (Actions -> Verifica
   tunnel -> Run workflow) fa le prove da un runner esterno: GET, preflight
   CORS e POST reale. Serve perche' dalla rete di casa il tunnel puo' essere
   bloccato e non si distinguerebbe "rotto" da "filtrato".

### Il link che dai agli altri

L'unico indirizzo da usare e' **<https://itszale0.github.io/carpool-intelligence/>**:
l'interfaccia e' su GitHub Pages, l'API dietro il tunnel. Non serve
ricordare l'IP del PC ne' l'URL del tunnel.

Il frontend conosce due puntatori e li usa entrambi:

| file | chi lo aggiorna | freschezza |
|---|---|---|
| `config.json` (su Pages) | deploy automatico | immediata (fetch senza cache) |
| `backend.json` (sul branch) | `scripts/tunnel.py` a ogni avvio | entro ~5 min (cache CDN di GitHub) |

Sceglie quello con il timestamp piu' recente e, se la chiamata all'API
fallisce, prova l'altro: dopo un riavvio dell'agente la dashboard si
riprende da sola.

### Il supervisore

`scripts/run_agent.py` fa tre cose:

- avvia l'agente e il tunnel **senza finestre** (usa `pythonw.exe`);
- **rilancia** un processo che muore (crash, blocco, killed);
- scrive i log in `data/logs/`: `agent.log` (supervisore), `agente.log`,
  `tunnel.log`.

Per fermarlo: `python scripts\stop_agent.py` (oppure `ferma-agente.bat`).
Per guardare come sta: `python scripts\agent_status.py`.

## 1. Avvia l'agente automaticamente

Perche' l'agente cerchi ogni ora anche quando non lo lanci a mano:

**Collegamento nella cartella di avvio (consigliato, senza privilegi)**

Gia' fatto: il collegamento *Carpool Hunter.lnk* punta a `avvia-agente.vbs`.
Per rifarlo:

```powershell
$startup = [Environment]::GetFolderPath("Startup")
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut((Join-Path $startup "Carpool Hunter.lnk"))
$link.TargetPath = "wscript.exe"
$link.Arguments = '"C:\percorso\avvia-agente.vbs"'
$link.WorkingDirectory = "C:\percorso"
$link.Save()
```

**Task Scheduler** (per partire anche senza aver fatto il login)

1. Apri *Task Scheduler* → *Create Task*.
2. *General*: nome `Carpool Hunter`, spunta **Run whether user is logged on or not**.
3. *Triggers*: **At startup** (o *At log on*), con *Delay 1 minute*.
4. *Actions* → New:
   - Program: `wscript.exe`
   - Arguments: `"C:\percorso\avvia-agente.vbs"`
   - Start in: `C:\percorso\`
5. *Settings*: spunta *If the task fails, restart every 1 minute*.

Oppure, in PowerShell **come amministratore**:

```powershell
python scripts\install_autostart.py            # crea la task
python scripts\install_autostart.py --remove   # la toglie
```

## 2. Tunnel Cloudflare (URL HTTPS stabile, gratis)

Serve un account Cloudflare (gratis). Il piano gratuito basta.

```powershell
winget install --id Cloudflare.cloudflared
cloudflared tunnel login                 # apre il browser, autorizza il dominio
cloudflared tunnel create carpool
cloudflared tunnel route dns carpool carpool.tuodominio.it
cloudflared tunnel run --url http://127.0.0.1:8787 carpool
```

Poi installa il tunnel come servizio Windows, cosi' parte col PC:

```powershell
cloudflared service install
```

> **Prova velocissima senza account**: `cloudflared tunnel --url http://127.0.0.1:8787`
> genera un URL `https://xxx.trycloudflare.com` valido finche' resta acceso.
> Comodo per un test, scomodo per la PWA (l'URL cambia).

Se preferisci un dominio tuo con **Cloudflare Access** davanti (login email/OTP),
l'URL resta segreto anche se qualcuno lo indovina.

## 3. Installare la PWA sul telefono

1. Apri l'URL del tunnel dal telefono (Chrome su Android, Safari su iOS).
2. Android: menu ⋮ → **Installa app** / *Aggiungi a schermata Home*.
   iOS: Condividi → **Aggiungi a Home**.
3. Compariranno icona e nome: si apre a schermo intero, come un'app.
4. Se vuoi la notifica push reale, attivala in **Impostazioni → Notifiche push**
   (*Attiva su questo dispositivo*): funziona solo su HTTPS — che il tunnel ti dà —
   o su `localhost`. Su iPhone/iPad servono iOS 16.4 o successivo e l'app aggiunta
   alla schermata Home. In alternativa tieni **Telegram** tra i canali. Richiede
   `pip install pywebpush` sul PC.

## 4. Interfaccia su GitHub Pages

Il frontend statico (cartella `static/`) e' gia' pubblicato su GitHub Pages:

```
https://<tuo-utente>.github.io/<nome-repo>/
```

Il deploy e' automatico: ogni push su `main` che tocca `static/` fa partire il
workflow `.github/workflows/pages.yml`, che copia `static/` nell'artefatto
Pages e lo pubblica.

La pagina e' un *client* dell'API: per funzionare deve sapere dove si trova il
backend. Si imposta con la variabile di repository **`CARPOOL_API_BASE`**
(Settings → Secrets and variables → Actions → Variables):

| Nome | Valore | Esempio |
| --- | --- | --- |
| `CARPOOL_API_BASE` | URL pubblico del backend (tunnel) | `https://carpool.tuodominio.it` |

Il workflow la copia in `config.json` dentro l'artefatto. Senza questa variabile
la pagina si apre ma resta "offline", perche' non sa a chi chiedere i dati.

**Con il tunnel "quick" l'URL cambia a ogni avvio**: per questo
`scripts/tunnel.py` aggiorna la variabile e rilancia il deploy da solo
(vedi sezione 0). Con un tunnel con nome e dominio tuo l'URL e' fisso e
non serve fare nulla.

Nel backend, in `.env`, consenti l'origine di Pages:

```
CARPOOL_CORS_ORIGINS=https://<tuo-utente>.github.io
CARPOOL_PUBLIC_URL=https://carpool.tuodominio.it
```

e riavvia l'agente. `CARPOOL_PUBLIC_URL` e' l'indirizzo che compare nei link
delle notifiche.

> **Alternativa: Cloudflare Pages.** Se preferisci l'hosting su Cloudflare, il bundle
> si prepara con `python scripts/build_pages.py --api https://carpool.tuodominio.it`
> e si pubblica con `npx wrangler pages deploy dist-pages --project-name carpool-hunter`.

## 4bis. Orari GTFS (treni e bus)

Gli orari ufficiali stanno in un database separato, `data/gtfs.db` (escluso da
git). Alla prima accensione va riempito una volta:

```powershell
python -m app.cli gtfs-update
python -m app.cli gtfs-list
```

Ci vogliono pochi secondi per i feed del Trentino e una decina di secondi per
Trenord; occupa qualche centinaio di MB. Da quel momento le ricerche usano gli
orari anche per le coincidenze con un cambio. Per rimandare l'aggiornamento
(pubblicano i feed nuovi ogni qualche mese), usa il pulsante **Aggiorna orari**
in Impostazioni → *Orari GTFS*, oppure rilancia `gtfs-update`. Se non ti serve,
spegni la fonte con `gtfs_enabled = 0`.

## 5. Codice su GitHub

Il repo serve per il codice, i backup e per pubblicare l'interfaccia.

```powershell
git init -b main
git add .
git commit -m "Carpool Hunter: ricerca passaggi + intelligenza Groq"
git remote add origin https://github.com/TUONOME/carpool-intelligence.git
git push -u origin main
```

`.gitignore` esclude `.env` e `data/` (il database con le tue ricerche e i
token), quindi restano sul PC.

> **Pubblico o privato?** Il piano gratuito di GitHub non permette GitHub Pages
> su repo privati. Con repo privato l'interfaccia si pubblica su Cloudflare
> Pages (vedi sezione 4); con repo pubblico si usa GitHub Pages. La protezione
> dei dati resta la stessa in entrambi i casi: i segreti (`telegram_token`,
> `groq_api_key`, `smtp_password`, `vapid_private_key`) stanno solo in
> `data/carpool.db` e in `.env`, mai nel repo.

**Alternativa ad "agente sempre acceso"**: un workflow GitHub Actions con cron
oraria puo' eseguire `python -m app.cli run-due` sui runner gratuiti (2000
minuti/mese per i repo privati). Lo stato andrebbe pero' ricommittato a ogni
giro: piu' fragile. Se il PC resta acceso, il servizio locale e' la scelta
migliore.

## Costi

| Voce | Costo |
| --- | --- |
| Agente sul PC | 0 (solo elettricita') |
| Cloudflare Tunnel | 0 |
| GitHub Pages | 0 (repo pubblico) |
| Cloudflare Pages | 0 (repo privato) |
| Modelli Groq (free tier) | 0 entro i limiti di richieste/giorno |
| GitHub repo | 0 |

## Sicurezza

L'app ha **accesso multi-utente**: nome utente, password (bcrypt) e sessione in
cookie `HttpOnly`. Ogni utente vede solo le proprie ricerche, offerte, avvisi
e notifiche. Però l'accesso protegge i dati, non il canale: chi conosce l'URL
del tunnel vede comunque la schermata di login. Quindi:

- non pubblicare l'URL;
- meglio se dietro **Cloudflare Access** (login) o con un sottodominio non indovinabile;
- non esporre mai `127.0.0.1:8787` direttamente su internet.

### Gestione degli account

| Azione | Come |
| --- | --- |
| Creare il primo account (amministratore) | dal form di registrazione, o `python -m app.cli user-add NOME` |
| Approvare un account in attesa | *Impostazioni → Utenti → Approva* |
| Disabilitare un account | *Impostazioni → Utenti → Disabilita* (revoca le sessioni) |
| Elencare gli account | `python -m app.cli users` |
| Cambiare la propria password | *Impostazioni → Account*, oppure dall'amministratore |

Le password non si vedono mai in chiaro: nel database c'e' solo l'hash bcrypt.
Il token di sessione viene memorizzato come hash SHA-256, e i cookie sono
`HttpOnly` + `SameSite=Lax` + `Secure` quando la connessione e' HTTPS.

### Collegare Telegram al proprio account

Il bot Telegram e' unico per l'istanza, ma ogni utente riceve solo le notifiche
delle proprie ricerche. Per collegare la chat:

1. in Impostazioni → Telegram, genera un codice;
2. scrivi al bot: `/link CODICE`;
3. da quel momento la chat e' associata al tuo account.

Le impostazioni di Telegram (token, chat ID) sono **per utente**: ognuno puo'
usare il proprio bot, se vuole.

### Dati personali nel database

`data/carpool.db` contiene tutto: account, hash delle password, token, ricerche.
Sta in `.gitignore` e non finisce mai nel repo. Se fai un backup, trattalo
come materiale sensibile.
