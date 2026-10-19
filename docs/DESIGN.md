# Progettazione — Carpool Hunter

Documento di architettura della v0.1 e traccia per le iterazioni successive.

---

## 1. Obiettivi

| Requisito utente | Come è risolto |
|---|---|
| Inserire luogo, data e **ora di arrivo massimo** | Form con partenza, arrivo, `depart_after`, `arrive_by`, budget, passeggeri, raggio |
| Cercare su **più fonti possibili** | Adapter indipendenti (BlaBlaCar, Omio, Trenitalia, FlixBus, Itabus, carsharing, mezzi pubblici) + link precompilati |
| Agente che cerca **ogni ora** e avvisa | Scheduler interno, intervallo configurabile (15 min–6 h), digest solo sulle novità |
| Sapere qual è il **modo più economico** | Badge *Più economico* + ordinamento per prezzo e punteggio |
| **Carsharing vicino** | Stima costo one-way per provider/città + fermate/stazioni entro pochi minuti a piedi |
| Interfaccia | Dashboard web locale, responsive, in italiano |
| Avvisi | Telegram, email, dashboard, notifica desktop |

**Non obiettivi v0.1:** prenotare, pagare, scraping massivo, orari GTFS reali, app mobile nativa.

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
| `watches` | una ricerca salvata | `from_place/lat/lon`, `to_place/lat/lon`, `depart_after`, `arrive_by`, `max_price`, `radius_km`, `sources` JSON, `notify` JSON, `interval_min`, `active`, `next_run_at` |
| `offers` | offerte trovate, una riga per (ricerca, fingerprint) | `source`, `mode`, `operator`, `depart_at`, `arrive_at`, `price`, `price_is_estimate`, `pickup/dropoff`, `score`, `badges` JSON, `details` JSON, `first_seen`, `last_seen`, `prev_price` |
| `runs` | cronologia esecuzioni | `status` (ok/partial/error), `offers_found`, `alerts_sent`, `errors` JSON, `notes` JSON |
| `alerts` | feed avvisi | `kind`, `title`, `message`, `read` |
| `settings` | configurazione runtime | token/SMTP/quiet hours, chiave Groq, modelli |
| `geocache` | cache geocoding | `query`, `lat`, `lon`, `display_name` |
| `questions` | domande dell'agente all'utente | `watch_id`, `key` (slug, dedupe), `text`, `options` JSON (con `prefs`), `status` (pending/answered/skipped), `answer`, `answer_source` |
| `plans` | piani calcolati, uno "corrente" per ricerca | `kind` (cheapest/alternative), `title`, `total_price`, `total_duration_min`, `steps` JSON, `explanation`, `watchouts` JSON, `confidence`, `fingerprint`, `is_current` |
| `ai_log` | controllo uso/costi AI | `watch_id`, `run_id`, `task`, `model`, `tokens`, `latency_ms`, `ok`, `error` |

La tabella `watches` è stata estesa con `prefs` JSON (preferenze apprese), `ai_summary`,
`ai_plan_fingerprint`, `ai_best_price`, `ai_updated_at`; `runs` con `notes`, `ai_used`,
`ai_tokens`, `plan_price`. Le migrazioni sono idempotenti (`db.MIGRATIONS`).

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
      arrive_at <= arrive_by + 10 min + flessibilità (prefs.flexible_days * 24 h)
      price <= max_price
      distanza pickup/dropoff <= max(raggio*1.4, raggio+2.5) km
6. calcola score + badge + costo effettivo
7. upsert offerte; rileva nuove e ribassi (>= max(0,50 €, 4%))
8. disattiva offerte già passate
9. pianificatore: costruisce le combinazioni candidate (1-2 tratte),
   l'AI sceglie la più economica e la spiega (oppure fallback deterministico)
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
  - combo a 2 tratte: tratta "accesso" (carsharing/rideshare/bus/transit)
    + tratta "principale" (bus/treno/rideshare) se il cambio è
    contiguo (<= max_transfer_km) e temporalmente sensato (15 min – 6 h)
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

### Bot Telegram in polling

`app/services/telegram_bot.py` gira nello stesso processo (job ogni 45 s):
legge `getUpdates`, gestisce i `callback_query` con `data = ans:{id}:{opzione}` e,
se scrivi un nuovo viaggio in chat, crea la ricerca. Nessun webhook, nessun server
pubblico: basta il token del bot già configurato.

---

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
| `trenitalia` | endpoint interno `app.lefrecce.it` BFF | media | prezzi ufficiali; non documentato |
| `blablacar` | API ufficiale v3, chiave gratuita | alta con chiave | quota API; senza chiave → hint + link |
| `flixbus` | endpoint pubblico del sito | media | non documentato, può cambiare |
| `itabus` | endpoint pubblico del sito + codici stazione | media | non documentato; rete solo Italia/Europa |
| `carsharing` | tabella tariffe + città coperte | alta (stima) | disponibilità veicolo da verificare |
| `transit` | Overpass (OSM) con 3 mirror | media | nessun orario, solo fermate vicine |

