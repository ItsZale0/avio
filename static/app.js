/* Carpool Hunter - frontend vanilla JS */

const state = {
  watches: [],
  selectedId: null,
  watch: null,
  offers: [],
  links: [],
  runs: [],
  alerts: [],
  sources: [],
  settings: {},
  ai: {},
  plan: null,
  planAlternatives: [],
  questions: [],
  tab: "best",
  // Movimento: la scheda entra solo al cambio ricerca, il pannello solo al
  // cambio scheda. I refresh dati non devono far ripartire nulla.
  enteredWatch: null,
  renderedTab: null,
  schedaEntrata: false,
  editingId: null,
  lastAlertId: Number(localStorage.getItem("ch_last_alert") || 0),
  booted: false,
  polling: null,
  carsharingZones: null,
  showZones: true,
  // True se l'app non chiede l'accesso: niente schermata di login.
  authDisabled: false,
  // Ce' una chiave segreta attiva? Serve per le impostazioni.
  hasKey: false,
  locked: false,
};

// Base dell'API: vuota quando frontend e backend sono sullo stesso host.
// Su Cloudflare Pages punta al tunnel (es. https://carpool.miodominio.it).
let API_BASE = "";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));

const euro = (value) =>
  value == null ? "—" : "€" + Number(value).toFixed(2).replace(".", ",");

const dtf = new Intl.DateTimeFormat("it-IT", {
  weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
});
const dfShort = new Intl.DateTimeFormat("it-IT", { day: "2-digit", month: "short" });
const dfDay = new Intl.DateTimeFormat("it-IT", { weekday: "short", day: "2-digit", month: "short" });
const tf = new Intl.DateTimeFormat("it-IT", { hour: "2-digit", minute: "2-digit" });

function offText(n) { return n === 1 ? "1 offerta" : n + " offerte"; }

function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? "—" : dtf.format(d);
}
function fmtDay(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? "—" : dfShort.format(d);
}
/* Il giorno con il giorno della settimana: un solo formato in tutta l'app. */
function fmtDayFull(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? "—" : dfDay.format(d);
}
function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? "—" : tf.format(d);
}
const ALT_PROFILE = {
  economico: "Più economico",
  veloce: "Più veloce",
  comodo: "Meno cambi",
  alternativa: "Alternativa",
  proposta: "Proposta",
};

// Gli abbonamenti che l'agente conosce arrivano dall'API (il catalogo
// ufficiale e' in app/services/passes.py): qui solo la copia in memoria.
let PASS_CATALOGO = [];

// La chat con l'agente: ogni ricerca ha la sua conversazione.
const CHAT = {};

/* Costruisce il selettore: ricerca + gruppi di caselle. */
function renderPassPicker(box, selezionati, nomeCampo) {
  if (!box) return;
  const scelti = new Set(selezionati || []);
  const perTipo = {};
  for (const p of PASS_CATALOGO) {
    (perTipo[p.tipo] = perTipo[p.tipo] || []).push(p);
  }
  const ordine = [
    ["regione", "Regione e province"],
    ["citta", "Citta'"],
    ["ferrovia", "Ferrovia"],
    ["altro", "Altri"],
  ];
  const gruppi = ordine
    .filter(([tipo]) => perTipo[tipo] && perTipo[tipo].length)
    .map(([tipo, titolo]) => {
      const voci = perTipo[tipo]
        .map(
          (p) => `<label class="chip" title="${esc(p.nota || p.nome)}">
            <input type="checkbox" name="${esc(nomeCampo)}" value="${esc(p.id)}"
              data-search="${esc((p.nome + " " + (p.nota || "")).toLowerCase())}"
              ${scelti.has(p.id) ? "checked" : ""} />
            ${esc(p.nome)}
          </label>`
        )
        .join("");
      return `<div class="pass-group"><h5>${esc(titolo)}</h5><div class="chips">${voci}</div></div>`;
    })
    .join("");
  box.innerHTML = `
    <input type="search" class="pass-search" placeholder="Cerca un abbonamento (es. altoadige, ATM, IVOL)…"
           data-target="${esc(box.id)}" />
    <div class="pass-list">${gruppi}</div>`;
}

/* Filtra le caselle mentre l'utente scrive. */
function filtraPass(ricerca) {
  const testo = (ricerca || "").trim().toLowerCase();
  document.querySelectorAll(".pass-list").forEach((lista) => {
    lista.querySelectorAll("label.chip").forEach((etichetta) => {
      const input = etichetta.querySelector("input");
      const haystack = (input?.dataset.search || "").toLowerCase();
      etichetta.style.display = !testo || haystack.includes(testo) ? "" : "none";
    });
    lista.querySelectorAll(".pass-group").forEach((gruppo) => {
      const visibili = gruppo.querySelectorAll(
        'label.chip:not([style*="display: none"])'
      ).length;
      gruppo.style.display = visibili ? "" : "none";
    });
  });
}

/* Carica il catalogo degli abbonamenti. */
async function caricaCatalogoPass() {
  try {
    const dati = await api("/api/passes");
    PASS_CATALOGO = dati.passes || [];
  } catch (_) {
    PASS_CATALOGO = [];
  }
}

