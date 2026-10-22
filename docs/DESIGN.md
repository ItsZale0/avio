# Progettazione — Carpool Hunter

Documento di architettura della v0.1 e traccia per le iterazioni successive.

---

## 1. Obiettivi

| Requisito utente | Come è risolto |
|---|---|
| Inserire luogo, data e **ora di arrivo massimo** | Form con partenza, arrivo, `depart_after`, `arrive_by`, budget, passeggeri, raggio |
| Cercare su **più fonti possibili** | Adapter indipendenti (BlaBlaCar, Omio, Trenitalia, FlixBus, Itabus, carsharing, mezzi pubblici, **orari GTFS**) + link precompilati |
| Agente che cerca **ogni ora** e avvisa | Scheduler interno, intervallo configurabile (15 min–6 h), digest solo sulle novità |
| Sapere qual è il **modo più economico** | Badge *Più economico* + ordinamento per prezzo e punteggio |
| **Carsharing vicino** | Stima costo one-way per provider/città + fermate/stazioni entro pochi minuti a piedi |
| Interfaccia | Dashboard web locale, responsive, in italiano |
| Avvisi | Telegram, email, dashboard, notifica desktop |

**Non obiettivi v0.1:** prenotare, pagare, scraping massivo, app mobile nativa.

---

## 2. Architettura

```
┌────────────────────┐        ┌──────────────────────────────────────────┐
│  Browser (SPA/PWA) │  REST  │  FastAPI (app/main.py)                   │
│  index.html/app.js │◄──────►│  /api/watches  /api/offers  /api/alerts  │
│  + sw.js (offline) │        │  /api/settings /api/geocode /api/ai/*    │
└────────────────────┘        └───────────────┬──────────────────────────┘
                                              │
              ┌───────────────────────────────┼──────────────────────────────┐
              │                               │                              │
     ┌────────▼────────┐            ┌─────────▼─────────┐          ┌─────────▼────────┐
     │ Scheduler       │            │ Search engine     │          │ Store / SQLite   │
     │ tick 20 s       │───────────►│ filtri + scoring  │◄────────►│ watches/offers/  │
     │ poll Telegram   │            │ persist + alert   │          │ plans/questions/ │
     │ next_run_at     │            └─────────┬─────────┘          │ runs/alerts/ai   │
     └────────┬────────┘                      │                    └──────────────────┘
              │                  ┌────────────┼───────────┐
              │                  │            │           │
              │            ┌─────▼─────┐ ┌────▼─────┐ ┌───▼────────┐
              │            │ BlaBlaCar │ │ FlixBus  │ │ Carsharing │
              │            │ API v3    │ │ endpoint │ │ + Transit  │
              │            └───────────┘ └──────────┘ └────────────┘
              │
     ┌────────▼─────────────────────────┐        ┌──────────────────────┐
     │ AI (app/ai): planner + Groq      │        │ Telegram/Email       │
     │ sceglie, spiega, fa domande      │        │ + bot getUpdates     │
     └──────────────────────────────────┘        └──────────────────────┘
```

### Perché queste scelte

- **FastAPI + SQLite**: un solo processo, zero servizi esterni, avvio con un comando.
- **Adapter isolati**: una fonte rotta non blocca le altre (`asyncio.gather` +
  `return_exceptions`, timeout per adapter).
- **Scheduler a polling**: un job ogni 20 s legge `next_run_at`; più semplice e
  robusto di un job per ricerca, e permette di riprogrammare subito dopo una modifica.
- **Frontend vanilla**: nessuna build, si apre subito, facile da modificare.
- **Link precompilati** come fallback: affidabili e conformi ai ToS.
- **AI grounded + fallback**: i modelli gratuiti Groq scelgono e spiegano, ma solo
  tra le offerte reali; senza chiave il motore deterministico continua a funzionare.
- **Telegram in polling** invece di webhook: nessun server pubblico necessario.

---

## 3. Modello dati (SQLite)

| Tabella | Ruolo | Campi chiave |
|---|---|---|
| `users` | account che accedono all'app | `username` (unico), `display_name`, `password_hash` (bcrypt), `is_admin`, `status` (active/pending/disabled), `telegram_chat_id` |
| `sessions` | sessioni di accesso | `token_hash` (unico, SHA-256), `user_id`, `expires_at`, `last_seen_at` |
| `password_resets` | token temporanei per il recupero password | `token_hash` (unico), `user_id`, `expires_at` |
| `user_settings` | impostazioni personali | `user_id` + `key` (PK composta), `value` |
| `telegram_links` | collegamento fra chat Telegram e account | `code` (unico), `user_id`, `chat_id`, `expires_at` |
| `telegram_chats` | chat, gruppi e canali dove il bot scrive | `user_id`, `chat_id`, `chat_type`, `title`, `is_default` |
| `watches` | una ricerca salvata | `user_id`, `from_place/lat/lon`, `to_place/lat/lon`, `depart_after`, `arrive_by`, `max_price`, `radius_km`, `sources` JSON, `notify` JSON, `interval_min`, `active`, `next_run_at` |
| `offers` | offerte trovate, una riga per (ricerca, fingerprint) | `watch_id`, `source`, `mode`, `operator`, `depart_at`, `arrive_at`, `price`, `price_is_estimate`, `pickup/dropoff`, `score`, `badges` JSON, `details` JSON, `first_seen`, `last_seen`, `prev_price` |
| `runs` | cronologia esecuzioni | `watch_id`, `status` (ok/partial/error), `offers_found`, `alerts_sent`, `errors` JSON, `notes` JSON |
| `alerts` | feed avvisi | `watch_id`, `kind`, `title`, `message`, `read` |
| `settings` | configurazione **comune** dell'istanza | chiave Groq, modelli AI, feed GTFS, chiavi VAPID, offset Telegram |
| `push_subscriptions` | dispositivi iscritti alle notifiche push | `user_id`, `endpoint` (unico), `p256dh`, `auth`, `label`, `fail_count`, `last_ok_at` |
| `geocache` | cache geocoding | `query`, `lat`, `lon`, `display_name` |
| `questions` | domande dell'agente all'utente | `watch_id`, `key` (slug, dedupe), `text`, `options` JSON (con `prefs`), `status` (pending/answered/skipped), `answer`, `answer_source` |
| `plans` | piani calcolati, uno "corrente" per ricerca | `watch_id`, `kind` (cheapest/alternative), `title`, `total_price`, `total_duration_min`, `steps` JSON, `explanation`, `watchouts` JSON, `confidence`, `fingerprint`, `is_current` |
| `ai_log` | controllo uso/costi AI | `watch_id`, `run_id`, `task`, `model`, `tokens`, `latency_ms`, `ok`, `error` |

