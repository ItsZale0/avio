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
                              installata in home      Cloudflare Pages
```

## 1. Avvia l'agente automaticamente

Perche' l'agente cerchi ogni ora anche quando non lo lanci a mano:

**Task Scheduler (consigliato)**
1. Apri *Task Scheduler* → *Create Task*.
2. *General*: nome `Carpool Hunter`, spunta **Run whether user is logged on or not**.
3. *Triggers*: **At startup** (o *At log on*), con *Delay 1 minute*.
4. *Actions* → New:
   - Program: `powershell.exe`
   - Arguments: `-NoProfile -ExecutionPolicy Bypass -File "C:\percorso\start.ps1" -NoInstall`
   - Start in: `C:\percorso\`
5. *Settings*: spunta *If the task fails, restart every 1 minute*.

Alternativa rapida: crea un collegamento a `start.bat` dentro
`shell:startup` (Win+R → `shell:startup`).

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
4. Se vuoi la notifica push reale, tieni **Telegram** tra i canali: e' la via
   piu' affidabile (le push web richiedono HTTPS — che il tunnel ti da).

## 4. (Opzionale) Interfaccia su Cloudflare Pages

Cosi' l'app parte anche se il PC e' spento (mostrando solo lo stato salvato) e
carica piu' in fretta. L'API resta dietro il tunnel.

```powershell
python scripts/build_pages.py --api https://carpool.tuodominio.it
npx wrangler pages deploy dist-pages --project-name carpool-hunter
```

Poi consenti l'origine di Pages nel backend, in `.env`:

```
CARPOOL_CORS_ORIGINS=https://carpool-hunter.pages.dev
CARPOOL_PUBLIC_URL=https://carpool.tuodominio.it
```

e riavvia l'agente. `CARPOOL_PUBLIC_URL` e' l'indirizzo che compare nei link
delle notifiche.

## 5. Codice su GitHub (repo privato)

Il repo serve per il codice e i backup, non per far girare l'agente.

```powershell
git init -b main
git add .
git commit -m "Carpool Hunter: ricerca passaggi + intelligenza Groq"
git remote add origin git@github.com:TUONOME/carpool-hunter.git
git push -u origin main
```

`.gitignore` esclude `.env` e `data/` (il database con le tue ricerche e i
token), quindi restano sul PC.

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
| Cloudflare Pages | 0 |
| Modelli Groq (free tier) | 0 entro i limiti di richieste/giorno |
| GitHub repo privato | 0 |

## Sicurezza

L'app **non ha autenticazione**: chi conosce l'URL del tunnel puo' leggere e
modificare le tue ricerche. Quindi:

- non pubblicare l'URL;
- meglio se dietro **Cloudflare Access** (login) o con un sottodominio non indovinabile;
- non esporre mai `127.0.0.1:8787` direttamente su internet.