function fmtDuration(minutes) {
  if (!minutes) return "";
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m`;
}
function relative(iso) {
  if (!iso) return "—";
  const diff = new Date(iso).getTime() - Date.now();
  if (isNaN(diff)) return "—";
  const mins = Math.round(diff / 60000);
  if (Math.abs(mins) < 1) return "adesso";
  if (mins > 0) {
    if (mins < 60) return `tra ${mins} min`;
    const hours = Math.round(mins / 60);
    if (hours < 24) return `tra ${hours} h`;
    return `tra ${Math.round(hours / 24)} g`;
  }
  const past = Math.abs(mins);
  if (past < 60) return `${past} min fa`;
  const hours = Math.round(past / 60);
  if (hours < 24) return `${hours} h fa`;
  return `${Math.round(hours / 24)} g fa`;
}
function toLocalInput(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const BADGES = {
  cheapest: "Più economico",
  best_value: "Miglior scelta",
  fastest: "Più veloce",
  closest: "Più vicino",
  price_drop: "Ribasso",
  under_budget: "Sotto budget",
  coperto: "Coperto",
  spezzato: "2 biglietti",
  diretto: "Diretto",
  estimate: "Stima",
  orario: "Orario",
};

/* Icone delle azioni secondarie: lo stato della ricerca si legge dal segno,
   non da un bottone pieno. */
const ICON_PAUSE = '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="M6 3.5v9M10 3.5v9" /></svg>';
const ICON_PLAY = '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5.5 3.5l7 4.5-7 4.5z" /></svg>';

const MODE_TOKEN = {
  rideshare: "--map-rideshare",
  bus: "--map-bus",
  train: "--map-train",
  tram: "--map-tram",
  flight: "--map-flight",
  carsharing: "--map-carsharing",
  transit: "--map-transit",
};

/* Un colore viene dal sistema, mai scritto a mano: la mappa non ha una
   palette parallela a quella del resto dell'app. */
function token(nome) {
  const grezzo = getComputedStyle(document.documentElement)
    .getPropertyValue(nome)
    .trim();
  return grezzo.replace(/^["']|["']$/g, "");
}

const MODE_LABEL = {
  rideshare: "Passaggio",
  bus: "Autobus",
  train: "Treno",
  tram: "Tram",
  flight: "Volo",
  carsharing: "Carsharing",
  transit: "Mezzi pubblici",
};

const MODE_COLOR = {
  rideshare: "--map-rideshare",
  bus: "--map-bus",
  train: "--map-train",
  tram: "--map-tram",
  flight: "--map-flight",
  carsharing: "--map-carsharing",
  transit: "--map-transit",
};

const MAX_MAP_OFFERS = 40;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/* Fa partire (o ripartire) l'animazione di arrivo di un blocco: il reflow
   fra remove e add dice al browser che e' un evento nuovo, non un reset. */
function anima(el) {
  if (!el) return;
  el.classList.remove("enter");
  void el.offsetWidth;
  el.classList.add("enter");
}

/* Il puntatore "fresco" del backend, letto direttamente dal repo: il tunnel
   cambia URL a ogni avvio e la config.json di GitHub Pages si aggiorna solo
   dopo il deploy (un paio di minuti). Questo file e' subito disponibile. */
const RAW_BACKEND =
  "https://raw.githubusercontent.com/ItsZale0/carpool-intelligence/main/backend.json";

/* Tutti i backend conosciuti, in ordine di preferenza. */
let API_FALLBACKS = [];

/* Se il backend non risponde, si spiega dove intervenire: il backend e' il
   server (la VM) che tiene l'app e l'API sulla stessa origine. */
const MESSAGGIO_TUNNEL =
  "Server non raggiungibile. Controlla la connessione e ricarica la pagina; " +
  "se l'app e' aperta su un altro indirizzo, apri l'app direttamente dal " +
  "backend: l'app e l'API devono stare sulla stessa origine, altrimenti il " +
  "browser blocca il cookie di accesso.";

async function api(path, options = {}) {
  let response;
  let lastError = null;

  // Le chiamate restano semplici: niente header customi, quindi nessun
  // preflight CORS e nessun modo di farsi bloccare da un proxy.
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
  };

  // Il backend puo' cambiare indirizzo (un tunnel riavviato, una VM
  // ricreata): se la richiesta fallisce a livello di rete si prova un altro
  // backend conosciuto (config.json di Pages, puntatore fresco su GitHub).
  for (let attempt = 0; attempt < 5; attempt++) {
    try {
      response = await fetch(API_BASE + path, {
        headers,
        // La sessione vive in un cookie HttpOnly: va sempre mandato.
        credentials: "include",
        ...options,
        headers,
      });
      break;
    } catch (error) {
      lastError = error;
      // 1) Prova subito un altro backend noto.
      const alternative = API_FALLBACKS.find((base) => base !== API_BASE);
      if (alternative) {
        API_BASE = alternative;
        API_FALLBACKS = [alternative, ...API_FALLBACKS.filter((b) => b !== alternative)];
        continue;
      }
      // 2) Rileggi i puntatori: potrebbero essersi aggiornati.
      if (attempt >= 1) {
        await loadRuntimeConfig();
        const fresh = API_FALLBACKS.find((base) => base !== API_BASE);
        if (fresh) {
          API_BASE = fresh;
          API_FALLBACKS = [fresh, ...API_FALLBACKS.filter((b) => b !== fresh)];
          continue;
        }
      }
      if (attempt >= 3) {
        scheduleApiRetry();
        throw new Error(MESSAGGIO_TUNNEL);
      }
      await sleep(1500 * (attempt + 1));
    }
  }
  if (!response) throw lastError || new Error(MESSAGGIO_TUNNEL);

  if (response.status === 401) {
    // Accesso richiesto: torna alla schermata di accesso. Con l'accesso
    // disattivato non dovrebbe accadere, ma meglio coprire il caso.
    if (!state.authDisabled) {
      showAuthScreen();
      throw new Error("Accesso richiesto");
    }
    throw new Error("Accesso richiesto");
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const data = await response.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
    } catch (_) { /* ignore */ }
    throw new Error(detail || `Errore ${response.status}`);
  }
  if (response.status === 204) return null;
  const text = await response.text();
  // ngrok free manda la pagina di avviso al posto dell'app: si riconosce
  // e si spiega, invece di dare un errore JSON incomprensibile.
  if (text.includes("You are about to visit")) {
    throw new Error(MESSAGGIO_TUNNEL);
  }
  try {
    return JSON.parse(text);
  } catch (_) {
    throw new Error("Risposta non valida dal server");
  }
}

async function loadRuntimeConfig() {
  // Se la pagina e' servita dall'app stessa (uso locale), l'API e' alla
  // stessa origine: la si usa direttamente, senza passare dal tunnel.
  try {
    const probe = await fetch("api/auth/status?ts=" + Date.now(), {
      cache: "no-store",
      credentials: "include",
    });
    if (probe.ok) {
      const body = await probe.json();
      if (body && typeof body === "object" && "locked" in body) {
        API_BASE = location.origin;
        API_FALLBACKS = [location.origin];
        showApiHint();
        return;
      }
    }
  } catch (_) { /* non e' l'app stessa: si prosegue */ }

  // Prova il file di config locale; su Pages contiene l'URL del backend.
  // Prima il path relativo: su GitHub Pages la root e' /<repo>/, non "/".
  // `cache: "no-store"` e i query param servono a non prendere una copia
  // vecchia: se il tunnel e' stato riavviato, l'URL e' cambiato e quello
  // precedente non porta piu' da nessuna parte.
  //
  // In coda si legge anche il puntatore "fresco" pubblicato dal tunnel sul
  // branch: copre la finestra in cui la config.json di Pages non e' ancora
  // stata rideployata.
  const stamp = Date.now();
  const found = [];
  const candidates = [`config.json?ts=${stamp}`, "/config.json"];
  for (const path of candidates) {
    try {
      const response = await fetch(path, { cache: "no-store" });
      if (!response.ok) continue;
      const config = await response.json();
      if (config && typeof config.apiBase === "string" && config.apiBase) {
        found.push({
          base: config.apiBase.replace(/\/$/, ""),
          updated: config.updated_at || "",
        });
      }
      break;
    } catch (_) { /* prova il successivo */ }
  }
  try {
    const response = await fetch(`${RAW_BACKEND}?ts=${stamp}`, { cache: "no-store" });
    if (response.ok) {
      const config = await response.json();
      if (config && typeof config.apiBase === "string" && config.apiBase) {
        found.push({
          base: config.apiBase.replace(/\/$/, ""),
          updated: config.updated_at || "",
        });
      }
    }
  } catch (_) { /* senza rete resta solo la config.json */ }

  // Via i duplicati, tenendo la data piu' recente per ogni URL.
  const perBase = new Map();
  for (const item of found) {
    const noto = perBase.get(item.base);
    if (!noto || String(item.updated) > String(noto.updated)) {
      perBase.set(item.base, item);
    }
  }
  const unici = [...perBase.values()].sort(
    (a, b) => String(b.updated).localeCompare(String(a.updated))
  );
  if (!unici.length) return;

  // Il puntatore piu' fresco non basta: il tunnel puo' essere caduto e
  // l'URL vecchio restare scritto nel file. Si tiene il primo che
  // risponde davvero, cosi' il link GitHub porta sempre a quello online.
  let vivo = null;
  for (const item of unici) {
    if (await backendVivo(item.base)) {
      vivo = item;
      break;
    }
  }
  const scelto = vivo || unici[0];
  API_FALLBACKS = [scelto.base, ...unici.map((i) => i.base).filter((b) => b !== scelto.base)];
  API_BASE = scelto.base;
  showApiHint();
  // Se il backend serve anche l'app, la porta giusta e' la sua: stessa
  // origine, niente CORS e niente cookie di terze parti (il browser li
  // blocca, e l'accesso non passerebbe). Con ?resta=1 l'app resta dove e'.
  if (vivo && !new URLSearchParams(location.search).has("resta")) {
    passaAlBackend();
  }
}

/* Il backend (la VM) serve anche il frontend: se l'app e' aperta su GitHub
   Pages, si passa a lui conservando la chiave d'accesso nell'URL. */
async function passaAlBackend() {
  let target;
  try {
    const origine = new URL(API_BASE).origin;
    if (origine === location.origin) return;
    target = new URL(origine);
  } catch (_) {
    return;
  }
  // Solo se quel backend serve davvero l'app: un'API sola non deve rubare
  // il passaggio.
  try {
    const risposta = await fetch(target.origin + "/", { cache: "no-store" });
    if (!risposta.ok) return;
    const testo = await risposta.text();
    if (!testo.includes('id="watchList"')) return;
  } catch (_) {
    return;
  }
  target.pathname = "/";
  target.search = location.search;
  target.hash = location.hash;
  location.replace(target.toString());
}

/* L'API risponde? (un tunnel "vivo" ma appeso non risponde: si scarta).
   Si controlla anche il contenuto: ngrok risponde 200 con la sua pagina
   di avviso, che non e' l'app. */
async function backendVivo(base, ms = 4000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);
  try {
    const response = await fetch(
      base.replace(/\/$/, "") + "/api/auth/status?ts=" + Date.now(),
      {
        cache: "no-store",
        credentials: "include",
        signal: ctrl.signal,
      }
    );
    if (!response.ok) return false;
    const body = await response.json();
    return !!(body && typeof body === "object" && "locked" in body);
  } catch (_) {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

/* Il backend (la VM) serve anche il frontend: li' frontend e API sono sulla
   stessa origine, il cookie di sessione e' di prima parte e l'accesso passa.
   Il passaggio dal link GitHub avviene in automatico (passaAlBackend). */

/* Se la pagina e' su un dominio diverso dall'API, il browser potrebbe
   bloccare il cookie di sessione: si mostra l'indirizzo diretto del backend,
   dove frontend e API sono sulla stessa origine e tutto funziona. */
function showApiHint() {
  const hint = $("#loginApiHint");
  if (!hint) return;
  if (!API_BASE || API_BASE === location.origin) {
    hint.textContent = "";
    return;
  }
  hint.innerHTML =
    "Se non riesci a entrare, apri l'app direttamente qui: " +
    `<a href="${esc(API_BASE)}" target="_blank" rel="noopener">${esc(API_BASE)}</a>` +
    " — l'app e l'API devono stare sulla stessa origine, altrimenti il browser " +
    "blocca il cookie di accesso.";
}

/* Se l'API non risponde, il tunnel potrebbe essere stato riavviato con un
   URL nuovo: si ricarica la pagina, con attese crescenti, per prendere il
   config.json fresco. */
let _apiRetry = 0;
function scheduleApiRetry() {
  if (_apiRetry) return;
  const delays = [5000, 15000, 30000];
  let index = 0;
  const retry = () => {
    if (index >= delays.length) return;
    const delay = delays[index++];
    const notice = document.querySelector("#loginHint");
    if (notice) {
      notice.textContent =
        "Server non raggiungibile: l'agente sul PC potrebbe essere spento, " +
        "o il tunnel bloccato dalla rete. " +
        `Riprovo tra ${Math.round(delay / 1000)} secondi…`;
      notice.className = "field-hint err";
    }
    _apiRetry = setTimeout(() => {
      _apiRetry = 0;
      window.location.reload();
    }, delay);
  };
  retry();
}

function toast(message, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  // Gli errori si annunciano subito: il contenitore e' "polite", l'errore sale.
  if (kind === "error") el.setAttribute("role", "alert");
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 5200);
}

function openModal(id) { $("#" + id).classList.remove("hidden"); }
function closeModal(id) {
  if (id === "confirmModal") { chiudiConferma(false); return; }
  $("#" + id).classList.add("hidden");
}

/* Conferma nel linguaggio dell'app: una promessa, un dialog, nessun confirm()
   nativo che porti il sistema operativo dentro l'interfaccia. */
let _confirmResolve = null;
let _confirmAttesa = "";
let _confirmTesto = "";

/* Un'azione irreversibile si conferma scrivendo: il click-OK e' troppo
   facile da premere per sbaglio. */
function chiediConferma({ titolo, testo, conferma = "Conferma", danger = false, digita = "" }) {
  $("#confirmTitle").textContent = titolo;
  $("#confirmText").innerHTML = testo;
  const ok = $("#confirmOk");
  ok.textContent = conferma;
  ok.className = danger ? "btn danger" : "btn primary";
  const campo = $("#confirmType");
  const blocco = $("#confirmTypeField");
  _confirmTesto = (digita || "").trim();
  _confirmAttesa = _confirmTesto.toLowerCase();
  if (_confirmAttesa) {
    blocco.classList.remove("hidden");
    campo.value = "";
    campo.placeholder = _confirmTesto;
    $("#confirmTypeHint").textContent = `Scrivi "${_confirmTesto}" per confermare.`;
    ok.disabled = true;
  } else {
    blocco.classList.add("hidden");
    ok.disabled = false;
  }
  openModal("confirmModal");
  (campo && _confirmAttesa ? campo : ok).focus({ preventScroll: true });
  return new Promise((resolve) => { _confirmResolve = resolve; });
}

function chiudiConferma(valore) {
  const resolve = _confirmResolve;
  _confirmResolve = null;
  _confirmAttesa = "";
  _confirmTesto = "";
  $("#confirmModal")?.classList.add("hidden");
  if (resolve) resolve(valore);
}

/* ------------------------------------------------------------------ */
/* Accesso                                                             */
/* ------------------------------------------------------------------ */
/* ------------------------------------------------------------------ */
/* Accesso: una password, o una chiave segreta nell'URL                 */
/* ------------------------------------------------------------------ */
function showAuthScreen() {
  const screen = $("#authScreen");
  if (screen) screen.classList.remove("hidden");
  document.querySelector(".layout")?.classList.add("hidden");
  document.querySelector(".topbar")?.classList.add("hidden");
  stopPolling();
}

function hideAuthScreen() {
  const screen = $("#authScreen");
  if (screen) screen.classList.add("hidden");
  document.querySelector(".layout")?.classList.remove("hidden");
  document.querySelector(".topbar")?.classList.remove("hidden");
}

async function submitLogin(event) {
  event.preventDefault();
  const hint = $("#loginHint");
  const button = $("#btnLogin");
  const password = $("#aLoginPass").value;
  button.disabled = true;
  hint.textContent = "";
  try {
    await api("/api/auth/unlock", {
      method: "POST",
      body: JSON.stringify({ password }),
    });
    $("#aLoginPass").value = "";
    // La chiave nell'URL non serve piu': si pulisce dalla barra.
    if (location.search.includes("key=")) {
      const url = new URL(location.href);
      url.searchParams.delete("key");
      history.replaceState({}, "", url);
    }
    await bootApp();
  } catch (error) {
    hint.textContent = error.message;
    hint.className = "field-hint err";
  } finally {
    button.disabled = false;
  }
}

/* Se l'URL contiene la chiave segreta, si sblocca da soli. */
async function tryKeyFromUrl() {
  const key = new URL(location.href).searchParams.get("key");
  if (!key) return false;
  try {
    await api("/api/auth/unlock", {
      method: "POST",
      body: JSON.stringify({ password: key }),
    });
    const url = new URL(location.href);
    url.searchParams.delete("key");
    history.replaceState({}, "", url);
    return true;
  } catch (_) {
    return false;
  }
}

async function logout() {
  try {
    await api("/api/auth/logout", { method: "POST" });
  } catch (_) { /* la sessione e' comunque da buttare */ }
  state.watches = [];
  state.selectedId = null;
  state.watch = null;
  showAuthScreen();
}

/* ------------------------------------------------------------------ */
/* Bootstrap                                                          */
/* ------------------------------------------------------------------ */
document.addEventListener("DOMContentLoaded", async () => {
  bindEvents();
  registerServiceWorker();
  await loadRuntimeConfig();
  try {
    // Se l'URL ha la chiave segreta, si sblocca senza digitare nulla.
    if (await tryKeyFromUrl()) {
      await bootApp();
      return;
    }
    const authState = await api("/api/auth/status");
    if (!authState.locked) {
      // Nessuna password impostata: l'app si apre direttamente.
      state.authDisabled = true;
      state.user = authState.user || null;
      await bootApp();
      return;
    }
    if (!authState.unlocked) {
      showAuthScreen();
      return;
    }
    state.user = authState.user;
    await bootApp();
  } catch (error) {
    showAuthScreen();
    const hint = $("#loginHint");
    hint.textContent = MESSAGGIO_TUNNEL;
    hint.className = "field-hint err";
    scheduleApiRetry();
  }
});

async function bootApp() {
  hideAuthScreen();
  try {
    // Il catalogo degli abbonamenti si carica qui: al primo avvio la pagina
    // e' ancora chiusa e la richiesta verrebbe respinta (401), lasciando il
    // selettore degli abbonamenti vuoto anche dopo il login.
    await caricaCatalogoPass();
    const status = await loadStatus();
    state.sources = status.sources || [];
    state.user = status.user || state.user;
    // Lo stato dell'accesso serve per mostrare la sezione "Chiave segreta".
    try {
      const access = await api("/api/auth/status");
      state.hasKey = !!access.has_key;
      state.locked = !!access.locked;
    } catch (_) { /* senza stato accesso: si prosegue */ }
    await Promise.all([loadWatches(), loadSettings()]);
    if (state.watches.length) {
      await selectWatch(state.watches[0].id);
    } else {
      showEmpty(true);
    }
    await loadAlerts(true);
    state.booted = true;
    startPolling();
  } catch (error) {
    toast("Impossibile caricare i dati: " + error.message, "error");
    $("#agentStatus")?.classList.add("offline");
    $("#agentText") && ($("#agentText").textContent = "non raggiungibile");
  }
}

function bindEvents() {
  // Accesso
  $("#loginForm")?.addEventListener("submit", submitLogin);

  $("#btnNew")?.addEventListener("click", () => openWatchModal(null));
  $("#btnEmptyNew")?.addEventListener("click", () => openWatchModal(null));
  $("#btnSettings")?.addEventListener("click", openSettings);
  $("#btnPromos")?.addEventListener("click", () => openPromos(false));
  $("#btnRefreshPromos")?.addEventListener("click", () => openPromos(true));
  $("#btnAlerts")?.addEventListener("click", () => { openModal("alertsDrawer"); loadAlerts(false); });
  $("#btnReadAll")?.addEventListener("click", markAllRead);
  $("#btnLogout")?.addEventListener("click", logout);

  $("#watchList")?.addEventListener("click", (event) => {
    const item = event.target.closest(".watch-item");
    if (item) {
      selectWatch(Number(item.dataset.id));
      return;
    }
    // Le cartelle: nuova, rinomina, elimina.
    if (event.target.closest("#btnNewGroup")) {
      const bottone = event.target.closest("#btnNewGroup");
      campoInline(
        bottone,
        "",
        async (nome) => {
          try {
            await creaCartella(nome);
          } catch (error) {
            toast(error.message, "error");
          }
        },
        () => renderWatchList(),
      );
      return;
    }
    const azione = event.target.closest("[data-group-action]");
    if (!azione) return;
    const id = Number(azione.dataset.group);
    if (azione.dataset.groupAction === "rename") {
      const cartella = state.groups.find((g) => g.id === id);
      const titolo = document.querySelector(`.watch-group[data-group="${id}"] .group-name`);
      if (!cartella || !titolo) return;
      campoInline(
        titolo,
        cartella.name,
        async (nome) => {
          try {
            await rinominaCartella(id, nome);
          } catch (error) {
            toast(error.message, "error");
            renderWatchList();
          }
        },
        () => renderWatchList(),
      );
    } else if (azione.dataset.groupAction === "delete") {
      eliminaCartella(id).catch((error) => toast(error.message, "error"));
    }
  });

  $("#tabs")?.addEventListener("click", (event) => {
    const button = event.target.closest(".tab");
    if (!button) return;
    state.tab = button.dataset.tab;
    $$(".tab").forEach((tab) => tab.classList.toggle("active", tab === button));
    renderTab();
  });

  $("#btnRunNow")?.addEventListener("click", runNow);
  $("#chatFab")?.addEventListener("click", apriChat);
  $("#btnToggleWatch")?.addEventListener("click", toggleWatch);
  $("#btnEditWatch")?.addEventListener("click", () => openWatchModal(state.watch));
  $("#btnDeleteWatch")?.addEventListener("click", deleteWatch);
  $("#confirmOk")?.addEventListener("click", () => chiudiConferma(true));
  // La conferma si sblocca solo quando la tratta scritta coincide.
  $("#confirmType")?.addEventListener("input", (event) => {
    const scritto = event.target.value.trim().toLowerCase();
    $("#confirmOk").disabled = !scritto || scritto !== _confirmAttesa;
  });

  $("#watchForm")?.addEventListener("submit", submitWatch);
  $("#settingsForm")?.addEventListener("submit", submitSettings);
  $("#btnTestTelegram")?.addEventListener("click", testTelegram);
  $("#btnAiFill")?.addEventListener("click", aiFill);
  $("#btnTestAi")?.addEventListener("click", testAi);
  $("#btnAiModels")?.addEventListener("click", listAiModels);
  $("#btnGtfsUpdate")?.addEventListener("click", updateGtfs);
  $("#btnPushEnable")?.addEventListener("click", enablePush);
  $("#btnPushDisable")?.addEventListener("click", disablePush);
  $("#btnPushTest")?.addEventListener("click", testPush);
  $("#btnSetAccess")?.addEventListener("click", setAccessPassword);
  $("#btnClearAccess")?.addEventListener("click", clearAccessPassword);
  $("#btnNewKey")?.addEventListener("click", generateKey);
  $("#btnDelKey")?.addEventListener("click", deleteKey);
  $("#btnTelegramLink")?.addEventListener("click", telegramLink);
  $("#btnTelegramConfirm")?.addEventListener("click", telegramConfirm);
  // "Nuova cartella…" nel modale: si apre il campo per il nome.
  $("#fGroup")?.addEventListener("change", (event) => {
    const nuovo = event.target.value === "new";
    $("#fGroupNew").classList.toggle("hidden", !nuovo);
    if (nuovo) $("#fGroupNew").focus();
  });
  document.addEventListener("input", (event) => {
    if (event.target.classList?.contains("pass-search")) {
      filtraPass(event.target.value);
    }
  });
  // Le impostazioni sono a schede: una per argomento.
  $("#settingsTabs")?.addEventListener("click", (event) => {
    const bottone = event.target.closest(".tab");
    if (!bottone) return;
    const gruppo = bottone.dataset.tab;
    $$("#settingsTabs .tab").forEach((t) => t.classList.toggle("active", t === bottone));
    $$("[data-pane]").forEach((pane) => {
      pane.classList.toggle("active", pane.dataset.pane === gruppo);
    });
  });
  $("#questionsCard")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-answer]");
    if (!button) return;
    answerQuestion(Number(button.dataset.qid), button.dataset.answer);
  });
  // Risposta aperta (invio col tasto Invio o col bottone).
  $("#questionsCard")?.addEventListener("submit", (event) => {
    const form = event.target.closest(".q-free");
    if (!form) return;
    event.preventDefault();
    answerFree(Number(form.dataset.qid), form);
  });

  document.addEventListener("click", (event) => {
    const closer = event.target.closest("[data-close]");
    if (closer) closeModal(closer.dataset.close);
    const geoButton = event.target.closest("[data-geo]");
    if (geoButton) checkGeo(geoButton.dataset.geo);
    if (event.target.classList.contains("modal-backdrop")) {
      if (event.target.id === "confirmModal") chiudiConferma(false);
      else event.target.classList.add("hidden");
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      $$(".modal-backdrop, .drawer-backdrop").forEach((el) => el.classList.add("hidden"));
      chiudiConferma(false);
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refreshAll();
  });
}

function startPolling() {
  if (state.polling) clearInterval(state.polling);
  state.polling = setInterval(() => {
    if (!document.hidden) refreshAll();
  }, 30000);
}

function stopPolling() {
  if (state.polling) {
    clearInterval(state.polling);
    state.polling = null;
  }
}

async function refreshAll() {
  if (!state.booted) return;
  try {
    await loadStatus();
    await loadWatches();
    if (state.selectedId) await loadDetail(state.selectedId);
    await loadAlerts(false);
  } catch (_) {
    /* un giro fallito non deve rompere la UI */
  }
}

/* ------------------------------------------------------------------ */
/* Stato agente                                                       */
/* ------------------------------------------------------------------ */
async function loadStatus() {
  const status = await api("/api/status");
  const running = status.scheduler?.running;
  $("#agentStatus").classList.toggle("offline", !running);
  const active = status.active_watches ?? 0;
  $("#agentText").textContent = running
    ? `agente attivo · ${active} ${active === 1 ? "ricerca" : "ricerche"}`
    : "agente in pausa";

  const ai = status.ai || {};
  state.ai = ai;
  const aiPill = $("#aiStatus");
  if (aiPill) {
    aiPill.classList.toggle("off", !ai.active);
    $("#aiText").textContent = ai.active
      ? `Agente · ${shortModel(ai.model_smart)}`
      : ai.configured
        ? "Agente disattivato"
        : "Agente non configurato";
  }
  if (status.sources && !state.sources.length) state.sources = status.sources;
  return status;
}

function shortModel(model) {
  if (!model) return "";
  const name = String(model).split("/").pop();
  return name.length > 18 ? name.slice(0, 17) + "…" : name;
}

/* ------------------------------------------------------------------ */
/* Ricerche                                                           */
/* ------------------------------------------------------------------ */
async function loadWatches() {
  const [watches, groups] = await Promise.all([
    api("/api/watches"),
    api("/api/groups"),
  ]);
  state.watches = watches;
  state.groups = groups;
  $("#watchCount").textContent = state.watches.length;
  renderWatchList();
}

function renderWatchList() {
  const box = $("#watchList");
  if (!state.watches.length) {
    box.innerHTML = `<div class="hint hint-block">Nessuna ricerca. Creane una con «+ Nuova ricerca».</div>`;
    return;
  }
  const voce = (watch) => {
    const active = watch.id === state.selectedId;
    const route = `${esc(watch.from_place)} <span class="arrow">→</span> ${esc(watch.to_place)}`;
    const price = watch.best_price != null ? euro(watch.best_price) : "—";
    // Un bottone, non un div con un click: la lista si cambia anche da tastiera.
    return `
      <button type="button" class="watch-item ${active ? "active" : ""} ${watch.active ? "" : "paused"}"
              data-id="${watch.id}"${active ? ' aria-current="true"' : ""}>
        <span class="watch-route"><span class="watch-status-dot"></span>${route}</span>
        ${watch.name ? `<span class="watch-name">${esc(watch.name)}</span>` : ""}
        <span class="watch-meta">
          <span>entro ${fmtDayFull(watch.arrive_by)}</span>
          <span class="price">${price}</span>
        </span>
      </button>`;
  };
  // Le ricerche si raccolgono per cartella; quelle senza cartella restano
  // in coda, sotto una intestazione che lo dice.
  const perCartella = new Map();
  const sciolte = [];
  for (const watch of state.watches) {
    const cartella = state.groups.find((g) => g.id === watch.group_id);
    if (cartella) {
      if (!perCartella.has(cartella.id)) perCartella.set(cartella.id, []);
      perCartella.get(cartella.id).push(watch);
    } else {
      sciolte.push(watch);
    }
  }
  const sezione = (nome, id, voci, conMenu) => `
    <section class="watch-group${id ? "" : " plain"}" data-group="${id ?? ""}">
      <header class="group-head">
        <span class="group-name">${esc(nome)}</span>
        <span class="group-count">${voci.length}</span>
        ${conMenu ? `<details class="group-menu">
          <summary aria-label="Azioni della cartella">⋯</summary>
          <div class="menu">
            <button type="button" data-group-action="rename" data-group="${id}">Rinomina</button>
            <button type="button" data-group-action="delete" data-group="${id}">Elimina</button>
          </div>
        </details>` : ""}
      </header>
      <div class="group-items">${voci.map(voce).join("")}</div>
    </section>`;
  const parti = state.groups.map((g) => sezione(g.name, g.id, perCartella.get(g.id) || [], true));
  if (sciolte.length) parti.push(sezione("Altre ricerche", null, sciolte, false));
  box.innerHTML = `${parti.join("")}
    <button type="button" class="group-add" id="btnNewGroup">+ Nuova cartella</button>`;
}

/* ------------------------------------------------------------------ */
/* Cartelle: creare, rinominare, eliminare                              */
/* ------------------------------------------------------------------ */
async function creaCartella(nome) {
  const gruppo = await api("/api/groups", {
    method: "POST",
    body: JSON.stringify({ name: nome }),
  });
  await loadWatches();
  return gruppo;
}

async function rinominaCartella(id, nome) {
  await api(`/api/groups/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ name: nome }),
  });
  await loadWatches();
}