La tabella `watches` è stata estesa con `prefs` JSON (preferenze apprese), `ai_summary`,
`ai_plan_fingerprint`, `ai_best_price`, `ai_updated_at`; `runs` con `notes`, `ai_used`,
`ai_tokens`, `plan_price`. Le migrazioni sono idempotenti (`db.MIGRATIONS`).

**Multi-utente**: `watches.user_id` e' il punto di innesto. Le tabelle figlie
(`offers`, `runs`, `alerts`, `questions`, `plans`) non hanno una colonna
`user_id`: ereditano la separazione dalla ricerca con `ON DELETE CASCADE`, e
ogni query le filtra per `watch_id IN (SELECT id FROM watches WHERE user_id = ?)`.
Le ricerche create prima dell'introduzione del login vengono assegnate al primo
account amministrativo (`db._assign_legacy_watches`), e le impostazioni personali
di una sola istanza migrano anch'esse (`db._assign_legacy_settings`).

**Fingerprint** = `source:external_id:depart_at` — *senza prezzo*, così un cambio
prezzo aggiorna la stessa offerta e può generare un avviso di ribasso.

Tutti gli orari sono salvati in **UTC ISO 8601**; l'interfaccia li mostra nel fuso
`Europe/Rome`.

---

## 4. Pipeline di una ricerca

```
1. carica watch (inclusi i prefs appresi)
2. costruisci SearchContext (coordinate, finestra, raggio, budget)
3. esegui gli adapter in parallelo (timeout 35 s ciascuno)
4. deduplica per fingerprint (tieni il prezzo più basso)
5. filtri duri:
      depart_at >= now - 5 min
      depart_at >= depart_after - 15 min
      arrive_at >= inizio del giorno richiesto (arrival_day_start)
      arrive_at <= arrive_by + 10 min + flessibilità (prefs.flexible_days * 24 h)
      price <= max_price
      distanza pickup/dropoff <= max(raggio*1.4, raggio+2.5) km
6. calcola score + badge + costo effettivo
7. upsert offerte; rileva nuove e ribassi (>= max(0,50 €, 4%))
8. disattiva le offerte già passate **e quelle che questo giro non ha
   più trovato**: la lista deve riflettere i filtri attuali, non quelli
   di una ricerca precedente
9. pianificatore: costruisce le combinazioni candidate (1-3 tratte:
   accesso → principale → ultimo miglio), l'AI sceglie la più economica
   e la spiega (oppure fallback deterministico)
10. salva il piano corrente + alternative; aggiorna ai_summary/ai_best_price
11. domande: al massimo 2 per giro, dedupe per `key`, inviate anche su Telegram
12. crea alert + invia digest (nuove offerte, ribassi o piano migliorato)
13. aggiorna last_run_at / next_run_at; se arrive_by è passato, mette in pausa
```

Errori di una fonte finiscono in `runs.errors`; suggerimenti e limiti in `runs.notes`.
Il run viene marcato `partial` se almeno una fonte, un canale di notifica o l'AI ha fallito.

---

## 4bis. Livello di intelligenza (Groq)

Il principio: **l'AI sceglie e spiega, non inventa**. I numeri vengono sempre da
offerte reali raccolte dagli adapter.

```
watch + offerte reali
        │
        ▼
app/ai/planner.py        combinazioni candidate (id c1, c2, …)
  - tratte singole che arrivano a destinazione
  - combo a 2 tratte: tratta "accesso" (carsharing/rideshare/bus/transit/tram)
    + tratta "principale" (bus/treno/rideshare/volo) se il cambio è
    contiguo (<= max_transfer_km) e temporalmente sensato (15 min – 6 h)
  - viaggi a 3 tratte (auto + treno + ultimo miglio), con lo stesso
    controllo sui due cambi; l'ultima tratta deve arrivare a destinazione
  - ordina per COSTO EFFETTIVO = prezzo + costo stimato di accesso
        │
        ▼
app/ai/intelligence.py   manda a Groq i candidati in JSON compatto
  - il modello risponde: chosen_id, title, explanation, confidence,
    watchouts, alternatives, questions
  - chosen_id non valido → si usa il candidato più economico
        │
        ▼
store.save_plans() + questions.create_many()
```

### La spezzatura: pagare meno con due biglietti

Il trucco italiano classico: sulla stessa tratta, comprare **due biglietti**
invece di uno costa spesso meno, perché il regionale è molto più economico
del Frecciarossa. Esempio reale misurato (Milano → Napoli, 22 ottobre):

| | Prezzo | Arrivo |
|---|---|---|
| Biglietto unico (Intercity) | 87,50 € | 20:29 |
| **Spezzato**: Milano→Bologna + Bologna→Napoli (regionali) | **66,40 €** | 20:29 |
| | **risparmio 21,10 €** | |

`app/services/split_tickets.py` fa così:

1. prende il viaggio ferroviario più economico trovato;
2. sceglie i **nodi ferroviari in linea** sul percorso (Bologna, Firenze,
   Verona…): una città è "in linea" se
   `dist(partenza, X) + dist(X, arrivo) − dist(partenza, arrivo) < 70 km`;
