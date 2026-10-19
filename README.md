# Carpool Hunter

Dashboard + agente che cerca **passaggi in auto, treno, autobus, carsharing e voli** su più fonti
reali, ti mostra **il modo più economico** per fare quel viaggio (anche combinando più tratte) e ti
avvisa su Telegram/email quando trova qualcosa di nuovo. Se un dato manca, **te lo chiede**.

> Pensato per l'Italia. **Solo dati reali**: nessuna offerta simulata. Agente sul tuo PC,
> intelligenza con i **modelli gratuiti Groq**.
> Interfaccia web + **PWA installabile sul telefono** (vedi [`docs/DEPLOY.md`](docs/DEPLOY.md)).

---

## Avvio rapido (Windows)

Doppio clic su **`start.bat`** (oppure `powershell -ExecutionPolicy Bypass -File start.ps1`).

Al primo avvio crea `.venv`, installa le dipendenze e apre il server su:

**http://127.0.0.1:8787**

Poi:

1. **+ Nuova ricerca** → inserisci partenza, arrivo, “entro quando”, budget, raggio.
   (Oppure scrivi il viaggio in una frase e premi **✨ Compila con l'AI**.)
2. Scegli le fonti e i canali di avviso.
3. L'agente fa subito il primo giro e poi ricontrolla ogni ora (configurabile).

Avvio manuale senza script:

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8787
```

---

## Intelligenza (Groq, gratis)

L'agente ha un **cervello**: capisce una richiesta scritta a parole, sceglie la
**combinazione più economica** tra le offerte reali (anche a due tratte, es. carsharing
fino alla stazione + bus) e **ti fa le domande** che sbloccano un piano migliore
(“posso arrivare il giorno dopo?”, “alzo il budget?”, “accetti un cambio?”).

1. Crea una chiave gratuita su <https://console.groq.com/keys>.
2. Incollala in **Impostazioni → Intelligenza (Groq)** → *Prova AI*.
3. Modelli di default: `llama-3.3-70b-versatile` (ragionamento) e
   `llama-3.1-8b-instant` (compiti veloci). Puoi cambiarli o elencarli dai pulsanti.

Senza chiave l'app **funziona lo stesso**: il piano resta (calcolato dal motore
deterministico) ma senza spiegazioni, domande e interpretazione del linguaggio naturale.

> L'app usa **solo dati reali**: ogni prezzo e orario arriva da una fonte esterna al momento
> del controllo. Se nessuna fonte risponde, la ricerca resta vuota (niente dati inventati).

### Le domande, e come rispondere

Le domande compaiono nella scheda della ricerca e — se Telegram è tra i canali —
arrivano in chat con i **pulsanti** pronti. La risposta diventa una preferenza
(`flexible_days`, `allow_multi_leg`, `max_walk_km`, `luggage`, …) usata dal giro
successivo. Puoi anche scrivere liberamente in chat: il bot interpreta la risposta.

---

## Configurazione notifiche

Apri l'ingranaggio in alto a destra nella dashboard.

### Telegram (consigliato)

1. Su Telegram cerca **@BotFather** → `/newbot` → copia il **token**.
2. Scrivi qualcosa al tuo bot.
3. Apri `https://api.telegram.org/bot<TOKEN>/getUpdates` e copia `chat.id`.
4. Incolla token e chat id nella dashboard → **Invia messaggio di prova**.

Da quel momento ricevi un messaggio solo quando ci sono **nuove offerte** o **ribassi**,
non a ogni controllo.

### Email

Compila server SMTP, porta, utente, password/app-password, mittente e destinatario.
Per Gmail serve una *app password* (verifica in due passaggi attiva).

### Silenzio

Imposta “silenzio dalle / fino alle” per non ricevere push di notte. Gli avvisi restano
comunque nella dashboard.

---

## Le fonti, e cosa aspettarsi davvero

Tutte le fonti restituiscono **dati reali** al momento del controllo: nessuna offerta è simulata.

| Fonte | Copre | Stato | Note |
|---|---|---|---|
| **Omio** | treni + bus + voli di decine di compagnie | ⚠️ best effort | Aggregatore (Trenitalia, Italo, FlixBus, Itabus, Marino, compagnie aeree…). Endpoint dell'app, non documentato. |
| **Trenitalia** | regionali, Frecciarossa, Intercity, notturni | ⚠️ best effort | Prezzi ufficiali Trenitalia. Endpoint interno, non documentato. |
| **FlixBus** | autobus | ⚠️ best effort | Endpoint pubblico non ufficiale: può cambiare o bloccare. |
| **Itabus** | autobus | ⚠️ best effort | Operatore low-cost italiano. Endpoint pubblico non ufficiale. |
| **BlaBlaCar** | passaggi in auto | 🔗 solo link | L'API pubblica è stata **dismessa**: niente dati automatici, la ricerca si apre sul sito ufficiale (scheda *Link utili*). |
| **Carsharing** | auto condivisa | ✅ | **Stima** di costo (non un preventivo) su tariffe Enjoy, Free2Move, Ubeeqo, E-Vai. |
| **Mezzi pubblici vicini** | fermate/stazioni | ⚠️ best effort | OpenStreetMap Overpass: fermate vicine, non orari. A volte lento/indisponibile. |
| **Orari GTFS** | treni e bus locali (Trentino, Lombardia, tram di Padova) | ✅ | Orari **ufficiali** da feed GTFS aperti, con coincidenze dirette e con **un cambio**. Il GTFS non contiene i prezzi: le card mostrano "solo orario". |

> Trenitalia/Omio/FlixBus/Itabus sono **endpoint non ufficiali** usati dai rispettivi siti/app: possono
> cambiare senza preavviso. Ogni fonte che cade è segnalata nelle note del giro e le altre restano attive.

Ogni ricerca include sempre la scheda **Link utili** con ricerche precompilate su
BlaBlaCar, FlixBus, Omio, Trainline, Trenitalia, Italo, Google Maps e app di carsharing,
più i link ai **codici sconto** dei treni: è il fallback affidabile quando una fonte non ha API.

### Offerte e codici sconto

Il pulsante **🏷️** in alto apre il pannello *Offerte & codici sconto*, che legge **solo
dalle pagine ufficiali** dei vettori e mostra:

- le **offerte del momento** (Trenitalia, Frecciarossa, Italo, last minute, carte
  sconto, studenti, Black Friday…) estratte dalle pagine offerte di Trainline e Italo;
- i **codici sconto / voucher** con il link diretto alle pagine ufficiali
  (codici promo Italo, codice sconto Trenitalia, voucher FlixBus) e agli aggregatori
  (Cuponation, Groupon).

**Nessun codice è inventato**: dove un codice non è pubblicamente leggibile, il pannello
ti porta alla pagina ufficiale che lo contiene. Le pagine vengono lette al momento e
messe in cache per 6 ore (pulsante **Aggiorna** per forzare).

### BlaBlaCar

L'API pubblica di BlaBlaCar è stata **dismessa**: l'app non può più leggere i
passaggi in modo automatico. La fonte resta attiva come **link di ricerca**: la
scheda *Link utili* apre il sito ufficiale con partenza, destinazione e data già
compilate.

---

## Come ragiona l'agente

- **Filtri** — scarta ciò che arriva dopo l'orario massimo, parte troppo presto,
  costa oltre il budget o ha punti di ritrovo fuori dal raggio.
- **Punteggio 0–100** — 52% prezzo, 22% rispetto della finestra oraria,
  16% vicinanza ai punti scelti, 10% affidabilità dichiarata.
- **Badge** — *Più economico*, *Migliore*, *Più veloce*, *Più vicino*, *Ribasso*,
  *Sotto budget*, *Stima*.
- **Piano più economico** — il pianificatore costruisce le combinazioni reali
  (tratta singola o due tratte collegate) e ordina per **costo effettivo**
  = prezzo + costo stimato per raggiungere il ritrovo. L'AI sceglie e spiega;
  senza AI sceglie comunque la più economica.
- **Costo effettivo** — al prezzo aggiunge una stima per raggiungere il punto di ritrovo
  (gratis sotto 1,2 km, ~2 € con i mezzi, taxi oltre 5 km).
- **Avvisi** — un digest per giro, solo se ci sono novità (nuove offerte, ribassi o
  **piano migliorato**). Deduplica per offerta: niente spam ogni ora.
- **Ribassi** — se il prezzo scende di almeno il 4% (min 0,50 €), ricevi un avviso dedicato.
- **Fermate precise** — ogni offerta mostra la fermata/stazione **esatta** di partenza e
  arrivo (es. *Verona Porta Nuova*, *Trento (Lungadige San Nicolò)*), non solo la città.
  Per i viaggi con cambi, la card si espande in **“dove ferma e cambia”**: l'elenco delle
  tratte con orari e mezzo (es. *Thiene → Vicenza · RE 16074*).
- **Carsharing** — stima il costo di un noleggio one-way nelle città coperte e mostra
  solo i provider che permettono quella tratta.
- **Fonti rotte** — l'AI non inventa nulla: se una fonte cade, il piano si costruisce
  con le altre e la fonte segnala il problema tra le note del giro.

Le tariffe del carsharing sono in `data/carsharing.json` (crealo partendo dai default in
`app/adapters/carsharing.py`): puoi correggerle quando cambiano.

---

## Struttura del progetto

```
app/
  main.py              API FastAPI + frontend + PWA
  config.py            percorsi, .env, host/porta, CORS, URL pubblico
  cli.py               comandi (run-due, list) per cron esterni
  db.py                SQLite (WAL) e schema
  schemas.py           modelli Pydantic
  store.py             CRUD ricerche, offerte, piani, avvisi, link
  geo.py               geocoding Nominatim + fallback offline + distanze
  scoring.py           punteggi, badge, costo effettivo
  notify.py            Telegram (messaggi, pulsanti, getUpdates), email, digest
  ai/
    groq.py            client Groq (chat JSON, modelli, log token)
    prompts.py         prompt di parsing, pianificazione, risposta
    planner.py         combinazioni reali (1-2 tratte), costo effettivo
    intelligence.py    orchestrazione: NLU, piano, spiegazioni, domande
  adapters/
    base.py            contratto comune delle fonti
    blablacar.py       link di ricerca (API pubblica dismessa)
    flixbus.py         autobus
    itabus.py          autobus low-cost
    trenitalia.py      treni (regionali, Frecce, Intercity)
    omio.py            aggregatore treni + bus + voli
    carsharing.py      stime noleggio one-way
    transit.py         fermate vicine (Overpass)
    gtfs.py            orari reali da feed GTFS + coincidenze
  gtfs/
    feeds.py           catalogo dei feed aperti (Trentino, Lombardia, Padova)
    importer.py        da zip GTFS a SQLite (data/gtfs.db)
    engine.py          coincidenze: dirette + un cambio, anche a piedi
  services/
    search.py          motore: fonti -> filtri -> punteggi -> piano -> avvisi
    scheduler.py       tick 20s, polling Telegram 45s
    questions.py       ciclo di vita delle domande + invio Telegram
    telegram_bot.py    risposte da chat, nuovi viaggi scritti in chat
    promos.py          offerte + codici sconto dalle pagine ufficiali
    settings.py        impostazioni a runtime
web/
  index.html, styles.css, app.js
  manifest.webmanifest, sw.js, icons/    PWA
scripts/
  make_icons.py        genera le icone PWA
  build_pages.py       bundle per Cloudflare Pages
data/                  database SQLite (generato)
docs/DESIGN.md         architettura e roadmap
docs/DEPLOY.md         telefono, tunnel, Pages, GitHub
tests/test_core.py     test di scoring/geo
tests/test_ai.py       test planner, fallback senza AI, finestra di flessibilità
```

---

## Sul telefono (senza server a pagamento)

Guida completa in **[`docs/DEPLOY.md`](docs/DEPLOY.md)**. In breve:

- l'agente gira sul PC, avviato all'accensione con Task Scheduler;
- un **Cloudflare Tunnel** gratuito lo rende raggiungibile in HTTPS;
- dal telefono apri l'URL → **Aggiungi a schermata Home** → è una PWA;
- (opzionale) l'interfaccia su **Cloudflare Pages**, l'API resta dietro il tunnel.

---

## Comandi utili

```powershell
# test
python -m unittest discover -s tests -v

# avvio con ricarica automatica (sviluppo)
python -m uvicorn app.main:app --reload --port 8787

# un giro di ricerca a mano (buono per un cron)
python -m app.cli run-due
python -m app.cli run-due --all

# orari GTFS (treni/bus): stato dei feed e aggiornamento import
python -m app.cli gtfs-list
python -m app.cli gtfs-update

# rigenera le icone PWA
python scripts/make_icons.py
```

---

## Limiti e responsabilità

- Le fonti senza API vengono aperte come **link di ricerca**: nessuno scraping aggressivo
  o aggiramento di protezioni. Rispetta i termini d'uso dei siti.
- I prezzi possono cambiare tra un controllo e l'altro: **verifica sempre nell'app/sito**
  prima di prenotare.
- Il carsharing è una **stima** basata su tariffe indicative, non una prenotazione.
- L'app non ha autenticazione: tienila su `127.0.0.1` o dietro un tunnel con URL segreto
  (meglio con **Cloudflare Access**). Se la esponi in rete, metti login e HTTPS.
- Orari e disponibilità dei mezzi pubblici non sono inclusi: i link puntano a Google Maps.
- **Orari GTFS**: treni e bus locali sono coperti dove esiste un feed aperto (Trentino,
  Lombardia, tram di Padova). Il GTFS **non contiene i prezzi**: le card mostrano
  "solo orario" e rimandano alla tariffa ufficiale dell'operatore.
- **L'AI non inventa prezzi**: sceglie solo tra le offerte raccolte. Restano possibili
  errori di interpretazione della richiesta o della risposta: l'ultima parola è la tua.

---

## Roadmap

- [x] PWA installabile sul telefono (manifest + service worker + icone)
- [x] Intelligenza Groq: linguaggio naturale, piano più economico, domande all'utente
- [x] Ricerche multi-tratta (auto/carsharing + bus) con confronto per costo effettivo
- [x] Trenitalia con prezzi reali (+ Italo via aggregatore Omio)
- [x] Più fonti reali: Omio (treni/bus/voli), Itabus, FlixBus, BlaBlaCar
- [x] Pannello offerte & codici sconto dalle pagine ufficiali dei vettori
- [x] Orari GTFS reali (treni/bus) con motore di coincidenze dirette + 1 cambio
- [ ] Mappa delle offerte e delle zone carsharing
- [ ] Web Push (oltre a Telegram) per la PWA
- [ ] Scraping opzionale (Playwright) attivabile per singola fonte
- [ ] Export calendario (.ics) e condivisione di una ricerca