async function eliminaCartella(id) {
  const cartella = state.groups.find((g) => g.id === id);
  if (!cartella) return;
  const ok = await chiediConferma({
    titolo: "Eliminare la cartella?",
    testo: `Le ricerche dentro <b>${esc(cartella.name)}</b> non si perdono: restano nell'elenco senza cartella.`,
    conferma: "Elimina",
    danger: true,
  });
  if (!ok) return;
  await api(`/api/groups/${id}`, { method: "DELETE" });
  await loadWatches();
}

/* Un campo inline al posto del titolo: si rinomina o si crea senza aprire
   un dialog. Invio conferma, Esc annulla. */
function campoInline(ancora, valore, alSalva, allAnnulla) {
  const input = document.createElement("input");
  input.className = "group-rename";
  input.type = "text";
  input.maxLength = 80;
  input.value = valore || "";
  input.setAttribute("aria-label", "Nome della cartella");
  let chiuso = false;
  const chiudi = (salva) => {
    if (chiuso) return;
    chiuso = true;
    const testo = input.value.trim();
    if (salva && testo) alSalva(testo);
    else allAnnulla();
  };
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); chiudi(true); }
    if (event.key === "Escape") { event.preventDefault(); chiudi(false); }
  });
  input.addEventListener("blur", () => chiudi(true));
  ancora.replaceWith(input);
  input.focus();
  input.select();
}

async function selectWatch(id) {
  state.selectedId = id;
  state.tab = "best";
  renderWatchList();
  $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === "best"));
  await loadDetail(id);
}

async function loadDetail(id) {
  const [watch, offers, links, runs, domande] = await Promise.all([
    api(`/api/watches/${id}`),
    api(`/api/watches/${id}/offers`),
    api(`/api/watches/${id}/links`),
    api(`/api/watches/${id}/runs`),
    api(`/api/watches/${id}/questions?status=pending`).catch(() => []),
  ]);
  state.watch = watch;
  state.offers = offers;
  state.links = links.links || [];
  state.runs = runs;
  // Le domande in sospeso arrivano dall'API: se il piano non c'e' (nessuna
  // combinazione valida) quelle del piano sarebbero vuote e l'utente non
  // vedrebbe mai la domanda "vuoi alzare il budget?".
  const inSospeso = (domande || []).filter((q) => q.status === "pending");
  try {
    const plan = await api(`/api/watches/${id}/plan`);
    state.plan = plan.plan || null;
    state.planAlternatives = plan.alternatives || [];
    state.questions = _unisceDomande(plan.questions || [], inSospeso);
  } catch (_) {
    state.plan = null;
    state.planAlternatives = [];
    state.questions = inSospeso;
  }
  showEmpty(false);
  renderDetail();
  renderTab();
}

/* Due liste di domande in una, senza ripetere la stessa. */
function _unisceDomande(a, b) {
  const visti = new Set();
  const fuori = [];
  for (const q of [...(a || []), ...(b || [])]) {
    if (!q || q.id == null || visti.has(q.id)) continue;
    visti.add(q.id);
    fuori.push(q);
  }
  return fuori;
}

function showEmpty(empty) {
  $("#emptyState").classList.toggle("hidden", !empty);
  $("#detail").classList.toggle("hidden", empty);
}

function renderDetail() {
  const watch = state.watch;
  if (!watch) return;

  $("#watchTitle").textContent = `${watch.from_place} → ${watch.to_place}`;
  const statePill = $("#watchState");
  statePill.textContent = watch.active ? "attiva" : "in pausa";
  statePill.classList.toggle("off", !watch.active);

  const passNames = ((watch.prefs && watch.prefs.passes) || [])
    .map((p) => {
      const tipo = typeof p === "string" ? p : p.tipo;
      const known = PASS_CATALOGO.find((item) => item.id === tipo);
      if (typeof p === "string") return known ? known.nome : tipo;
      return (p.nome || (known ? known.nome : tipo)).replace(/\s*\([^)]*\)\s*$/, "");
    })
    .join(", ");
  // I criteri della ricerca come chip: una riga leggibile invece di un muro
  // di testo. L'elenco delle fonti non sta qui: ogni riga di offerta dice la
  // sua fonte, ogni scheda vuota spiega come è andata, e in Impostazioni si
  // scelgono. I punti di ritrovo si chiamano "da"/"a", come il titolo
  // della tratta: "arrivo" da solo diceva due cose diverse.
  const criteri = [`entro ${fmtDate(watch.arrive_by)}`];
  if (watch.max_price != null) criteri.push(`budget ${euro(watch.max_price)}`);
  if (watch.interval_min) criteri.push(`ogni ${watch.interval_min} min`);
  if (watch.radius_km != null) criteri.push(`raggio ${watch.radius_km} km`);
  if (watch.from_point) criteri.push(`da: ${watch.from_point}`);
  if (watch.to_point) criteri.push(`a: ${watch.to_point}`);
  if (passNames) criteri.push(`abbonamenti: ${passNames}`);
  $("#watchSub").innerHTML = criteri
    .map((c) => `<span class="crit">${esc(c)}</span>`)
    .join("");

  const btnToggle = $("#btnToggleWatch");
  if (btnToggle) {
    const etichetta = watch.active ? "Metti in pausa" : "Riprendi la ricerca";
    btnToggle.setAttribute("aria-label", etichetta);
    btnToggle.setAttribute("title", etichetta);
    btnToggle.innerHTML = watch.active ? ICON_PAUSE : ICON_PLAY;
  }

  const priced = state.offers.filter((o) => o.price != null && !o.price_is_estimate);
  const best = priced.length ? Math.min(...priced.map((o) => o.price)) : null;
  const bestOffer = priced.find((o) => o.price === best);
  $("#statBest").textContent = best != null ? euro(best) : "—";
  $("#statBestNote").textContent = bestOffer ? (bestOffer.operator || bestOffer.source) : "in attesa di offerte";

  $("#statCount").textContent = state.offers.length;
  const transitCount = state.offers.filter((o) => o.mode === "transit").length;
  $("#statCountNote").textContent = transitCount ? `${transitCount} combinazioni di mezzi` : "";

  $("#statLastRun").textContent = watch.last_run_at ? relative(watch.last_run_at) : "mai";
  const lastRun = state.runs[0];
  $("#statLastRunNote").textContent = lastRun
    ? `${offText(lastRun.offers_found)} · ${lastRun.status === "ok" ? "ok" : lastRun.status}`
    : "nessun controllo";

  $("#statNextRun").textContent = watch.active ? relative(watch.next_run_at) : "—";
  $("#statNextRunNote").textContent = watch.active ? "controllo automatico" : "ricerca in pausa";

  $("#countAll").textContent = state.offers.length;
  renderPlan();
  renderQuestions();
  // Il prezzo lo mostra il pannello; nella striscia resta solo se non c'e'
  // un piano, cosi' lo stesso numero non compare due volte a schermo.
  const codaPrezzo = !!state.plan;
  $("#statBestCell")?.classList.toggle("hidden", codaPrezzo);
  $("#statBestCell")?.closest(".stat-grid")?.classList.toggle("compact", codaPrezzo);
  if (state.enteredWatch !== watch.id) {
    anima($("#detail"));
    state.enteredWatch = watch.id;
    state.schedaEntrata = true;
  }
}

/* ------------------------------------------------------------------ */
/* Piano AI + domande                                                 */
/* ------------------------------------------------------------------ */
function renderConfronti(confronti) {
  // "Tratte singole": quanto cambia davvero a spezzare il viaggio.
  if (!confronti || !confronti.length) return "";
  const righe = confronti
    .map((c) => {
      const deltaPrezzo = Number(c.delta_prezzo || 0);
      const deltaMinuti = Number(c.delta_minuti || 0);
      constConveniente = deltaPrezzo < -0.01;
      const tempo = deltaMinuti > 0 ? `+${deltaMinuti} min` : `${deltaMinuti} min`;
      const soldi = deltaPrezzo > 0
        ? `+${euro(deltaPrezzo)}`
        : deltaPrezzo < 0 ? `-${euro(Math.abs(deltaPrezzo))}` : "uguale";
      const tratte = (c.tratte || [])
        .map((t) => {
          const listino = Number(t.prezzo || 0);
          const effettivo =
            t.prezzo_effettivo != null ? Number(t.prezzo_effettivo) : listino;
          const nota = t.pass
            ? ` (${esc(String(t.pass).replace(/\s*\([^)]*\)\s*$/, ""))}${t.pass_km ? `, ${Number(t.pass_km).toLocaleString("it-IT", { maximumFractionDigits: 1 })} km` : ""})`
            : "";
          const prezzo =
            Math.abs(effettivo - listino) > 0.005
              ? `<s>${euro(listino)}</s> ${euro(effettivo)}${nota}`
              : `${euro(listino)}${t.sconto_xgo ? " (X-GO)" : ""}`;
          return `${esc(t.da)}→${esc(t.a)} ${prezzo}`;
        })
        .join(" + ");
      const totale =
        c.totale_effettivo != null ? Number(c.totale_effettivo) : Number(c.totale || 0);
      const diretto =
        c.diretto_effettivo != null ? Number(c.diretto_effettivo) : Number(c.diretto_prezzo || 0);
      return `<div class="conf-row ${deltaPrezzo < -0.01 ? "ok" : "no"}">
        <span class="conf-via">via ${esc(c.via || "")}</span>
        <span class="conf-tratte">${tratte}</span>
        <span class="conf-delta">
          <b>${euro(totale)}</b>
          <span class="vs">diretto ${euro(diretto)}</span>
          <span class="pill ${deltaPrezzo < -0.01 ? "good" : deltaPrezzo > 0.01 ? "bad" : ""}">
            ${soldi} · ${tempo}
          </span>
        </span>
      </div>`;
    })
    .join("");
  const nessunRisparmio = confronti.every((c) => Number(c.delta_prezzo || 0) >= -0.01);
  return `<div class="confronti">
    <h4 title="Confronto reale sui prezzi Trenitalia: quanto costa e quanto tempo cambia spezzando il viaggio.">Tratte singole</h4>
    ${righe}
    ${nessunRisparmio
      ? '<small class="conf-note">Nessun risparmio: spezzando il viaggio si paga uguale o di più, conviene il biglietto unico.</small>'
      : ""}
  </div>`;
}