3. chiede all'API Trenitalia il prezzo dei **due segmenti** separati
   (il secondo biglietto parte almeno 25 minuti dopo l'arrivo del primo);
4. **tiene la spezzatura solo se è più economica** del biglietto diretto e
   rispetta la finestra di arrivo.

Il risultato entra nel pool come offerta normale (`source="spezzatura"`),
quindi compete con le altre nel piano: se vince, l'AI lo spiega ("sono due
biglietti, risparmi X €") e la card mostra i due ticket con i rispettivi
orari. Il trucco esiste anche fra operatori diversi: il planner mette già in
concorrenza le tratte di fonti diverse (Trenitalia + bus, auto + treno).

### La finestra di ricerca: due interrogazioni, non una

L'API Trenitalia restituisce **una decina di soluzioni ordinate per orario di
partenza**, non tutto quello che c'è. Se si interroga dal giorno prima
("parto dopo le 6 di martedì, devo essere a Trento mercolì alle 11"), la
risposta contiene solo i treni di martedì: quelli di mercoledì non arrivano
**mai** in lista e vengono scartati come "arrivati il giorno prima".

Per questo `query_starts()` interroga l'API due volte:

1. la sera del giorno prima (6 ore prima di mezzanotte): treni notturni e
   viaggi lunghi;
2. il giorno di arrivo: i treni della giornata.

Le soluzioni vengono unite e deduplicate per codice e per orario.

### Gli abbonamenti: il catalogo reale

`app/services/passes.py` contiene gli abbonamenti **verificati** (dati
ufficiali 2026), non un generico "abbonamento regionale":

| abbonamento | copre |
|---|---|
| altoadigemobilita (250 €/anno, 39 €/mese, U26 153 €/21 €, U19 21 €, 65+ 21/77/153 €) | tutti i mezzi di sudtirolmobil: treni regionali delle 4 linee (Brennero-Bolzano-Trento, Val Venosta, Merano-Bolzano, Val Pusteria), autobus urbani ed extraurbani, impianti di risalita, trenino del Renon, funicolare della Mendola |
| altoadigemobilita **Flex** (0,13 €/km, poi 0,08/0,03/0,02/0; max 650 €/anno e 22 €/giorno; minimo 10 km tariffari) | tutta la rete dell'Alto Adige, ma **a tariffa chilometrica**: non è gratis, si paga il km percorso |
| altoadigemobilita **FlexFamily** (ex Euregio Family Pass, 0,11 €/km, max 540 €/anno, tessera prepagata) | idem, con tariffa ridotta per le famiglie |
| Euregio Ticket (750 €/anno) | Trentino, Alto Adige, Tirolo: treni regionali, autobus urbani e regionali, funivie; in Tirolo tutta la rete VVT |
| abbonamento provinciale Trentino | tutta la rete provinciale: autobus Trentino Trasporti, Trento-Male, Valsugana, Brennero (Borghetto-Ora), funivia Trento-Sardagna |
| Guest Card Trentino / Val di Sole | gratis sul trenino Trento-Male e sui bus del Trentino |
| abbonamento regionale Trenitalia (uno per regione) | treni regionali della regione (tariffe 40/xx/REGIONE) |
| IVOL (Lombardia) | tutti i mezzi pubblici della Lombardia |
| Salta Su (Emilia-Romagna) | Card Unica + abbonamento ferroviario regionale; "Mi Muovo Tutto Treno" apre gli Intercity sulla tratta del proprio abbonamento |
| AUR / Formula (Piemonte), CIS settimanale (Lazio, 29 €/7 giorni), Toscana, Veneto, FVG, Carnet | i rispettivi networks |

**La copertura è parziale**, e questo è il punto: un abbonamento regionale
copre solo la parte di viaggio nella sua area. Il calcolo è fatto **tratto per
tratto** sulle tappe del viaggio, quindi un treno che entra in Trentino a
metà percorso (linea del Brennero) risulta coperto solo da lì:

```
treno Thiene -> Trento (via Brennero), altoadigemobilita
  quota coperta 36%  ->  7,65 EUR invece di 11,90 EUR
treno Thiene -> Trento (via Bassano), altoadigemobilita
  quota coperta 56%  ->  5,27 EUR invece di 11,90 EUR
```

Il prezzo di listino non viene mai cancellato: accanto al costo effettivo
resta sempre quanto costerebbe senza abbonamento, con la percentuale
coperta. Il "trenitalia_regionale" generico deduce la regione dal punto di
partenza (quello di casa).

**Il Flex non è gratis.** Gli abbonamenti a tariffa fissa coprono la tratta e
basta; quelli a **tariffa chilometrica** (altoadigemobilita Flex) coprono la
tratta ma si paga **il km**: il motore calcola i km coperti, applica il
minimo di 10 km tariffari a viaggio e il tetto di 22 €/giorno, e restituisce
il costo vero invece di azzerarlo. Gli scaglioni sono annuali e l'app non sa
quanti km hai già percorso quest'anno: usa il primo (0,13 €/km), il più
caro, quindi la stima non è mai ottimista.

```
treno Merano -> Bolzano, altoadigemobilita Flex
  24 km tariffati a 0,13 EUR/km  ->  3,18 EUR invece di 5,90 EUR
```

La copertura geografica è calcolata **campionando** il percorso (12 punti per
tratto) invece di stimarla dagli estremi: su una tratta lunga la stima dagli
estremi sbagliava di parecchio (la distanza dal Trentino a Merano non è la
distanza percorsa per uscire dall'Alto Adige).

### Gli sconti X-GO: le condizioni vere

"X-GO" **non è un operatore**: è il programma fedeltà di Trenitalia (2 punti
per euro, cashback). Sui treni regionali dà due sconti del 20%: **Young**
(under 30) e **Senior** (60 e più).

Le condizioni (fonte: trenitalia.com/it/offerte/young-regionale.html) sono
restrittive, e `app/services/discounts.py` le applica una per una:

1. solo treni **regionali** (Regionale, Regionale Veloce, Regionale Express):
   niente Frecce, Intercity, EuroCity, **Frecce Regionale** (le ibride
   premium) e niente treni degli operatori privati (Italo/NTV);
2. **non vale** in Campania, Abruzzo, Sardegna, Puglia e nelle province
   autonome di Trento e Bolzano;
3. vale **solo sui treni selezionati** dalla promozione (in base a tratta,
   giorno e orario): non tutti i regionali ne sono coperti;
4. l'acquisto va fatto **entro le 23:59 del giorno prima**;
5. la promozione **vale fino al 30 novembre 2026**.

Il treno escluso dalla promozione resta comunque in lista, con la ragione
("lo sconto X-GO non vale in Bolzano"): si sa perché non si applica, senza
nasconderlo. Il prezzo scontato è marcato come **stima** e mostra il
listino accanto.

### BlaBlaCar: lettura dei dati reali

L'API pubblica e' stata dismessa e il sito e' protetto, ma la pagina di
ricerca contiene gia' la risposta JSON del backend, sepolta in
`window["__INFRASTRUCTURE__cache"]` (la cache di infrastruttura).

`app/services/blablacar_browser.py` apre la pagina con un browser vero
(Chrome di sistema via Playwright, contesto non persistente: con un profilo
nuovo il filtro anti-bot mostra la pagina di verifica), lascia che la verifica
si risolva, accetta la banner dei cookie e legge la cache. I risultati
valgono 15 minuti in memoria, cosi' il browser non si riapre a ogni giro.

`app/adapters/blablacar.py` ricava per ogni viaggio: prezzo reale (dal
tokenizzato), orario di partenza e arrivo, punto di ritrovo esatto con le
coordinate, e la scarsita' ("ultimi posti disponibili"). Se il punto di
ritrovo non e' noto (i passaggi in auto lo sceglie l'autista), la carta lo
dice.

Nessuna stima: senza browser l'adapter non propone nulla e lo dice ("qui non
c'e' un browser installato"), lasciando il link di ricerca. La cartella della
copia diagnostica (`ultima_pagina.html`) sta in `CARPOOL_DATA_DIR`, cosi' anche
sulla VM si puo' guardare cosa ha risposto il sito.

### Il catalogo degli abbonamenti (59 tipi)

`app/services/passes.py` contiene gli abbonamenti **verificati** (fonti
ufficiali 2026), non un generico "abbonamento regionale":

- **29 ferroviari**: l'abbonamento regionale Trenitalia di ognuna delle 20
  regioni (tariffe 40/xx) + Trenitalia Pass, Interrail, Carta Plus Lombardia
  (Trenord) e il Carnet Italo;
- **10 pass integrati regionali**: altoadigemobilita, Euregio Ticket,
  abbonamento provinciale Trentino, Guest Card Trentino, IVOL (Lombardia),
  AUR (Piemonte), Salta Su (Emilia-Romagna), Umbria Vai, FVG unico, Toscana
  Mobilita;
- **20 urbani**: ATM Milano, ATAC Roma, GTT Torino, ACTV Venezia, Tper
  Bologna, Metrocitta/EAV Napoli, AMT Genova, Autolinee/TFT Firenze, ACTR Pisa,
  AMT Verona, Moovie Padova, SMS Bergamo, ATP Palermo, AMT Catania, AMT
  Cagliari, Asu Foggia e altri.

Ogni voce porta le **condizioni vere** (cosa non copre, dove non vale, quanto
costa) e la fonte. La copertura e' quasi sempre **parziale** e viene calcolata
tratto per tratto.

La UI usa un **picker con ricerca** (59 voci in 3 gruppi) e il catalogo
arriva dall'API `/api/passes`, cosi' non si duplica in JS. L'agente
riconosce anche il nome scritto a parole: "ho l'abbonamento alto adige
mobilita" -> `altoadigemobilita`, "ho IVOL" -> `ivol_lombardia`.

### Il tempo, i cambi e le priorità

Il punteggio (`app/scoring.py`) pesa sei fattori, non solo il prezzo:

| fattore | peso base | con "preferisco arrivare prima" |
|---|---|---|
| prezzo | 0,40 | 0,28 |
| durata del viaggio | 0,16 | 0,30 |
| numero di cambi | 0,10 | 0,12 |
| arrivo anticipato (cuscinetto) | 0,14 | 0,14 |
| distanza dai punti richiesti | 0,10 | 0,08 |
| affidabilità | 0,10 | 0,08 |

- La **durata** è confrontata con il viaggio completo più rapido trovato
  (non con una tratta di accesso: un treno di 3 ore non è "lento" rispetto
  a un'auto di 12 minuti).
- I **cambi** sono dedotti dai dati della fonte (`details["cambi"]`) o dal
  numero di tappe.
- Con `prefs["prefer_fast"]` il tempo pesa quanto il prezzo: il piano
  diventa il più rapido, non il più economico.
- Il **profilo** di ogni piano ("economico", "veloce", "comodo") permette di
  presentare alternative con caratteri diversi invece di tre prezzi vicini.

### Gli abbonamenti: il viaggio che hai già pagato

`app/services/passes.py` gestisce gli abbonamenti dichiarati dall'utente:

- un abbonamento **regionale** copre solo i regionali (non Frecce o Intercity);
  un abbonamento annuale copre tutti i treni;
- se la scadenza è nota e il viaggio è dopo, l'abbonamento non copre;
- la tratta coperta ha **costo effettivo 0**, ma il prezzo del biglietto
  resta visibile: sono due informazioni diverse;
- il carnet "consuma" un biglietto: la nota lo dice;
- un abbonamento urbano azzera anche lo spostamento per raggiungere il
  punto di ritrovo.

Flusso: `prefs["passes"]` → `apply_passes()` mette `net_cost = 0` sulle
offerte coperte → il filtro budget, lo scoring, il planner e l'AI lavorano
tutti sul **costo effettivo** (`effective_cost`), non sul prezzo nominale.

L'agente chiede gli abbonamenti con la domanda `abbonamenti` (o con
risposta libera: "ho l'abbonamento annuale Trenitalia" → `trenitalia_tutti`).

- **Router a due modelli**: `llama-3.3-70b-versatile` per pianificare/parsare,
  `llama-3.1-8b-instant` per compiti brevi. Nomi configurabili; se un modello
  non esiste il client prova automaticamente gli altri (`GroqClient._model_order`).
- **Structured output**: `response_format={"type":"json_object"}` con estrazione
  tollerante (gestisce ```json e testo attorno).
- **Degradazione**: senza chiave (o AI disattivata) il piano esiste comunque —
  è il candidato più economico — con spiegazione da template. L'app non si blocca.
- **Token accounting** in `ai_log` (task, modello, token, latenza, esito).
- L'app usa **solo offerte reali**: nessun dato simulato. Se nessuna fonte risponde,
  la ricerca resta vuota e l'agente lo segnala.

### Domande all'utente

Se il planner non trova nessuna combinazione valida, l'agente diagnostica il motivo
e chiede, mirato:

| Situazione | `key` | Opzioni |
|---|---|---|
| Arriverebbe dopo il limite | `flexible_days` | no / +1 giorno / +2 giorni |
| Tutto sopra il budget | `budget_flex` | resto sul budget / alzo a X € |

Ogni opzione porta con sé la patch di preferenze (`{"flexible_days": 1}`). La risposta
aggiorna `watches.prefs` (o `max_price`) e fa ripartire una ricerca subito. Le domande
già poste o risposte non si ripetono (dedupe per `key`). Con l'AI attiva, il modello
può aggiungere fino a 2 domande proprie ("accetti un cambio?", "quanti bagagli?").

**La finestra temporale.** "Devo essere a Trento entro le 11 di giovedì" vuol
dire tre cose: il giorno è giovedì, l'ora è le 11, e il viaggio avviene
dentro quella giornata. Per questo l'arrivo deve essere **nel giorno
richiesto** (`arrival_day_start` = mezzanotte di quel giorno, ora italiana) e
non oltre l'ora indicata. `depart_after` ("non partire prima delle 6") è un
vincolo diverso: governs la partenza, non l'arrivo.

Quando non esiste nulla che arriva in tempo, l'agente propone le due
flessibilità sensate, una alla volta:

| Situazione | `key` | Opzioni |
|---|---|---|
| Il più vicino arriva la sera prima | `arrivo_giorno_prima` | no, voglio arrivare quel giorno / sì, arrivo la sera prima |
| Una corsa arriva dopo l'ora | `flexible_days` | no / anche il giorno dopo / fino a 2 giorni dopo |
| Il budget è troppo basso | `budget_flex` | resto sul budget / alzo a X € |

**Il budget non deve mai far sparire i risultati.** Se il tetto scarta tutte
le offerte, l'agente non propone un piano fuori filtro: chiede, con il
**prezzo reale** della soluzione più economica ("costa 11,90 €, sopra il budget
di 8 €"), e un clic porta il tetto al giusto livello.

### Bot Telegram in polling

`app/services/telegram_bot.py` gira nello stesso processo (job ogni 45 s):
legge `getUpdates`, gestisce i `callback_query` con `data = ans:{id}:{opzione}` e,
se scrivi un nuovo viaggio in chat, crea la ricerca. Nessun webhook, nessun server
pubblico: basta il token del bot già configurato.

**Chat, gruppi e canali.** Le chat dove il bot può scrivere stanno in
`telegram_chats` (una riga per chat, con `is_default` per indicare quella
predefinita). Si registrano in tre modi:

1. **aggiungendo il bot a un gruppo**: Telegram manda un messaggio di servizio
   `new_chat_members`, il bot lo intercetta e registra la chat da solo, con un
   messaggio di conferma. È il modo più semplice per "dare l'accesso" a un
   gruppo ( anche privato: basta che il bot sia aggiunto come membro).
2. con il comando `/chat` scritto dentro la chat da registrare;
3. dalla web: *Impostazioni → Chat e gruppi Telegram*, che elenca le chat
   registrate e permette di scegliere la predefinita o toglierne una.

I comandi del bot: `/start`, `/help`, `/chi` (le tue ricerche), `/chat`
(registra questa chat), `/chats` (elenco), `/removechat`, `/link CODICE`
(collega la chat al tuo profilo).

Gli avvisi di una ricerca arrivano a **tutte** le chat registrate
dell'utente: `notify.send_telegram_message(..., user_id=...)` scorre
`telegram_chats.chat_ids(user_id)` e invia a ciascuna, con fallback sulla chat
globale se non ce n'è nessuna.

---

## 4ter. Orari GTFS e motore di coincidenze

Gli orari ufficiali di treni e bus arrivano dai **feed GTFS aperti** delle agenzie
regionali, in un database dedicato (`data/gtfs.db`) separato da `carpool.db`.

**Catalogo** (`app/gtfs/feeds.py`) — solo feed pubblici verificati, con licenza:

| Feed | Copertura | Note |
|---|---|---|
| `trentino-extraurbano` | Provincia di Trento: bus + ferrovia Trento–Malè | ~2.860 fermate |
| `trentino-urbano` | Trento e Rovereto | ~1.100 fermate |
| `trenord` | Treni regionali della Lombardia (dati.lombardia.it) | ~567 fermate |
| `busitalia-veneto-tram` | Tram di Padova | ~48 fermate |

Veneto e Vicenza non pubblicano un GTFS aperto: i treni veneti restano coperti
dall'adapter `trenitalia` dal vivo. Il **prezzo non è nel GTFS**, quindi non viene
inventato: la card mostra "solo orario" e rimanda alla tariffa ufficiale.

**Motore** (`app/gtfs/engine.py`) — `find_connections()`:

1. **Fermate vicine**: le più prossime entro il raggio, più alcune delle più
   trafficate del raggio (una stazione importante può essere poco più lontana ma
   servire molte più corse).
2. **Corridoio**: solo le fermate entro ~30 km dal segmento partenza→arrivo, per
   evitare cambi assurdi.
3. **Tratte dirette**: stessa corsa dalla partenza all'arrivo.
4. **Un cambio**: `partenza → X → arrivo` con attesa minima 5 min e massima 3 h.
   Lo stesso punto di cambio spesso ha **due fermate distinte a pochi metri**
   (es. "Mezzana. Ftm" e "Mezzana. Staz Ftm"): il motore collega anche fermate
   entro 400 m a piedi, aggiungendo il tempo di camminata all'attesa.
5. **Pareto**: scarta i collegamenti dominati (parte prima e arriva dopo).

I risultati diventano `Offer` con `details["tappe"]` (una voce per tratta, con
eventuale passo a piedi), `orario_gtfs = True` e `price = None`. Nello scoring le
offerte senza prezzo vengono valutate su tempo/vicinanza/affidabilità rinormalizzati
e ricevono il badge **`orario`**; non possono vincere il badge *miglior valore*
(che richiede un prezzo reale).

**Aggiornamento**: `python -m app.cli gtfs-update` oppure Impostazioni → Orari GTFS
→ *Aggiorna orari* (`POST /api/gtfs/update`, in background). Lo stato è in
`GET /api/gtfs`. La fonte si spegne con `gtfs_enabled = 0`.

---

## 5. Contratto degli adapter

```python
class Adapter:
    id: str
    label: str
    requires: str | None      # chiave impostazioni necessaria
    async def search(self, ctx: SearchContext) -> AdapterResult: ...
```

`Offer` è un dataclass normalizzato: sorgente, modalità, orari, prezzo, punti di
ritrovo, posti, rating, deeplink, `details` liberi.

### Aggiungere una fonte in 3 passi

1. Crea `app/adapters/miafonte.py` con una classe che estende `Adapter`.
2. Restituisci `AdapterResult(offers=[...])`; usa `hints` per spiegare limiti/errori.
3. Registrala in `app/adapters/__init__.py` dentro `_INSTANCES`.

Non serve toccare il motore: filtri, scoring, deduplica e avvisi sono automatici.

### Stato delle fonti

| Adapter | Meccanismo | Affidabilità | Costo/limiti |
|---|---|---|---|
| `omio` | endpoint app (GoEuro) `v5/searches` + `v5/results` | media | aggregatore multi-compagnia (treni/bus/voli); non documentato |
| `googleflights` | solo link di ricerca (nessuna chiamata) | — | Google Voli apre il modulo ma i risultati restano su "caricamento dei risultati" quando la richiesta arriva da un server (verificato 22/10/2026 con Chrome vero sulla VM): l'adapter restituisce l'hint con il link già compilato, nessuna offerta. Nessun prezzo inventato |
| `skyscanner` | solo link di ricerca (nessuna chiamata) | — | Risponde "sei una persona o un robot?" a un server (stessa verifica). Il link usa i codici aeroporto (`/transport/flights/mxp/fco/261025/`): con i nomi delle città risponde "pagina non trovata" |
| `trenitalia` | endpoint interno `app.lefrecce.it` BFF | media | prezzi ufficiali; non documentato |
| `blablacar` | pagina di ricerca aperta con Chrome vero (Playwright) | media | il sito risponde `403` con interstitial CAPTCHA (DataDome) ai client non-browser; con un browser vero la pagina si carica e la risposta JSON sta in `window["__INFRASTRUCTURE__cache"]`. Verificato sulla VM Oracle (Milano): 3 viaggi reali in circa 23 secondi. Senza browser installato resta l'hint con il link. |
| `flixbus` | endpoint pubblico del sito | media | non documentato, può cambiare |
| `itabus` | endpoint pubblico del sito + codici stazione | media | non documentato; rete solo Italia/Europa |
| `carsharing` | tabella tariffe + città coperte | alta (stima) | disponibilità veicolo da verificare |
| `transit` | Overpass (OSM) con 3 mirror | media | nessun orario, solo fermate vicine |

Ogni adapter popola `details["tappe"]`: la lista delle tratte reali (`da`, `a`,
`partenza`, `arrivo`, `mezzo`) ricavata da `solution.nodes` (Trenitalia), `segments`
(Omio), `legs` (FlixBus, Itabus). La UI mostra la fermata esatta di partenza/arrivo e,
per i viaggi con cambi, l'elenco espandibile delle tratte. Helper condiviso `tappa()`
in `adapters/base.py`.

I **link utili** (BlaBlaCar, FlixBus, Omio, Google Voli, Skyscanner, Trainline,
Trenitalia, Italo, Google Maps, app carsharing, codici sconto treni) sono sempre
disponibili e non dipendono da API.

### I voli: perché solo link

Google Voli e Skyscanner sono fonti di voli che **non** restituiscono offerte:
servono a mettere in *Link utili* la ricerca già compilata della tratta. La
ragione è misurata, non supposta: dalla VM (indirizzo da datacenter, Chrome
vero, nessun travestimento) Google Voli compila il modulo ma i risultati
restano su "caricamento dei risultati", e Skyscanner risponde con la pagina
"sei una persona o un robot?". Leggere quei prezzi richiederebbe di aggirare
le protezioni: questo progetto non lo fa.

Il legame con gli aeroporti sta in `app/services/airports.py`: una tabella
curata di città con aeroporto (codice principale + tutti gli aeroporti), che
riconosce anche "Milano Centrale", "Malpensa", "FCO" e "Monaco di Baviera".
Serve a due cose: capire se la tratta ha senso in aereo (sotto i 200 km le due
fonti stanno zitte) e costruire i link, perché Skyscanner vuole i codici e non
i nomi. Chi ha un prezzo di volo letto in automatico resta **Omio**.

### Offerte e codici sconto

Oltre alle fonti di viaggio, un servizio separato (`app/services/promos.py`) legge le
**pagine offerte ufficiali** (Trainline, Italo) ed estrae le offerte del momento
(titolo + link), più un elenco verificato di pagine **codici sconto/voucher** (Italo,
Trenitalia, FlixBus) e aggregatori (Cuponation, Groupon). **Nessun codice è inventato**:
dove non è pubblicamente leggibile si linka la pagina ufficiale. La lettura è in cache
per 6 ore (`?refresh=true` per forzarla) ed è esposta su `GET /api/promos`.

---

## 6. Scoring

```
score = 0,52 * prezzo
      + 0,22 * finestra_oraria
      + 0,16 * vicinanza
      + 0,10 * affidabilità

prezzo        = 100 * (prezzo_minimo / prezzo)          [cap 100]
finestra      = 100 se arriva 20–180 min prima del limite
                80 se <= 20 min, 82 se <= 6 h, 62 se <= 12 h, 45 oltre
vicinanza     = 100 * exp(-distanza_max / raggio)
affidabilità  = rating/5 * 100, default 68
```

- Le **stime** di carsharing hanno un malus del 15% sul punteggio prezzo.
- Il **costo effettivo** aggiunge il costo di accesso al punto di ritrovo.
- Badge assegnati una volta per ricerca: `cheapest`, `best_value`, `fastest`,
  `closest`, `under_budget`, `over_budget`, `estimate`, `price_drop`.
- I mezzi pubblici (prezzo ignoto) non entrano nella classifica “Migliori”: stanno
  nella scheda **Carsharing & vicino**.

---

## 7. Avvisi e anti-spam

- Un solo **digest** per esecuzione, con al massimo 4 nuove offerte e 3 ribassi,
  più il **piano più economico** (titolo, prezzo, passi, avvertenze).
- Nessun avviso se non ci sono novità: nuove offerte, ribassi **o piano migliorato**
  (nuovo costo effettivo inferiore di almeno max(1 €, 4%)).
- Canali: dashboard (sempre), Telegram, email, **push** (browser/PWA), notifica desktop.
- **Push (Web Push + VAPID)**: le chiavi si generano al primo uso e restano nel DB locale
  (`vapid_private_key` è un segreto mascherato); ogni dispositivo registra il proprio
  `endpoint` in `push_subscriptions`. Il canale `push` di una ricerca manda titolo, testo
  e link al service worker, che mostra la notifica e apre la dashboard al tocco.
  Gli endpoint scaduti (404/410) vengono rimossi da soli; `pywebpush` è opzionale.
- `notify_enabled` globale + fascia di silenzio configurabile.
- Deduplica tramite fingerprint e `prev_price`; un'offerta già vista non riavvisa
  allo stesso prezzo.
- Le stime di carsharing e i mezzi pubblici **non** generano push: entrano solo
  nel piano se sono la via più economica (e sono marcate come stima).

---

## 8. Scheduler

- APScheduler `AsyncIOScheduler`, job `tick` ogni 20 s, `poll` Telegram ogni 45 s.
- Seleziona fino a 5 ricerche con `next_run_at <= now`, prenota subito il prossimo
  giro (evita doppi avvii su run lenti), poi lancia il task.
- `RUNNING` impedisce esecuzioni concorrenti sulla stessa ricerca.
- Cleanup giornaliero alle 04:10 di offerte inattive, run e alert più vecchi di 90 giorni.
- Se `arrive_by` è nel passato la ricerca va in pausa automaticamente.
- `python -m app.cli run-due` esegue un giro anche senza scheduler (cron/CI).

---

## 9. API principale

| Metodo | Endpoint | Note |
|---|---|---|
| GET | `/api/auth/status` | sessione corrente, serve al frontend per il login |
| POST | `/api/auth/register` | crea l'account (il primo e' admin, gli altri in attesa) |
| POST | `/api/auth/login` | accesso: nome utente + password |
| POST | `/api/auth/logout` | chiude la sessione |
| GET | `/api/auth/me` | dati dell'utente loggato |
| POST | `/api/auth/password` | cambia la propria password |
| GET | `/api/auth/users` | elenco account (**solo admin**) |
| POST | `/api/auth/users` | crea un account attivo (**solo admin**) |
| PATCH | `/api/auth/users/{id}` | approva o disabilita un account (**solo admin**) |
| POST | `/api/auth/users/{id}/password` | reset password di un account (**solo admin**) |
| POST | `/api/auth/telegram/link` | genera il codice per collegare la chat Telegram |
| POST | `/api/auth/telegram/confirm` | conferma il codice e associa la chat |
| GET | `/api/status` | stato agente, fonti, contatori, stato AI |
| GET/POST | `/api/watches` | elenco / crea (geocoding automatico) |
| GET/PATCH/DELETE | `/api/watches/{id}` | dettaglio / modifica / elimina |
| POST | `/api/watches/{id}/run` | forza un giro |
| POST | `/api/watches/{id}/toggle` | pausa / riprendi |
| GET | `/api/watches/{id}/offers` | offerte attive (o tutte) |
| GET | `/api/watches/{id}/runs` | cronologia |
| GET | `/api/watches/{id}/links` | link di ricerca precompilati |
| GET | `/api/watches/{id}/plan` | piano corrente + alternative + domande |
| GET | `/api/watches/{id}/questions` | domande della ricerca |
| GET | `/api/questions` | tutte le domande in attesa |
| POST | `/api/questions/{id}/answer` | registra la risposta (web o Telegram) |
| POST | `/api/questions/{id}/skip` | salta la domanda |
| GET | `/api/alerts` | feed avvisi |
| POST | `/api/alerts/read` | segna letti |
| GET/PUT | `/api/settings` | impostazioni (segreti mascherati) |
| POST | `/api/settings/test-telegram` | invio di prova |
| POST | `/api/settings/test-email` | invio di prova |
| GET | `/api/ai/status` | chiave presente, modelli, token usati |
| GET | `/api/ai/models` | elenco modelli Groq disponibili |
| POST | `/api/ai/parse` | frase in italiano → campi di ricerca |
| POST | `/api/ai/test` | prova rapida del modello |
| GET | `/api/promos` | offerte + codici sconto dalle pagine ufficiali (cache 6 h) |
| GET | `/api/carsharing/zones` | città coperte dal carsharing con coordinate (mappa) |
| GET | `/api/gtfs` | stato e feed degli orari GTFS |
| POST | `/api/gtfs/update` | riscarica e reimporta i feed (in background) |
| GET | `/api/push/status` | libreria push presente, chiave pubblica VAPID, dispositivi |
| POST | `/api/push/subscribe` | registra l'iscrizione del browser |
| POST | `/api/push/unsubscribe` | rimuove l'iscrizione |
| POST | `/api/push/test` | notifica di prova ai dispositivi iscritti |
| GET | `/api/geocode?q=` | verifica località |
| GET | `/config.json` | `apiBase` per il frontend (Pages/tunnel) |
| GET | `/sw.js`, `/manifest.webmanifest` | PWA (dalla radice, per lo scope) |

Documentazione interattiva: `http://127.0.0.1:8787/docs`.
CORS: abilitato solo per le origini in `CARPOOL_CORS_ORIGINS`, con
`allow_credentials=True` (serve al cookie di sessione cross-origin).

**Autenticazione**: un middleware blocca le rotte `/api/*` che richiedono una
sessione valida, tranne quelle pubbliche elencate in `app/deps.py`
(`PUBLIC_PATHS`: login, registrazione, stato sessione, logout, stato e fonti).
Le richieste preflight `OPTIONS` passano sempre. Ogni handler chiama
`deps.require_user(request)` e filtra i dati per l'utente corrente.

---

## 10. Frontend

- **Schermata di accesso** a tutto schermo, con due tab (Accedi / Registrati).
  Al primo avvio (nessun account) si apre direttamente la registrazione. Se
  l'API risponde 401, il frontend torna qui da solo.
- Sidebar con ricerche, stato attivo/pausa e miglior prezzo.
- Dettaglio con 4 KPI, tab **Migliori / Tutte / Mappa / Carsharing & vicino / Link utili / Cronologia**.
- **Mappa** (Leaflet 1.9 da CDN + OpenStreetMap): partenza/arrivo, punti di ritrovo e
  arrivo di ogni offerta con linea tratteggiata e colore per mezzo, popup con operatore,
  orari e prezzo, zone carsharing (cerchi) attivabili/disattivabili. Se Leaflet non è
  disponibile (offline) mostra un messaggio invece della mappa.
- **Card "Piano più economico"** in testa: titolo, prezzo totale, passi con orari e
  link, avvertenze, alternative più economiche, percentuale di fiducia.
- **Card domande**: pulsanti per rispondere in un tocco (o "Salta").
- Modale ricerca con campo in linguaggio naturale + **✨ Compila con l'AI**.
- Chip stato AI nella barra in alto (modello attivo / non configurata).
- Pulsante **🏷️ Offerte & codici sconto** in alto: pannello con offerte del momento e
  link ufficiali ai codici sconto/voucher (cache 6 h, pulsante *Aggiorna*).
- Modali per ricerca e impostazioni (sezione Groq con *Prova AI* / *Elenca modelli*);
  drawer avvisi.
- Impostazioni → **Account**: dati dell'utente, cambio password, e (solo per
  l'amministratore) l'elenco degli account con Approva / Disabilita e la creazione
  di un nuovo account.
- Impostazioni → **Telegram**: *Collega la mia chat* genera un codice da
  inoltrare al bot (`/link CODICE`).
- Impostazioni → **Notifiche push**: stato dei dispositivi, *Attiva* / *Disattiva su questo
  dispositivo* (iscrizione `pushManager`) e *Invia prova*.
- Polling ogni 30 s; refresh immediato dopo creazione o "Cerca ora".
- Toast + notifica desktop per le novità; badge non letti.
- PWA: `manifest.webmanifest`, `sw.js` (shell in cache, mai le `/api`), icone
  generate, pulsante **Installa** quando il browser lo consente.
- Responsive fino a 760 px.

---

## 11. Sicurezza e privacy

- Bind predefinito su `127.0.0.1`: nessuna esposizione di rete.
- **Accesso con una sola password** (facoltativa): se e' impostata, il sito la
  chiede. La password si conserva come **hash bcrypt** (`access_password` in
  `settings`), mai in chiaro. Una **chiave segreta** (`access_key`, hash
  SHA-256) permette di entrare mettendola nell'URL (`?key=...`): comoda sul
  telefono, ma finisce nella cronologia del browser.
- La sessione e' un token casuale in cookie `HttpOnly` (`SameSite=None; Secure`
  su HTTPS, per reggere il frontend su un dominio diverso dal backend). Nel
  database finisce solo lo **hash SHA-256** del token di sessione, mai il token.
- **Un solo proprietario**: le ricerche hanno un `user_id` ma non serve un
  account per persona. Le impostazioni personali (Telegram, SMTP, fascia di
  silenzio) vivono in `user_settings`, separate dalle comuni.
- Nessun dato inviato a terzi oltre alle query necessarie (Nominatim, fonti,
  Telegram/SMTP, **Groq** per le richieste AI).
- L'AI riceve, per pianificare, la richiesta di viaggio e le offerte raccolte
  (orari, prezzi, nomi dei punti). Non riceve credenziali né il database.
- Segreti salvati nel DB locale e **mascherati** nelle risposte API.
- Il file `.env` e `data/` sono in `.gitignore`.
- Per il telefono: **Cloudflare Tunnel** (HTTPS) e, in caso, Cloudflare Access.
  L'accesso protegge i dati, non il canale: l'URL resta da tenere riservato.
  Vedi `docs/DEPLOY.md`.

---

## 12. Test

```powershell
python -m unittest discover -s tests -v
```

- `tests/test_core.py`: distanze, geocoding offline, filtri temporali/budget/raggio,
  scoring e badge, costo effettivo, generazione link, import GTFS e coincidenze
  (tratta diretta, Pareto, cambio a piedi tra fermate a pochi metri), iscrizioni push
  (dedupe per endpoint, chiavi richieste, chiave pubblica VAPID, invio senza dispositivi).
- `tests/test_ai.py`: planner (più economico, combo a 2 tratte e viaggi a 3 tratte
  con costo totale, rifiuto per cambio lontano, cambio troppo corto o ultimo miglio
  fuori destinazione, `allow_multi_leg`), fallback senza chiave Groq, domanda quando
  l'arrivo è oltre il limite, finestra di flessibilità (`flexible_days`).
- `tests/test_auth.py`: hash e verifica password, normalizzazione dello username,
  stato degli account (attivo / in attesa / disabilitato), sessioni (creazione,
  revoca, scadenza, token mai in chiaro), separazione dei dati per utente
  (ricerche, avvisi, cascade), impostazioni personali vs comuni, push e
  Telegram per-utente, migrazione delle ricerche pre-login al primo admin.

---

## 13. Prossime iterazioni (ordine consigliato)

1. ~~**GTFS regionale**~~ ✔ **fatto**: motore di coincidenze su feed GTFS aperti
   (Trentino, Lombardia, tram di Padova), tratte dirette + un cambio anche tra
   fermate a pochi metri, badge `orario` e `gtfs-update` in Impostazioni.
   Restano da valutare GTFS-RT (ritardi in tempo reale) e nuovi feed quando le
   regioni li pubblicano (Veneto).
2. **BlaBlaCar**: ✔ **fatto** — lettura con browser vero (Chrome via Playwright); senza browser resta il link, mai un prezzo stimato.
3. ~~**Mappa**~~ ✔ **fatto**: Leaflet + OpenStreetMap, offerte con punti di ritrovo/arrivo
   e linea, popup con operatore/orari/prezzo, zone carsharing da `/api/carsharing/zones`.
4. ~~**Web Push**~~ ✔ **fatto**: VAPID + `pywebpush`, iscrizioni in `push_subscriptions`,
   canale `push` per ricerca e service worker con `push`/`notificationclick`.
5. **Scraping opzionale** con Playwright, attivabile per fonte e con rate limit,
   rispettando robots.txt e ToS.
6. **Memoria delle preferenze**: usare `prefs` anche per imparare dai clic/risposte
   passate (es. "di solito viaggia il weekend").
7. **App mobile nativa**: la API è pronta; una PWA è il passo più economico.
