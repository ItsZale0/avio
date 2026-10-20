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

Lo script avvia anche il **tunnel Cloudflare**, che rende l'app raggiungibile
dal telefono. Il tunnel "quick" assegna un URL nuovo a ogni avvio: per questo
`scripts/tunnel.py` aggiorna da solo il `.env`, la variabile GitHub
`CARPOOL_API_BASE` e rilancia il deploy di GitHub Pages. Per farlo serve un
token GitHub con scope `repo` in `GITHUB_TOKEN` (`setx GITHUB_TOKEN "ghp_..."`).

### Il link unico: GitHub Pages

L'indirizzo da usare, da qualunque dispositivo e da qualunque rete, e':

**<https://itszale0.github.io/carpool-intelligence/>**

L'interfaccia vive su GitHub Pages, l'API dietro il tunnel. Non serve
ricordare l'URL del tunnel ne' l'IP del PC: a ogni avvio dell'agente il
tunnel cambia indirizzo, e la dashboard lo segue da sola.

Il meccanismo e' a due puntatori, per non restare mai fermi:

| file | chi lo aggiorna | quando e' fresco |
|---|---|---|
| `config.json` (su Pages) | il deploy automatico | subito (fetch senza cache) |
| `backend.json` (sul branch) | lo script del tunnel, a ogni avvio | entro ~5 min (cache CDN) |

Il frontend legge entrambi, sceglie il piu' recente e, se il backend non
risponde, prova l'altro: se il tunnel e' stato riavviato, la pagina si
riprende da sola in un secondo.

**Per farlo girare in background senza finestre di terminale:**

```powershell
wscript.exe avvia-agente.vbs     # avvia (agente + tunnel) senza finestre
python scripts\agent_status.py   # stato e ultimi log
python scripts\stop_agent.py     # ferma tutto
```

Il collegamento nella cartella di avvio e' gia' installato: al login l'agente
parte da solo, in silenzio. Il supervisore rilancia un processo che muore e
scrive i log in `data/logs/`.

Poi:

1. **Accesso**: di default l'app si apre direttamente. Per metterci una
   password: *Impostazioni → Accesso al sito → Password d'accesso*.
   Da quel momento il sito chiede **solo la password** (niente nome utente).
   > C'è anche la **chiave segreta**: *Impostazioni → Chiave segreta →
   > Genera chiave*. Mettila nell'URL (`https://.../?key=XXXX`) e l'app si
   > apre con un tocco — perfetto per il telefono, dove il segreto finisce
   > nella cronologia. La chiave si vede una volta sola (nel database c'è
   > solo un hash SHA-256).
2. **+ Nuova ricerca** → inserisci partenza, arrivo, “entro quando”, budget, raggio.
   (Oppure scrivi il viaggio in una frase e premi **✨ Compila con l'AI**.)
3. Scegli le fonti e i canali di avviso.
4. L'agente fa subito il primo giro e poi ricontrolla ogni ora (configurabile).

Le ricerche, le offerte e le notifiche appartengono a un solo proprietario:
non c'è bisogno di account separati. Le impostazioni personali (Telegram,
SMTP, fascia di silenzio) sono separate da quelle comuni (chiave Groq, orari
GTFS).

Da riga di comando:

```powershell
python -m app.cli users              # elenco account e stato
python -m app.cli user-add mario     # crea un account attivo
```

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

### I trucchi che un booking non fa

L'agente non si limita al prezzo più basso in assoluto: cerca anche i modi
**intelligenti** di pagare meno.

- **Spezzatura**: due biglietti invece di uno. Su molte tratte il regionale
  costa molto meno del Frecciarossa, e comprare il viaggio a pezzi conviene.
  Misurato davvero, Milano → Napoli:

  | | Prezzo |
  |---|---|
  | Biglietto unico (Intercity) | 87,50 € |
  | **Due biglietti** (Milano→Bologna + Bologna→Napoli, regionali) | **66,40 €** |
  | | **risparmi 21,10 €** |

  L'agente chiama l'API Trenitalia per i singoli segmenti, e propone la
  spezzatura **solo se batte davvero** il prezzo diretto. Nella card vedi i due
  ticket con gli orari e il risparmio.

- **Mezi diversi sulla stessa tratta**: treno + bus, auto + treno, e così via.
  Il planner mette in concorrenza tutte le combinazioni possibili e sceglie
  il costo totale più basso, tempo di percorrenza compreso.

- **Flessibilità**: se non esiste niente che arriva in tempo, l'agente chiede
  (“arrivo la sera prima va bene?”, “alzo il budget a 13 €?”, “meglio il
  giorno dopo?”) e poi ricalcola.

### I punti di ritrovo e il tempo a piedi

Un viaggio non finisce alla stazione: finisce alla **porta**. Ogni ricerca puo' avere
due punti esatti ("Stazione di Thiene" -> "Piazza Duomo, Trento") oltre alle citta'.
L'app li geolocalizza e calcola il **tempo porta a porta**: cammino + mezzo + cammino.