function renderPlan() {
  const box = $("#planCard");
  const vuoto = $("#noPlanCard");
  const plan = state.plan;
  if (!plan) {
    box.classList.add("hidden");
    box.innerHTML = "";
    // Nessun piano: si spiega perche', altrimenti la pagina sembra vuota
    // e rotta. Il testo viene dal backend (budget, orario, abbonamenti).
    const motivo = state.watch?.ai_summary || "";
    if (motivo) {
      vuoto.classList.remove("hidden");
      vuoto.innerHTML = `
        <h4 class="noplan-title">Nessun piano per questo viaggio</h4>
        <p class="noplan-motivo">${esc(motivo)}</p>
        <p class="muted note-block">
          Le fonti sono state interrogate e i prezzi sono quelli veri: cambiando
          un filtro (budget, orario di arrivo, punto di ritrovo) il piano
          ricompare.
        </p>
        <div class="noplan-actions">
          <button class="btn ghost" id="btnNoPlanEdit">Modifica i filtri</button>
          <button class="btn ghost" id="btnNoPlanRun">Cerca ora</button>
          <button class="btn ghost" id="btnNoPlanChat">Chiedi all'agente</button>
        </div>`;
      $("#btnNoPlanEdit")?.addEventListener("click", () => openWatchModal(state.watch));
      $("#btnNoPlanRun")?.addEventListener("click", runNow);
      $("#btnNoPlanChat")?.addEventListener("click", apriChat);
    } else {
      vuoto.classList.add("hidden");
      vuoto.innerHTML = "";
    }
    return;
  }
  vuoto.classList.add("hidden");
  vuoto.innerHTML = "";
  box.classList.remove("hidden");

  const steps = (plan.steps || []).map((step, i) => {
    const dep = step.depart_at ? fmtTime(step.depart_at) : "—";
    const arr = step.arrive_at ? fmtTime(step.arrive_at) : "—";
    const day = step.depart_at ? fmtDayFull(step.depart_at) : "";
    // Con una sola tratta il prezzo e' gia' quello del piano: non si ripete.
    const unicaTratta = (plan.steps || []).length === 1;
    const price = step.price != null ? euro(step.price) : "da verificare";
    const link = step.deeplink
      ? ` <a class="ext-link" href="${esc(step.deeplink)}" target="_blank" rel="noreferrer">apri ↗</a>`
      : "";
    // Spezzatura: un passo che racchiude due biglietti.
    const biglietti = step.spezzatura && step.biglietti && step.biglietti.length
      ? `<div class="ps-split">${step.biglietti.map((b, i) => `
           <div class="ps-ticket">
             <span class="tk">${i + 1}</span>
             <span>${esc(b.da || "")} → ${esc(b.a || "")}</span>
             <span class="tk-time">${b.partenza ? fmtTime(b.partenza) : ""} → ${b.arrivo ? fmtTime(b.arrivo) : ""}</span>
           </div>`).join("")}
           <div class="ps-save">risparmi ${euro(step.risparmio)} rispetto al biglietto unico (${euro(step.prezzo_diretto)})</div>
         </div>`
      : "";

    return `
      <div class="plan-step">
        <span class="idx">${step.leg || 1}</span>
        <div class="ps-main">
          <b>${esc(step.operator || step.source || "")}</b>${link ? ` ${link}` : ""}
          <small>${esc(MODE_LABEL[step.mode] || step.mode || "")} · ${dep} → ${arr} ${day} ·
            ${esc(step.pickup_name || "")} → ${esc(step.dropoff_name || "")}${step.price_is_estimate ? " · stima" : ""}${i === 0 ? ` · ${(plan.steps || []).length === 1 ? (plan.changes === 0 ? "tratta diretta" : "1 tratta") : `${(plan.steps || []).length} tratte`}` : ""}</small>
          ${biglietti}
        </div>
        ${unicaTratta ? "" : `<span class="ps-price">${price}</span>`}
      </div>`;
  }).join("");

  const duration = fmtDuration(plan.total_duration_min);
  const confidence = plan.confidence != null ? Math.round(Number(plan.confidence) * 100) : null;
  const watchouts = (plan.watchouts || []).map((w) => `<span>${esc(w)}</span>`).join("");
  const changes = plan.changes || 0;
  const changeLabel = changes === 0
    ? "nessun cambio"
    : changes === 1 ? "1 cambio" : `${changes} cambi`;
  // Il prezzo grande e' quello che si spende davvero: biglietto + spostamento
  // per raggiungere il punto di ritrovo. Se un abbonamento copre il viaggio,
  // il costo effettivo e' zero e il prezzo del biglietto resta informazione.
  const accessCost = plan.access_cost || 0;
  const covered = !!plan.covered;
  const netTotal = plan.net_total_price != null ? plan.net_total_price : plan.total_price;
  const effective = covered
    ? (netTotal || 0) + accessCost
    : (plan.effective_cost != null
        ? plan.effective_cost
        : (plan.total_price != null ? plan.total_price + accessCost : null));
  const hasAccess = accessCost > 0.01;
  const passNames = (plan.passes || []).join(", ");
  // Il tempo reale: a piedi, poi il viaggio, poi a piedi.
  const walkOut = Number(plan.walk_out_min || 0);
  const walkIn = Number(plan.walk_in_min || 0);
  const doorToDoor = plan.door_to_door_min != null ? Number(plan.door_to_door_min) : null;
  // Il cammino: quanto a piedi prima e dopo, e quando uscire di casa.
  const partiDiCasa = (() => {
    if (!plan.depart_at || !walkOut) return "";
    const partenza = new Date(plan.depart_at);
    if (Number.isNaN(partenza.getTime())) return "";
    partenza.setMinutes(partenza.getMinutes() - walkOut);
    return `esci di casa alle ${partenza.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" })}`;
  })();
  const walkNote =
    walkOut || walkIn
      ? `<span>${walkOut ? walkOut + " min" : ""}${walkOut && walkIn ? " + " : ""}${walkIn ? walkIn + " min" : ""} a piedi</span>`
      : "";
  const walkNote2 = walkIn
    ? `<span>${walkIn} min a piedi fino alla destinazione</span>`
    : "";
  const alts = state.planAlternatives.length
    ? `<div class="plan-alts">
         <h4>Alternative</h4>
         ${state.planAlternatives.map((alt) => {
           const altCovered = !!alt.covered;
           const altNet = altCovered
             ? (alt.net_total_price != null ? alt.net_total_price : 0)
             : alt.total_price;
           return `
           <div class="plan-alt">
             <span class="alt-name">${esc(ALT_PROFILE[alt.profile] || "Alternativa")}</span>
             <span>${esc(alt.title || "")}${alt.total_duration_min ? " · " + fmtDuration(alt.total_duration_min) : ""}${alt.changes ? " · " + (alt.changes === 1 ? "1 cambio" : alt.changes + " cambi") : ""}${alt.door_to_door_min && alt.door_to_door_min > (alt.total_duration_min || 0) ? " · p.a.p. " + fmtDuration(alt.door_to_door_min) : ""}${altCovered ? " · coperto" : ""}</span>
             <b>${altNet != null ? euro(altNet) : "—"}${altCovered && alt.total_price != null ? `<span class="was">${euro(alt.total_price)}</span>` : ""}</b>
           </div>`;
         }).join("")}
       </div>`
    : "";

  box.innerHTML = `
    <div class="plan-head">
      <div>
        <div class="plan-title">${esc(plan.title || "")}${plan.profile ? `<span class="alt-profile">${esc(ALT_PROFILE[plan.profile] || "")}</span>` : ""}</div>
      </div>
      <div class="plan-price">
        <strong>${effective != null ? euro(effective) : "—"}</strong>
        ${covered && plan.total_price != null ? `<span class="was">${euro(plan.total_price)}</span>` : ""}
        <small>${covered
          ? `coperto da ${esc(passNames || "abbonamento")}`
          : plan.partial ? "totale noto · tratte da verificare"
          : hasAccess ? "biglietto + spostamento" : "costo totale"}${plan.ai && confidence != null ? ` · fiducia ${confidence}/100` : ""}</small>
        ${covered && plan.total_price != null ? `<small>biglietto ${euro(plan.total_price)}, ma è già coperto</small>` : ""}
        ${!covered && hasAccess && plan.total_price != null ? `<small>biglietti ${euro(plan.total_price)} + ${euro(accessCost)} spostamento</small>` : ""}
      </div>
    </div>
    <div class="plan-facts">
      ${plan.depart_at ? `<span>partenza ${fmtDate(plan.depart_at)}</span>` : ""}
      ${plan.arrive_at ? `<span>arrivo ${fmtDate(plan.arrive_at)}</span>` : ""}
      ${duration ? `<span>${duration}</span>` : ""}
      <span>${changeLabel}</span>
      ${plan.partial ? "<span>prezzo parziale</span>" : ""}
      ${walkNote}
      ${walkNote2}
      ${partiDiCasa ? `<span>${partiDiCasa}</span>` : ""}
      ${doorToDoor && (walkOut || walkIn) ? `<span>porta a porta ${fmtDuration(doorToDoor)}</span>` : ""}
      ${covered ? `<span>coperto da ${esc(passNames || "abbonamento")}</span>` : ""}
      ${plan.transfer_km != null ? `<span>${plan.transfer_km} km a piedi nel cambio</span>` : ""}
    </div>
    ${steps ? `<div class="plan-steps">${steps}</div>` : ""}
    ${plan.explanation ? `<details class="plan-why">
      <summary>Perché questo piano</summary>
      <p class="plan-explanation">${esc(plan.explanation)}</p>
    </details>` : ""}
    ${watchouts ? `<div class="plan-watchouts">
      <span class="plan-label">Da tenere d’occhio</span>
      ${watchouts}
    </div>` : ""}
    ${renderConfronti(plan.confronti)}
    ${alts}`;
}

function renderQuestions() {
  const box = $("#questionsCard");
  const questions = state.questions || [];
  if (!questions.length) {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }
  box.classList.remove("hidden");
  box.innerHTML = `
    <h4>L'agente ha bisogno di un'informazione</h4>
    ${questions.map((question) => {
      const options = (question.options || []).map((option) =>
        `<button class="btn" data-qid="${question.id}" data-answer="${esc(option.id)}">${esc(option.label)}</button>`
      ).join("");
      const skip = `<button class="btn ghost" data-qid="${question.id}" data-answer="skip">Salta</button>`;
      // Risposta aperta: si scrive a parole, l'agente capisce e ricalcola.
      const libero = question.allow_free_text === false ? "" : `
        <form class="q-free" data-qid="${question.id}">
          <input type="text" name="answer" maxlength="300" autocomplete="off"
                 placeholder="Oppure scrivi qui la risposta…" />
          <button class="btn" type="submit">Invia</button>
        </form>`;
      return `<div class="q-item">
        <p>${esc(question.text)}</p>
        <div class="q-options">${options}${skip}</div>
        ${libero}
      </div>`;
    }).join("")}`;
}

async function answerQuestion(questionId, answer) {
  try {
    const endpoint = answer === "skip" ? "skip" : "answer";
    const options = answer === "skip"
      ? { method: "POST" }
      : { method: "POST", body: JSON.stringify({ answer, source: "web" }) };
    await api(`/api/questions/${questionId}/${endpoint}`, options);
    toast("Aggiorno la ricerca.");
    await refreshAll();
    setTimeout(refreshAll, 4000);
  } catch (error) {
    toast(error.message, "error");
  }
}

/* Risposta libera: l'agente interpreta la frase e se la usa come
   preferenza (un abbonamento, un budget, un giorno piu' tardi...). */
async function answerFree(questionId, form) {
  const input = form.querySelector("input[name=answer]");
  const text = (input?.value || "").trim();
  if (!text) {
    toast("Scrivi la risposta.", "error");
    input?.focus();
    return;
  }
  const send = form.querySelector("button[type=submit]");
  if (send) {
    send.disabled = true;
    send.textContent = "Invio…";
  }
  try {
    const result = await api(`/api/questions/${questionId}/answer`, {
      method: "POST",
      body: JSON.stringify({ answer: text, source: "web" }),
    });
    const note = result?.applied?.note;
    toast(note ? `Grazie: ${note}` : "Grazie: aggiorno la ricerca.");
    await refreshAll();
    setTimeout(refreshAll, 4000);
  } catch (error) {
    toast(error.message, "error");
    if (send) {
      send.disabled = false;
      send.textContent = "Invia";
    }
  }
}

function renderTab() {
  const panel = $("#tabPanel");
  if (!state.watch) return;
  if (state.tab === "best") renderBest(panel);
  else if (state.tab === "all") renderAll(panel);
  else if (state.tab === "map") renderMap(panel);
  else if (state.tab === "nearby") renderNearby(panel);
  else if (state.tab === "links") renderLinks(panel);
  else if (state.tab === "runs") renderRuns(panel);
  else if (state.tab === "ask") renderChat(panel);
  // Dissolvenza solo quando cambia davvero scheda: al cambio ricerca ci pensa
  // già l'ingresso della scheda (un solo movimento, non due sovrapposti).
  const chiave = `${state.watch.id}:${state.tab}`;
  if (state.renderedTab !== chiave && !state.schedaEntrata) anima(panel);
  state.renderedTab = chiave;
  state.schedaEntrata = false;
  // Con la chat aperta il pulsante flottante è ridondante: si chiude da "Chiudi".
  $("#chatFab")?.classList.toggle("hidden", state.tab === "ask");
}

function isInitialRun() {
  if (state.offers.length < 2) return false;
  const times = state.offers
    .map((o) => (o.first_seen ? new Date(o.first_seen).getTime() : 0))
    .filter((t) => t > 0);
  if (!times.length) return false;
  const spread = Math.max(...times) - Math.min(...times);
  return spread < 5 * 60 * 1000;
}