I **link utili** (BlaBlaCar, FlixBus, Omio, Trainline, Trenitalia, Italo, Google Maps,
app carsharing) sono sempre disponibili e non dipendono da API.

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
  `closest`, `under_budget`, `estimate`, `price_drop`.
- I mezzi pubblici (prezzo ignoto) non entrano nella classifica “Migliori”: stanno
  nella scheda **Carsharing & vicino**.

---

## 7. Avvisi e anti-spam

- Un solo **digest** per esecuzione, con al massimo 4 nuove offerte e 3 ribassi,
  più il **piano più economico** (titolo, prezzo, passi, avvertenze).
- Nessun avviso se non ci sono novità: nuove offerte, ribassi **o piano migliorato**
  (nuovo costo effettivo inferiore di almeno max(1 €, 4%)).
- Canali: dashboard (sempre), Telegram, email, notifica desktop.
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
| GET | `/api/geocode?q=` | verifica località |
| GET | `/config.json` | `apiBase` per il frontend (Pages/tunnel) |
| GET | `/sw.js`, `/manifest.webmanifest` | PWA (dalla radice, per lo scope) |

Documentazione interattiva: `http://127.0.0.1:8787/docs`.
CORS: abilitato solo per le origini in `CARPOOL_CORS_ORIGINS`.

---

## 10. Frontend

- Sidebar con ricerche, stato attivo/pausa e miglior prezzo.
- Dettaglio con 4 KPI, tab **Migliori / Tutte / Carsharing & vicino / Link utili / Cronologia**.
- **Card "Piano più economico"** in testa: titolo, prezzo totale, passi con orari e
  link, avvertenze, alternative più economiche, percentuale di fiducia.
- **Card domande**: pulsanti per rispondere in un tocco (o "Salta").
- Modale ricerca con campo in linguaggio naturale + **✨ Compila con l'AI**.
- Chip stato AI nella barra in alto (modello attivo / non configurata).
- Modali per ricerca e impostazioni (sezione Groq con *Prova AI* / *Elenca modelli*);
  drawer avvisi.
- Polling ogni 30 s; refresh immediato dopo creazione o "Cerca ora".
- Toast + notifica desktop per le novità; badge non letti.
- PWA: `manifest.webmanifest`, `sw.js` (shell in cache, mai le `/api`), icone
  generate, pulsante **Installa** quando il browser lo consente.
- Responsive fino a 760 px.

---

## 11. Sicurezza e privacy

- Bind predefinito su `127.0.0.1`: nessuna esposizione di rete.
- Nessun dato inviato a terzi oltre alle query necessarie (Nominatim, fonti,
  Telegram/SMTP, **Groq** per le richieste AI).
- L'AI riceve, per pianificare, la richiesta di viaggio e le offerte raccolte
  (orari, prezzi, nomi dei punti). Non riceve credenziali né il database.
- Segreti salvati nel DB locale e **mascherati** nelle risposte API.
- Il file `.env` e `data/` sono in `.gitignore`.
- Per il telefono: **Cloudflare Tunnel** (HTTPS) e, in caso, Cloudflare Access;
  l'app non ha autenticazione propria, quindi l'URL va tenuto riservato.
  Vedi `docs/DEPLOY.md`.

---

## 12. Test

```powershell
python -m unittest discover -s tests -v
```

- `tests/test_core.py`: distanze, geocoding offline, filtri temporali/budget/raggio,
  scoring e badge, costo effettivo, generazione link.
- `tests/test_ai.py`: planner (più economico, combo a 2 tratte, rifiuto per cambio
  lontano o orari incompatibili), fallback senza chiave Groq, domanda quando l'arrivo
  è oltre il limite, finestra di flessibilità (`flexible_days`).

---

## 13. Prossime iterazioni (ordine consigliato)

1. **GTFS regionale** (Veneto/Trentino, Lombardia) per orari reali di treni/bus:
   l'adapter `transit` diventa un vero motore di coincidenze (e alimenta le combo).
2. **Trenitalia/Italo**: verificare endpoint pubblici o accordi; altrimenti deep link
   con prezzi manuali.
3. **Mappa**: Leaflet con offerte, punti di ritrovo e zone carsharing.
4. **Web Push** per la PWA, oltre a Telegram (richiede VAPID + pywebpush).
5. **Scraping opzionale** con Playwright, attivabile per fonte e con rate limit,
   rispettando robots.txt e ToS.
6. **Memoria delle preferenze**: usare `prefs` anche per imparare dai clic/risposte
   passate (es. "di solito viaggia il weekend").
7. **App mobile nativa**: la API è pronta; una PWA è il passo più economico.