```
Treno regionale    4h 08m di viaggio  +  74 min a piedi  =  5h 22m porta a porta
Passaggio in auto  59 min porta a porta,  ~7 EUR (stima) =  59 min
```

Senza punti esatti l'app **te lo chiede** (domanda "Dove vuoi essere preso
esattamente?") e la risposta libera vale: scrivi "Piazza Vittoria" e il
punto viene salvato e geolocalizzato.

### BlaBlaCar: i dati veri, letti dal sito

L'API pubblica di BlaBlaCar e' stata dismessa e il sito e' protetto da un
filtro anti-bot. Ma la pagina di ricerca contiene gia' la risposta JSON del
backend: l'app la apre con un **browser vero** (Chrome di sistema, via
Playwright), lascia che il filtro si risolva da solo e legge i dati.

Per ogni viaggio l'app prende il **prezzo reale**, l'orario di partenza e
arrivo, il **punto di ritrovo esatto** ("Autostazione San Donato", "Assago
M2") con le coordinate, e se restano pochi posti. Nessuna stima: se il
browser non e' disponibile l'adapter non propone nulla e lascia il link.

E con il punto di ritrovo esatto l'app fa la cosa che solo un'agenzia fa:
calcola i minuti a piedi per arrivarci davvero e confronta il totale
porta a porta.

```
BlaBlaCar Bus  11,48 EUR  03:15 -> 11:50  punto: Autostazione Lampugnano
BlaBlaCar Bus  29,99 EUR  00:30 -> 08:50  punto: Autostazione San Donato
BlaBlaCar (passaggio in auto)  26,19 EUR  01:50 -> 07:50
```

### Rispondi a parole

Ogni domanda dell'agente ha i pulsanti **e** una **casella di testo libera**.
Ci scrivi come parli e lui capisce: “alzo il budget a 15” cambia il tetto,
“ho il carnet da 10 biglietti regionali” aggiunge l'abbonamento, “arrivo la
sera prima va bene” sblocca la giornata precedente. Funziona anche da
Telegram: un messaggio qualsiasi diventa la risposta alla domanda in attesa.

- **Gli abbonamenti contano**: l'agente chiede quali abbonamenti hai
  (Trenitalia regionale, annuale, carnet da 10 biglietti, abbonamento urbano)
  e **se la tratta e' coperta il biglietto non lo paghi**. Il piano mostra
  "0,00 EUR" con il prezzo del biglietto barrato e la dicitura "coperto da...".
  Un regionale di 6 ore coperto da un abbonamento batte un Frecciarossa da
  30 euro: l'agente lo sceglie e te lo spiega. Gli abbonamenti si impostano
  nella scheda della ricerca ("Abbonamenti in corso").

### Il tempo conta quanto il prezzo

Un bravo travel agent non sceglie solo il piu' economico: guarda anche
**durata, cambi e orari**. Il punteggio pesa prezzo, durata del viaggio,
numero di cambi, arrivo anticipato, distanza e affidabilita'; se dici
"preferisco arrivare prima", il tempo pesa quanto il prezzo. Ogni piano
mostra partenza, arrivo, durata e cambi, e le alternative sono scelte per
**profilo** ("piu' veloce", "meno cambi", "piu' economico"), non per prezzo.

1. Crea una chiave gratuita su <https://console.groq.com/keys>.
2. Incollala in **Impostazioni → Intelligenza (Groq)** → *Prova AI*.
3. **Scegli i modelli** nei due menu a tendina: *Modello ragionamento* (sceglie
   il piano e scrive la spiegazione) e *Modello veloce* (interpreta richieste e
   risposte). I menu si popolano con i modelli che la tua chiave può usare
   davvero, con il contesto indicato; il pulsante *Aggiorna elenco modelli* li
   rilegge quando Groq ne aggiunge di nuovi.

> Il free tier di Groq cambia nel tempo: i modelli salvati che non sono più
> disponibili restano selezionabili (con un avviso) e l'agente prova
> automaticamente il modello successivo se uno non risponde.

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

### Notifiche push (telefono)

1. Installa la PWA (vedi *Sul telefono*) oppure usa la dashboard su `http://127.0.0.1:8787`.
2. Impostazioni → **Notifiche push** → *Attiva su questo dispositivo* e concedi il permesso.
3. *Invia prova* per verificare; poi spunta **Push (telefono)** tra i canali di una ricerca.

Le push si possono attivare su più dispositivi (telefono e PC insieme) e arrivano solo
quando ci sono novità. Servono HTTPS o `localhost`; su iPhone/iPad servono iOS 16.4+ e
l'app aggiunta alla schermata Home. Se la libreria non è installata:
`pip install pywebpush`.

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
  (tratta singola, due tratte o **tre tratte**: auto/carsharing fino alla stazione,
  treno, e ultimo miglio fino a destinazione) e ordina per **costo effettivo**
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

## Mappa

La scheda **Mappa** (Leaflet + OpenStreetMap) disegna, per la ricerca selezionata:

- **partenza** (verde) e **arrivo** (rosso);
- ogni offerta come **punto di ritrovo → punto di arrivo** uniti da una linea
  tratteggiata, con un colore per mezzo (auto, bus, treno, tram, volo, carsharing);
- cliccando un punto si vede operatore, orari e prezzo;
- le **zone carsharing** coperte (cerchi gialli, disattivabili con la casella in alto).

Le zone arrivano da `GET /api/carsharing/zones`, cioè dall'elenco città dei provider in
`data/carsharing.json`. La mappa ha bisogno di internet per le "mattonelle" della cartina:
senza connessione resta il resto dell'app.

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
    push.py            notifiche push (VAPID) per la PWA
    settings.py        impostazioni a runtime (globali e per-utente)
  auth.py             account, bcrypt, sessioni, approvazione
  deps.py             dipendenze FastAPI: sessione corrente, ruolo admin
static/
  index.html, styles.css, app.js
  manifest.webmanifest, sw.js, icons/    PWA (pubblicata su GitHub Pages)
.github/workflows/pages.yml  deploy automatico del frontend su GitHub Pages
Dockerfile            immagine per il deploy su un server/container
scripts/
  make_icons.py        genera le icone PWA
  build_pages.py       bundle per Cloudflare Pages
  export_deploy.py     esporta il database per un server
  install_oracle.sh    installazione completa su VM Ubuntu (Oracle Cloud)
  tunnel.py            tunnel Cloudflare + allineamento automatico di Pages
  install_autostart.py avvio automatico dell'agente (Task Scheduler)
data/                  database SQLite (generato)
docs/DESIGN.md         architettura e roadmap
docs/DEPLOY.md         telefono, tunnel, Pages, GitHub
docs/ORACLE.md         server sempre acceso su Oracle Cloud
tests/test_core.py     test di scoring/geo
tests/test_ai.py       test planner, fallback senza AI, finestra di flessibilità
tests/test_auth.py     test di accesso, sessioni e separazione dei dati
```

---

## Sul telefono (senza server a pagamento)

Guida completa in **[`docs/DEPLOY.md`](docs/DEPLOY.md)**. In breve:

- l'agente gira sul PC, avviato all'accensione con Task Scheduler;
- un **Cloudflare Tunnel** gratuito lo rende raggiungibile in HTTPS;
- dal telefono apri l'URL → **Aggiungi a schermata Home** → è una PWA;
- (opzionale) l'interfaccia su **Cloudflare Pages**, l'API resta dietro il tunnel.

### Opzione: server sempre acceso, anche col PC spento

Se vuoi che l'agente continui a girare quando il PC e' spento, c'e' il
deploy su **Oracle Cloud**: piano Always Free con una VM Linux gratuita
24/7, IP pubblico e volume persistente per il database.

- Guida: **[`docs/ORACLE.md`](docs/ORACLE.md)**
- `scripts/install_oracle.sh` installa e configura tutto in un colpo;
- `Dockerfile` per chi preferisce un container;
- l'interfaccia resta su GitHub Pages, l'API dietro l'IP o il dominio.

```powershell
# esporta account, ricerche e impostazioni per il server
python scripts\export_deploy.py
```

---

## Comandi utili

```powershell
# test
python -m unittest discover -s tests -v

# stato dell'agente (processi, URL pubblico, log)
python scripts\agent_status.py

# avvio in background senza finestre / arresto
wscript.exe avvia-agente.vbs
python scripts\stop_agent.py

# accesso con password: set = imposta, clear = toglie, key = genera la chiave
python -m app.cli access status
python -m app.cli access set --password "la-tua-password"
python -m app.cli access key

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
- L'accesso è **una sola password** (facoltativa): se la imposti, il sito la
  chiede. Nel database c'è solo l'hash bcrypt, mai il testo in chiaro. La
  **chiave segreta** (per entrare con un tocco) è salvata come hash SHA-256.
- **L'URL del tunnel è la protezione**: chi lo conosce può usare l'app.
  Per un URL stabile e non indovinabile serve un dominio tuo (tunnel con nome
  su Cloudflare) o **Cloudflare Access** davanti.
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
- [x] Ricerche multi-tratta (auto/carsharing + bus o treno) con confronto per costo effettivo
- [x] Viaggi a tre tratte: auto/carsharing + treno + ultimo miglio, con costo totale
- [x] Trenitalia con prezzi reali (+ Italo via aggregatore Omio)
- [x] Più fonti reali: Omio (treni/bus/voli), Itabus, FlixBus, BlaBlaCar
- [x] Pannello offerte & codici sconto dalle pagine ufficiali dei vettori
- [x] Orari GTFS reali (treni/bus) con motore di coincidenze dirette + 1 cambio
- [x] Mappa (Leaflet/OpenStreetMap) con offerte, punti di ritrovo e zone carsharing
- [x] Web Push per la PWA (VAPID + pywebpush), oltre a Telegram ed email
- [x] Multi-utente: accesso con password, ricerche e notifiche separate per account
- [ ] Scraping opzionale (Playwright) attivabile per singola fonte
- [ ] Export calendario (.ics) e condivisione di una ricerca