function offerCard(offer, suppressNew = false) {
  const badges = (offer.badges || []).filter((b) => BADGES[b]);
  const isNew = offer.first_seen && (Date.now() - new Date(offer.first_seen).getTime() < 2 * 3600 * 1000);
  if (isNew && !suppressNew && !badges.includes("new")) badges.unshift("new");

  // Una sola pastiglia per riga (il verdetto), gli altri aggettivi restano
  // testo semplice: cinque chip colorati facevano rumore senza informare.
  const VERDETTO = ["best_value", "cheapest", "spezzato", "coperto", "under_budget", "price_drop", "fastest", "closest"];
  const idxVerdetto = badges.findIndex((b) => VERDETTO.includes(b));
  const etichettaVerdetto = idxVerdetto >= 0 ? badges[idxVerdetto] : null;
  const altri = badges
    .filter((b, i) => i !== idxVerdetto)
    .map((b) => (b === "new" ? "Nuovo" : BADGES[b]));
  const tagHtml = etichettaVerdetto
    ? `<span class="tag ${etichettaVerdetto}">${esc(BADGES[etichettaVerdetto])}</span>`
    : "";
  const altriHtml = altri.length ? `<span class="tag-meta">${altri.map(esc).join(" · ")}</span>` : "";

  const dep = offer.depart_at ? fmtTime(offer.depart_at) : "—";
  const arr = offer.arrive_at ? fmtTime(offer.arrive_at) : "—";
  const dayLabel = offer.depart_at ? fmtDayFull(offer.depart_at) : "";
  const duration = fmtDuration(offer.duration_min);
  const pickupKm = offer.details?.km_pickup_a_piedi;
  const dropoffKm = offer.details?.km_dropoff_a_piedi;
  const effective = offer.details?.costo_effettivo;
  const tappe = Array.isArray(offer.details?.tappe)
    ? offer.details.tappe.filter((t) => t && (t.da || t.a))
    : [];

  const detailBits = [];
  // I minuti a piedi arrivano solo dal percorso reale delle mappe (il
  // backend li calcola quando l'utente ha scelto un punto esatto): qui non
  // si stima nulla.
  const walkKmReale = offer.details?.km_pickup_a_piedi;
  const walkMin = offer.details?.minuti_pickup_a_piedi;
  if (walkMin != null && walkMin > 0) {
    detailBits.push(
      `${walkMin} min a piedi fino al ritrovo` +
        (walkKmReale != null && walkKmReale > 0.05 ? ` (${walkKmReale} km)` : "")
    );
  }
  const changes = offer.details?.cambi;
  if (changes != null) {
    detailBits.push(
      changes === 0 ? "diretto, nessun cambio" : `${changes} ${changes === 1 ? "cambio" : "cambi"}`
    );
  }
  if (offer.seats) detailBits.push(`${offer.seats} posti`);
  if (offer.rating) detailBits.push(`valutazione ${Number(offer.rating).toFixed(1)}`);
  if (effective != null && offer.price != null && Math.abs(effective - offer.price) > 0.01) {
    detailBits.push(`costo effettivo ${euro(effective)}`);
  }
  if (offer.details?.partenza_alternativa) {
    detailBits.push(`partenza da ${esc(offer.details.partenza_alternativa)} (~${offer.details.distanza_partenza_km || 0} km)`);
  }
  if (offer.details?.arrivo_alternativo) {
    detailBits.push(`arrivo a ${esc(offer.details.arrivo_alternativo)} (~${offer.details.distanza_arrivo_km || 0} km)`);
  }
  if (offer.details?.tariffa) detailBits.push(esc(offer.details.tariffa));
  if (offer.details?.disponibilita) detailBits.push(esc(offer.details.disponibilita));
  if (offer.details?.spezzatura) {
    detailBits.push(
      `2 biglietti invece di 1: risparmi ${euro(offer.details.risparmio)} ` +
      `(diretto ${euro(offer.details.prezzo_diretto)})`
    );
  }
  if (offer.details?.pass) {
    const tariffa = offer.details.pass_tariffa;
    if (tariffa) {
      // Pass a tariffa chilometrica: non e' gratis, si paga il km.
      detailBits.push(
        `${esc(offer.details.pass)}: ${esc(tariffa)}, ` +
        `${offer.details.pass_km} km tariffati (~${euro(offer.details.pass_costo_km)})`
      );
    } else {
      const quota = offer.details.pass_quota;
      const quanto = quota != null && quota < 100 ? ` al ${quota}%` : "";
      const parz = offer.details.pass_note_parziale
        ? ` · ${offer.details.pass_note_parziale}`
        : "";
      detailBits.push(
        `coperto${quanto} da ${esc(offer.details.pass)}${parz}: non lo paghi`
      );
    }
  }

  // Con un pass a tariffa chilometrica si paga il km: il prezzo vero e'
  // quello con l'abbonamento, quello del biglietto resta come riferimento.
  const nettoKm = offer.details?.pass_tariffa ? offer.details.net_cost : null;
  const priceHtml = offer.price == null
    ? (offer.details?.orario_gtfs
        ? `<div class="price plain">solo orario<small>prezzo da tariffa ufficiale</small></div>`
        : `<div class="price plain">da verificare</div>`)
    : nettoKm != null && nettoKm < offer.price
    ? `<div class="price"><span class="was">${euro(offer.price)}</span>${euro(nettoKm)}
        <small>con il tuo pass, il km</small>
      </div>`
    : `<div class="price">${offer.prev_price && offer.prev_price > offer.price
        ? `<span class="was">${euro(offer.prev_price)}</span>` : ""}${euro(offer.price)}
        ${offer.price_is_estimate ? "<small>stima, non prezzo finale</small>" : ""}
      </div>`;

  return `
    <article class="offer ${offer.price_is_estimate ? "estimate" : ""}">
      <div class="offer-main">
        <div class="offer-top">
          <span class="operator">${esc(offer.operator || offer.source)}</span>
          <span class="source-tag">${esc(MODE_LABEL[offer.mode] || offer.mode)}</span>
          ${tagHtml}
          ${altriHtml}
        </div>
        <div class="times">
          <strong>${dep}</strong><span class="sep">→</span><strong>${arr}</strong>
          <span class="dur">${dayLabel}${duration ? " · " + duration : ""}</span>
        </div>
        <div class="places">
          <span>↑ ${esc(offer.pickup_name || "—")}${pickupKm != null && pickupKm > 0.05 ? ` <span class="km">· ${pickupKm} km</span>` : ""}</span>
          <span>↓ ${esc(offer.dropoff_name || "—")}${dropoffKm != null && dropoffKm > 0.05 ? ` <span class="km">· ${dropoffKm} km</span>` : ""}</span>
        </div>
        ${detailBits.length ? `<div class="details-line">${detailBits.map((b) => `<span>${b}</span>`).join("")}</div>` : ""}
        ${tappe.length > 1 ? `
          <details class="tappe">
            <summary>${tappe.length} tratte · dove ferma e cambia</summary>
            <ol>${tappe.map((t, i) => `
              <li>
                <span class="tappe-n">${i + 1}</span>
                <span class="tappe-place"><b>${esc(t.da || "—")}</b><span class="arrow">→</span><b>${esc(t.a || "—")}</b></span>
                <span class="tappe-when">${t.partenza ? fmtTime(t.partenza) : ""}${t.arrivo ? " – " + fmtTime(t.arrivo) : ""}${t.mezzo ? " · " + esc(t.mezzo) : ""}</span>
              </li>`).join("")}</ol>
          </details>` : ""}
      </div>
      <div class="offer-side">
        ${priceHtml}
        ${offer.score != null ? `<div class="score">punteggio <b>${Math.round(offer.score)}</b>/100</div>` : ""}
        ${offer.deeplink ? `<a class="ext-link" href="${esc(offer.deeplink)}" target="_blank" rel="noreferrer">apri ↗</a>` : ""}
      </div>
    </article>`;
}

function renderBest(panel) {
  const offers = state.offers
    .filter((o) => o.mode !== "transit" && o.score != null)
    .sort((a, b) => (b.score || 0) - (a.score || 0));

  if (!offers.length) {
    const notes = (state.runs[0]?.notes || []).slice(0, 4);
    panel.innerHTML = emptyTab(
      "Ancora nessuna offerta con prezzo",
      "L'agente controlla ogni ora. Puoi chiederne uno adesso: «Cerca ora».",
      true,
      notes
    );
    return;
  }
  const initial = isInitialRun();
  panel.innerHTML = `<div class="offers">${offers.map((o) => offerCard(o, initial)).join("")}</div>`;
}

function renderAll(panel) {
  if (!state.offers.length) {
    panel.innerHTML = emptyTab("Nessuna offerta salvata", "Avvia una ricerca o allarga il raggio o il budget.");
    return;
  }
  const groups = {};
  for (const offer of state.offers) {
    (groups[offer.mode] ||= []).push(offer);
  }
  const order = ["rideshare", "bus", "train", "tram", "flight", "carsharing", "transit"];
  const initial = isInitialRun();
  const html = order.filter((mode) => groups[mode]?.length).map((mode) => `
    <div class="group-title">${esc(MODE_LABEL[mode] || mode)} · ${groups[mode].length}</div>
    <div class="offers">${groups[mode]
      .sort((a, b) => (b.score || 0) - (a.score || 0))
      .map((o) => offerCard(o, initial)).join("")}</div>
  `).join("");
  panel.innerHTML = html;
}

function renderNearby(panel) {
  const offers = state.offers.filter((o) => o.mode === "carsharing" || o.mode === "transit");
  if (!offers.length) {
    panel.innerHTML = emptyTab(
      "Nessuna alternativa vicina",
      "Il carsharing compare solo dove il provider copre la tratta; le fermate vicine arrivano se la fonte è attiva."
    );
    return;
  }
  panel.innerHTML = `<div class="offers">${offers.map((o) => offerCard(o, isInitialRun())).join("")}</div>`;
}

let mapView = null;
let mapLayer = null;

function ensureMap(canvas) {
  if (mapView && mapView.getContainer() !== canvas) {
    mapView.remove();
    mapView = null;
  }
  if (!mapView) {
    mapView = L.map(canvas, { zoomControl: true });
    // Tile scuri: la mappa fa parte del sistema, non un'isola chiara.
    // Base (Esri Dark Gray Canvas) + un livello di etichette sopra.
    L.tileLayer(token("--map-tile-url"), {
      maxZoom: 19,
      attribution: token("--map-attribution"),
    }).addTo(mapView);
    L.tileLayer(token("--map-tile-label-url"), {
      maxZoom: 19,
      interactive: false,
      zIndex: 2,
    }).addTo(mapView);
  }
  return mapView;
}

async function renderMap(panel) {
  if (typeof L === "undefined") {
    panel.innerHTML = emptyTab(
      "Mappa non disponibile",
      "La mappa non si è caricata: controlla la connessione e ricarica la pagina."
    );
    return;
  }
  if (!state.carsharingZones) {
    try {
      const data = await api("/api/carsharing/zones");
      state.carsharingZones = data.zones || [];
    } catch (_) {
      state.carsharingZones = [];
    }
  }
  panel.innerHTML = `
    <div class="map-toolbar">
      <label class="map-toggle"><input type="checkbox" id="mapZones" ${state.showZones ? "checked" : ""} /> Zone carsharing</label>
      <span class="map-hint" id="mapLegend"></span>
    </div>
    <div class="map-canvas" id="mapCanvas"></div>`;
  $("#mapZones")?.addEventListener("change", (event) => {
    state.showZones = event.target.checked;
    drawMap();
  });

  const map = ensureMap($("#mapCanvas"));
  map.invalidateSize();
  drawMap();
}

function drawMap() {
  if (!mapView) return;
  if (mapLayer) mapView.removeLayer(mapLayer);
  mapLayer = L.layerGroup().addTo(mapView);

  const bounds = [];
  const watch = state.watch || {};
  const origin = [watch.from_lat, watch.from_lon];
  const dest = [watch.to_lat, watch.to_lon];

  const dot = (lat, lon, color, radius) =>
    L.circleMarker([lat, lon], {
      radius,
      color,
      weight: 2,
      fillColor: color,
      fillOpacity: 0.85,
    });

  if (origin[0] != null && origin[1] != null) {
    dot(origin[0], origin[1], token("--map-origin"), 9)
      .bindPopup(`<strong>Partenza</strong><br>${esc(watch.from_place || "")}`)
      .addTo(mapLayer);
    bounds.push(origin);
  }
  if (dest[0] != null && dest[1] != null) {
    dot(dest[0], dest[1], token("--map-dest"), 9)
      .bindPopup(`<strong>Arrivo</strong><br>${esc(watch.to_place || "")}`)
      .addTo(mapLayer);
    bounds.push(dest);
  }

  let placed = 0;
  for (const offer of state.offers.slice(0, MAX_MAP_OFFERS)) {
    const a = [offer.pickup_lat, offer.pickup_lon];
    const b = [offer.dropoff_lat, offer.dropoff_lon];
    if (a[0] == null || a[1] == null || b[0] == null || b[1] == null) continue;
    const color = token(MODE_COLOR[offer.mode] || "--map-transit");
    L.polyline([a, b], { color, weight: 2, opacity: 0.55, dashArray: "5,6" }).addTo(mapLayer);
    const label = `<strong>${esc(MODE_LABEL[offer.mode] || offer.mode)}</strong><br>${esc(offer.operator || "")}`;
    const price = offer.price != null ? `${euro(offer.price)}${offer.price_is_estimate ? " (stima)" : ""}` : "solo orario";
    dot(a[0], a[1], color, 6)
      .bindPopup(`${label}<br>Partenza: ${fmtTime(offer.depart_at)}<br>${price}`)
      .addTo(mapLayer);
    dot(b[0], b[1], color, 4).bindPopup(`${label}<br>Arrivo: ${fmtTime(offer.arrive_at)}`).addTo(mapLayer);
    bounds.push(a, b);
    placed += 1;
  }

  if (state.showZones) {
    for (const zone of state.carsharingZones || []) {
      L.circle([zone.lat, zone.lon], {
        radius: 9000,
        color: token("--map-zone"),
        weight: 1,
        fillColor: token("--map-zone"),
        fillOpacity: 0.08,
      })
        .bindPopup(`<strong>${esc(zone.provider)}</strong><br>Carsharing a ${esc(zone.city)}`)
        .addTo(mapLayer);
    }
  }

  if (bounds.length >= 2) {
    mapView.fitBounds(bounds, { padding: [30, 30], maxZoom: 12 });
  } else if (bounds.length === 1) {
    mapView.setView(bounds[0], 11);
  } else {
    mapView.setView([42.5, 12.5], 5);
  }

  const zones = state.showZones ? (state.carsharingZones || []).length : 0;
  $("#mapLegend").textContent = `${placed} offerte sulla mappa · ${zones} zone carsharing`;

  setTimeout(() => mapView && mapView.invalidateSize(), 60);
}

function renderLinks(panel) {
  if (!state.links.length) {
    panel.innerHTML = emptyTab("Nessun link", "Aggiungi una ricerca per generare i link.");
    return;
  }
  panel.innerHTML = `<div class="links-grid">${state.links.map((link) => `
    <a class="link-card" href="${esc(link.url)}" target="_blank" rel="noreferrer">
      <div class="lc-head"><strong>${esc(link.label)}</strong><span class="lc-arrow">↗</span></div>
      <small>${esc(link.note || "")}</small>
    </a>`).join("")}</div>`;
}

function renderRuns(panel) {
  if (!state.runs.length) {
    panel.innerHTML = emptyTab("Nessun controllo", "La cronologia dei controlli apparirà dopo il primo giro.");
    return;
  }
  panel.innerHTML = `<div>${state.runs.map((run) => `
    <div class="run-row">
      <span>${fmtDate(run.started_at)}</span>
      <span class="run-status ${esc(run.status)}">${esc(run.status)}</span>
      <span>${run.offers_found} offerte · ${run.alerts_sent} avvisi</span>
            <span class="run-errors" title="${esc((run.errors || []).join(" · "))}">
        ${run.errors?.length ? esc(run.errors.join(" · ")) : (run.notes?.length ? esc(run.notes.join(" · ")) : `(${esc(run.trigger)})`)}
      </span>
    </div>`).join("")}</div>`;
}

function emptyTab(title, text, withLinks = false, notes = []) {
  return `<div class="empty-tab">
    <h2>${esc(title)}</h2>
    <p>${esc(text)}</p>
    ${notes.length ? `<div class="src-notes">${notes.map((n) => `<span>${esc(n)}</span>`).join("")}</div>` : ""}
    <div class="empty-tab-actions">
      <button class="btn" onclick="runNow()">Cerca ora</button>
      ${withLinks ? `<button class="btn ghost" onclick="document.querySelector('[data-tab=links]').click()">Link utili</button>` : ""}
    </div></div>`;
}

/* ------------------------------------------------------------------ */
/* Azioni sulla ricerca                                               */
/* ------------------------------------------------------------------ */
async function runNow() {
  if (!state.watch) return;
  try {
    toast("Controllo in corso…");
    await api(`/api/watches/${state.watch.id}/run`, { method: "POST" });
    setTimeout(refreshAll, 2500);
    setTimeout(refreshAll, 7000);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function toggleWatch() {
  if (!state.watch) return;
  try {
    await api(`/api/watches/${state.watch.id}/toggle`, { method: "POST" });
    await loadWatches();
    await loadDetail(state.watch.id);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function deleteWatch() {
  if (!state.watch) return;
  const w = state.watch;
  const ok = await chiediConferma({
    titolo: "Eliminare la ricerca?",
    testo: `La ricerca <b>${esc(w.from_place)} → ${esc(w.to_place)}</b> e le sue offerte vengono cancellate. Non si può annullare.`,
    conferma: "Elimina",
    danger: true,
    digita: `${w.from_place} → ${w.to_place}`,
  });
  if (!ok) return;
  try {
    await api(`/api/watches/${w.id}`, { method: "DELETE" });
    state.selectedId = null;
    state.watch = null;
    await loadWatches();
    if (state.watches.length) await selectWatch(state.watches[0].id);
    else showEmpty(true);
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ------------------------------------------------------------------ */
/* Modale ricerca                                                     */
/* ------------------------------------------------------------------ */
function openWatchModal(watch) {
  state.editingId = watch?.id || null;
  $("#watchModalTitle").textContent = watch ? "Modifica ricerca" : "Nuova ricerca";
  $("#btnSaveWatch").textContent = watch ? "Salva modifiche" : "Avvia ricerca";
  $("#watchError").classList.add("hidden");

  const defaults = {
    name: "", from_place: "", to_place: "",
    depart_after: "", arrive_by: "", max_price: "", passengers: 1, radius_km: 5,
    interval_min: 60,
    sources: ["blablacar", "flixbus", "itabus", "trenitalia", "omio", "carsharing", "transit", "gtfs"],
    notify: ["web", "telegram"],
  };

  const data = watch ? {
    ...watch,
    depart_after: toLocalInput(watch.depart_after),
    arrive_by: toLocalInput(watch.arrive_by),
    max_price: watch.max_price ?? "",
  } : defaults;

  if (!watch) {
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    tomorrow.setHours(18, 0, 0, 0);
    const morning = new Date(tomorrow);
    morning.setHours(8, 0, 0, 0);
    data.arrive_by = toLocalInput(tomorrow.toISOString());
    data.depart_after = toLocalInput(morning.toISOString());
  }

  $("#fName").value = data.name || "";
  // La cartella: le esistenti, più la voce per crearne una nuova.
  const selezione = $("#fGroup");
  selezione.innerHTML = `<option value="">— nessuna —</option>` +
    state.groups
      .map((g) => `<option value="${g.id}">${esc(g.name)}</option>`)
      .join("") +
    `<option value="new">Nuova cartella…</option>`;
  selezione.value = watch && watch.group_id ? String(watch.group_id) : "";
  $("#fGroupNew").classList.add("hidden");
  $("#fGroupNew").value = "";
  $("#fFrom").value = data.from_place || "";
  $("#fTo").value = data.to_place || "";
  $("#fDepart").value = data.depart_after || "";
  $("#fArrive").value = data.arrive_by || "";
  $("#fPrice").value = data.max_price ?? "";
  $("#fFromPoint").value = data.from_point || "";
  $("#fToPoint").value = data.to_point || "";
  $("#fPassengers").value = data.passengers || 1;
  $("#fRadius").value = data.radius_km || 5;
  $("#fInterval").value = String(data.interval_min || 60);
  $("#geoFrom").textContent = "";
  $("#geoTo").textContent = "";
  $("#fAiText").value = "";
  $("#aiFillHint").textContent = "";
  $("#aiFillHint").className = "field-hint";
  const formQuestions = $("#formQuestions");
  if (formQuestions) formQuestions.remove();

  const selectedSources = data.sources || defaults.sources;
  $("#sourcesBox").innerHTML = state.sources.map((source) => {
    const checked = selectedSources.includes(source.id) ? "checked" : "";
    const soon = source.requires ? `<span class="soon">link</span>` : "";
    return `<label class="chip" title="${esc(source.description)}">
      <input type="checkbox" name="source" value="${esc(source.id)}" ${checked} />
      ${esc(source.label)} ${soon}
    </label>`;
  }).join("");

  const notify = data.notify || defaults.notify;
  $$("#notifyBox input[name=notify]").forEach((input) => {
    input.checked = notify.includes(input.value);
  });

  // Abbonamenti: il catalogo viene dall'API, con i selezionati della ricerca.
  const currentPasses = (data.prefs && data.prefs.passes) || [];
  const selectedPasses = currentPasses.map((p) => (typeof p === "string" ? p : p.tipo));
  renderPassPicker($("#passesBox"), selectedPasses, "pass");

  openModal("watchModal");
  setTimeout(() => $("#fFrom").focus(), 60);
}

async function aiFill() {
  const text = $("#fAiText").value.trim();
  const hint = $("#aiFillHint");
  if (!text) {
    hint.textContent = "Scrivi prima una frase sul viaggio.";
    hint.className = "field-hint err";
    return;
  }
  const button = $("#btnAiFill");
  button.disabled = true;
  hint.textContent = "L'agente sta leggendo…";
  hint.className = "field-hint";
  try {
    const result = await api("/api/ai/parse", { method: "POST", body: JSON.stringify({ text }) });
    const trip = result.trip || {};
    if (trip.from_place) $("#fFrom").value = trip.from_place;
    if (trip.to_place) $("#fTo").value = trip.to_place;
    if (trip.arrive_by) $("#fArrive").value = toLocalInput(trip.arrive_by);
    if (trip.depart_after) $("#fDepart").value = toLocalInput(trip.depart_after);
    if (trip.max_price != null) $("#fPrice").value = trip.max_price;
    if (trip.passengers) $("#fPassengers").value = trip.passengers;
    if (trip.radius_km) $("#fRadius").value = trip.radius_km;
    if (trip.notes && !$("#fName").value) $("#fName").value = trip.notes.slice(0, 120);

    const missing = result.missing || [];
    const questions = result.questions || [];
    if (missing.length) {
      hint.textContent = "Manca ancora: " + missing.map(fieldLabel).join(", ") + ".";
      hint.className = "field-hint err";
    } else {
      hint.textContent = "Compilato. Controlla i campi e premi Avvia ricerca.";
      hint.className = "field-hint ok";
    }
    if (questions.length) {
      renderFormQuestions(questions);
    }
  } catch (error) {
    hint.textContent = error.message;
    hint.className = "field-hint err";
  } finally {
    button.disabled = false;
  }
}

function fieldLabel(name) {
  return { from_place: "partenza", to_place: "destinazione", arrive_by: "orario di arrivo" }[name] || name;
}

function renderFormQuestions(questions) {
  let box = $("#formQuestions");
  if (!box) {
    box = document.createElement("div");
    box.id = "formQuestions";
    box.className = "questions-card";
    $("#aiFillHint").closest(".ai-box").appendChild(box);
  }
  box.innerHTML = questions.map((question) => `
    <div class="q-item">
      <p>${esc(question.text)}</p>
      <div class="q-options">
        ${(question.options || []).map((option) =>
          `<button type="button" class="btn" data-fill="${esc(option.label)}">${esc(option.label)}</button>`
        ).join("")}
      </div>
    </div>`).join("");
  box.querySelectorAll("[data-fill]").forEach((button) => {
    button.addEventListener("click", () => {
      $("#fAiText").value = ($("#fAiText").value + " " + button.dataset.fill).trim();
      box.remove();
      aiFill();
    });
  });
}

async function checkGeo(fieldId) {
  const input = $("#" + fieldId);
  const hint = $(fieldId === "fFrom" ? "#geoFrom" : "#geoTo");
  const query = input.value.trim();
  if (!query) return;
  hint.textContent = "cerco…";
  hint.className = "field-hint";
  try {
    const result = await api("/api/geocode?q=" + encodeURIComponent(query));
    if (!result.found) {
      hint.textContent = "località non trovata, prova con «Città» o «Città Stazione»";
      hint.className = "field-hint err";
      return;
    }
    const short = (result.name || query).split(",").slice(0, 2).join(",");
    hint.textContent = `trovato: ${short} (${result.lat.toFixed(3)}, ${result.lon.toFixed(3)})`;
    hint.className = "field-hint ok";
  } catch (error) {
    hint.textContent = error.message;
    hint.className = "field-hint err";
  }
}

async function submitWatch(event) {
  event.preventDefault();
  const errorBox = $("#watchError");
  errorBox.classList.add("hidden");

  const fromPlace = $("#fFrom").value.trim();
  const toPlace = $("#fTo").value.trim();
  const arriveLocal = $("#fArrive").value;
  const departLocal = $("#fDepart").value;

  const fail = (message) => {
    errorBox.textContent = message;
    errorBox.classList.remove("hidden");
  };

  if (!fromPlace || !toPlace) return fail("Inserisci partenza e arrivo.");
  if (!arriveLocal) return fail("Indica entro quando devi arrivare.");
  if (departLocal && new Date(departLocal) >= new Date(arriveLocal)) {
    return fail("La partenza minima deve essere precedente all'arrivo massimo.");
  }

  const sources = $$("#sourcesBox input[name=source]:checked").map((i) => i.value);
  if (!sources.length) return fail("Seleziona almeno una fonte.");
  const notify = $$("#notifyBox input[name=notify]:checked").map((i) => i.value);
  const passTypes = $$("#passesBox input[name=pass]:checked").map((i) => i.value);

  const payload = {
    name: $("#fName").value.trim() || null,
    from_place: fromPlace,
    to_place: toPlace,
    depart_after: departLocal ? new Date(departLocal).toISOString() : null,
    arrive_by: new Date(arriveLocal).toISOString(),
    max_price: $("#fPrice").value ? Number($("#fPrice").value) : null,
    from_point: $("#fFromPoint").value.trim() || null,
    to_point: $("#fToPoint").value.trim() || null,
    passengers: Number($("#fPassengers").value || 1),
    radius_km: Number($("#fRadius").value || 5),
    interval_min: Number($("#fInterval").value || 60),
    sources,
    notify,
    active: true,
  };
  // La cartella: una nuova si crea qui, con il nome scritto nel campo.
  const sceltaCartella = $("#fGroup").value;
  if (sceltaCartella === "new") {
    const nome = $("#fGroupNew").value.trim();
    if (!nome) return fail("Scrivi il nome della cartella.");
    try {
      const cartella = await creaCartella(nome);
      payload.group_id = cartella.id;
    } catch (error) {
      return fail(error.message);
    }
  } else if (sceltaCartella) {
    payload.group_id = Number(sceltaCartella);
  }
  // Gli abbonamenti sono parte delle preferenze: il resto si conserva.
  // Si manda sempre (anche vuota): togliere un abbonamento deve valere.
  payload.prefs = { passes: passTypes.map((tipo) => ({ tipo })) };

  const button = $("#btnSaveWatch");
  button.disabled = true;
  button.textContent = "Salvo…";
  try {
    let watch;
    if (state.editingId) {
      watch = await api(`/api/watches/${state.editingId}`, {
        method: "PATCH", body: JSON.stringify(payload),
      });
    } else {
      watch = await api("/api/watches", { method: "POST", body: JSON.stringify(payload) });
    }
    closeModal("watchModal");
    await loadWatches();
    await selectWatch(watch.id);
    toast(state.editingId ? "Ricerca aggiornata" : "Controllo in corso: le prime offerte arrivano tra poco.");
    setTimeout(refreshAll, 3000);
    setTimeout(refreshAll, 8000);
  } catch (error) {
    fail(error.message);
  } finally {
    button.disabled = false;
    button.textContent = state.editingId ? "Salva modifiche" : "Avvia ricerca";
  }
}

/* ------------------------------------------------------------------ */
/* Impostazioni                                                       */
/* ------------------------------------------------------------------ */
async function loadSettings() {
  state.settings = await api("/api/settings");
}

async function openSettings() {
  try {
    await loadSettings();
    const s = state.settings;
    const isAdmin = !!state.user?.is_admin;

    // Le impostazioni comuni (Groq, GTFS) si vedono solo da amministratore.
    ["#sGroqKey", "#sAiSmart", "#sAiFast", "#sAiEnabled", "#sGtfsEnabled", "#btnGtfsUpdate",
     "#btnTestAi", "#btnAiModels"].forEach((sel) => {
      const el = $(sel);
      if (el) el.closest(".field, .field.inline-field, .gtfs-feeds")?.classList.toggle("hidden", !isAdmin);
    });
    $$(".admin-only").forEach((el) => el.classList.toggle("hidden", !isAdmin));

    renderAccessInfo();
    loadTelegramChats();

    impostaValore("#sTelegramToken", s.telegram_token);
    impostaValore("#sTelegramChat", s.telegram_chat_id);
    impostaValore("#sXgo", s.xgo);
    impostaValore("#sNgrokToken", s.ngrok_authtoken);
    impostaValore("#sNgrokDomain", s.ngrok_domain);
    // Gli abbonamenti globali dell'utente.
    let globalPasses = [];
    try {
      globalPasses = JSON.parse(s.passes || "[]");
    } catch (_) {
      globalPasses = [];
    }
    if (globalPasses && !Array.isArray(globalPasses)) globalPasses = [];
    const selezionati = globalPasses.map((p) => (typeof p === "string" ? p : p.tipo));
    renderPassPicker($("#globalPassesBox"), selezionati, "globalpass");
    impostaChecked("#sNotifyEnabled", s.notify_enabled, "1");
    impostaValore("#sQuietFrom", s.quiet_from);
    impostaValore("#sQuietTo", s.quiet_to);
    impostaValore("#sGroqKey", s.groq_api_key);
    impostaChecked("#sAiEnabled", s.ai_enabled, "1");
    impostaChecked("#sGtfsEnabled", s.gtfs_enabled, "1");
    impostaTesto("#aiTestResult", "");
    $("#settingsError")?.classList.add("hidden");
    impostaTesto("#telegramTestResult", "");
    impostaTesto("#telegramLinkResult", "");
    loadGtfsStatus();
    renderPushStatus();
    openModal("settingsModal");
    // I modelli si caricano dopo aver aperto il pannello, cosi' la
    // finestra non resta in attesa.
    loadModelOptions(s.ai_model_smart, s.ai_model_fast);
  } catch (error) {
    toast(error.message, "error");
  }
}

/* Popola i menu dei modelli Groq.
   - se il modello salvato non e' piu' disponibile, resta comunque
     selezionabile (con un avviso): il free tier cambia spesso; */
let _modelDetails = [];

function fillModelSelect(select, selected) {
  const options = [`<option value="">— modello predefinito —</option>`];
  for (const model of _modelDetails) {
    const ctx = model.context_length
      ? ` · ${Math.round(model.context_length / 1000)}k ctx`
      : "";
    const mark = model.id === selected ? " (attivo)" : "";
    options.push(
      `<option value="${esc(model.id)}"${model.id === selected ? " selected" : ""}>` +
        `${esc(model.id)}${esc(ctx)}${mark}</option>`
    );
  }
  select.innerHTML = options.join("");
  if (selected && !_modelDetails.some((m) => m.id === selected)) {
    // Il modello salvato non e' fra quelli disponibili: resta selezionabile
    // (il free tier di Groq cambia spesso), ma con un avviso.
    const extra = document.createElement("option");
    extra.value = selected;
    extra.textContent = `${selected} (non disponibile ora)`;
    select.prepend(extra);
  }
}

async function loadModelOptions(smart, fast) {
  const hint = $("#aiTestResult");
  const smartSelect = $("#sAiSmart");
  const fastSelect = $("#sAiFast");
  smartSelect.disabled = true;
  fastSelect.disabled = true;
  try {
    const result = await api("/api/ai/models");
    _modelDetails = result.details || (result.models || []).map((id) => ({ id, context_length: 0 }));
    fillModelSelect(smartSelect, smart || result.model_smart);
    fillModelSelect(fastSelect, fast || result.model_fast);
    smartSelect.disabled = false;
    fastSelect.disabled = false;
    const count = _modelDetails.length;
    hint.textContent = count
      ? `${count} modelli disponibili.`
      : "Nessun modello leggibile (controlla la chiave Groq).";
    hint.className = "field-hint";
  } catch (error) {
    // Senza elenco i select restano con il solo default: si puo' comunque
    // scrivere un id a mano.
    smartSelect.innerHTML = `<option value="">— modello predefinito —</option>`;
    fastSelect.innerHTML = `<option value="">— modello predefinito —</option>`;
    smartSelect.disabled = false;
    fastSelect.disabled = false;
    hint.textContent = "Errore: " + error.message;
    hint.className = "field-hint err";
  }
}

/* Un campo che non esiste (o e' vuoto) non deve rompere il salvataggio. */
function val(selettore) {
  const elemento = $(selettore);
  return elemento ? String(elemento.value || "").trim() : "";
}

function checked(selettore) {
  const elemento = $(selettore);
  return elemento ? !!elemento.checked : false;
}

/* Scrive un valore solo se il campo esiste davvero. */
function impostaValore(selettore, valore) {
  const elemento = $(selettore);
  if (elemento) elemento.value = valore || "";
}

/* Spunta una casella solo se il campo esiste davvero. */
function impostaChecked(selettore, valore, predefinito) {
  const elemento = $(selettore);
  if (elemento) elemento.checked = (valore ?? predefinito) === "1";
}

/* Scrive un testo solo se l'etichetta esiste davvero. */
function impostaTesto(selettore, testo) {
  const elemento = $(selettore);
  if (elemento) elemento.textContent = testo;
}

function settingsPayload() {
  // Le impostazioni personali vanno sempre; quelle comuni (Groq, modelli,
  // GTFS) solo se l'utente e' amministratore.
  const personal = {
    telegram_token: val("#sTelegramToken"),
    telegram_chat_id: val("#sTelegramChat"),
    quiet_from: val("#sQuietFrom"),
    quiet_to: val("#sQuietTo"),
    notify_enabled: checked("#sNotifyEnabled") ? "1" : "0",
  };
  if (!state.user?.is_admin) return personal;
  return {
    ...personal,
    groq_api_key: val("#sGroqKey"),
    ngrok_authtoken: val("#sNgrokToken"),
    ngrok_domain: val("#sNgrokDomain"),
    xgo: val("#sXgo"),
    // Gli abbonamenti globali: una lista JSON di {tipo}.
    passes: JSON.stringify(
      $$("#globalPassesBox input[name=globalpass]:checked").map((i) => ({ tipo: i.value }))
    ),
    // Stringa vuota = torna al modello predefinito dell'app.
    ai_model_smart: $("#sAiSmart").value,
    ai_model_fast: $("#sAiFast").value,
    ai_enabled: $("#sAiEnabled").checked ? "1" : "0",
    gtfs_enabled: $("#sGtfsEnabled").checked ? "1" : "0",
  };
}

async function submitSettings(event) {
  event.preventDefault();
  const errorBox = $("#settingsError");
  errorBox.classList.add("hidden");
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify(settingsPayload()) });
    closeModal("settingsModal");
    toast("Impostazioni salvate");
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.classList.remove("hidden");
  }
}

/* ---------- accesso: password e chiave segreta ---------- */
function renderAccessInfo() {
  const user = state.user || {};
  const telegram = user.telegram_chat_id ? "collegata" : "non collegata";
  const access = $("#accessInfo");
  if (access) {
    access.textContent =
      `Sei dentro come ${user.display_name || user.username || "proprietario"}` +
      ` · Telegram ${telegram}`;
  }
  const key = $("#keyInfo");
  if (key) {
    key.textContent = state.hasKey
      ? "C'e' una chiave segreta: mettila nell'URL per entrare con un tocco."
      : "Nessuna chiave. Generane una per entrare con un tocco.";
  }
}

async function setAccessPassword() {
  const hint = $("#accessResult");
  const password = $("#sAccessPassword").value;
  if (!password) {
    hint.textContent = "Scrivi la nuova password.";
    return;
  }
  try {
    const result = await api("/api/auth/access", {
      method: "POST",
      body: JSON.stringify({ password }),
    });
    hint.textContent = result.message || "Password impostata.";
    $("#sAccessPassword").value = "";
    state.hasKey = state.hasKey ?? false;
    renderAccessInfo();
  } catch (error) {
    hint.textContent = error.message;
  }
}

async function clearAccessPassword() {
  const hint = $("#accessResult");
  try {
    const result = await api("/api/auth/access", {
      method: "POST",
      body: JSON.stringify({ password: "" }),
    });
    hint.textContent = result.message || "Password rimossa.";
    renderAccessInfo();
  } catch (error) {
    hint.textContent = error.message;
  }
}

async function generateKey() {
  const hint = $("#accessResult");
  try {
    const result = await api("/api/auth/key", { method: "POST" });
    $("#keyValue").value = result.key;
    $("#keyBox").classList.remove("hidden");
    hint.textContent = result.message || "Chiave generata.";
    state.hasKey = true;
    renderAccessInfo();
  } catch (error) {
    hint.textContent = error.message;
  }
}

async function deleteKey() {
  const hint = $("#accessResult");
  try {
    const result = await api("/api/auth/key", { method: "DELETE" });
    $("#keyBox").classList.add("hidden");
    $("#keyValue").value = "";
    hint.textContent = result.message || "Chiave rimossa.";
    state.hasKey = false;
    renderAccessInfo();
  } catch (error) {
    hint.textContent = error.message;
  }
}

/* ---------- collegamento della chat Telegram ---------- */
async function telegramLink() {
  const hint = $("#telegramLinkResult");
  try {
    const result = await api("/api/auth/telegram/link", { method: "POST" });
    $("#telegramLinkCode").value = result.code;
    hint.textContent =
      result.message + " Poi scrivi al bot: /link " + result.code;
  } catch (error) {
    hint.textContent = error.message;
  }
}

async function telegramConfirm() {
  const hint = $("#telegramLinkResult");
  const code = $("#telegramLinkCode").value.trim();
  if (!code) {
    hint.textContent = "Genera prima il codice.";
    return;
  }
  try {
    const result = await api("/api/auth/telegram/confirm", {
      method: "POST",
      body: JSON.stringify({ code }),
    });
    hint.textContent = "Chat collegata.";
    if (state.user) state.user.telegram_chat_id = result.telegram_chat_id;
    renderAccessInfo();
    loadTelegramChats();
  } catch (error) {
    hint.textContent = error.message;
  }
}

/* ---------- chat e gruppi Telegram ---------- */
async function loadTelegramChats() {
  const list = $("#telegramChatsList");
  const info = $("#telegramChatsInfo");
  if (!list) return;
  try {
    const data = await api("/api/telegram/chats");
    const chats = data.chats || [];
    if (data.add_url) {
      $("#telegramAddBotBox")?.classList.remove("hidden");
      const link = $("#telegramAddBot");
      if (link) link.href = data.add_url;
    }
    if (!chats.length) {
      info.textContent =
        "Nessuna chat registrata. Aggiungimi a un gruppo (o a un canale): mi registro da solo e ti scrivo li'.";
      list.innerHTML = "";
      return;
    }
    info.textContent = `${chats.length} chat registrate. Gli avvisi arrivano in tutte, tranne se ne scegli una predefinita.`;
    list.innerHTML = chats
      .map(
        (chat) => `
        <div class="user-row">
          <span>${esc(chat.title || chat.username || chat.chat_id)}
            <small>${esc(chat.chat_type || "")} · ${esc(chat.chat_id)}${chat.is_default ? " · predefinita" : ""}</small>
          </span>
          <span>
            ${chat.is_default ? "" : `<button type="button" class="btn ghost tiny" data-tg-default="${esc(chat.chat_id)}">Predefinita</button>`}
            <button type="button" class="btn ghost tiny" data-tg-remove="${esc(chat.chat_id)}">Togli</button>
          </span>
        </div>`
      )
      .join("");

    list.querySelectorAll("[data-tg-default]").forEach((button) =>
      button.addEventListener("click", async () => {
        try {
          await api(`/api/telegram/chats/${encodeURIComponent(button.dataset.tgDefault)}/default`, {
            method: "POST",
          });
          loadTelegramChats();
        } catch (error) {
          toast(error.message, "error");
        }
      })
    );
    list.querySelectorAll("[data-tg-remove]").forEach((button) =>
      button.addEventListener("click", async () => {
        try {
          await api(`/api/telegram/chats/${encodeURIComponent(button.dataset.tgRemove)}`, {
            method: "DELETE",
          });
          loadTelegramChats();
        } catch (error) {
          toast(error.message, "error");
        }
      })
    );
  } catch (error) {
    info.textContent = "errore: " + error.message;
  }
}

let gtfsPoll = null;

async function loadGtfsStatus() {
  try {
    renderGtfs(await api("/api/gtfs"));
  } catch (error) {
    $("#gtfsStatus").textContent = "errore: " + error.message;
    $("#gtfsStatus").className = "field-hint err";
    $("#gtfsFeeds").innerHTML = "";
  }
}

function renderGtfs(data) {
  const hint = $("#gtfsStatus");
  const box = $("#gtfsFeeds");
  const imported = (data.feeds || []).filter((feed) => feed.imported).length;
  if (data.running) {
    hint.textContent = (data.log || []).slice(-1)[0] || "aggiornamento in corso…";
    hint.className = "field-hint";
  } else {
    hint.textContent = `${imported}/${(data.feeds || []).length} feed importati · ${data.enabled ? "attivi" : "disattivati"}`;
    hint.className = "field-hint" + (imported ? " ok" : "");
  }
  box.innerHTML = (data.feeds || []).map((feed) => `
    <div class="gtfs-feed ${feed.imported ? "on" : ""}">
      <span class="gtfs-dot"></span>
      <span class="gtfs-name">${esc(feed.label)}</span>
      <span class="gtfs-meta">${feed.imported ? `${feed.stops} fermate · ${feed.trips} corse` : (feed.license || "")}</span>
    </div>`).join("");
}

async function updateGtfs() {
  const hint = $("#gtfsStatus");
  hint.textContent = "avvio aggiornamento…";
  hint.className = "field-hint";
  try {
    await api("/api/gtfs/update", { method: "POST" });
  } catch (error) {
    hint.textContent = "Errore: " + error.message;
    hint.className = "field-hint err";
    return;
  }
  if (gtfsPoll) clearInterval(gtfsPoll);
  gtfsPoll = setInterval(async () => {
    try {
      const data = await api("/api/gtfs");
      renderGtfs(data);
      if (!data.running) {
        clearInterval(gtfsPoll);
        gtfsPoll = null;
      }
    } catch (_) { /* ignora errori di polling */ }
  }, 3000);
}

function urlBase64ToUint8Array(base64) {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

async function renderPushStatus() {
  const hint = $("#pushStatus");
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    hint.textContent = "Questo browser non supporta le notifiche push.";
    hint.className = "field-hint err";
    return;
  }
  try {
    const data = await api("/api/push/status");
    if (!data.available) {
      hint.textContent = "Server: libreria push non installata (pip install pywebpush).";
      hint.className = "field-hint err";
      return;
    }
    const registration = await navigator.serviceWorker.getRegistration();
    const subscription = registration ? await registration.pushManager.getSubscription() : null;
    hint.textContent = subscription
      ? `Attive su questo dispositivo · ${data.subscriptions} dispositivi collegati`
      : `Non attive su questo dispositivo · ${data.subscriptions} dispositivi collegati`;
    hint.className = "field-hint" + (subscription ? " ok" : "");
  } catch (error) {
    hint.textContent = "Errore: " + error.message;
    hint.className = "field-hint err";
  }
}

async function enablePush() {
  const hint = $("#pushStatus");
  try {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
      throw new Error("push non supportate da questo browser");
    }
    const permission = await Notification.requestPermission();
    if (permission !== "granted") throw new Error("permesso notifiche negato");
    const data = await api("/api/push/status");
    if (!data.available) throw new Error("server: libreria push non installata");
    const registration = await navigator.serviceWorker.ready;
    let subscription = await registration.pushManager.getSubscription();
    if (!subscription) {
      subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(data.public_key),
      });
    }
    const json = subscription.toJSON();
    await api("/api/push/subscribe", {
      method: "POST",
      body: JSON.stringify({
        endpoint: subscription.endpoint,
        keys: { p256dh: json.keys.p256dh, auth: json.keys.auth },
        label: navigator.platform || "dispositivo",
      }),
    });
    await renderPushStatus();
  } catch (error) {
    hint.textContent = "Errore: " + error.message;
    hint.className = "field-hint err";
  }
}

async function disablePush() {
  const hint = $("#pushStatus");
  try {
    const registration = await navigator.serviceWorker.getRegistration();
    const subscription = registration ? await registration.pushManager.getSubscription() : null;
    if (subscription) {
      await api("/api/push/unsubscribe", {
        method: "POST",
        body: JSON.stringify({ endpoint: subscription.endpoint }),
      });
      await subscription.unsubscribe();
    }
    await renderPushStatus();
  } catch (error) {
    hint.textContent = "Errore: " + error.message;
    hint.className = "field-hint err";
  }
}

async function testPush() {
  const hint = $("#pushStatus");
  hint.textContent = "invio…";
  hint.className = "field-hint";
  try {
    const data = await api("/api/push/test", { method: "POST" });
    if (!data.ok) throw new Error(data.error || "nessun dispositivo raggiunto");
    toast("Notifica di prova inviata");
  } catch (error) {
    hint.textContent = "Errore: " + error.message;
    hint.className = "field-hint err";
    return;
  }
  await renderPushStatus();
}

async function testTelegram() {
  const hint = $("#telegramTestResult");
  hint.textContent = "invio…";
  hint.className = "field-hint";
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify(settingsPayload()) });
    const result = await api("/api/settings/test-telegram", { method: "POST" });
    hint.textContent = result.ok ? "messaggio inviato" : "errore: " + result.error;
    hint.className = "field-hint " + (result.ok ? "ok" : "err");
  } catch (error) {
    hint.textContent = "Errore: " + error.message;
    hint.className = "field-hint err";
  }
}

/* ------------------------------------------------------------------ */
/* Chiedi all'agente                                                  */
/* ------------------------------------------------------------------ */
const CHAT_CARICATA = new Set();

// Lo storico arriva dal server: la conversazione resta anche dopo il ricarico.
async function loadChatHistory(watchId) {
  try {
    const dati = await api(`/api/watches/${watchId}/chat`);
    CHAT[watchId] = (dati.messages || []).map((m) => ({
      ruolo: m.role === "io" ? "io" : "agente",
      testo: m.text || "",
      cambiamenti: m.changes || [],
    }));
  } catch (error) {
    CHAT[watchId] = CHAT[watchId] || [];
  }
  CHAT_CARICATA.add(Number(watchId));
  if (
    state.watch &&
    Number(state.watch.id) === Number(watchId) &&
    state.tab === "ask"
  ) {
    renderTab();
  }
}

// Il pulsante flottante: la chat e' a portata di mano da qualsiasi scheda.
function apriChat() {
  if (!state.watch) {
    toast("Prima scegli una ricerca: poi chiedimi quello che vuoi.");
    return;
  }
  state.tab = "ask";
  $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === "ask"));
  renderTab();
  $("#tabs")?.scrollIntoView({ behavior: "smooth", block: "start" });
  setTimeout(() => $("#chatText")?.focus(), 150);
}

// "Chiudi": torna alla scheda delle offerte, cosi' la chat non resta appesa.
function chiudiChat() {
  state.tab = "best";
  $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === "best"));
  renderTab();
}

function renderChat(panel) {
  const watch = state.watch;
  if (!watch) return;
  if (!CHAT_CARICATA.has(Number(watch.id))) {
    panel.innerHTML = `<div class="loading">Carico la conversazione…</div>`;
    loadChatHistory(watch.id);
    return;
  }
  const messaggi = CHAT[watch.id] || [];
  const esempi = [
    "Quanto costa il viaggio piu' economico?",
    "Qual e' l'opzione che arriva prima?",
    "Alzo il budget a 15 euro",
    "Preferisco viaggiare in treno, senza cambi",
    "Ho l'abbonamento IVOL",
  ];
  panel.innerHTML = `
    <div class="chat">
      <div class="chat-head">
        <div class="chat-head-row">
          <strong>Chiedi all'agente</strong>
          <div class="chat-azioni">
            ${
              messaggi.length
                ? `<button class="btn ghost tiny" type="button" id="chatPulisci">Azzera</button>`
                : ""
            }
            <button class="btn ghost tiny" type="button" id="chatChiudi">Chiudi</button>
          </div>
        </div>
        <small class="field-hint">
          Fai una domanda sui viaggi trovati o cambia la ricerca a parole
          (budget, orario, mezzo, abbonamento, punto di partenza). Le risposte
          usano i prezzi e gli orari reali di questa ricerca.
        </small>
      </div>
      <div class="chat-messaggi" id="chatMessaggi">
        ${
          messaggi.length
            ? messaggi
                .map(
                  (m) => `<div class="chat-msg ${m.ruolo === "io" ? "me" : "agente"}">
                    <span class="chat-testi">${esc(m.testo)}</span>
                    ${
                      m.cambiamenti && m.cambiamenti.length
                        ? `<small class="chat-cambio">aggiornato: ${esc(m.cambiamenti.join(", "))}</small>`
                        : ""
                    }
                  </div>`
                )
                .join("")
            : `<div class="chat-vuoto">
                <p>Nessuna domanda per ora. Prova con una di queste:</p>
                <div class="chips">
                  ${esempi
                    .map(
                      (e) =>
                        `<button class="chip chat-esempio" data-esempio="${esc(e)}">${esc(e)}</button>`
                    )
                    .join("")}
                </div>
              </div>`
        }
      </div>
      <form class="chat-input" id="chatForm">
        <input
          id="chatText"
          type="text"
          maxlength="1000"
          autocomplete="off"
          placeholder="Scrivi la domanda o la richiesta…"
        />
        <button class="btn primary" type="submit" id="chatSend">Invia</button>
      </form>
      <small class="field-hint" id="chatStato"></small>
    </div>`;

  $("#chatForm")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("#chatText");
    const testo = (input?.value || "").trim();
    if (!testo) return;
    input.value = "";
    chiediAllAgente(testo);
  });
  panel.querySelectorAll(".chat-esempio").forEach((bottone) => {
    bottone.addEventListener("click", () => chiediAllAgente(bottone.dataset.esempio));
  });
  $("#chatPulisci")?.addEventListener("click", async () => {
    try {
      await api(`/api/watches/${watch.id}/chat`, { method: "DELETE" });
    } catch (error) {
      /* se lo storico non si azzera, si riprova al prossimo giro */
    }
    CHAT[watch.id] = [];
    renderChat($("#tabPanel"));
  });
  $("#chatChiudi")?.addEventListener("click", chiudiChat);
  const scatola = $("#chatMessaggi");
  if (scatola) scatola.scrollTop = scatola.scrollHeight;
}

async function chiediAllAgente(testo) {
  const watch = state.watch;
  if (!watch) return;
  const lista = CHAT[watch.id] || (CHAT[watch.id] = []);
  lista.push({ ruolo: "io", testo });
  renderChat($("#tabPanel"));
  const stato = $("#chatStato");
  if (stato) stato.textContent = "Sto pensando…";
  const pulsante = $("#chatSend");
  if (pulsante) pulsante.disabled = true;
  try {
    const risultato = await api(`/api/watches/${watch.id}/ask`, {
      method: "POST",
      body: JSON.stringify({ text: testo }),
    });
    const cambiamenti = risultato.changes_labels || [];
    lista.push({
      ruolo: "agente",
      testo: risultato.reply || "Non ho capito la richiesta.",
      cambiamenti,
    });
    if (risultato.rerun) {
      if (stato) stato.textContent = "Ho aggiornato la ricerca, ricalcolo…";
      setTimeout(() => refreshAll(), 1500);
      setTimeout(() => refreshAll(), 5000);
    } else if (stato) {
      stato.textContent = "";
    }
  } catch (error) {
    lista.push({ ruolo: "agente", testo: "Non sono riuscito a rispondere: " + error.message });
    if (stato) stato.textContent = "";
  } finally {
    if (pulsante) pulsante.disabled = false;
    renderChat($("#tabPanel"));
  }
}

/* ------------------------------------------------------------------ */
/* Avvisi                                                             */
/* ------------------------------------------------------------------ */
async function loadAlerts(initial = false) {
  const alerts = await api("/api/alerts?limit=40");
  state.alerts = alerts;
  const unread = alerts.filter((a) => !a.read);
  const badge = $("#alertBadge");
  badge.textContent = unread.length;
  badge.classList.toggle("hidden", unread.length === 0);

  if (alerts.length) {
    const maxId = Math.max(...alerts.map((a) => a.id));
    if (!initial && maxId > state.lastAlertId) {
      const newest = alerts.filter((a) => a.id > state.lastAlertId);
      newest.slice(0, 2).forEach((alert) => toast(alert.title + " · " + routeOfAlert(alert)));
      maybeDesktopNotify(newest);
    }
    state.lastAlertId = Math.max(state.lastAlertId, maxId);
    localStorage.setItem("ch_last_alert", String(state.lastAlertId));
  }

  renderAlerts();
}

function routeOfAlert(alert) {
  const watch = state.watches.find((w) => w.id === alert.watch_id);
  return watch ? `${watch.from_place} → ${watch.to_place}` : "";
}

function renderAlerts() {
  const box = $("#alertsList");
  if (!state.alerts.length) {
    box.innerHTML = `<div class="hint hint-block roomy">Nessun avviso. Quando l'agente trova qualcosa di nuovo lo vedrai qui.</div>`;
    return;
  }
  box.innerHTML = state.alerts.map((alert) => `
    <div class="alert-item ${alert.read ? "" : "unread"}">
      <h4>${esc(alert.title)}</h4>
      <time>${fmtDate(alert.created_at)} · ${esc(routeOfAlert(alert))}</time>
      <p>${esc(alert.message)}</p>
    </div>`).join("");
}

function maybeDesktopNotify(alerts) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  for (const alert of alerts) {
    const watch = state.watches.find((w) => w.id === alert.watch_id);
    if (!watch || !watch.notify?.includes("desktop")) continue;
    try {
      new Notification("Carpool Hunter · " + alert.title, {
        body: routeOfAlert(alert),
        tag: "carpool-" + alert.id,
      });
    } catch (_) { /* ignore */ }
  }
}

async function markAllRead() {
  try {
    await api("/api/alerts/read", { method: "POST", body: JSON.stringify({}) });
    await loadAlerts(false);
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ------------------------------------------------------------------ */
/* Offerte e codici sconto                                            */
/* ------------------------------------------------------------------ */
async function openPromos(force = false) {
  openModal("promosModal");
  const body = $("#promosBody");
  body.innerHTML = `<div class="loading">Carico le offerte…</div>`;
  try {
    const data = await api("/api/promos" + (force ? "?refresh=true" : ""));
    renderPromos(data);
  } catch (error) {
    body.innerHTML = `<div class="form-error">${esc(error.message)}</div>`;
  }
}

function promoCards(items, subtitle) {
  return items.map((item) => `
    <a class="link-card" href="${esc(item.url)}" target="_blank" rel="noreferrer">
      <div class="lc-head"><strong>${esc(item.title || item.operator)}</strong><span class="lc-arrow">↗</span></div>
      <small>${esc(subtitle(item))}</small>
    </a>`).join("");
}

/* Raggruppa per operatore: le offerte di un vettore insieme. */
function perOperatore(elementi) {
  const gruppi = {};
  for (const item of elementi) {
    const chiave = (item.operator || "Altro").trim() || "Altro";
    (gruppi[chiave] = gruppi[chiave] || []).push(item);
  }
  return Object.entries(gruppi).sort((a, b) => a[0].localeCompare(b[0]));
}

function renderPromos(data) {
  const offers = data.offers || [];
  const coupons = data.coupons || [];
  const errors = data.errors || [];
  const updated = data.updated_at ? fmtDate(data.updated_at) : "";

  const gruppiOfferte = perOperatore(offers);
  const offersHtml = gruppiOfferte.length
    ? gruppiOfferte
        .map(
          ([operatore, voci]) => `<div class="promo-gruppo">
            <h5>${esc(operatore)}</h5>
            <div class="links-grid">${promoCards(voci, () => "offerta")}</div>
          </div>`
        )
        .join("")
    : `<div class="hint hint-block roomy">Nessuna offerta letta adesso: riprova tra poco o apri le pagine dei vettori qui sotto.</div>`;

  const gruppiCoupon = perOperatore(coupons);
  const couponsHtml = gruppiCoupon.length
    ? gruppiCoupon
        .map(
          ([operatore, voci]) => `<div class="promo-gruppo">
            <h5>${esc(operatore)}</h5>
            <div class="links-grid">${promoCards(voci, (c) => c.note || "codici e promozioni")}</div>
          </div>`
        )
        .join("")
    : `<div class="hint hint-block roomy">Nessun codice trovato.</div>`;

  $("#promosBody").innerHTML = `
    <p class="muted note-block tight">
      Offerte e codici letti direttamente dalle pagine ufficiali dei vettori:
      quello che trovi qui e' quello che vale adesso, non un archivio.
      ${updated ? `Ultimo aggiornamento: ${esc(updated)}.` : ""}
    </p>
    <div class="section-title">Offerte del momento</div>
    ${offersHtml}
    <div class="section-title">Codici sconto &amp; voucher</div>
    <p class="muted note-block soft">
      I codici veri stanno nelle pagine ufficiali del vettore: le apri con un
      tocco e trovi quello attivo.
    </p>
    ${couponsHtml}
    ${errors.length ? `<small class="field-hint">Alcune pagine non hanno risposto: ${esc(errors.join(" · "))}</small>` : ""}`;
}

/* ------------------------------------------------------------------ */
/* AI: prova e modelli                                                */
/* ------------------------------------------------------------------ */
async function testAi() {
  const hint = $("#aiTestResult");
  hint.textContent = "provo…";
  hint.className = "field-hint";
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify(settingsPayload()) });
    const result = await api("/api/ai/test", { method: "POST" });
    if (result.ok) {
      hint.textContent = `risposta da ${shortModel(result.model)}: «${result.reply}»`;
      hint.className = "field-hint ok";
      await loadStatus();
    } else {
      hint.textContent = "errore: " + (result.error || "nessuna risposta");
      hint.className = "field-hint err";
    }
  } catch (error) {
    hint.textContent = "Errore: " + error.message;
    hint.className = "field-hint err";
  }
}

async function listAiModels() {
  const hint = $("#aiTestResult");
  hint.textContent = "carico l'elenco…";
  hint.className = "field-hint";
  try {
    // Salva prima: la chiave potrebbe essere appena stata inserita.
    await api("/api/settings", { method: "PUT", body: JSON.stringify(settingsPayload()) });
    await loadModelOptions($("#sAiSmart").value, $("#sAiFast").value);
  } catch (error) {
    hint.textContent = "Errore: " + error.message;
    hint.className = "field-hint err";
  }
}

/* ------------------------------------------------------------------ */
/* PWA: service worker e installazione                                */
/* ------------------------------------------------------------------ */
function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.register("sw.js").catch(() => { /* offline non disponibile */ });
}

let deferredPrompt = null;
window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  deferredPrompt = event;
  const button = $("#btnInstall");
  if (button) {
    button.classList.remove("hidden");
    button.addEventListener("click", async () => {
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      await deferredPrompt.userChoice;
      deferredPrompt = null;
      button.classList.add("hidden");
    }, { once: true });
  }
});

/* Rende disponibile runNow() agli onclick inline generati da emptyTab */
window.runNow = runNow;
